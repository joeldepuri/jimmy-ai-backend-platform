import boto3
import json
import ssl
import urllib.request
import base64
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest

DEFAULT_CLUSTER = "eks-cluster"
DEFAULT_NAMESPACE = "boutique"
REGION = "us-east-1"


def get_eks_token(cluster_name, region):
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


def get_current_replicas(endpoint, token, namespace, deployment_name):
    url = f"{endpoint}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("spec", {}).get("replicas", 1)


def patch_replicas(endpoint, token, namespace, deployment_name, replicas):
    url = f"{endpoint}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}/scale"
    body = json.dumps({"spec": {"replicas": replicas}}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/merge-patch+json",
        },
    )
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=15) as resp:
        return json.loads(resp.read())


def _parse_params(event):
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
    deployment_name = params.get("deployment_name", "")
    replicas = int(params.get("replicas", "2"))
    reason = params.get("reason", "Scaled by AIOps runbook automation")

    if not deployment_name:
        return _response(event, {"status": "error", "message": "deployment_name is required"})

    if replicas < 1 or replicas > 10:
        return _response(event, {"status": "error", "message": "replicas must be between 1 and 10"})

    try:
        endpoint = get_cluster_endpoint(cluster_name, REGION)
        token = get_eks_token(cluster_name, REGION)

        current = get_current_replicas(endpoint, token, namespace, deployment_name)
        patch_replicas(endpoint, token, namespace, deployment_name, replicas)

        result = {
            "status": "scaled",
            "deployment": deployment_name,
            "namespace": namespace,
            "cluster": cluster_name,
            "previous_replicas": current,
            "new_replicas": replicas,
            "reason": reason,
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
