# Runbook: OOMKilled (Out of Memory)

**Incident Type**: oom-killed  
**Severity**: High  
**Owner**: Platform Engineering

## Symptoms
- Pod status shows `OOMKilled` in terminated state
- Container restarts with reason OOMKilled
- Prometheus alert: `KubePodOOMKilled`

## Likely Root Causes
1. Memory limit set too low for actual usage
2. Memory leak in application code
3. Large dataset loaded into memory
4. Request spike causing memory spike

## Jimmy's Automated Response

### Step 1 — Restart the pod
- Call `restart_pod` with the pod name
- OOMKilled containers can't self-heal without a restart

### Step 2 — Scale out to distribute memory load
- Call `scale_deployment` with `replicas=2`
- More replicas = each instance serves fewer requests = less memory per pod

### Step 3 — Verify
- Call `fetch_service_health` to confirm pod is Running without OOMKilled
- Call `fetch_metrics` with metric_name=pod_memory_utilization

### Step 4 — Report
- Call `send_incident_report` with severity=high
- Note in actions_taken that memory limits may need to be increased via manifest update

## Escalation Threshold
If pod OOMKilled > 3 times in 1 hour → set resolution_status=escalated.
Long-term fix: increase `resources.limits.memory` in the deployment manifest.
