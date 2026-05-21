"""asyncpg connection pool and raw SQL operations for the telegram-bot service.

Telegram-bot manages its own DB pool — it is a separate service and must not
share the rule-engine's pool. This client is a subset (6 methods) of the
rule-engine DbClient, covering only what the bot commands need.
"""
from uuid import UUID

import asyncpg
import structlog

from telegram_bot.domain.enums import AlertField, AlertFrequency, AlertOperator, AlertStatus
from telegram_bot.domain.models import UserAlertEvent, UserAlertRule

logger = structlog.get_logger(__name__)

_HISTORY_LIMIT = 20


class DbClient:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        logger.info("db_pool_created")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("db_pool_closed")

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("DbClient not connected — call connect() first")
        return self._pool

    async def get_or_create_user(self, telegram_id: int) -> UUID:
        row = await self.pool.fetchrow(
            """
            INSERT INTO users (telegram_id)
            VALUES ($1)
            ON CONFLICT (telegram_id) DO UPDATE SET telegram_id = EXCLUDED.telegram_id
            RETURNING user_id
            """,
            telegram_id,
        )
        return UUID(str(row["user_id"]))  # type: ignore[index]

    async def insert_rule(self, rule: UserAlertRule) -> UUID:
        row = await self.pool.fetchrow(
            """
            INSERT INTO user_alert_rules
                (user_id, symbols, field, operator, threshold,
                 frequency, cooldown_min, status)
            VALUES ($1, $2, $3::alert_field, $4::alert_operator, $5,
                    $6::alert_frequency, $7, $8::alert_status)
            RETURNING rule_id
            """,
            rule.user_id,
            rule.symbols,
            rule.field.value,
            rule.operator.value,
            rule.threshold,
            rule.frequency.value,
            rule.cooldown_min,
            rule.status.value,
        )
        rule_id = UUID(str(row["rule_id"]))  # type: ignore[index]
        logger.info("rule_inserted", rule_id=str(rule_id), user_id=str(rule.user_id))
        return rule_id

    async def update_rule_status(self, rule_id: UUID, status: AlertStatus) -> None:
        await self.pool.execute(
            """
            UPDATE user_alert_rules
            SET status = $1::alert_status, updated_at = now()
            WHERE rule_id = $2
            """,
            status.value,
            rule_id,
        )
        logger.info("rule_status_updated", rule_id=str(rule_id), status=status.value)

    async def get_rules_for_user(self, user_id: UUID) -> list[UserAlertRule]:
        rows = await self.pool.fetch(
            """
            SELECT rule_id, user_id, symbols, field, operator, threshold,
                   frequency, cooldown_min, status, created_at, updated_at
            FROM user_alert_rules
            WHERE user_id = $1
            ORDER BY created_at DESC
            """,
            user_id,
        )
        return [_row_to_rule(r) for r in rows]

    async def get_alert_history(
        self, user_id: UUID, symbol: str | None = None
    ) -> list[UserAlertEvent]:
        if symbol:
            rows = await self.pool.fetch(
                """
                SELECT event_id, rule_id, user_id, symbol, triggered_at,
                       field_snapshot, operator_snapshot,
                       threshold_snapshot, triggered_value
                FROM user_alert_events
                WHERE user_id = $1 AND symbol = $2
                ORDER BY triggered_at DESC
                LIMIT $3
                """,
                user_id,
                symbol.upper(),
                _HISTORY_LIMIT,
            )
        else:
            rows = await self.pool.fetch(
                """
                SELECT event_id, rule_id, user_id, symbol, triggered_at,
                       field_snapshot, operator_snapshot,
                       threshold_snapshot, triggered_value
                FROM user_alert_events
                WHERE user_id = $1
                ORDER BY triggered_at DESC
                LIMIT $2
                """,
                user_id,
                _HISTORY_LIMIT,
            )
        return [_row_to_event(r) for r in rows]

    async def delete_rule(self, rule_id: UUID, user_id: UUID) -> bool:
        result = await self.pool.execute(
            """
            DELETE FROM user_alert_rules
            WHERE rule_id = $1 AND user_id = $2
            """,
            rule_id,
            user_id,
        )
        deleted = result == "DELETE 1"
        logger.info("rule_deleted", rule_id=str(rule_id), deleted=deleted)
        return deleted

    async def rule_belongs_to_user(self, rule_id: UUID, user_id: UUID) -> bool:
        return bool(
            await self.pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM user_alert_rules WHERE rule_id = $1 AND user_id = $2)",
                rule_id,
                user_id,
            )
        )


# ── Row mappers ───────────────────────────────────────────────────────────────

def _row_to_rule(row: asyncpg.Record) -> UserAlertRule:
    return UserAlertRule(
        rule_id=UUID(str(row["rule_id"])),
        user_id=UUID(str(row["user_id"])),
        symbols=list(row["symbols"]),
        field=AlertField(row["field"]),
        operator=AlertOperator(row["operator"]),
        threshold=row["threshold"],
        frequency=AlertFrequency(row["frequency"]),
        cooldown_min=row["cooldown_min"],
        status=AlertStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_event(row: asyncpg.Record) -> UserAlertEvent:
    return UserAlertEvent(
        event_id=UUID(str(row["event_id"])),
        rule_id=UUID(str(row["rule_id"])),
        user_id=UUID(str(row["user_id"])),
        symbol=row["symbol"],
        triggered_at=row["triggered_at"],
        field_snapshot=AlertField(row["field_snapshot"]),
        operator_snapshot=AlertOperator(row["operator_snapshot"]),
        threshold_snapshot=row["threshold_snapshot"],
        triggered_value=row["triggered_value"],
    )
