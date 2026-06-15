# AIOps Assistant — Jimmy

An AI-powered SRE agent built on AWS Bedrock. Jimmy automates incident response end-to-end: it detects Kubernetes incidents every 5 minutes, looks up the correct runbook from S3, remediates by restarting pods or scaling deployments, verifies the fix, and sends a structured email report via SNS.

This is a purpose-built evolution beyond the original **Kira** agent (by Vishakha Sadhwani), which focused on log analysis and root cause diagnosis. Jimmy adds autonomous remediation, runbook-driven workflows, and automated incident detection.

---

## Architecture

```
EventBridge (every 5 min)
      │
      ▼
incident_detector Lambda
  - Scans EKS cluster for 7 incident types
  - Deduplicates, builds prompt
      │
      ▼
Bedrock Agent (Jimmy)
      │
      ├── fetch_service_health  → EKS cluster + node groups + pods
      ├── fetch_logs            → CloudWatch Logs
      ├── fetch_metrics         → Prometheus (ELB endpoint)
      ├── fetch_runbook         → S3 runbook bucket
      ├── restart_pod           → Kubernetes API (delete pod)
      ├── scale_deployment      → Kubernetes API (patch replicas)
      └── send_incident_report  → SNS → Gmail
```

---

## Incident Types Detected

| # | Incident Type | Detection Logic |
|---|---|---|
| 1 | CrashLoopBackOff | Container waiting reason = CrashLoopBackOff |
| 2 | OOMKilled | Container terminated/waiting reason contains OOM |
| 3 | ImagePullBackOff | Waiting reason in {ImagePullBackOff, ErrImagePull} |
| 4 | Readiness Probe Failure | Phase=Running but Ready condition=False |
| 5 | High Restart Count | restartCount ≥ 5 while pod is Running |
| 6 | Pending Too Long | Phase=Pending for > 5 minutes |
| 7 | Service No Endpoints | Deployment has 0 availableReplicas + 0 readyReplicas |

---

## Runbook Automation Workflow

Jimmy always follows this 7-step sequence before taking any action:

```
Step 1  ASSESS    — fetch_service_health (identify unhealthy pods/nodes)
Step 2  DIAGNOSE  — fetch_logs + fetch_metrics (error pattern + spike)
Step 3  CLASSIFY  — determine incident type
Step 4  RUNBOOK   — fetch_runbook from S3 (always read before acting)
Step 5  REMEDIATE — restart_pod or scale_deployment per runbook guidance
Step 6  VERIFY    — fetch_service_health again (confirm fix worked)
Step 7  REPORT    — send_incident_report (SNS → Gmail)
```

---

## Prerequisites

- AWS account with Bedrock model access enabled (Claude 3.5 Haiku)
- EKS cluster (`eks-cluster`) running the boutique application
- Prometheus exposed as a LoadBalancer service in the `monitoring` namespace
- S3 bucket for runbooks (created by `deploy.sh`)
- SNS topic with a confirmed email subscription (Gmail)
- AWS CLI configured (`aws configure`)
- Python 3.10+

---

## Step 1: Set Up IAM Roles

```bash
chmod +x setup-iam.sh
./setup-iam.sh
```

This creates:

| Role | Used By | Permissions |
|------|---------|-------------|
| `aiops-lambda-role` | All Lambda functions | CloudWatch Logs read, EKS describe, Lambda basic execution, SNS publish, S3 read |
| `aiops-bedrock-agent-role` | Bedrock Agent | Invoke the 7 Lambda functions, invoke Bedrock models |

---

## Step 2: Upload Runbooks to S3

The `runbooks/` directory contains 8 Markdown runbooks. Upload them to your S3 bucket before running `deploy.sh`:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="aiops-runbooks-${ACCOUNT_ID}"

aws s3 mb s3://$BUCKET --region us-east-1

