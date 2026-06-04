from alert_service.schema import AlertEvent, AlertSeverity, CustomAlertEvent, RuleName

_SEVERITY_EMOJI = {AlertSeverity.HIGH: "🔴", AlertSeverity.MEDIUM: "🟡"}

_BATCH_DATA_NOTE = (
    "\n⚠️ _Note: this indicator reflects end-of-previous-day batch data, not real-time intraday values._"
)

# Fields sourced from daily batch (not real-time intraday) — must note in alert messages.
# Values must match AlertField.RSI_14.value / AlertField.BB_POSITION.value in rule-engine.
_BATCH_DAILY_FIELDS: frozenset[str] = frozenset({"rsi_14", "bb_position"})


def format_message(alert: AlertEvent) -> str:
    """Return a Telegram MarkdownV2-safe message string for the given alert."""
    emoji = _SEVERITY_EMOJI[alert.severity]
    header = f"{emoji} *{alert.severity.value} ALERT* — `{alert.symbol}`"
    rule_line = f"Rule: *{alert.rule_name.value.replace('_', ' ').title()}*"
    value_line = f"Triggered value: `{alert.triggered_value}` (threshold: `{alert.threshold}`)"
    time_line = f"Time: `{alert.event_ts}`"

    ctx_lines = "\n".join(f"  • `{k}`: `{v}`" for k, v in alert.context_snapshot.items())
    ctx_block = f"Context:\n{ctx_lines}" if ctx_lines else ""

    parts = [header, rule_line, value_line, time_line]
    if ctx_block:
        parts.append(ctx_block)

    if alert.rule_name in (RuleName.RSI_EXTREME, RuleName.BOLLINGER_BREAKOUT):
        parts.append(_BATCH_DATA_NOTE)

    return "\n".join(parts)


def format_custom_message(event: CustomAlertEvent) -> str:
    """Return a plain-text message string for a custom user alert.

    Uses parse_mode=None (not MarkdownV2) to avoid escaping issues with
    user-supplied field/operator strings.
    """
    batch_note = " ⚠️ (end-of-previous-day)" if event.field in _BATCH_DAILY_FIELDS else ""
    return (
        f"⚡ Custom Alert: {event.symbol}\n"
        f"Field: {event.field}{batch_note}\n"
        f"Condition: {event.field} {event.operator} {event.threshold}\n"
        f"Current value: {event.triggered_value:.4f}\n"
        f"Time: {event.triggered_at}"
    )
