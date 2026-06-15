"""
Jimmy Incident Detector — triggered by EventBridge every 5 minutes.

Detects 7 widespread Kubernetes incident types:
  1. CrashLoopBackOff          — container crash-restarting
  2. OOMKilled                 — container killed due to memory limit
  3. ImagePullBackOff          — cannot pull container image
  4. Readiness Probe Failure   — pod Running but 0/1 Ready
  5. High Restart Count        — pod recovered but has restarted > 5 times
  6. Pending Too Long          — pod stuck Pending > 5 minutes
  7. Service No Endpoints      — deployment has 0 available replicas (label mismatch / all NotReady)

On any incident: invokes Jimmy (Bedrock Agent) with full context.
Jimmy then: fetch_runbook → remediate → send_incident_report (SNS → Gmail)
"""

import boto3
import json
import os
import ssl
import uuid
import urllib.request
import base64
from datetime import datetime, timezone
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest

REGION           = os.environ.get("AWS_REGION", "us-east-1")
CLUSTER_NAME     = os.environ.get("EKS_CLUSTER_NAME", "eks-cluster")
NAMESPACE        = os.environ.get("K8S_NAMESPACE", "boutique")
BEDROCK_AGENT_ID = os.environ.get("BEDROCK_AGENT_ID", "DV8ZOYJQ2M")
BEDROCK_ALIAS_ID = os.environ.get("BEDROCK_AGENT_ALIAS_ID", "TSTALIASID")
RESTART_THRESHOLD = 5
PENDING_THRESHOLD_SECONDS = 300  # 5 minutes

CRASH_REASONS  = {"CrashLoopBackOff", "Error", "OOMKilled", "OOMKill"}
PULL_REASONS   = {"ImagePullBackOff", "ErrImagePull", "InvalidImageName"}


# ── EKS / K8s helpers ────────────────────────────────────────────────────────

def get_eks_token(cluster_name, region):
    session = boto3.session.Session()
    creds   = session.get_credentials().get_frozen_credentials()
    url     = f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15"
    req     = AWSRequest(method="GET", url=url, headers={"x-k8s-aws-id": cluster_name})
    SigV4QueryAuth(creds, "sts", region, expires=60).add_auth(req)
    return "k8s-aws-v1." + base64.urlsafe_b64encode(req.url.encode()).decode().rstrip("=")


def get_cluster_endpoint(cluster_name, region):
    return boto3.client("eks", region_name=region).describe_cluster(
        name=cluster_name)["cluster"]["endpoint"]


def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def k8s_get(endpoint, token, path):
    req = urllib.request.Request(
        f"{endpoint}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, context=_ctx(), timeout=15) as r:
        return json.loads(r.read())


# ── Incident detection ────────────────────────────────────────────────────────

def detect_all_incidents(endpoint, token, namespace):
    incidents = []
    now_ts    = datetime.now(timezone.utc).timestamp()

    pods_data = k8s_get(endpoint, token, f"/api/v1/namespaces/{namespace}/pods")
    pods      = pods_data.get("items", [])

    # ── 1‑5: per-pod checks ──────────────────────────────────────────────────
    for pod in pods:
        pod_name = pod["metadata"]["name"]
        phase    = pod["status"].get("phase", "")

        all_cs = (
            pod["status"].get("containerStatuses", []) +
            pod["status"].get("initContainerStatuses", [])
        )

        for cs in all_cs:
            waiting    = cs.get("state", {}).get("waiting", {})
            terminated = cs.get("state", {}).get("terminated", {})
            reason     = waiting.get("reason") or terminated.get("reason", "")
            restarts   = cs.get("restartCount", 0)

            # 1. CrashLoopBackOff
            if reason == "CrashLoopBackOff":
                incidents.append({
                    "type": "pod-crashloop",
                    "pod": pod_name, "container": cs["name"],
                    "reason": reason, "restarts": restarts,
                    "severity": "high",
                    "description": f"Pod '{pod_name}' is in CrashLoopBackOff with {restarts} restarts. Container '{cs['name']}' keeps crashing on startup.",
                })

            # 2. OOMKilled
            elif "OOM" in reason:
                incidents.append({
                    "type": "oom-killed",
                    "pod": pod_name, "container": cs["name"],
                    "reason": reason, "restarts": restarts,
                    "severity": "high",
                    "description": f"Pod '{pod_name}' container '{cs['name']}' was OOMKilled ({restarts} restarts). Memory limit exceeded.",
                })

            # 3. ImagePullBackOff
            elif reason in PULL_REASONS:
                msg = waiting.get("message", "")
                incidents.append({
                    "type": "image-pull-error",
                    "pod": pod_name, "container": cs["name"],
                    "reason": reason,
                    "severity": "high",
                    "description": f"Pod '{pod_name}' cannot pull image. Reason: {reason}. {msg}",
                })

            # 4. High restart count (pod recovered but previously crashed)
            elif restarts >= RESTART_THRESHOLD and phase == "Running":
                incidents.append({
                    "type": "pod-crashloop",
                    "pod": pod_name, "container": cs["name"],
                    "reason": f"HighRestartCount({restarts})",
                    "restarts": restarts,
                    "severity": "medium",
                    "description": f"Pod '{pod_name}' has restarted {restarts} times. Currently Running but unstable.",
                })

        # 5. Readiness probe failure — Running but not Ready
        if phase == "Running":
            conditions = pod["status"].get("conditions", [])
            ready_cond = next((c for c in conditions if c["type"] == "Ready"), None)
            if ready_cond and ready_cond["status"] == "False":
                msg = ready_cond.get("message", "readiness probe failed")
                incidents.append({
                    "type": "readiness-probe-failure",
                    "pod": pod_name,
                    "reason": "NotReady",
                    "severity": "high",
                    "description": f"Pod '{pod_name}' is Running but NOT Ready (0/1). It cannot serve traffic. Message: {msg}",
                })

        # 6. Pending too long
        if phase == "Pending":
            start_raw = pod["metadata"].get("creationTimestamp", "")
            if start_raw:
                try:
                    start_ts = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).timestamp()
                    pending_secs = now_ts - start_ts
                    if pending_secs > PENDING_THRESHOLD_SECONDS:
                        incidents.append({
                            "type": "deployment-unavailable",
                            "pod": pod_name,
                            "reason": f"PendingTooLong({int(pending_secs)}s)",
                            "severity": "high",
                            "description": f"Pod '{pod_name}' has been Pending for {int(pending_secs//60)} min. Possible node capacity or scheduling issue.",
                        })
                except Exception:
                    pass

    # ── 7: Service no-endpoints check ────────────────────────────────────────
    try:
        deploys = k8s_get(endpoint, token, f"/apis/apps/v1/namespaces/{namespace}/deployments")
        for dep in deploys.get("items", []):
            dep_name  = dep["metadata"]["name"]
            desired   = dep["spec"].get("replicas", 1)
            available = dep["status"].get("availableReplicas", 0)
            ready     = dep["status"].get("readyReplicas", 0)
            if desired > 0 and available == 0 and ready == 0:
                incidents.append({
                    "type": "service-no-endpoints",
                    "deployment": dep_name,
                    "reason": "ZeroAvailableReplicas",
                    "severity": "critical",
                    "description": (
                        f"Deployment '{dep_name}' has {desired} desired replicas but 0 available. "
                        "Service will return 503 — possible label mismatch or all pods failing readiness."
                    ),
                })
    except Exception:
        pass

    return incidents


