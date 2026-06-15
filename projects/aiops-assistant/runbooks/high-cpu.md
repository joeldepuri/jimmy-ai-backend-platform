# Runbook: High CPU Utilization

**Incident Type**: high-cpu  
**Severity**: Medium → High (depends on duration)  
**Owner**: Platform Engineering

## Symptoms
- CPU usage > 80% sustained for 5+ minutes
- Pod response times increasing
- Prometheus alert: `KubePodCPUUsageHigh`

## Likely Root Causes
1. Traffic spike — more requests than replicas can handle
2. Infinite loop or CPU-intensive bug introduced in recent deploy
3. Runaway background job
4. Insufficient CPU limits set too low for workload

## Jimmy's Automated Response

### Step 1 — Scale up the affected deployment
- Call `scale_deployment` with `replicas=3`
- Reason: "CPU spike — scaling out to distribute load"

### Step 2 — Verify metrics drop
- Call `fetch_metrics` with metric_name=pod_cpu_utilization after 60 s

### Step 3 — Report
- Call `send_incident_report` with severity=medium, actions_taken describing scale-out

## Escalation Threshold
If CPU stays > 90% after scaling to 3 replicas → set resolution_status=escalated.
