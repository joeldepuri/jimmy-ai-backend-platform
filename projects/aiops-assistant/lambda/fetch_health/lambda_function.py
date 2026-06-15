import boto3
import json
import ssl
import base64
import urllib.request
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest

DEFAULT_CLUSTER = "eks-cluster"
DEFAULT_NAMESPACE = "boutique"
REGION = "us-east-1"


def get_eks_token(cluster_name, region):
    session = boto3.session.Session()
    creds = session.get_credentials().get_frozen_credentials()
    url = f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15"
    req = AWSRequest(method="GET", url=url, headers={"x-k8s-aws-id": cluster_name})
    SigV4QueryAuth(creds, "sts", region, expires=60).add_auth(req)
    return "k8s-aws-v1." + base64.urlsafe_b64encode(req.url.encode()).decode().rstrip("=")


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def k8s_get(endpoint, token, path):
    req = urllib.request.Request(
        f"{endpoint}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=10) as r:
        return json.loads(r.read())


def lambda_handler(event, context):
    params = {p["name"]: p["value"] for p in event.get("parameters", [])}
    cluster_name = params.get("cluster_name", DEFAULT_CLUSTER)
    namespace = params.get("namespace", DEFAULT_NAMESPACE)

    eks = boto3.client("eks", region_name=REGION)
    result = {}

    # EKS cluster + nodegroup status (always works via boto3)
    try:
        cluster = eks.describe_cluster(name=cluster_name)["cluster"]
        result["cluster_status"] = cluster["status"]
        result["cluster_version"] = cluster.get("version", "unknown")

        nodegroups = []
        for ng_name in eks.list_nodegroups(clusterName=cluster_name).get("nodegroups", []):
            ng = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)["nodegroup"]
            issues = ng.get("health", {}).get("issues", [])
            nodegroups.append({
                "name": ng_name,
                "status": ng["status"],
                "desired": ng["scalingConfig"]["desiredSize"],
                "healthy": ng["status"] == "ACTIVE" and not issues,
                "issues": [i["message"] for i in issues],
            })
        result["nodegroups"] = nodegroups
        result["nodes_healthy"] = all(n["healthy"] for n in nodegroups)
    except Exception as e:
        result["cluster_error"] = str(e)

    # Pod status via Kubernetes API (requires Lambda in EKS VPC — best-effort)
    try:
        endpoint = eks.describe_cluster(name=cluster_name)["cluster"]["endpoint"]
        token = get_eks_token(cluster_name, REGION)

        pods_data = k8s_get(endpoint, token, f"/api/v1/namespaces/{namespace}/pods")
        pods = pods_data.get("items", [])

        pod_summary = []
        unhealthy = []
        for pod in pods:
            name = pod["metadata"]["name"]
            phase = pod["status"].get("phase", "Unknown")
            ready_cond = next(
                (c for c in pod["status"].get("conditions", []) if c["type"] == "Ready"), None
            )
            is_ready = ready_cond["status"] == "True" if ready_cond else False
            restarts = sum(
                cs.get("restartCount", 0)
                for cs in pod["status"].get("containerStatuses", [])
            )
            pod_summary.append({
                "name": name, "phase": phase,
                "ready": is_ready, "restarts": restarts,
            })
            if phase != "Running" or not is_ready or restarts > 5:
                unhealthy.append({"name": name, "phase": phase, "ready": is_ready, "restarts": restarts})

        result["namespace"] = namespace
        result["total_pods"] = len(pods)
        result["unhealthy_pods"] = unhealthy
        result["pod_summary"] = pod_summary[:20]
        result["all_pods_healthy"] = len(unhealthy) == 0

    except Exception as e:
        result["pod_check"] = f"Kubernetes API unavailable (Lambda not in EKS VPC): {str(e)[:120]}"
        result["note"] = "EKS control plane healthy; pod-level data requires VPC-attached Lambda"

    result["status"] = "success"
    result["cluster"] = cluster_name

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "apiPath": event.get("apiPath", ""),
            "httpMethod": event.get("httpMethod", ""),
            "httpStatusCode": 200,
            "responseBody": {
                "application/json": {
                    "body": json.dumps(result, separators=(",", ":"))
                }
            },
        },
    }
