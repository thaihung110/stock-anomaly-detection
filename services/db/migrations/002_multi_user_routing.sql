-- =====================================================================
-- 002: Multi-user alert routing + watchlist + preferences
-- =====================================================================
-- Mục tiêu:
--   * Cho phép route Telegram alert tới đúng chat của từng user (fix bug
--     ẩn: rule-engine.infrastructure.telegram.py:41 gửi mọi custom alert
--     tới một admin chat duy nhất).
--   * Hỗ trợ watchlist per-user (chỉ nhận alert cho symbol quan tâm).
--   * Hỗ trợ toggle system alert per-user (ALL / WATCHLIST_ONLY / OFF).
--
-- Backward compatibility:
--   * users.chat_id NULLABLE — user cũ vẫn tồn tại, alert sẽ fallback về
--     env TELEGRAM_CHAT_ID cho đến khi họ /start lại.
--   * Trigger auto-tạo row trong user_preferences cho mọi user mới.
--   * Backfill cuối file: tạo prefs cho user hiện có.
--
-- Reference: docs/backend-redesign-plan.md (Phase 0)
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 2.1: Augment users — thêm chat_id (private chat ID Telegram, KHÁC
-- telegram_id ở group chat), audit fields.
-- ---------------------------------------------------------------------
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS chat_id      BIGINT,
    ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;

-- Partial UNIQUE — cho phép nhiều row NULL, nhưng đảm bảo chat không
-- thuộc 2 user khác nhau (Telegram chat_id là duy nhất per chat).
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_chat_id
    ON users(chat_id) WHERE chat_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- 2.2: user_watchlist — N-N giữa user và symbol.
-- ON DELETE CASCADE: xoá user (GDPR) → watchlist xoá theo.
-- Index idx_watchlist_symbol: hỗ trợ lookup ngược "ai watch symbol X"
-- khi alert-service fan-out.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_watchlist (
    user_id  UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    symbol   TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON user_watchlist(symbol);

-- ---------------------------------------------------------------------
-- 2.3: ENUM system_alert_mode + bảng user_preferences (1 row/user).
-- ENUM thay vì 2 bool độc lập (system_enabled + watchlist_only) vì
-- 2 bool tạo 4 trạng thái, 1 vô nghĩa (system_enabled=false + watchlist_only=true).
-- Mặc định WATCHLIST_ONLY: user mới không bị spam, phải /watch mới nhận.
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'system_alert_mode') THEN
        CREATE TYPE system_alert_mode AS ENUM ('ALL', 'WATCHLIST_ONLY', 'OFF');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id              UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    system_alert_mode    system_alert_mode NOT NULL DEFAULT 'WATCHLIST_ONLY',
    custom_alert_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 2.4: Index hỗ trợ OLTP→OLAP sync per-user (Spark sync_custom_alerts).
-- Query pattern: WHERE user_id = ? AND triggered_at > watermark.
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_uae_user_triggered
    ON user_alert_events(user_id, triggered_at);

-- ---------------------------------------------------------------------
-- 2.5: Trigger auto-create user_preferences khi user mới được INSERT.
-- Giảm boilerplate trong application layer + đảm bảo invariant
-- "mọi user đều có row prefs" → application không cần LEFT JOIN.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_default_preferences()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO user_preferences(user_id) VALUES (NEW.user_id)
    ON CONFLICT (user_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_create_prefs ON users;
CREATE TRIGGER trg_users_create_prefs
    AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION create_default_preferences();

-- ---------------------------------------------------------------------
-- 2.6: Backfill — tạo prefs cho user hiện có.
-- An toàn nếu chạy lại migration (ON CONFLICT DO NOTHING).
-- ---------------------------------------------------------------------
INSERT INTO user_preferences(user_id)
SELECT user_id FROM users
ON CONFLICT (user_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2.7: Trigger auto-update users.updated_at on UPDATE.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION touch_users_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_touch_updated ON users;
CREATE TRIGGER trg_users_touch_updated
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION touch_users_updated_at();

-- Tương tự cho user_preferences
CREATE OR REPLACE FUNCTION touch_preferences_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prefs_touch_updated ON user_preferences;
CREATE TRIGGER trg_prefs_touch_updated
    BEFORE UPDATE ON user_preferences
    FOR EACH ROW EXECUTE FUNCTION touch_preferences_updated_at();

COMMIT;

-- =====================================================================
-- Smoke checks (chạy manually sau khi apply):
--
--   \d users                  -- show chat_id, updated_at, last_seen_at
--   \d user_watchlist
--   \d user_preferences
--   SELECT COUNT(*) FROM users;
--   SELECT COUNT(*) FROM user_preferences;  -- phải = COUNT(users)
--   SELECT enum_range(NULL::system_alert_mode);
-- =====================================================================
