#!/usr/bin/env bash
# =============================================================================
# Jimmy — AIOps Runbook Automation Agent  |  Full Deployment Script
#
# Creates / updates:
#   - 7 Lambda functions (fetch_logs, fetch_metrics, fetch_health,
#                         fetch_runbook, restart_pod, scale_deployment,
#                         send_incident_report)
#   - 1 incident_detector Lambda (triggered by EventBridge every 5 min)
#   - AWS Bedrock Agent "jimmy" with all 7 action groups
#   - EventBridge rule for automated incident detection
#
# Prerequisites (run setup-iam.sh first):
#   - IAM role aiops-lambda-role
#   - IAM role aiops-bedrock-agent-role
#   - S3 bucket aiops-runbooks-522814724315 (runbooks already uploaded)
#   - SNS topic aiops-incident-alerts (subscription confirmed)
# =============================================================================

set -euo pipefail

REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AGENT_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/aiops-bedrock-agent-role"
LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/aiops-lambda-role"
AGENT_NAME="jimmy"
SNS_TOPIC_ARN="arn:aws:sns:${REGION}:${ACCOUNT_ID}:aiops-incident-alerts"
RUNBOOK_BUCKET="aiops-runbooks-${ACCOUNT_ID}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "============================================="
echo " Jimmy — AIOps Runbook Automation Deployment"
echo " Account : $ACCOUNT_ID  |  Region: $REGION"
echo "============================================="
echo ""

# =============================================================================
# HELPER: package and deploy a Lambda function
# =============================================================================
deploy_lambda() {
  local func_name="$1"
  local src_dir="$2"
  local description="$3"
  local env_vars="${4:-}"

  echo "  Packaging $func_name ..."
  local zip_path="/tmp/${func_name}.zip"
  (cd "$src_dir" && zip -q "$zip_path" lambda_function.py)

  if aws lambda get-function --function-name "$func_name" --region "$REGION" &>/dev/null; then
    aws lambda update-function-code \
      --function-name "$func_name" \
      --zip-file "fileb://${zip_path}" \
      --region "$REGION" \
      --query 'FunctionName' --output text > /dev/null
    echo "  ✓ Updated: $func_name"
  else
    local create_args=(
      --function-name "$func_name"
      --runtime python3.12
      --role "$LAMBDA_ROLE_ARN"
      --handler lambda_function.lambda_handler
      --zip-file "fileb://${zip_path}"
      --timeout 60
      --description "$description"
      --region "$REGION"
    )
    if [ -n "$env_vars" ]; then
      create_args+=(--environment "Variables={${env_vars}}")
    fi
    aws lambda create-function "${create_args[@]}" \
      --query 'FunctionName' --output text > /dev/null
    echo "  ✓ Created: $func_name"
  fi

  # Allow Bedrock to invoke it
  aws lambda add-permission \
    --function-name "$func_name" \
    --statement-id AllowBedrockInvoke \
    --action lambda:InvokeFunction \
    --principal bedrock.amazonaws.com \
    --region "$REGION" 2>/dev/null || true
}

# =============================================================================
# STEP 1: Deploy all Lambda functions
# =============================================================================
echo "[1/4] Deploying Lambda functions..."

deploy_lambda "aiops-fetch-logs"    "$SCRIPT_DIR/lambda/fetch_logs"    "Jimmy: fetch CloudWatch logs"
deploy_lambda "aiops-fetch-metrics" "$SCRIPT_DIR/lambda/fetch_metrics" "Jimmy: fetch Prometheus metrics"
deploy_lambda "aiops-fetch-health"  "$SCRIPT_DIR/lambda/fetch_health"  "Jimmy: check EKS health"
deploy_lambda "aiops-fetch-runbook" "$SCRIPT_DIR/lambda/fetch_runbook" "Jimmy: fetch runbook from S3" \
  "RUNBOOK_BUCKET=${RUNBOOK_BUCKET}"
deploy_lambda "aiops-restart-pod"      "$SCRIPT_DIR/lambda/restart_pod"      "Jimmy: restart Kubernetes pod"
deploy_lambda "aiops-scale-deployment" "$SCRIPT_DIR/lambda/scale_deployment" "Jimmy: scale Kubernetes deployment"
deploy_lambda "aiops-send-report"      "$SCRIPT_DIR/lambda/send_incident_report" "Jimmy: send SNS incident report" \
  "SNS_TOPIC_ARN=${SNS_TOPIC_ARN}"

