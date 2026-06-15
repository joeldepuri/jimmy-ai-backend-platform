# Runbook: Readiness Probe Failure (Pod Running, App Not Responding)

**Incident Type**: readiness-probe-failure
**Severity**: High
**Owner**: Platform Engineering

## Symptoms
- Pod phase is `Running` but READY column shows `0/1`
- Service receives requests but returns 502/503
- Kubernetes events show: `Readiness probe failed: HTTP probe failed with statuscode: 500`
- Application is alive but not healthy enough to serve traffic

## Why This Happens (in order of likelihood)
1. **Database not ready on startup** — app starts but cannot connect to DB, /health returns 500
2. **Missing secret or env var** — app starts but a dependency is not configured
3. **Readiness probe path wrong** — probe hitting wrong endpoint after a code change
4. **App stuck in init state** — warming up (loading models, caches) taking longer than initialDelaySeconds
5. **Downstream service unhealthy** — app health check calls a dependency that is down

## Jimmy's Automated Response

### Step 1 — Confirm pod is Running but not Ready
- fetch_service_health: look for pods where `available < desired` despite `Running` phase
- fetch_logs: search for "readiness probe failed" or "connection refused" or "health check"

### Step 2 — Restart the pod to clear transient state
- Call `restart_pod` with the pod name
- Kubernetes will reschedule and retry the readiness probe from scratch

### Step 3 — If restart doesn't help, scale down then up
- Call `scale_deployment` with `replicas=0`, then `replicas=2`
- Forces all pods to restart and re-read config/secrets

### Step 4 — Verify
- Call `fetch_service_health` — confirm READY shows `1/1`

### Step 5 — Report
- Call `send_incident_report` with severity=high
- Note in actions_taken whether the pod recovered after restart

## Escalation
If pod remains `0/1` after two restarts → resolution_status=escalated.
Engineer must check readiness probe configuration and app startup logs manually.
