"""Telegram command handlers for user-defined custom alerts (Phase 3.3).

All handlers are pure async functions injected with dependencies via closures
(_make_*_handler pattern). This keeps handlers testable — no global state.

Commands: /setalert /listalerts /pausealert /resumealert /resetalert /delalert /alerthistory

Rule selection UX: /listalerts stores a {index: rule_id} map in context.user_data["rule_index"].
All mutating commands accept either a 1-based index (e.g. "1") or a full UUID. This lets
users type "/pausealert 1" on mobile instead of copy-pasting a 36-char UUID.
"""
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

import structlog
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from telegram_bot.application.alert_service import AlertService
from telegram_bot.domain.enums import (
    BATCH_DAILY_FIELDS,
    OPERATOR_TOKENS,
    AlertField,
    AlertFrequency,
    AlertStatus,
)
from telegram_bot.infrastructure.telegram import BotApp

logger = structlog.get_logger(__name__)

_HandlerFunc = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]

_BATCH_DAILY_NOTE = "⚠️ batch daily"
_FREQ_DISPLAY = {
    AlertFrequency.ONCE: "once",
    AlertFrequency.EVERY_TIME: "every time",
}
_STATUS_EMOJI = {
    AlertStatus.ACTIVE: "🟢 ACTIVE",
    AlertStatus.PAUSED: "⏸ PAUSED",
    AlertStatus.TRIGGERED: "✅ TRIGGERED",
}
_RULE_INDEX_KEY = "rule_index"


def _resolve_rule_id(arg: str, context: ContextTypes.DEFAULT_TYPE) -> UUID | None:
    """Resolve a user-supplied argument to a UUID.

    Accepts either a 1-based index from the last /listalerts (e.g. "1") or a
    full UUID string. Returns None if neither form is valid.
    """
    rule_index: dict[int, UUID] = (context.user_data or {}).get(_RULE_INDEX_KEY, {})
    if arg.isdigit():
        return rule_index.get(int(arg))
    try:
        return UUID(arg)
    except ValueError:
        return None


# ── /setalert ─────────────────────────────────────────────────────────────────

def _make_setalert_handler(svc: AlertService) -> _HandlerFunc:
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None:
            return
        msg = update.effective_message
        telegram_id = update.effective_user.id
        args = context.args or []

        # /setalert <SYMBOL|*> <field> <op> <threshold> [once|every]
        if len(args) < 4:
            await msg.reply_text(
                "Usage: /setalert <SYMBOL|*> <field> <op> <threshold> [once|every]\n"
                "Example: /setalert AAPL price > 200 every\n"
                "Fields: price, daily_return, day_volume, volume_zscore, "
                "volume_ratio_20d, price_zscore, rsi_14, bb_position\n"
                "Operators: > < >= <= crosses_up crosses_down"
            )
            return

        raw_symbol = args[0].upper()
        raw_field = args[1].lower()
        raw_op = args[2].lower()
        raw_threshold = args[3]
        raw_freq = args[4].lower() if len(args) >= 5 else "every"

        try:
            field = AlertField(raw_field)
        except ValueError:
            await msg.reply_text(
                f"Unknown field: {raw_field!r}.\n"
                "Valid: price, daily_return, day_volume, volume_zscore, "
                "volume_ratio_20d, price_zscore, rsi_14, bb_position"
            )
            return

        operator = OPERATOR_TOKENS.get(raw_op)
        if operator is None:
            await msg.reply_text(
                f"Unknown operator: {raw_op!r}.\n"
                "Valid: > < >= <= crosses_up crosses_down"
            )
            return

        try:
            threshold = float(raw_threshold)
        except ValueError:
            await msg.reply_text(f"Threshold must be a number, got: {raw_threshold!r}")
            return

        freq_map = {"once": AlertFrequency.ONCE, "every": AlertFrequency.EVERY_TIME}
        frequency = freq_map.get(raw_freq)
        if frequency is None:
            await msg.reply_text("Frequency must be 'once' or 'every'.")
            return

        symbols = ["*"] if raw_symbol == "*" else [raw_symbol]

        try:
            rule_id = await svc.create_alert(
                telegram_id=telegram_id,
                symbols=symbols,
                field=field,
                operator=operator,
                threshold=threshold,
                frequency=frequency,
            )
        except Exception:
            logger.exception("setalert_failed", telegram_id=telegram_id)
            await msg.reply_text("Failed to create alert. Please try again later.")
            return

        batch_note = (
            f"\n{_BATCH_DAILY_NOTE} — reflects end-of-previous-day value"
            if field in BATCH_DAILY_FIELDS
            else ""
        )
        freq_label = _FREQ_DISPLAY[frequency]
        sym_label = raw_symbol
        await msg.reply_text(
            f"Alert set!\n"
            f"Rule ID: {str(rule_id)[:8]}\n"
            f"{sym_label} {field.value} {operator.value} {threshold}"
            f" ({freq_label}, cooldown 60 min){batch_note}"
        )

    return handle


