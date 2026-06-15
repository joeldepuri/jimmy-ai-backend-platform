# Runbook: ImagePullBackOff / ErrImagePull

**Incident Type**: image-pull-error
**Severity**: High
**Owner**: Platform Engineering / DevOps

## Symptoms
- Pod status shows `ImagePullBackOff` or `ErrImagePull`
- Kubernetes events: `Failed to pull image: unauthorized` or `manifest unknown`
- Pod never starts — stuck in waiting state
- Recent CI/CD deployment pushed a bad image tag

## Why This Happens (in order of likelihood)
1. **ECR auth token expired** — the node's Docker auth to ECR is > 12 hours old
2. **Wrong image tag** — CI pushed a tag that doesn't exist in ECR (typo, failed build)
3. **ECR repository doesn't exist** — new service, ECR repo was never created
4. **IAM permissions** — node role missing `ecr:GetAuthorizationToken` or `ecr:BatchGetImage`
5. **Network issue** — node cannot reach ECR endpoint (VPC routing/security group)

## Jimmy's Automated Response

### Step 1 — Identify which pod and image is failing
- fetch_service_health: find pods in ImagePullBackOff state
- fetch_logs: search for "ImagePullBackOff" or "unauthorized" in pod events

### Step 2 — Restart the pod (fixes expired ECR token)
- Call `restart_pod` with the pod name
- Kubernetes will attempt a fresh image pull with a renewed ECR token
- This fixes ~70% of ImagePullBackOff cases (expired auth)

### Step 3 — If restart doesn't fix it
- The image tag likely doesn't exist in ECR
- Resolution requires a human to push the correct image tag or re-trigger CI
- Scale deployment to 0 to stop retry spam: `scale_deployment replicas=0`

### Step 4 — Report
- Call `send_incident_report` with severity=high
- Set resolution_status=resolved if restart fixed it, or escalated if image tag is wrong

## Escalation
If `ErrImagePull` persists after pod restart → the image tag is genuinely missing.
Set resolution_status=escalated. Engineer must check ECR console and re-run CI pipeline.