aws s3 cp runbooks/ s3://$BUCKET/runbooks/ --recursive
```

Available runbooks:

| Runbook | Incident Type |
|---------|--------------|
| `pod-crashloop.md` | CrashLoopBackOff, High Restart Count |
| `oom-killed.md` | OOMKilled |
| `image-pull-error.md` | ImagePullBackOff |
| `readiness-probe-failure.md` | Readiness Probe Failure |
| `deployment-unavailable.md` | Pending Too Long, Service No Endpoints |
| `service-no-endpoints.md` | Service No Endpoints |
| `high-cpu.md` | High CPU / resource pressure |
| `database-connection.md` | Database connectivity issues |

---

## Step 3: Update the Prometheus URL

Both `fetch_metrics` and `fetch_health` lambdas query Prometheus directly. Update `PROMETHEUS_URL` in each file before deploying:

```python
PROMETHEUS_URL = "http://<YOUR_PROMETHEUS_ELB_URL>:9090"
```

To get the Prometheus ELB URL:

```bash
kubectl patch svc kube-prometheus-stack-prometheus -n monitoring \
  -p '{"spec": {"type": "LoadBalancer"}}'

kubectl get svc kube-prometheus-stack-prometheus -n monitoring
# Copy the EXTERNAL-IP value
```

---

## Step 4: Deploy Everything

Run the deploy script. It handles all 8 Lambda functions, the Bedrock Agent, all 7 action groups, and the EventBridge schedule in one shot:

```bash
chmod +x deploy.sh
./deploy.sh
```

What it does:

1. **Deploys 8 Lambda functions** — 7 action groups (fetch_logs, fetch_metrics, fetch_health, fetch_runbook, restart_pod, scale_deployment, send_incident_report) + 1 EventBridge-triggered incident_detector
2. **Creates the Bedrock Agent** named `jimmy` with the full SRE system prompt
3. **Attaches all 7 action groups** with their OpenAPI schemas
4. **Creates the EventBridge rule** `jimmy-incident-detector` (rate: 5 minutes)

At the end, the script prints your **Agent ID**.

---

## Step 5: Confirm SNS Subscription

After `deploy.sh` runs, AWS sends a confirmation email to the address subscribed to `aiops-incident-alerts`. You must click the link in the email or Jimmy's reports won't be delivered.

---

## Step 6: Run the Streamlit UI

```bash
cp .env.example .env
```

Edit `.env`:

```env
AWS_REGION=us-east-1
BEDROCK_AGENT_ID=<YOUR_AGENT_ID>
BEDROCK_AGENT_ALIAS_ID=TSTALIASID

# Optional — omit to use AWS CLI profile / SSO / IAM role:
# AWS_ACCESS_KEY_ID=<YOUR_ACCESS_KEY>
# AWS_SECRET_ACCESS_KEY=<YOUR_SECRET_KEY>
# AWS_SESSION_TOKEN=<YOUR_SESSION_TOKEN>
```

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open **http://localhost:8501** in your browser.

---

## Project Structure

```
aiops-assistant/
├── app.py                      # Streamlit chat UI (Jimmy branding)
├── deploy.sh                   # Full deployment: 8 Lambdas + Bedrock Agent + EventBridge
├── setup-iam.sh                # IAM roles and policies setup
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
├── lambda/
│   ├── fetch_logs/             # CloudWatch Logs query
│   ├── fetch_metrics/          # Prometheus metrics query
│   ├── fetch_health/           # EKS cluster health check
│   ├── fetch_runbook/          # S3 runbook retrieval
│   ├── restart_pod/            # Kubernetes pod deletion (triggers reschedule)
│   ├── scale_deployment/       # Kubernetes replica count change
│   ├── send_incident_report/   # SNS publish → Gmail
│   └── incident_detector/      # EventBridge-triggered scanner (7 incident types)
├── runbooks/
│   ├── pod-crashloop.md
│   ├── oom-killed.md
│   ├── image-pull-error.md
│   ├── readiness-probe-failure.md
│   ├── deployment-unavailable.md
│   ├── service-no-endpoints.md
│   ├── high-cpu.md
│   └── database-connection.md
└── schemas/
    ├── fetch_logs.json
    ├── fetch_metrics.json
    ├── fetch_health.json
    ├── fetch_runbook.json
    ├── restart_pod.json
    ├── scale_deployment.json
    └── send_incident_report.json
