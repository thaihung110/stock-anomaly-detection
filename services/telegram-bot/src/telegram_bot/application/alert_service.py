"""AlertService — application use-case layer for custom alert management.

Orchestrates IAlertRepository and IRuleEngineClient. Knows WHAT to do (the
business flow) but not HOW (DB queries, HTTP calls). This keeps the use cases
testable with mock adapters.
"""
from uuid import UUID

import structlog

from telegram_bot.domain.enums import AlertField, AlertFrequency, AlertOperator, AlertStatus
from telegram_bot.domain.models import UserAlertEvent, UserAlertRule
from telegram_bot.domain.ports import IAlertRepository, IRuleEngineClient

logger = structlog.get_logger(__name__)

_DEFAULT_COOLDOWN_MIN = 60


class AlertService:
    def __init__(
        self,
        repo: IAlertRepository,
        re_client: IRuleEngineClient,
    ) -> None:
        self._repo = repo
        self._re_client = re_client

    async def create_alert(
        self,
        telegram_id: int,
        symbols: list[str],
        field: AlertField,
        operator: AlertOperator,
        threshold: float,
        frequency: AlertFrequency,
    ) -> UUID:
        """Insert a new rule and trigger Rule Engine hot-reload. Returns rule_id."""
        user_id = await self._repo.get_or_create_user(telegram_id)
        rule = UserAlertRule(
            user_id=user_id,
            symbols=symbols,
            field=field,
            operator=operator,
            threshold=threshold,
            frequency=frequency,
            cooldown_min=_DEFAULT_COOLDOWN_MIN,
            status=AlertStatus.ACTIVE,
        )
        rule_id = await self._repo.insert_rule(rule)
        reloaded = await self._re_client.reload_user_rules()
        logger.info(
            "alert_created",
            rule_id=str(rule_id),
            telegram_id=telegram_id,
            rule_engine_reloaded=reloaded,
        )
        return rule_id

    async def list_alerts(self, telegram_id: int) -> list[UserAlertRule]:
        user_id = await self._repo.get_or_create_user(telegram_id)
        return await self._repo.get_rules_for_user(user_id)

    async def pause_alert(self, telegram_id: int, rule_id: UUID) -> bool:
        """Set status PAUSED. Returns False if rule not found for this user."""
        return await self._change_status(telegram_id, rule_id, AlertStatus.PAUSED)

    async def resume_alert(self, telegram_id: int, rule_id: UUID) -> bool:
        return await self._change_status(telegram_id, rule_id, AlertStatus.ACTIVE)

    async def reset_alert(self, telegram_id: int, rule_id: UUID) -> bool:
        """Reset TRIGGERED → ACTIVE so a ONCE rule can fire again."""
        return await self._change_status(telegram_id, rule_id, AlertStatus.ACTIVE)

    async def delete_alert(self, telegram_id: int, rule_id: UUID) -> bool:
        user_id = await self._repo.get_or_create_user(telegram_id)
        deleted = await self._repo.delete_rule(rule_id, user_id)
        if deleted:
            await self._re_client.reload_user_rules()
        return deleted

    async def alert_history(
        self, telegram_id: int, symbol: str | None
    ) -> list[UserAlertEvent]:
        user_id = await self._repo.get_or_create_user(telegram_id)
        return await self._repo.get_alert_history(user_id, symbol)

    # ── private ───────────────────────────────────────────────────────────────

    async def _change_status(
        self, telegram_id: int, rule_id: UUID, status: AlertStatus
    ) -> bool:
        """Update status only if the rule belongs to this user."""
        user_id = await self._repo.get_or_create_user(telegram_id)
        if not await self._repo.rule_belongs_to_user(rule_id, user_id):
            logger.info("rule_not_owned", rule_id=str(rule_id), telegram_id=telegram_id)
            return False
        await self._repo.update_rule_status(rule_id, status)
        return True
