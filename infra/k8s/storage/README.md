## Storage

Phần storage:

- [minio](#cài-đặt-minio)
- [postgresql](#cài-đặt-postgresql)
- [keycloak](#cài-đặt-keycloak)

### Cài đặt Minio

---

Minio nên cài ngoài bare metal thay vì ảo hóa thêm 1 lớp trên k8s.

- [cài minio trên môi trường enterprise](https://docs.min.io/enterprise/aistor-object-store/installation/linux/install/deploy-aistor-on-ubuntu-server/)
- [cài minio trên môi trường community](https://docs.min.io/community/minio-object-store/operations/deployments/baremetal-deploy-minio-on-ubuntu-linux.html)

Ngoài ra còn một số link bên ngoài về [cài đặt minio](https://kifarunix.com/how-to-install-minio-on-ubuntu-24-04-step-by-step/).

Tạm thời sẽ cài đặt trên k8s: [helm chart bitnami minio](https://artifacthub.io/packages/helm/bitnami/minio), helm version: 17.0.22, minio version: 2025.7.23.

docker images:

- docker.io/bitnami/minio:2025.7.23-debian-12-r3
- docker.io/bitnami/minio-client:2025.7.21-debian-12-r2
- docker.io/bitnami/minio-object-browser:2.0.2-debian-12-r3
- docker.io/bitnami/os-shell:12-debian-12-r50

cài đặt

```shell
./scripts/install_minio.sh
```

user: _admin_, password: _admin123_

### Cài đặt Postgresql

---

Postgres nên cài đặt ngoài bare metal thay vì ảo hóa thêm 1 lớp trên k8s. sử dụng document chính thức của nó để cài.

Tạm thời sẽ cài đặt trên k8s: [helm chart bitnami postgresql](https://artifacthub.io/packages/helm/bitnami/postgresql), helm version: 16.7.26, postgresql version: 17.6.0.

docker images:

- docker.io/bitnami/os-shell:12-debian-12-r50
- docker.io/bitnami/postgres-exporter:0.17.1-debian-12-r15
- docker.io/bitnami/postgresql:17.6.0-debian-12-r0

cài đặt

```shell
./scripts/install_postgresql.sh
```

Tạo 1 database primary và 1 database read. User: _postgres_, password: _admin_.

Tạo thêm user, và database cho các thành phần trong hệ thống.

```postgresql
-- Keycloak: xác thực cho Gravitino Web UI và Spark batch jobs
create database keycloak;
create user keycloak;
alter user keycloak with encrypted password 'keycloak';
alter database keycloak owner to keycloak;

-- Gravitino: metadata catalog backend
create database gravitino_db;
create user gravitino;
alter user gravitino with encrypted password 'gravitino';
alter database gravitino_db owner to gravitino;

-- Iceberg REST catalog backend (dùng bởi Gravitino)
create database iceberg_catalog_db;
create user iceberg;
alter user iceberg with encrypted password 'iceberg';
alter database iceberg_catalog_db owner to iceberg;

-- Stock Anomaly Detection: OLTP tables (users, user_alert_rules, user_alert_events, sync_watermarks)
create database stock_anomaly;
create user stock_user;
alter user stock_user with encrypted password 'stock_user';
alter database stock_anomaly owner to stock_user;
```

### Cài đặt Keycloak

---

[helm chart bitnami keycloak](https://artifacthub.io/packages/helm/bitnami/keycloak), helm version: 25.2.3, keycloak version: 26.3.3

Docker images:

- docker.io/bitnami/keycloak:26.3.3-debian-12-r0
- docker.io/bitnami/keycloak-config-cli:6.4.0-debian-12-r11

Tạo self-cert cho keycloak

```shell
./scripts/create_secret_keycloak_tls.sh
```

Cài đặt

```shell
./scripts/install_keycloak.sh
```

> **Lưu ý:** Sau khi cài Keycloak, copy file `tls/keycloak_tls.cert` sang thư mục `tls/` để script `create_secret_gravitino_tls.sh` có thể import CA cert vào JVM truststore của Gravitino.

### Cài đặt Gravitino

---

Trước khi cài Gravitino, tạo TLS cert cho ingress:

```shell
./scripts/create_secret_gravitino_tls_ingress.sh
```

Tạo Keycloak CA cert cho JVM truststore của Gravitino (để trust HTTPS Keycloak):

```shell
./scripts/create_secret_gravitino_tls.sh
```

Một số chú ý khi config:

- Keycloak phải chạy trên HTTPS vì Spark batch jobs sử dụng OAuth2 authentication.
- bỏ `KC_HOSTNAME` trong helm chart, comment KC_HOSTNAME trong configmap-env-vars.yaml.
- thêm `proxyHeaders: "xforwarded"` trong file config keycloak.yaml.

## Keycloak Configuration

Keycloak được sử dụng để xác thực cho:

- **Gravitino Web UI**: Authorization Code + PKCE flow (browser-based login)
- **Spark batch jobs**: Client Credentials flow

---

**1. Create Client 'gravitino' for Gravitino Web UI**

Create a **public** client for Gravitino UI (Authorization Code + PKCE):

- Client ID: `gravitino`
- Client type: **OpenID Connect**
- Standard flow: **ON**
- Client authentication: **OFF** (public client, no secret)

Tab **Settings**:

- Valid redirect URIs: `https://openhouse.gravitino.test/ui/oauth/callback`
- Web origins: `https://openhouse.gravitino.test`

**2. Create Client Scope 'gravitino' (Audience Mapper)**

Create a client scope named `gravitino`:

- Name: `gravitino`
- Protocol: `openid-connect`
- Include in token scope: **ON**

Then add an Audience Mapper to this scope:

- Go to scope `gravitino` → **Mappers** → **Add mapper** → **By configuration** → **Audience**
- Mapper name: `gravitino-audience`
- Included Client Audience: `gravitino`
- Add to access token: **ON**

Then assign scope to client `gravitino`:

- Go to Client `gravitino` → **Client Scopes** → **Add client scope**
- Select: `gravitino`
- Assigned type: **Optional**

**3. Create Client 'spark' for Spark Jobs**

Create a confidential client for Spark:

- Client ID: `spark`
- Client authentication: **ON**

![Create Client Spark](../../../assets/client-spark.png)

After creation, go to Credentials tab to get the **client secret**:

![Client Spark Credentials](../../../assets/client-spark-2.png)

**4. Create Client Scope 'sign' for Spark**

Create a client scope named `sign`:

- Include in token scope: **ON**

![Client Scope Sign](../../../assets/client-scope-sign.png)

Then add this scope to client `spark`:

- Go to Client `spark` → Client Scopes → Add client scope
- Select: `sign`
- Assigned type: **Default**

**5. Assign 'gravitino' Scope to 'spark' Client (Required for Iceberg REST)**

This step is required for Gravitino's Iceberg REST service (port 9001) to authenticate with Gravitino API (port 8090) via dynamic catalog config.

Gravitino uses `spark` client credentials with `scope=gravitino` to obtain a token. That token must contain `aud: gravitino` (added by the Audience Mapper on the `gravitino` scope). Without this, Gravitino API returns 401 → `ServiceFailureException`.

> **Why Default, not Optional?**
> Client Credentials flow (machine-to-machine) does not have a user to request scopes — only **Default** scopes are included automatically. Optional scopes are only included when explicitly requested by a user in Authorization Code flow.

- Go to Client `spark` → **Client Scopes** → **Add client scope**
- Select: `gravitino`
- Assigned type: **Default**

**6. Remove 'roles' scope from 'spark' client (Fix JWT aud claim)**

Gravitino's `JwksTokenValidator` requires `aud` to be exactly `"gravitino"` (single string). Keycloak's built-in `roles` scope automatically adds `account` client to `aud`, producing `["gravitino", "account"]` — this causes:

```
JWKS JWT validation error: JWT aud claim has value [gravitino, account], must be [gravitino]
```

Fix: remove the `roles` scope from `spark` client's default scopes.

- Go to Client `spark` → **Client Scopes**
- In **Assigned Default Client Scopes**, find `roles`
- Click `roles` → **Remove**

After this, tokens issued for `spark` will have `"aud": "gravitino"` (single value) and Gravitino will accept them.

> **Verify:** Get a token with `grant_type=client_credentials&client_id=spark&scope=gravitino` and decode the JWT payload — `aud` must be a plain string, not an array.

**7. Create User**

Go to Users → Create new user:

- Username: `admin`
- Set password: `admin`
- Temporary: **OFF**

### Cấu hình ingress

---

Tải Ingress-NGINX controller:

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.13.0/deploy/static/provider/cloud/deploy.yaml
```

## Cấu hình Iceberg catalog trên Gravitino UI (PostgreSQL + MinIO)

### 1. Tạo Metalake

Trước khi tạo catalog, phải tạo metalake `stock_metalake` — đây là namespace cấp cao nhất trong Gravitino. Iceberg REST service (port 9001) dùng metalake này để đọc dynamic catalog config.

1. Vào `https://openhouse.gravitino.test/ui`
2. Click **Create Metalake**
3. Điền:
   - **Name**: `stock_metalake`
   - **Comment**: để trống hoặc tuỳ ý
4. Click **Create**

> **Lưu ý:** Metalake `stock_metalake` phải tồn tại trước khi Spark jobs chạy. Nếu không, mọi request từ Spark tới Iceberg REST port 9001 sẽ fail với lỗi `metalake not found`.

### 2. Tạo Iceberg catalog trên Gravitino UI

JDBC URL kết nối đến `iceberg_catalog_db` (đã tạo ở bước PostgreSQL):

```text
jdbc:postgresql://openhouse-postgresql-primary:5432/iceberg_catalog_db
```

1. Đăng nhập Gravitino UI.
2. Vào metalake mong muốn → tab **Catalogs** → **Create Catalog**.
3. Điền:
   - **Name**: `iceberg_minio` (hoặc tên bạn muốn)
   - **Type**: `Relational`
   - **Provider**: `Apache Iceberg`

4. Ở phần **Properties**, thêm các cặp Key/Value sau:

```text
catalog-backend      = jdbc
uri                  = jdbc:postgresql://openhouse-postgresql-primary:5432/iceberg_catalog_db
warehouse            = s3://bronze/warehouse

jdbc-driver          = org.postgresql.Driver
jdbc-user            = iceberg
jdbc-password        = iceberg
authentication.type  = simple

io-impl              = org.apache.iceberg.aws.s3.S3FileIO
s3-endpoint          = http://openhouse-minio:9000
s3-region            = us-east-1
s3-path-style-access = true
s3-access-key-id     = admin
s3-secret-access-key = admin123
```

5. Nhấn **Create** để tạo catalog.

### 3. Ghi chú

- `warehouse` có thể đổi thành `s3://silver/warehouse` nếu bạn muốn dùng bucket `silver`.
- Đảm bảo các bucket `bronze`/`silver` đã tồn tại trên MinIO và user `admin` có quyền đọc/ghi.