# ── /listalerts ───────────────────────────────────────────────────────────────

def _make_listalerts_handler(svc: AlertService) -> _HandlerFunc:
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None:
            return
        msg = update.effective_message
        telegram_id = update.effective_user.id

        try:
            rules = await svc.list_alerts(telegram_id)
        except Exception:
            logger.exception("listalerts_failed", telegram_id=telegram_id)
            await msg.reply_text("Failed to fetch alerts. Please try again later.")
            return

        if not rules:
            await msg.reply_text("You have no alert rules yet. Use /setalert to create one.")
            return

        active_count = sum(1 for r in rules if r.status == AlertStatus.ACTIVE)
        paused_count = sum(1 for r in rules if r.status == AlertStatus.PAUSED)
        triggered_count = sum(1 for r in rules if r.status == AlertStatus.TRIGGERED)

        header_parts: list[str] = []
        if active_count:
            header_parts.append(f"{active_count} active")
        if paused_count:
            header_parts.append(f"{paused_count} paused")
        if triggered_count:
            header_parts.append(f"{triggered_count} triggered")

        # Build index map so user can reference rules by number in subsequent commands
        rule_index: dict[int, UUID] = {}
        lines: list[str] = [
            f"Your Alerts ({', '.join(header_parts)}):\n",
            "Use the number with /pausealert, /resumealert, /resetalert, /delalert\n",
        ]
        for i, rule in enumerate(rules, start=1):
            rule_index[i] = rule.rule_id  # type: ignore[assignment]
            status_label = _STATUS_EMOJI.get(rule.status, rule.status.value)
            sym_label = ", ".join(rule.symbols)
            freq_label = _FREQ_DISPLAY.get(rule.frequency, rule.frequency.value)
            batch_note = f" {_BATCH_DAILY_NOTE}" if rule.field in BATCH_DAILY_FIELDS else ""
            triggered_hint = (
                " (use /resetalert to reuse)" if rule.status == AlertStatus.TRIGGERED else ""
            )
            lines.append(
                f"{i}. [{status_label}] "
                f"{sym_label} {rule.field.value} {rule.operator.value} {rule.threshold}"
                f" ({freq_label}){batch_note}{triggered_hint}"
            )

        if context.user_data is not None:
            context.user_data[_RULE_INDEX_KEY] = rule_index

        await msg.reply_text("\n".join(lines))

    return handle


# ── /pausealert /resumealert /resetalert ──────────────────────────────────────

