# Airflow Helm Values — Configuration Notes

## 1. git-sync SSH Secret (`extraSecrets`)

### What was changed
```yaml
extraSecrets: {}
# airflow-ssh-secret is managed externally via kubectl (not by Helm).
# Created with: kubectl create secret generic airflow-ssh-secret --from-file=gitSshKey=~/.ssh/airflow_gitsync -n stock-anomaly-detection
```

### Why
The original `extraSecrets` block embedded the base64-encoded SSH private key directly inside the Helm values file. This means every `helm upgrade` would attempt to re-create or overwrite the secret. Since the secret was already created manually via `kubectl`, Helm would conflict with it.

Setting `extraSecrets: {}` tells Helm to not manage this secret at all. The git-sync sidecar still finds the secret because `gitSync.sshKeySecret: airflow-ssh-secret` references it by name — Helm just stops owning it.

The key name inside the secret **must** be `gitSshKey` — this is hardcoded by the git-sync container, not configurable.

---

## 2. Keycloak Authentication (Airflow 3.x — KeycloakAuthManager)

### Why not FAB OAuth2

The previous approach used Flask-AppBuilder (FAB) with `webserverConfig` / `webserver_config.py`. That was the Airflow 2.x pattern. In **Airflow 3.x**, authentication moved to a dedicated auth manager system. The `apache-airflow-providers-keycloak` package provides a `KeycloakAuthManager` that is the correct, native way to integrate Keycloak in Airflow 3.

The FAB provider still exists and can be installed separately, but it is no longer the default and requires extra boilerplate. `KeycloakAuthManager` is purpose-built for Airflow 3 and configured entirely via environment variables — no `webserver_config.py` needed.

---

### 2a. Global `extraEnv` — auth manager configuration

```yaml
extraEnv: |
  - name: AIRFLOW__CORE__AUTH_MANAGER
    value: 'airflow.providers.keycloak.auth_manager.keycloak_auth_manager.KeycloakAuthManager'
  - name: AIRFLOW__KEYCLOAK_AUTH_MANAGER__CLIENT_ID
    value: 'airflow'
  - name: AIRFLOW__KEYCLOAK_AUTH_MANAGER__REALM
    value: 'iceberg'
  - name: AIRFLOW__KEYCLOAK_AUTH_MANAGER__SERVER_URL
    value: 'http://openhouse-keycloak.stock-anomaly-detection.svc.cluster.local'
  - name: _PIP_ADDITIONAL_REQUIREMENTS
    value: 'apache-airflow-providers-keycloak'
```

These are placed at the **global** `extraEnv` level (not inside `webserver:`), so they apply to all Airflow pods — API server, scheduler, webserver, workers — which all need to know the auth manager.

| Variable | Value | Purpose |
|----------|-------|---------|
| `AIRFLOW__CORE__AUTH_MANAGER` | `KeycloakAuthManager` full path | Tells Airflow to use the Keycloak auth manager instead of the default FAB auth manager |
| `AIRFLOW__KEYCLOAK_AUTH_MANAGER__CLIENT_ID` | `airflow` | The client ID registered in Keycloak's `iceberg` realm |
| `AIRFLOW__KEYCLOAK_AUTH_MANAGER__REALM` | `iceberg` | The Keycloak realm containing Airflow users and roles |
| `AIRFLOW__KEYCLOAK_AUTH_MANAGER__SERVER_URL` | internal cluster URL | Keycloak's base URL used by Airflow pods to call the Keycloak API server-side. Uses the internal Kubernetes service (`svc.cluster.local`) to avoid TLS issues with the self-signed `.test` domain cert |
| `_PIP_ADDITIONAL_REQUIREMENTS` | `apache-airflow-providers-keycloak` | Installs the keycloak provider at pod startup. **For production, build a custom Docker image with this package instead.** |

---

### 2b. Global `extraEnvFrom` — client secret injection

```yaml
extraEnvFrom: |
  - secretRef:
      name: airflow-keycloak-secret
```

Injects all keys from the `airflow-keycloak-secret` Kubernetes Secret as env vars into every Airflow pod. The secret must contain the key `AIRFLOW__KEYCLOAK_AUTH_MANAGER__CLIENT_SECRET`.

**How to create the secret:**
```bash
kubectl create secret generic airflow-keycloak-secret \
  --from-literal=AIRFLOW__KEYCLOAK_AUTH_MANAGER__CLIENT_SECRET=<client-secret-from-keycloak> \
  -n stock-anomaly-detection
```

The client secret is never stored in the values file (which is committed to git).

---

### 2c. Why internal service URL

```
http://openhouse-keycloak.stock-anomaly-detection.svc.cluster.local
```