# Incident detector (not a Bedrock action group — invoked by EventBridge)
deploy_lambda "aiops-incident-detector" "$SCRIPT_DIR/lambda/incident_detector" \
  "Jimmy: scheduled incident detector (EventBridge every 5 min)"

echo ""
echo "[1/4] ✓ All Lambda functions deployed"

# =============================================================================
# STEP 2: Create / update Bedrock Agent "jimmy"
# =============================================================================
echo ""
echo "[2/4] Creating Bedrock Agent: $AGENT_NAME ..."

AGENT_INSTRUCTION="You are Jimmy, a senior Site Reliability Engineer with deep expertise in Kubernetes, AWS EKS, and production incident response. You run automated runbook-based remediation for the boutique microservices platform on EKS.

You have 7 tools:
1. fetch_service_health  — check EKS cluster, node groups, and pod health
2. fetch_logs            — search CloudWatch Logs for errors and warnings
3. fetch_metrics         — retrieve Prometheus metrics (CPU, memory, restarts)
4. fetch_runbook         — retrieve the relevant runbook from S3
5. restart_pod           — delete a pod so Kubernetes reschedules it fresh
6. scale_deployment      — change the number of replicas for a deployment
7. send_incident_report  — publish structured incident report via SNS (email)

RUNBOOK AUTOMATION WORKFLOW — always follow this sequence:
Step 1  ASSESS    — fetch_service_health to identify unhealthy pods or nodes
Step 2  DIAGNOSE  — fetch_logs for error pattern, fetch_metrics for CPU/memory spike
Step 3  CLASSIFY  — determine incident type (pod-crashloop, high-cpu, oom-killed, database-connection, deployment-unavailable)
Step 4  RUNBOOK   — fetch_runbook with the classified incident type, read it carefully
Step 5  REMEDIATE — follow the runbook: restart_pod or scale_deployment as directed
Step 6  VERIFY    — fetch_service_health again to confirm the fix worked
Step 7  REPORT    — send_incident_report with full root cause, actions taken, and resolution_status

RULES:
- Never skip the runbook lookup before taking remediation action
- Never restart a pod that is Running and healthy
- Scale to maximum 3 replicas without explicit human approval
- Always send_incident_report at the end, even when escalating
- When unsure, set resolution_status=escalated in the report
- Be concise but thorough — cite specific pod names, error messages, and metrics"

EXISTING_AGENT_ID=$(aws bedrock-agent list-agents \
  --region "$REGION" \
  --query "agentSummaries[?agentName=='$AGENT_NAME'].agentId | [0]" \
  --output text 2>/dev/null || echo "")

if [ -n "$EXISTING_AGENT_ID" ] && [ "$EXISTING_AGENT_ID" != "None" ]; then
  AGENT_ID="$EXISTING_AGENT_ID"
  echo "  ✓ Agent already exists: $AGENT_ID (updating instruction...)"
  aws bedrock-agent update-agent \
    --agent-id "$AGENT_ID" \
    --agent-name "$AGENT_NAME" \
    --agent-resource-role-arn "$AGENT_ROLE_ARN" \
    --instruction "$AGENT_INSTRUCTION" \
    --foundation-model "anthropic.claude-3-5-haiku-20241022-v1:0" \
    --region "$REGION" \
    --query 'agent.agentStatus' --output text > /dev/null
else
  AGENT_ID=$(aws bedrock-agent create-agent \
    --agent-name "$AGENT_NAME" \
    --agent-resource-role-arn "$AGENT_ROLE_ARN" \
    --foundation-model "anthropic.claude-3-5-haiku-20241022-v1:0" \
    --instruction "$AGENT_INSTRUCTION" \
    --region "$REGION" \
    --query 'agent.agentId' --output text)
  echo "  ✓ Agent created: $AGENT_ID"
  sleep 8
fi

# =============================================================================
# STEP 3: Create action groups
# =============================================================================
echo ""
echo "[3/4] Adding action groups..."

python3 - <<PYEOF
import boto3, json, sys

region     = "$REGION"
agent_id   = "$AGENT_ID"
account_id = "$ACCOUNT_ID"
script_dir = "$SCRIPT_DIR"

client = boto3.client("bedrock-agent", region_name=region)

