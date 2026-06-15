import boto3
import json
import ssl
import urllib.request
import urllib.parse
import base64
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest

DEFAULT_CLUSTER = "eks-cluster"
DEFAULT_NAMESPACE = "boutique"
REGION = "us-east-1"


def get_eks_token(cluster_name, region):
    """Generate a bearer token for EKS using STS presigned URL (equivalent to aws eks get-token)."""
    session = boto3.session.Session()
    credentials = session.get_credentials().get_frozen_credentials()
    url = f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15"
    request = AWSRequest(
        method="GET",
        url=url,
        headers={"x-k8s-aws-id": cluster_name},
    )
    SigV4QueryAuth(credentials, "sts", region, expires=60).add_auth(request)
    return "k8s-aws-v1." + base64.urlsafe_b64encode(request.url.encode()).decode().rstrip("=")


def get_cluster_endpoint(cluster_name, region):
    eks = boto3.client("eks", region_name=region)
    return eks.describe_cluster(name=cluster_name)["cluster"]["endpoint"]


def _ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def k8s_get(endpoint, token, path):
    req = urllib.request.Request(
        f"{endpoint}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=15) as resp:
        return json.loads(resp.read())


def k8s_delete(endpoint, token, path):
    req = urllib.request.Request(
        f"{endpoint}{path}",
        method="DELETE",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=15) as resp:
        return json.loads(resp.read())


def _parse_params(event):
    """Handle both GET query params and POST request body formats from Bedrock Agent."""
    params = {p["name"]: p["value"] for p in event.get("parameters", [])}
    body_props = (
        event.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("properties", [])
    )
    params.update({p["name"]: p["value"] for p in body_props})
    return params


def lambda_handler(event, context):
    params = _parse_params(event)

    cluster_name = params.get("cluster_name", DEFAULT_CLUSTER)
    namespace = params.get("namespace", DEFAULT_NAMESPACE)
    pod_name = params.get("pod_name", "")
    deployment_name = params.get("deployment_name", "")

    if not pod_name and not deployment_name:
        return _response(event, {"status": "error", "message": "Provide either pod_name or deployment_name"})

    try:
        endpoint = get_cluster_endpoint(cluster_name, REGION)
        token = get_eks_token(cluster_name, REGION)
        restarted = []

        if pod_name:
            k8s_delete(endpoint, token, f"/api/v1/namespaces/{namespace}/pods/{pod_name}")
            restarted.append(pod_name)
        else:
            pods_data = k8s_get(endpoint, token, f"/api/v1/namespaces/{namespace}/pods")
            for pod in pods_data.get("items", []):
                name = pod["metadata"]["name"]
                app_label = pod["metadata"].get("labels", {}).get("app", "")
                if name.startswith(deployment_name) or app_label == deployment_name:
                    k8s_delete(endpoint, token, f"/api/v1/namespaces/{namespace}/pods/{name}")
                    restarted.append(name)

        if not restarted:
            result = {
                "status": "not_found",
                "message": f"No pods matched '{pod_name or deployment_name}' in namespace {namespace}",
            }
        else:
            result = {
                "status": "restarted",
                "pods_restarted": restarted,
                "namespace": namespace,
                "cluster": cluster_name,
                "message": f"Deleted {len(restarted)} pod(s). Kubernetes will reschedule them automatically.",
            }

    except Exception as e:
        result = {"status": "error", "message": str(e)}

    return _response(event, result)


def _response(event, result):
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "apiPath": event.get("apiPath", ""),
            "httpMethod": event.get("httpMethod", ""),
            "httpStatusCode": 200,
            "responseBody": {"application/json": {"body": json.dumps(result, separators=(",", ":"))}},
        },
    }
