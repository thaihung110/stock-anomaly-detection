# ArgoCD Applications for Spark Jobs

This directory contains ArgoCD Application manifests for managing SparkApplication resources via GitOps.

## Applications

### spark-jobs-bronze

- **Path**: `infra/k8s/compute/applications/spark/bronze-layer/jobs/`
- **Purpose**: Manages Bronze layer SparkApplication manifests
- **Sync**: Automatic with self-healing

### spark-jobs-silver

- **Path**: `infra/k8s/compute/applications/spark/silver-layer/jobs/`
- **Purpose**: Manages Silver layer SparkApplication manifests
- **Sync**: Automatic with self-healing

### spark-jobs-gold

- **Path**: `infra/k8s/compute/applications/spark/gold-layer/jobs/`
- **Purpose**: Manages Gold layer SparkApplication manifests
- **Sync**: Automatic with self-healing

## Deployment

### Apply ArgoCD Applications

```bash
# Apply all ArgoCD Applications
kubectl apply -f infra/k8s/compute/applications/argocd/

# Or apply individually
kubectl apply -f infra/k8s/compute/applications/argocd/spark-jobs-bronze.yaml
kubectl apply -f infra/k8s/compute/applications/argocd/spark-jobs-silver.yaml
kubectl apply -f infra/k8s/compute/applications/argocd/spark-jobs-gold.yaml
```

### Verify Applications

```bash
# List all applications
kubectl get applications -n argocd

# Check specific application status
kubectl get application spark-jobs-bronze -n argocd
kubectl describe application spark-jobs-bronze -n argocd

# Watch sync status
kubectl get application spark-jobs-bronze -n argocd -w
```

## How It Works

1. **Airflow generates SparkApplication manifests** from templates in `spark/{layer}/template.yaml`
2. **Airflow pushes manifests** to Git repository in `spark/{layer}/jobs/` folder
3. **ArgoCD detects changes** in Git repository (polling every 3 minutes or via webhook)
4. **ArgoCD syncs manifests** to Kubernetes cluster
5. **Spark Operator** watches for new SparkApplication CRDs and creates Spark jobs

## Configuration

### Update Repository URL

Before deploying, update the `repoURL` in each Application manifest:

```yaml
source:
  repoURL: https://github.com/your-org/data-platform.git # Update this
```

### Sync Policy

All applications use:

- **Automated sync**: Automatically sync when Git changes
- **Prune**: Delete resources not in Git
- **Self-heal**: Auto-sync when Kubernetes drifts from Git

### Ignore Differences

ArgoCD ignores status changes in SparkApplication resources to prevent sync conflicts:

- `/status` - Job status changes frequently
- `/metadata/generation` - Generation increments on updates

## Troubleshooting

### Application Not Syncing

```bash
# Check ArgoCD controller logs
kubectl logs -n argocd deployment/argocd-application-controller

# Manually trigger sync
argocd app sync spark-jobs-bronze

# Check Git connection
argocd repo list
```

### Application Out of Sync

```bash
# Check diff
argocd app diff spark-jobs-bronze

# Force sync
argocd app sync spark-jobs-bronze --force
```

### No Resources Found

- Ensure `jobs/` folder contains at least one `.yaml` file
- Check that files match the `include: "*.yaml"` pattern
- Verify Git repository path is correct

### Xem logs SparkApplication

```bash
# Xem SparkApplication
kubectl get sparkapplication -n data-platform
kubectl describe sparkapplication <name> -n data-platform

# Xem logs driver
kubectl logs -l spark-role=driver,spark-app-name=<name> -n data-platform

# Xem logs executor (ví dụ exec-1)
kubectl logs -l spark-role=executor,spark-app-name=<name> -n data-platform
```
