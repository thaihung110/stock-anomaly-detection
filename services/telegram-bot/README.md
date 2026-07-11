# telegram-bot

The user-facing command surface: handles every `/command` (custom alerts, watchlist, preferences), owns the `users`/`user_watchlist`/`user_preferences`/`user_alert_rules` tables from the write side, and triggers hot-reloads on both `rule-engine` and `alert-service` whenever a mutation needs to take effect immediately. Runs as a webhook server (not polling).

> **Note on `CLAUDE.md`**: the project-level "Telegram Commands" list only documents the custom-alert commands (`/setalert`, `/listalerts`, etc.). The bot also implements `/start`, `/help`, `/watch`, `/unwatch`, `/watchlist`, `/systemalerts`, `/customalerts`, `/preferences` — see the full table below.

## Architecture

```
Telegram Bot API ──(webhook POST)──→ /webhook (python-telegram-bot Application)
                                            │
                          ┌─────────────────┼──────────────────┐
                     AlertService     WatchlistService   PreferenceService
                          │                 │                   │
                    UserAlertRepository  WatchlistRepository  PreferenceRepository
                          │                 │                   │
                          └─────────── PostgreSQL (asyncpg) ─────┘
                          │                 │                   │
                   RuleEngineClient   AlertServiceClient  AlertServiceClient
                          │                 │                   │
              POST rule-engine:8080   POST alert-service:8080  POST alert-service:8080
              /internal/reload-       /internal/reload-        /internal/reload-
              user-rules              subscribers               subscribers
```

Clean/hexagonal layering: `domain/ports.py` defines `IAlertRepository`/`IWatchlistRepository`/`IPreferenceRepository`/`IRuleEngineClient`/`IAlertServiceClient` protocols; `application/*_service.py` holds only business flow against those ports; `infrastructure/` provides the concrete `asyncpg`/`httpx` adapters. This is what makes `application/` unit-testable without a real Postgres or real Telegram/HTTP calls.

## Pipeline Steps

### Startup (`BotRunner.__init__` / `.run()`)

Builds one `DbClient` (asyncpg pool) shared by all three repositories, one `RuleEngineClient` and one `AlertServiceClient` (both plain `httpx.AsyncClient` wrappers), wires the three application services, then hands everything to `create_application()` (`bot_factory.py`) which registers every command handler on a single `python-telegram-bot` `Application`. `run_webhook()` binds `0.0.0.0:{app_port}` at `webhook_path`, publicly reachable at `webhook_url` (`webhook_host + webhook_path`).

### `/start` (`handlers/start.py`)

Only responds in a **private chat** (group/channel triggers a "please DM me" reply instead) — `UPSERT`s `chat_id` onto the `users` row so `alert-service` has somewhere to deliver to. This is the **only** place a user's `chat_id` is first recorded; every other command that needs routing (`/watch`, `/systemalerts`, `/customalerts`) also upserts it defensively so a user who mutates preferences before running `/start` still ends up routable.

### Custom alert commands (`handlers/alert_commands.py` → `AlertService`)

| Command | Effect | Triggers hot-reload? |
|---|---|---|
| `/setalert <SYMBOL\|*> <field> <op> <threshold> [once\|every]` | Validates field/operator/threshold/frequency tokens, inserts a new `ACTIVE` rule (default `cooldown_min=60`) | **Yes** — `rule-engine`'s `/internal/reload-user-rules` |
| `/listalerts` | Lists all rules for the user, grouped active/paused/triggered; stores a `{1: rule_id, 2: ...}` map in `context.user_data` so subsequent commands accept a short number instead of a UUID | No |
| `/pausealert <n>` / `/resumealert <n>` / `/resetalert <n>` | Status transition only if the rule belongs to the calling user | **No** — status flips are picked up by `rule-engine` next time it evaluates a quote against its already-loaded rule cache; see Known Issues |
| `/delalert <n>` | Deletes the rule (ownership-checked) | **Yes** — reload |
| `/alerthistory [SYMBOL]` | Lists `user_alert_events` for the user, optionally filtered by symbol | No |

`_resolve_rule_id` accepts either the 1-based index from the last `/listalerts` or a raw UUID string — lets mobile users type `/pausealert 1` instead of pasting a 36-character UUID. Fields carrying `rsi_14`/`bb_position` get an explicit "batch daily" note appended to every reply that shows them, per `CLAUDE.md`'s Real-Time vs. Batch Field Distinction.