- `https://openhouse.keycloak.test` uses a self-signed TLS certificate — Airflow pods would reject it without explicit cert trust configuration.
- The internal `svc.cluster.local` address uses plain HTTP on port 80, eliminating TLS entirely for pod-to-pod communication.
- The browser still redirects to the external `openhouse.keycloak.test` hostname for the login page — that is fine since the user's browser handles it, not the pod.

**Confirmed service:**
```
openhouse-keycloak   ClusterIP   10.105.207.52   <none>   80/TCP
```

---

## 3. Keycloak Client Setup (one-time, in `iceberg` realm)

### Step 1 — Create client
**Clients** → **Create client**
- Client ID: `airflow`
- Type: `OpenID Connect`
- Enable **Client authentication** (confidential client — required for a client secret)

### Step 2 — Settings tab
- Valid redirect URIs: `http://<airflow-webserver-host>/login/keycloak/authorized`
- Web origins: `http://<airflow-webserver-host>`

### Step 3 — Get client secret
**Credentials** tab → copy the secret → create K8s secret:
```bash
kubectl create secret generic airflow-keycloak-secret \
  --from-literal=AIRFLOW__KEYCLOAK_AUTH_MANAGER__CLIENT_SECRET=<paste-here> \
  -n stock-anomaly-detection
```

### Step 4 — Apply
```bash
helm upgrade airflow apache-airflow/airflow \
  -n stock-anomaly-detection \
  -f infra/k8s/orchestration/config/airflow.yaml
```

### Step 5 — Check logs
```bash
kubectl logs -n stock-anomaly-detection -l component=webserver -f
kubectl logs -n stock-anomaly-detection -l component=api-server -f
```

---

## 4. nginx Ingress Annotations for OAuth2 Login (`ingress.apiServer.annotations`)

### What was changed

```yaml
ingress:
  apiServer:
    annotations:
      nginx.ingress.kubernetes.io/proxy-next-upstream: "off"
      nginx.ingress.kubernetes.io/proxy-buffering: "off"
      nginx.ingress.kubernetes.io/proxy-buffer-size: "16k"
```

### Why

Three separate nginx issues all caused the same symptom — `invalid_grant: Code not valid` from Keycloak during the OAuth2 login callback.

#### Root cause chain

1. Airflow's `login_callback` handler exchanges the authorization code with Keycloak successfully and prepares a `303 redirect` response. This response contains a `Set-Cookie` header with a large JWT token.
2. nginx's default `proxy_buffer_size` is **4k**. The JWT `Set-Cookie` header exceeds this limit, so nginx drops the response and returns **502 Bad Gateway** to the browser.
3. The browser retries the same callback URL (`/auth/login_callback?code=xxx`) with the already-used authorization code.
4. Keycloak rejects the reused code with `invalid_grant: Code not valid`.
5. Airflow returns **500** to the browser.

#### Annotation breakdown

| Annotation | Value | Purpose |
|-----------|-------|---------|
| `proxy-buffer-size` | `16k` | **Primary fix.** Increases the header buffer from 4k to 16k so nginx can forward the large JWT `Set-Cookie` header without returning 502. |
| `proxy-buffering` | `off` | Disables response body buffering. Prevents nginx from holding the full response body in memory before forwarding, reducing the chance of buffer overflows on large responses. |
| `proxy-next-upstream` | `off` | Prevents nginx from retrying a failed request on a second upstream connection. Without this, a transient upstream error could cause nginx to replay the `GET /auth/login_callback` request, consuming the code a second time. |

#### Why `proxy-buffering: off` alone is not enough

`proxy-buffering` controls response **body** buffering. It has no effect on response **header** buffering, which is controlled separately by `proxy_buffer_size`. The 502 is caused by an oversized **header**, so only `proxy-buffer-size` fixes the root cause.

---

## 5. createUserJob disabled (Airflow 3.x compatibility)

### What was changed

```yaml
createUserJob:
  enabled: false
  args: []
```

### Why

Airflow 3.x removed the `airflow users` CLI command. The Helm chart's default `createUserJob` still invoked `airflow users create`, which caused a `CrashLoopBackOff` on every deploy. Since Keycloak manages users entirely, the job is not needed and must be disabled.

---

## 6. Production Note — custom Docker image

`_PIP_ADDITIONAL_REQUIREMENTS` installs the keycloak provider on every pod startup, which is slow and unreliable in production. Replace it with a custom image:

```dockerfile
FROM apache/airflow:3.0.2
RUN pip install apache-airflow-providers-keycloak
```

Then set in values:
```yaml
images:
  airflow:
    repository: your-registry/airflow
    tag: "3.0.2-keycloak"
```

And remove `_PIP_ADDITIONAL_REQUIREMENTS` from `extraEnv`.
