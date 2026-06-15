import boto3
import json
import os
from datetime import datetime, timezone

REGION = os.environ.get("AWS_REGION", "us-east-1")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:522814724315:aiops-incident-alerts")

SEVERITY_EMOJI = {"critical": "🚨", "high": "🔴", "medium": "🟡", "low": "🟢", "info": "ℹ️"}


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

    incident_summary  = params.get("incident_summary", "No summary provided")
    severity          = params.get("severity", "medium").lower()
    affected_service  = params.get("affected_service", "unknown")
    root_cause        = params.get("root_cause", "Under investigation")
    actions_taken     = params.get("actions_taken", "None yet")
    resolution_status = params.get("resolution_status", "investigating")

    emoji     = SEVERITY_EMOJI.get(severity, "⚠️")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    subject = f"{emoji} [{severity.upper()}] Jimmy Alert: {affected_service}"
    message = f"""{emoji} Jimmy — AIOps Runbook Automation Incident Report
Generated : {timestamp}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SEVERITY  : {severity.upper()}
SERVICE   : {affected_service}
STATUS    : {resolution_status.upper()}

SUMMARY
{incident_summary}

ROOT CAUSE
{root_cause}

ACTIONS TAKEN BY JIMMY
{actions_taken}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Agent   : Jimmy (AIOps Runbook Automation Agent)
Cluster : eks-cluster  |  Namespace : boutique
Region  : {REGION}
""".strip()

    try:
        sns = boto3.client("sns", region_name=REGION)
        resp = sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=message)
        result = {
            "status": "sent",
            "message_id": resp["MessageId"],
            "topic_arn": SNS_TOPIC_ARN,
            "severity": severity,
            "affected_service": affected_service,
            "resolution_status": resolution_status,
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
