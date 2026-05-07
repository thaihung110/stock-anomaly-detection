# Airflow + Keycloak: Authentication & Authorization Guide

## Overview

Airflow 3.x uses a pluggable **Auth Manager** system. The `apache-airflow-providers-keycloak` package provides `KeycloakAuthManager`, which replaces the Airflow 2.x Flask-AppBuilder (FAB) approach. This guide covers how the two systems interact, and how to configure permissions so users can actually access Airflow after logging in.

---

## Authentication Flow (How Login Works)

The login uses the **OAuth2 Authorization Code flow** between the browser, the Airflow API server, and Keycloak.

```
Browser                    Airflow API Server              Keycloak
  |                               |                            |
  |  GET /                        |                            |
  |------------------------------>|                            |
  |  302 → /auth/login            |                            |
  |<------------------------------|                            |
  |                               |                            |
  |  GET /auth/login              |                            |
  |------------------------------>|                            |
  |  [build authorization URL]    |                            |
  |  302 → Keycloak /authorize    |                            |
  |<------------------------------|                            |
  |                               |                            |
  |  GET /realms/iceberg/protocol/openid-connect/auth?...     |
  |---------------------------------------------------------->|
  |  [Keycloak login page]        |                            |
  |<----------------------------------------------------------|
  |                               |                            |
  |  [user submits credentials]   |                            |
  |---------------------------------------------------------->|
  |  302 → /auth/login_callback?code=<one-time-code>&state=.. |
  |<----------------------------------------------------------|
  |                               |                            |
  |  GET /auth/login_callback?code=...                        |
  |------------------------------>|                            |
  |                        POST /token (code exchange)        |
  |                        ------------------------------>    |
  |                        {access_token, id_token, ...}      |
  |                        <------------------------------    |
  |                               |                            |
  |                        POST /userinfo                      |
  |                        ------------------------------>    |
  |                        {sub, email, roles, ...}            |
  |                        <------------------------------    |
  |                               |                            |
  |  303 → / (Set-Cookie: jwt=...) |                           |
  |<------------------------------|                            |
  |                               |                            |
  |  GET / (with JWT cookie)      |                            |
  |------------------------------>|                            |
```

### Key components

| Component                  | Role                                                                                                                                               |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /auth/login`          | Airflow builds the Keycloak authorization URL with `client_id`, `redirect_uri`, `state`, `nonce`, and redirects the browser                        |
| `GET /auth/login_callback` | Airflow receives the code from Keycloak, exchanges it for tokens via server-to-server call, validates the JWT, sets a cookie, and redirects to `/` |
| JWT cookie                 | Airflow stores the access token as a cookie. Every subsequent request to the API server includes this cookie.                                      |
| `redirect_uri`             | Must be `http(s)://<airflow-host>/auth/login_callback` — must exactly match what is registered in the Keycloak client's **Valid Redirect URIs**    |

### State and CSRF protection

When building the authorization URL, Airflow generates a random `state` value and stores it in a session cookie. When the callback arrives, Airflow verifies that the `state` query parameter matches the cookie. If they do not match (e.g., because the browser retried the callback after a 502 error), Airflow returns **403 Forbidden**.

---

## Authorization Flow (How Permission Checks Work)

After login, every API request that requires access control goes through this flow:

```
Browser → Airflow API Server → Keycloak Authorization Services (UMA)
```

`KeycloakAuthManager` uses Keycloak's **User-Managed Access (UMA)** protocol to check whether the authenticated user is allowed to perform a specific action on a specific resource.

### UMA permission check

For every protected endpoint, Airflow calls:

```
POST /realms/{realm}/protocol/openid-connect/token
  grant_type=urn:ietf:params:oauth2:grant-type:uma-ticket
  audience={client_id}
  permission={resource}#{scope}
```

For example, when a user opens the DAG list page, Airflow sends:

```
permission=Dag#LIST
```

Keycloak evaluates its configured **Policies** and **Permissions** for the `airflow` client and returns either a permission token (allowed) or `access_denied`.

---

## Keycloak Authorization Setup (Required Steps)

Authorization Services must be fully configured in the Keycloak `airflow` client. There are four layers:

### Layer 1 — Enable Authorization Services

**Clients → airflow → Capability config → Authorization: On**

This enables the **Authorization** tab on the client and activates the UMA endpoint.

### Layer 2 — Scopes

Scopes represent the actions that can be performed. Create these 6 scopes under:

**Authorization → Scopes → Create authorization scope**

| Scope    | Meaning                 |
| -------- | ----------------------- |
| `GET`    | Read a single resource  |
| `LIST`   | List / browse resources |
| `POST`   | Create a resource       |
| `PUT`    | Update a resource       |
| `DELETE` | Delete a resource       |
| `MENU`   | Access a UI menu item   |

### Layer 3 — Resources

Resources represent the Airflow object types. Create these 12 resources under:

**Authorization → Resources → Create resource**

For each resource, set **Name** and assign all 6 scopes created above.

