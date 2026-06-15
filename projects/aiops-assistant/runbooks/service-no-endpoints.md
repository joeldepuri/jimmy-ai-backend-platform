# Runbook: Service No Endpoints / Label Mismatch

**Incident Type**: service-no-endpoints
**Severity**: Critical
**Owner**: Platform Engineering

## Symptoms
- Service exists but all requests return 503 / connection refused
- `kubectl get endpoints <service>` shows `<none>` or empty
- Pods are Running and Ready but service routes to nothing
- Often happens after a deployment rename or label change

## Why This Happens
1. **Label mismatch** — Service selector (e.g., `app: order-service`) doesn't match pod labels (e.g., `app: orders`)
2. **Wrong namespace** — Service and pods are in different namespaces
3. **Pod not Ready** — Service only routes to Ready pods; if all pods fail readiness, endpoints are empty
4. **Selector typo in manifest** — a recent manifest change introduced a typo in the selector

## Jimmy's Automated Response

### Step 1 — Confirm the service has no endpoints
- fetch_service_health: check deployment available replica count
- fetch_logs: search for "no endpoints available" or "connection refused" or "503"

### Step 2 — Restart all pods for the affected deployment
- Call `restart_pod` with `deployment_name` (this forces pods to re-register)
- If pods are Running and Ready, the service will pick them up automatically

### Step 3 — Scale to ensure pods are present
- Call `scale_deployment` with `replicas=2`
- Ensures at least 2 pods are available for the service to route to

### Step 4 — Verify
- fetch_service_health: confirm available replicas > 0
- fetch_logs: confirm 503 errors have stopped

### Step 5 — Report
- Call `send_incident_report` with severity=critical
- Note: if the label mismatch is in the manifest, a manifest fix and redeploy is needed

## Escalation
If pods are Running+Ready but service still has no endpoints after restart → label mismatch in manifest.
Set resolution_status=escalated. Engineer must check `kubectl describe svc` selector vs pod labels.
