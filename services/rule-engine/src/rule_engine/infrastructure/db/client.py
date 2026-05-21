from uuid import UUID

import asyncpg
import structlog

from rule_engine.domain.enums import AlertField, AlertFrequency, AlertOperator, AlertStatus
from rule_engine.domain.models import UserAlertEvent, UserAlertRule

logger = structlog.get_logger(__name__)


class DbClient:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=10)
        logger.info("db_pool_created", dsn=self._dsn)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("db_pool_closed")

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("DbClient not connected — call connect() first")
        return self._pool

    # ── Phase 3.2 ────────────────────────────────────────────────────────────

    async def get_active_rules(self) -> list[UserAlertRule]:
        rows = await self.pool.fetch(
            """
            SELECT rule_id, user_id, symbols, field, operator, threshold,
                   frequency, cooldown_min, status, created_at, updated_at
            FROM user_alert_rules
            WHERE status = $1::alert_status
            """,
            AlertStatus.ACTIVE.value,
        )
        return [_row_to_rule(r) for r in rows]

    async def insert_alert_event(self, event: UserAlertEvent) -> None:
        await self.pool.execute(
            """
            INSERT INTO user_alert_events
                (rule_id, user_id, symbol, triggered_at,
                 field_snapshot, operator_snapshot, threshold_snapshot, triggered_value)
            VALUES ($1, $2, $3, $4, $5::alert_field, $6::alert_operator, $7, $8)
            """,
            event.rule_id,
            event.user_id,
            event.symbol,
            event.triggered_at,
            event.field_snapshot.value,
            event.operator_snapshot.value,
            event.threshold_snapshot,
            event.triggered_value,
        )
        logger.info(
            "alert_event_inserted",
            rule_id=str(event.rule_id),
            symbol=event.symbol,
        )

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

    # ── Phase 3.3 ────────────────────────────────────────────────────────────

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
                LIMIT 100
                """,
                user_id,
                symbol.upper(),
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
                LIMIT 100
                """,
                user_id,
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