| Resource name   | Airflow object         |
| --------------- | ---------------------- |
| `Asset`         | Data assets / datasets |
| `AssetAlias`    | Asset aliases          |
| `Backfill`      | Backfill runs          |
| `Configuration` | Airflow configuration  |
| `Connection`    | Connections            |
| `Custom`        | Custom resources       |
| `Dag`           | DAGs                   |
| `Menu`          | UI menu entries        |
| `Pool`          | Executor pools         |
| `Team`          | Teams (if used)        |
| `Variable`      | Variables              |
| `View`          | UI views               |

### Layer 4 — Policies

Policies define **who** is allowed. Create a role-based policy under:

**Authorization → Policies → Create policy → Role**

| Field | Value                                                                |
| ----- | -------------------------------------------------------------------- |
| Name  | `admin-policy`                                                       |
| Roles | Realm role `admin` (or whichever role your Airflow admin users have) |
| Logic | Positive                                                             |

To support multiple roles (e.g., `viewer`, `editor`), create a separate policy per role, or use a JS policy for more complex logic.

### Layer 5 — Permissions

Permissions link Resources + Scopes + Policies. Create one permission under:

**Authorization → Permissions → Create permission → Resource-based**

| Field                | Value                   |
| -------------------- | ----------------------- |
| Name                 | `admin-full-access`     |
| Resources            | Select all 12 resources |
| Authorization scopes | Select all 6 scopes     |
| Policies             | `admin-policy`          |
| Decision strategy    | Affirmative             |

---

## Role Mapping: Keycloak Roles → Airflow Access

`KeycloakAuthManager` does **not** use Airflow's internal role system (Admin, Viewer, Op, User, Public). Access is entirely determined by Keycloak policies. The Keycloak realm role assigned to a user controls what they can do in Airflow.

### Assigning a role to a user

**Users → select user → Role mappings → Assign role → select realm role**

Make sure the role assigned here (e.g., `admin`) matches the role referenced in the policy created in Layer 4.

### Multi-role setup example

To have separate admin and read-only access:

1. Create Keycloak realm roles: `airflow-admin`, `airflow-viewer`
2. Create two policies:
   - `admin-policy` → role `airflow-admin`
   - `viewer-policy` → role `airflow-viewer`
3. Create two permissions:
   - `admin-full-access` → all resources, all scopes, `admin-policy`
   - `viewer-read-access` → all resources, scopes `GET` + `LIST` + `MENU`, `viewer-policy`
4. Assign the appropriate role to each user in Keycloak

---

## Common Errors and Fixes

| Error                                                       | Cause                                                                                                             | Fix                                                                               |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `invalid_grant: Code not valid`                             | nginx returned 502 to browser due to oversized JWT `Set-Cookie` header; browser retried with an already-used code | Add `nginx.ingress.kubernetes.io/proxy-buffer-size: "16k"` to ingress annotations |
| `403 Forbidden` on callback                                 | `state` cookie mismatch — browser retried the callback after a failed first attempt                               | Fix the upstream 502 first; 403 is a symptom, not the root cause                  |
| `Client does not support permissions`                       | Authorization Services not enabled on the Keycloak client                                                         | Enable **Authorization** toggle in Capability config                              |
| `invalid_scope. One of the given scopes [X] is invalid`     | Required scopes not created in Keycloak Authorization                                                             | Create all 6 scopes under Authorization → Scopes                                  |
| `invalid_resource. Resource with id [X] does not exist`     | Required resources not created in Keycloak Authorization                                                          | Create all 12 resources under Authorization → Resources                           |
| `access_denied` (user logged in but gets 403 on every page) | No matching permission in Keycloak grants access to the user's role                                               | Create a Policy and Permission, assign the correct role to the user               |

---

## Configuration Reference

### Airflow environment variables

| Variable                                        | Example value                                                                       | Purpose                                                                                 |
| ----------------------------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `AIRFLOW__CORE__AUTH_MANAGER`                   | `airflow.providers.keycloak.auth_manager.keycloak_auth_manager.KeycloakAuthManager` | Activates the Keycloak auth manager                                                     |
| `AIRFLOW__KEYCLOAK_AUTH_MANAGER__CLIENT_ID`     | `airflow`                                                                           | Keycloak client ID                                                                      |
| `AIRFLOW__KEYCLOAK_AUTH_MANAGER__REALM`         | `iceberg`                                                                           | Keycloak realm                                                                          |
| `AIRFLOW__KEYCLOAK_AUTH_MANAGER__SERVER_URL`    | `http://openhouse-keycloak.stock-anomaly-detection.svc.cluster.local`               | Keycloak base URL — use internal cluster URL to avoid TLS issues with self-signed certs |
| `AIRFLOW__KEYCLOAK_AUTH_MANAGER__CLIENT_SECRET` | _(from K8s secret)_                                                                 | Keycloak client secret — never hardcode in values file                                  |

### Keycloak client settings

| Setting               | Value                                       |
| --------------------- | ------------------------------------------- |
| Client type           | OpenID Connect                              |
| Client authentication | On (confidential client)                    |
| Authorization         | On                                          |
| Valid redirect URIs   | `http://<airflow-host>/auth/login_callback` |
| Web origins           | `http://<airflow-host>`                     |