# ── Deduplicate ───────────────────────────────────────────────────────────────

def deduplicate(incidents):
    seen = set()
    out  = []
    for i in incidents:
        key = (i["type"], i.get("pod", i.get("deployment", "")))
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


# ── Jimmy invocation ──────────────────────────────────────────────────────────

def build_prompt(incidents):
    lines = []
    for idx, inc in enumerate(incidents, 1):
        svc   = inc.get("pod") or inc.get("deployment", "unknown")
        lines.append(f"{idx}. [{inc['type'].upper()}] {inc['description']}")

    return (
        f"AUTOMATED MULTI-INCIDENT ALERT\n"
        f"Cluster: {CLUSTER_NAME}  |  Namespace: {NAMESPACE}  |  Incidents: {len(incidents)}\n"
        f"{'='*60}\n\n"
        + "\n".join(lines)
        + "\n\n"
        "For EACH incident above, please:\n"
        "  1. Confirm with fetch_service_health\n"
        "  2. Fetch the relevant runbook from S3 using fetch_runbook\n"
        "  3. Execute the first-response remediation (restart_pod or scale_deployment)\n"
        "  4. Verify the fix with fetch_service_health\n"
        "Then send ONE consolidated send_incident_report covering all incidents:\n"
        "  - severity = highest severity seen\n"
        "  - list every incident, runbook used, and action taken\n"
        "  - resolution_status = resolved/mitigated/escalated as appropriate"
    )


def invoke_jimmy(incidents):
    client  = boto3.client("bedrock-agent-runtime", region_name=REGION)
    prompt  = build_prompt(incidents)
    output  = ""
    try:
        resp = client.invoke_agent(
            agentId      = BEDROCK_AGENT_ID,
            agentAliasId = BEDROCK_ALIAS_ID,
            sessionId    = str(uuid.uuid4()),
            inputText    = prompt,
        )
        for event in resp["completion"]:
            if "chunk" in event and "bytes" in event["chunk"]:
                output += event["chunk"]["bytes"].decode("utf-8")
    except Exception as e:
        output = f"Error invoking Jimmy: {e}"
    return output


# ── Lambda handler ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    # Support manual invocation with injected test incidents
    test_incidents = event.get("test_incidents", [])

    if test_incidents:
        incidents = test_incidents
    else:
        try:
            endpoint = get_cluster_endpoint(CLUSTER_NAME, REGION)
            token    = get_eks_token(CLUSTER_NAME, REGION)
            raw      = detect_all_incidents(endpoint, token, NAMESPACE)
            incidents = deduplicate(raw)
        except Exception as e:
            incidents = [{"type": "api-error", "description": str(e), "severity": "high"}]

    if not incidents:
        return {"statusCode": 200, "body": json.dumps({"status": "healthy", "message": "All checks passed"})}

    jimmy_response = "Agent not configured"
    if BEDROCK_AGENT_ID:
        jimmy_response = invoke_jimmy(incidents)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "status":          "incidents_detected",
            "count":           len(incidents),
            "incidents":       incidents,
            "jimmy_response":  jimmy_response[:2000],
        }),
    }
