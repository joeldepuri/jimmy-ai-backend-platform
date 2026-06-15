import boto3
import json
import os
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNBOOK_BUCKET = os.environ.get("RUNBOOK_BUCKET", "aiops-runbooks-522814724315")

RUNBOOK_INDEX = {
    "crashloop": "pod-crashloop.md",
    "crashloopbackoff": "pod-crashloop.md",
    "crash": "pod-crashloop.md",
    "oomkilled": "oom-killed.md",
    "oom": "oom-killed.md",
    "out-of-memory": "oom-killed.md",
    "memory": "oom-killed.md",
    "high-cpu": "high-cpu.md",
    "cpu": "high-cpu.md",
    "throttl": "high-cpu.md",
    "database": "database-connection.md",
    "db": "database-connection.md",
    "connection": "database-connection.md",
    "postgres": "database-connection.md",
    "unavailable": "deployment-unavailable.md",
    "pending": "deployment-unavailable.md",
    "imagepull": "image-pull-error.md",
    "errimagepull": "image-pull-error.md",
    "invalidimage": "image-pull-error.md",
    "readiness": "readiness-probe-failure.md",
    "probe": "readiness-probe-failure.md",
    "notready": "readiness-probe-failure.md",
    "noendpoints": "service-no-endpoints.md",
    "servicenoendpoints": "service-no-endpoints.md",
    "503": "service-no-endpoints.md",
    "labelmismatch": "service-no-endpoints.md",
    "zeroreplicasavailable": "service-no-endpoints.md",
}


def resolve_runbook_key(incident_type: str) -> str | None:
    normalized = incident_type.lower().replace(" ", "").replace("-", "").replace("_", "")
    for keyword, filename in RUNBOOK_INDEX.items():
        if keyword.replace("-", "") in normalized:
            return filename
    return None


def lambda_handler(event, context):
    params = {p["name"]: p["value"] for p in event.get("parameters", [])}
    incident_type = params.get("incident_type", "")

    runbook_key = resolve_runbook_key(incident_type)

    s3 = boto3.client("s3", region_name=REGION)

    if not runbook_key:
        try:
            resp = s3.list_objects_v2(Bucket=RUNBOOK_BUCKET)
            available = [o["Key"] for o in resp.get("Contents", [])]
        except Exception:
            available = []
        result = {
            "status": "not_found",
            "message": f"No runbook matched '{incident_type}'. Available: {', '.join(available)}",
        }
        return _response(event, result)

    try:
        obj = s3.get_object(Bucket=RUNBOOK_BUCKET, Key=runbook_key)
        content = obj["Body"].read().decode("utf-8")
        result = {
            "status": "found",
            "incident_type": incident_type,
            "runbook_key": runbook_key,
            "bucket": RUNBOOK_BUCKET,
            "content": content,
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            result = {"status": "not_found", "message": f"Runbook '{runbook_key}' missing from S3"}
        else:
            result = {"status": "error", "message": str(e)}
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