action_groups = [
    {"name": "fetch_logs",            "func": "aiops-fetch-logs",       "schema": "fetch_logs.json",           "desc": "Search CloudWatch Logs for errors, warnings, and application events"},
    {"name": "fetch_metrics",         "func": "aiops-fetch-metrics",     "schema": "fetch_metrics.json",        "desc": "Retrieve Prometheus performance metrics (CPU, memory, restarts)"},
    {"name": "fetch_service_health",  "func": "aiops-fetch-health",      "schema": "fetch_health.json",         "desc": "Check live health of EKS cluster, node groups, deployments, and pods"},
    {"name": "fetch_runbook",         "func": "aiops-fetch-runbook",     "schema": "fetch_runbook.json",        "desc": "Retrieve the correct runbook from S3 for a given incident type"},
    {"name": "restart_pod",           "func": "aiops-restart-pod",       "schema": "restart_pod.json",          "desc": "Delete a pod or all pods of a deployment so Kubernetes reschedules them"},
    {"name": "scale_deployment",      "func": "aiops-scale-deployment",  "schema": "scale_deployment.json",     "desc": "Change replica count of a Kubernetes deployment"},
    {"name": "send_incident_report",  "func": "aiops-send-report",       "schema": "send_incident_report.json", "desc": "Publish structured incident report via SNS (email notification)"},
]

existing = client.list_agent_action_groups(agentId=agent_id, agentVersion="DRAFT")
existing_names = [ag["actionGroupName"] for ag in existing.get("actionGroupSummaries", [])]

for ag in action_groups:
    func_arn = f"arn:aws:lambda:{region}:{account_id}:function:{ag['func']}"
    with open(f"{script_dir}/schemas/{ag['schema']}") as f:
        schema = f.read()
    try:
        if ag["name"] in existing_names:
            # find and update existing action group
            ags = client.list_agent_action_groups(agentId=agent_id, agentVersion="DRAFT")
            ag_id = next(a["actionGroupId"] for a in ags["actionGroupSummaries"] if a["actionGroupName"] == ag["name"])
            client.update_agent_action_group(
                agentId=agent_id, agentVersion="DRAFT",
                actionGroupId=ag_id,
                actionGroupName=ag["name"],
                description=ag["desc"],
                actionGroupExecutor={"lambda": func_arn},
                apiSchema={"payload": schema},
                actionGroupState="ENABLED",
            )
            print(f"  ✓ Updated: {ag['name']}")
        else:
            client.create_agent_action_group(
                agentId=agent_id, agentVersion="DRAFT",
                actionGroupName=ag["name"],
                description=ag["desc"],
                actionGroupExecutor={"lambda": func_arn},
                apiSchema={"payload": schema},
            )
            print(f"  ✓ Created: {ag['name']}")
    except Exception as e:
        print(f"  ✗ {ag['name']}: {e}", file=sys.stderr)

# Prepare agent
client.prepare_agent(agentId=agent_id)
print("  ✓ Agent prepared (DRAFT → ready)")
PYEOF

# =============================================================================
# STEP 4: EventBridge rule for automated incident detection every 5 min
# =============================================================================
echo ""
echo "[4/4] Creating EventBridge schedule for incident detector..."

DETECTOR_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:aiops-incident-detector"

aws events put-rule \
  --name "jimmy-incident-detector" \
  --schedule-expression "rate(5 minutes)" \
  --description "Triggers Jimmy's incident_detector Lambda every 5 min" \
  --state ENABLED \
  --region "$REGION" \
  --query 'RuleArn' --output text > /dev/null

aws events put-targets \
  --rule "jimmy-incident-detector" \
  --targets "Id=jimmy-detector,Arn=${DETECTOR_ARN}" \
  --region "$REGION" > /dev/null

aws lambda add-permission \
  --function-name aiops-incident-detector \
  --statement-id AllowEventBridgeInvoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --region "$REGION" 2>/dev/null || true

echo "  ✓ EventBridge rule: jimmy-incident-detector (every 5 min)"

echo ""
echo "============================================="
echo " Done! Jimmy is live."
echo "============================================="
echo ""
echo " Agent ID   : $AGENT_ID"
echo " Alias      : TSTALIASID (test alias)"
echo " SNS Topic  : $SNS_TOPIC_ARN"
echo " Runbooks   : s3://$RUNBOOK_BUCKET"
echo ""
echo " Next steps:"
echo "  1. Copy .env.example → .env and set BEDROCK_AGENT_ID=$AGENT_ID"
echo "  2. Run the Streamlit UI:"
echo "     pip install -r requirements.txt"
echo "     streamlit run app.py"
echo "  3. Check your Gmail for the SNS subscription confirmation"
echo "     (must confirm or emails won't arrive)"
echo ""
echo " To test the full runbook flow, ask Jimmy:"
echo '  "order-service is in CrashLoopBackOff — investigate, fix, and report"'
echo ""
