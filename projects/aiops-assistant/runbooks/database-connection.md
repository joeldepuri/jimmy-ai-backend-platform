# Runbook: Database Connection Failure

**Incident Type**: database-connection  
**Severity**: Critical  
**Owner**: Platform Engineering

## Symptoms
- Services returning 500 errors with "connection refused" or "database does not exist"
- Logs show `FATAL: database does not exist` or `connection refused`
- Multiple backend services failing simultaneously

## Likely Root Causes
1. PostgreSQL pod restarted and databases need to be re-created (init scripts didn't run)
2. boutique-postgres StatefulSet crashed
3. Service DNS resolution failed (boutique-postgres service missing)
4. Wrong database credentials in secrets

## Jimmy's Automated Response

### Step 1 — Check DB pod health
- Call `fetch_service_health` — look at boutique-postgres StatefulSet
- If boutique-postgres pod is not Running → restart it

### Step 2 — Restart the database pod
- Call `restart_pod` with `pod_name=boutique-postgres-0`

### Step 3 — Restart affected backend services
- Call `restart_pod` with deployment_name=order-service
- Call `restart_pod` with deployment_name=auth
- Call `restart_pod` with deployment_name=orders

### Step 4 — Verify
- Call `fetch_service_health` after 60 s to confirm all pods Running

### Step 5 — Report
- Call `send_incident_report` with severity=critical

## Escalation Threshold
If boutique-postgres-0 keeps restarting → set resolution_status=escalated.
This may require manual `kubectl exec` to recreate databases.
