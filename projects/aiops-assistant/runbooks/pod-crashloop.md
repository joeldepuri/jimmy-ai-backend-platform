# Runbook: CrashLoopBackOff

**Incident Type**: pod-crashloop  
**Severity**: High  
**Owner**: Platform Engineering

## Symptoms
- Pod status shows `CrashLoopBackOff`
- Restart count increasing (typically > 3)
- Application exits immediately after start

## Likely Root Causes
1. Missing or wrong environment variable / secret
2. Cannot connect to database on startup
3. Unhandled exception in application init code
4. OOM on startup (memory limit too low)
5. Bad image pushed (compilation error shipped to prod)

## Jimmy's Automated Response

### Step 1 — Restart the pod
- Call `restart_pod` with the pod name or deployment name
- Kubernetes will reschedule with a fresh start

### Step 2 — If restart count > 5, scale down then up
- Call `scale_deployment` with `replicas=0`, wait 10 s, then `replicas=2`
- Forces config and secrets to be re-read

### Step 3 — Verify
- Call `fetch_service_health` to confirm pod is Running
- Check restart count has reset to 0

### Step 4 — Report
- Call `send_incident_report` with severity=high, resolution_status=resolved or escalated

## Escalation Threshold
If pod restarts > 10 times with same error → set resolution_status=escalated in report.
