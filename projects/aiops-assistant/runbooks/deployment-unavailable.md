# Runbook: Deployment Unavailable / Pending Pods

**Incident Type**: deployment-unavailable  
**Severity**: High  
**Owner**: Platform Engineering

## Symptoms
- Pod stuck in `Pending` state
- `0/2 nodes available` in pod events
- Deployment has 0 available replicas

## Likely Root Causes
1. Node capacity exhausted — too many pods for 2 nodes
2. Resource requests too high for remaining node capacity
3. Node not ready (node issue)
4. PVC not bound (storage issue)

## Jimmy's Automated Response

### Step 1 — Check cluster health
- Call `fetch_service_health` to identify which nodes are ready
- If node count < 2 → escalate (cannot auto-scale node groups)

### Step 2 — Scale down non-critical deployments to free capacity
- If boutique namespace is full, check if monitoring pods can be reduced

### Step 3 — Restart the deployment
- Call `restart_pod` with the deployment_name of the Pending deployment

### Step 4 — Report
- Call `send_incident_report` with severity=high
- Note in actions_taken which pods were restarted and current node capacity

## Escalation Threshold
If nodes < 2 or no capacity after restart → resolution_status=escalated.
Long-term fix: scale EKS node group to 3 nodes via Terraform.