```

---

## What Jimmy Can Answer

Jimmy is powered by **Claude 3.5 Haiku via AWS Bedrock**. The 7 automated incident types are what the `incident_detector` Lambda scans for on a schedule — they are not a limit on what you can ask Jimmy directly.

### Automated incident response (the 7 types)
These are handled autonomously by EventBridge → `incident_detector` → Jimmy without any human prompt:
- CrashLoopBackOff, OOMKilled, ImagePullBackOff, Readiness Probe Failure, High Restart Count, Pending Too Long, Service No Endpoints

### Any DevOps / Kubernetes question about this platform
Jimmy has access to your live cluster, logs, and metrics — so you can ask anything:

- `order-service is in CrashLoopBackOff — investigate, fix, and report`
- `Why are we seeing 503 errors in the last hour?`
- `Scale up frontend to handle traffic spike`
- `Run the OOMKilled runbook for auth pod`
- `Check all services and send me a health report`
- `Are all pods healthy? Any restarts?`
- `What does the CPU spike on product-service at 14:30 UTC mean?`
- `Explain what's in the pod-crashloop runbook`
- `How do I debug a Pending pod in Kubernetes?`
- `What PromQL query would show me 5xx error rate per service?`
- `Walk me through what a readiness probe failure means and how to fix it`

### General questions (outside DevOps)
Because the underlying model is Claude, Jimmy can answer questions on any topic — it will just respond in its SRE persona. The system prompt defines Jimmy's role and preferred workflow but does not restrict it from answering general queries. This is intentional: the 7 incident types represent the most common production Kubernetes issues, but the agent is not limited to them.

---

## What Jimmy Sends in Email

Every incident report via SNS includes:

```
🔴 [HIGH] Jimmy Alert: order-service

SEVERITY  : HIGH
SERVICE   : order-service
STATUS    : RESOLVED

SUMMARY
Pod 'order-service-xyz' was in CrashLoopBackOff with 8 restarts...

ROOT CAUSE
Container failed to connect to postgres on startup. Missing DB_HOST env var...

ACTIONS TAKEN BY JIMMY
1. Fetched pod-crashloop runbook from S3
2. Restarted pod order-service-xyz
3. Confirmed pod Running 1/1 after 45 seconds

Agent   : Jimmy (AIOps Runbook Automation Agent)
Cluster : eks-cluster  |  Namespace : boutique
```

---

## Potential Issues

### Bedrock model access not enabled

Go to **AWS Console → Bedrock → Model access** and enable access for `anthropic.claude-3-5-haiku-20241022-v1:0` before running `deploy.sh`.

### Prometheus URL unreachable from Lambda

`fetch_metrics` and `fetch_health` make HTTP calls to the Prometheus ELB. Keep Lambda outside a VPC (default), or ensure a NAT gateway is present and the ELB security group allows inbound on port 9090.

### Agent stuck in PREPARING state

Normal — takes 30–60 seconds. If it persists, check the Bedrock console for schema validation errors or missing Lambda ARNs.

### SNS emails not arriving

The subscription must be confirmed. Check your spam folder for the AWS confirmation email and click the link before testing Jimmy.

### incident_detector returns api-error

Verify the Lambda execution role has `eks:DescribeCluster` permissions and that the `EKS_CLUSTER_NAME` environment variable matches your actual cluster name.

### fetch_logs returns no results

The default log group is `/eks/boutique/pods`. This only exists after Fluent Bit starts shipping logs. Run `aws-for-fluent-bit` in the cluster first, or run `scripts/generate_sample_data.py` to seed CloudWatch with test data.

### Lambda execution role missing permissions

IAM propagation takes ~10–15 seconds. Wait and retry. Verify with:

```bash
aws iam get-role-policy \
  --role-name aiops-lambda-role \
  --policy-name aiops-lambda-inline-policy
```