def _make_status_handler(svc: AlertService, action: str) -> _HandlerFunc:
    """Generic handler for pause / resume / reset — they share the same shape."""
    action_map = {
        "pause": svc.pause_alert,
        "resume": svc.resume_alert,
        "reset": svc.reset_alert,
    }
    past_tense_map = {"pause": "paused", "resume": "resumed", "reset": "reset"}
    action_fn = action_map[action]
    past_tense = past_tense_map[action]

    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None:
            return
        msg = update.effective_message
        telegram_id = update.effective_user.id
        args = context.args or []

        if not args:
            await msg.reply_text(f"Usage: /{action}alert <number> — run /listalerts first")
            return

        rule_id = _resolve_rule_id(args[0], context)
        if rule_id is None:
            await msg.reply_text(
                f"Rule {args[0]!r} not found.\nRun /listalerts first, then use the number (e.g. /{action}alert 1)."
            )
            return

        try:
            ok = await action_fn(telegram_id, rule_id)
        except Exception:
            logger.exception("alert_status_change_failed", action=action, telegram_id=telegram_id)
            await msg.reply_text("Operation failed. Please try again later.")
            return

        if ok:
            await msg.reply_text(f"Rule {args[0]} {past_tense}.")
        else:
            await msg.reply_text(
                f"Rule {args[0]!r} not found or does not belong to you."
            )

    return handle


# ── /delalert ─────────────────────────────────────────────────────────────────

def _make_delalert_handler(svc: AlertService) -> _HandlerFunc:
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None:
            return
        msg = update.effective_message
        telegram_id = update.effective_user.id
        args = context.args or []

        if not args:
            await msg.reply_text("Usage: /delalert <number> — run /listalerts first")
            return

        rule_id = _resolve_rule_id(args[0], context)
        if rule_id is None:
            await msg.reply_text(
                f"Rule {args[0]!r} not found.\nRun /listalerts first, then use the number (e.g. /delalert 1)."
            )
            return

        try:
            deleted = await svc.delete_alert(telegram_id, rule_id)
        except Exception:
            logger.exception("delalert_failed", telegram_id=telegram_id)
            await msg.reply_text("Failed to delete rule. Please try again later.")
            return

        if deleted:
            await msg.reply_text(f"Rule {args[0]} deleted.")
        else:
            await msg.reply_text(
                f"Rule {args[0]!r} not found or does not belong to you."
            )

    return handle


# ── /alerthistory ─────────────────────────────────────────────────────────────

def _make_history_handler(svc: AlertService) -> _HandlerFunc:
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None:
            return
        msg = update.effective_message
        telegram_id = update.effective_user.id
        args = context.args or []
        symbol: str | None = args[0].upper() if args else None

        try:
            events = await svc.alert_history(telegram_id, symbol)
        except Exception:
            logger.exception("alerthistory_failed", telegram_id=telegram_id)
            await msg.reply_text("Failed to fetch history. Please try again later.")
            return

        sym_header = f" for {symbol}" if symbol else ""
        header = f"Alert History{sym_header}:\n"

        if not events:
            await msg.reply_text(f"{header}No events found.")
            return

        lines: list[str] = [header]
        for ev in events:
            ts = ev.triggered_at.strftime("%Y-%m-%d %H:%M UTC") if ev.triggered_at else "?"
            lines.append(
                f"{ts} — {ev.symbol} {ev.field_snapshot.value} "
                f"{ev.operator_snapshot.value} {ev.threshold_snapshot} "
                f"| value: {ev.triggered_value:.4f}"
            )

        await msg.reply_text("\n".join(lines))

    return handle


# ── Registration ──────────────────────────────────────────────────────────────

def register_alert_handlers(app: BotApp, svc: AlertService) -> None:
    """Register all custom-alert command handlers.

    Single registration point — adding a new command = one new line here.
    Closures inject AlertService so handlers carry no global state.
    """
    app.add_handler(CommandHandler("setalert", _make_setalert_handler(svc)))
    app.add_handler(CommandHandler("listalerts", _make_listalerts_handler(svc)))
    app.add_handler(CommandHandler("pausealert", _make_status_handler(svc, "pause")))
    app.add_handler(CommandHandler("resumealert", _make_status_handler(svc, "resume")))
    app.add_handler(CommandHandler("resetalert", _make_status_handler(svc, "reset")))
    app.add_handler(CommandHandler("delalert", _make_delalert_handler(svc)))
    app.add_handler(CommandHandler("alerthistory", _make_history_handler(svc)))