### Watchlist commands (`handlers/watchlist_commands.py` → `WatchlistService`)

`/watch <SYMBOL>` / `/unwatch <SYMBOL>` / `/watchlist` — `normalize_and_validate()` (`domain/symbol.py`) enforces a strict `^[A-Z]{1,5}$` format (uppercased) before touching the DB; a mutation (`watch`/`unwatch` that actually changed something) calls `alert_client.reload_subscribers()` so `alert-service`'s `SubscriberCache` doesn't wait out its TTL before seeing the new watchlist.

### Preference commands (`handlers/preferences.py` → `PreferenceService`)

`/systemalerts <all|watchlist|off>` sets `system_alert_mode` (mirrors the PostgreSQL `system_alert_mode` ENUM); `/customalerts <on|off>` toggles `custom_alert_enabled`; `/preferences` shows both. Every mutation calls `reload_subscribers()` on `alert-service`, same as watchlist mutations.

## HTTP Endpoints Called (outbound)

| Target Service | Endpoint | Called from | Purpose |
|---|---|---|---|
| `rule-engine` | `POST /internal/reload-user-rules` | `AlertService.create_alert` / `.delete_alert` | Refresh rule-engine's in-memory custom-rule cache + context |
| `alert-service` | `POST /internal/reload-subscribers` | `WatchlistService.watch`/`.unwatch`, `PreferenceService.set_system_alert_mode`/`.toggle_custom_alerts` | Invalidate the subscriber TTL cache immediately |

Both clients (`RuleEngineClient`, `AlertServiceClient`) catch `httpx.HTTPError` and log a warning rather than raising — a reload failure never blocks the user-facing command from completing (the mutation is already durably in Postgres; the next TTL expiry or manual reload picks it up eventually).

## Configuration

