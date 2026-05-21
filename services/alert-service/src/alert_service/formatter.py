from alert_service.schema import AlertEvent, AlertSeverity, RuleName

_SEVERITY_EMOJI = {AlertSeverity.HIGH: "🔴", AlertSeverity.MEDIUM: "🟡"}

_BATCH_DATA_NOTE = (
    "\n⚠️ _Note: this indicator reflects end-of-previous-day batch data, not real-time intraday values._"
)


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