Env vars read by `Settings` (`config.py`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | |
| `WEBHOOK_HOST` | **Yes** | — | Public HTTPS URL, e.g. an ngrok tunnel in local dev |
| `WEBHOOK_PATH` | No | `/webhook` | |
| `APP_PORT` | No | `8080` | |
| `PG_DSN` | **Yes** | — | `postgresql://user:pass@host:5432/dbname` — assembled from `PG_*` parts via `env` interpolation in k8s, not by `Settings` itself (unlike `rule-engine`/`alert-service`) |
| `RULE_ENGINE_URL` | **Yes** | — | e.g. `http://rule-engine:8080` |
| `ALERT_SERVICE_URL` | No | `http://alert-service:8080` | |

## Prerequisites: Public Webhook Tunnel

Telegram requires a **public HTTPS** endpoint to deliver webhook updates — a kubeadm cluster's Ingress is only reachable inside the cluster/host network, so a tunnel is required in any setup that isn't behind a real public domain + TLS cert.

### Option A — ngrok (recommended for local/dev)

```bash
# 1. Install ngrok
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
  | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" \
  | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok
ngrok config add-authtoken <your-ngrok-authtoken>   # from https://dashboard.ngrok.com/get-started/your-authtoken

# 2. Start the tunnel (keep it running — e.g. in a systemd unit or tmux/screen session)
nohup ngrok http https://localhost:443 --host-header=rewrite > /tmp/ngrok.log 2>&1 &

# 3. Read the public URL
NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['tunnels'][0]['public_url'])")
echo "ngrok URL: ${NGROK_URL}"

# 4. Patch the ConfigMap and restart the bot so it picks up the new WEBHOOK_HOST
kubectl patch configmap telegram-bot-config -n stock-anomaly-detection \
  --type merge -p "{\"data\":{\"WEBHOOK_HOST\":\"${NGROK_URL}\"}}"
kubectl rollout restart deploy/telegram-bot -n stock-anomaly-detection
kubectl rollout status deploy/telegram-bot -n stock-anomaly-detection

# 5. Register the webhook with Telegram
BOT_TOKEN="<your-bot-token>"
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -d "url=${NGROK_URL}/webhook"
# Expected: {"ok":true,"result":true}

# Verify
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

> ⚠️ ngrok's free tier issues a **new URL on every restart** of the tunnel — steps 3–5 must be repeated whenever the tunnel process restarts, not just on first setup.

### Option B — Cloudflare Tunnel (more stable free URL)

```bash
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
cloudflared tunnel --url https://localhost:443 --no-tls-verify
# Yields a URL like https://random-name.trycloudflare.com
# Then repeat steps 4-5 above (patch ConfigMap + register webhook) with that URL
```

## Kubernetes Resource Sizing

From `k8s/telegram-bot/deployment.yaml`:

- 1 replica · **Requests**: 50m CPU, 128Mi memory · **Limits**: 200m CPU, 256Mi memory (lightest of the 4 core services — no Iceberg/Kafka client, just Postgres + 2 HTTP clients)
- Liveness/readiness are **TCP socket checks** on port 8080 (not HTTP `/health` like the other 3 services — this service doesn't expose a `/health` route)
- `PG_DSN` is built in the manifest itself via `$(PG_USER):$(PG_PASSWORD)@$(PG_HOST):$(PG_PORT)/$(PG_DATABASE)` env-var interpolation, referencing the same `PG_*` ConfigMap/Secret keys as `rule-engine`/`alert-service`
- Ships with an `Ingress` (`nginx.ingress.kubernetes.io/ssl-redirect: "false"`) routing `/webhook` to the service — required because Telegram delivers updates via HTTPS webhook POST, not the bot polling out
- `WEBHOOK_HOST` must be patched to the current public tunnel URL before the bot can receive updates — see [Prerequisites: Public Webhook Tunnel](#prerequisites-public-webhook-tunnel) above
- Secret `telegram-bot-secret` required: `TELEGRAM_BOT_TOKEN`, `PG_PASSWORD`

```bash
kubectl create secret generic telegram-bot-secret \
  -n stock-anomaly-detection \
  --from-literal=TELEGRAM_BOT_TOKEN="<bot_token>" \
  --from-literal=PG_PASSWORD="<pg_password>"
```

## Build & Run

```bash
cd services
./scripts/build_and_push-telegram-bot.sh v0.3
./scripts/run-telegram-bot.sh
./scripts/stop-telegram-bot.sh
```

> ⚠️ `scripts/build_and_push-telegram-bot.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/telegram-bot/deployment.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Requires `db/migrations/001_initial_schema.sql` and `002_multi_user_routing.sql` applied, and a reachable `rule-engine` + `alert-service` for hot-reload calls to succeed (failures are logged, not fatal — see above). Also requires the public HTTPS tunnel set up per [Prerequisites: Public Webhook Tunnel](#prerequisites-public-webhook-tunnel) above — without it, Telegram has nowhere to deliver webhook updates in a local/kubeadm setup without a real domain.

## Known Issues

- **`/pausealert`/`/resumealert`/`/resetalert` do not trigger a rule-engine reload** — only `create_alert` and `delete_alert` call `reload_user_rules()`. `rule-engine`'s `UserAlertProcessor` holds its own rule cache and only re-checks a rule's `status` at evaluation time if the cache itself is refreshed; a `pausealert` therefore relies on `rule-engine` re-reading the DB on its own schedule (or a later `/setalert`/`/delalert` from anyone triggering a reload) rather than taking effect immediately. Worth confirming whether this is intentional (avoids reload storms) or a gap.
- **`domain/symbol.py`'s regex (`^[A-Z]{1,5}$`) rejects dotted/hyphenated tickers** (e.g. `BRK.A`, `BRK-B`) that the platform's 500-symbol universe may include — `/watch BRK.A` would fail format validation even if `BRK.A` is a valid tracked symbol elsewhere in the pipeline (`rule-engine`'s `AlertEvent.symbol` validator allows `.`/`-`).
- No `/health` HTTP endpoint — k8s liveness/readiness use a raw TCP check on port 8080 instead, so a wedged event loop that still accepts TCP connections (but never responds) would not be caught by the probes.
- `WEBHOOK_HOST` must be manually re-patched every time the tunnel URL changes (e.g. ngrok free-tier URLs rotate on restart) — there's no automation that keeps this in sync.

## Testing

Has a `tests/` directory but coverage is thin relative to the other 3 core services — currently covers:

- `test_start_handler.py` — `/start` chat-type gating + `chat_id` upsert
- `test_symbol.py` — `normalize_and_validate` format validation
- `test_watchlist_service.py` — `WatchlistService.watch`/`.unwatch` + reload-trigger behavior
- `test_preference_service.py` — `PreferenceService` mode/toggle + reload-trigger behavior

No tests yet for `AlertService` (custom alert CRUD), the Telegram command handlers themselves (`alert_commands.py`, `preferences.py`, `watchlist_commands.py`), or the `RuleEngineClient`/`AlertServiceClient` HTTP adapters.
