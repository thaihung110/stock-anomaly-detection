from alert_service.schema import (
    AlertEvent,
    AlertSeverity,
    ConfirmedAlertEvent,
    CustomAlertEvent,
    FollowUpEvent,
    LLMJudgement,
    RuleName,
)

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


_JUDGEMENT_LABEL: dict[LLMJudgement, str] = {
    LLMJudgement.EXPLAINED: "EXPLAINED — news context found",
    LLMJudgement.UNEXPLAINED: "UNEXPLAINED — no news backing detected",
    LLMJudgement.UNCERTAIN: "UNCERTAIN — AI analysis unavailable",
}


def format_confirmed_message(event: ConfirmedAlertEvent) -> str:
    """Return a plain-text message for a confirmed (LLM-validated) alert.

    Uses parse_mode=None — LLM-generated text may contain MarkdownV2 special chars.
    """
    emoji = _SEVERITY_EMOJI[event.severity]
    header = f"{emoji} {event.severity.value} ALERT — {event.symbol}"
    rule_line = f"Rule: {event.rule_name.value.replace('_', ' ').title()}"
    value_line = f"Triggered value: {event.triggered_value} (threshold: {event.threshold})"
    time_line = f"Time: {event.event_ts}"

    ctx_lines = "\n".join(f"  • {k}: {v}" for k, v in event.context_snapshot.items())
    ctx_block = f"Context:\n{ctx_lines}" if ctx_lines else ""

    judgement_line = f"\nAI Analysis: {_JUDGEMENT_LABEL[event.llm_judgement]}"
    parts = [header, rule_line, value_line, time_line]
    if ctx_block:
        parts.append(ctx_block)

    if event.rule_name in (RuleName.RSI_EXTREME, RuleName.BOLLINGER_BREAKOUT):
        parts.append(_BATCH_DATA_NOTE)

    parts.append(judgement_line)

    if event.final_explanation:
        parts.append(event.final_explanation)

    if event.news_refs:
        ref_lines = "\n".join(
            f"  • {ref.title}" + (f" ({ref.source})" if ref.source else "")
            for ref in event.news_refs[:3]
        )
        parts.append(f"\nRelated news:\n{ref_lines}")

    return "\n".join(parts)


def format_followup_message(event: FollowUpEvent) -> str:
    """Return a plain-text follow-up message when re-check changes the verdict.

    Uses parse_mode=None — may contain LLM-generated text.
    """
    is_flip = event.prev_judgement != event.new_judgement
    header = f"Follow-up Update: {event.symbol}"
    verdict_line = (
        f"{event.prev_judgement.value} -> {event.new_judgement.value}"
        + (" [VERDICT CHANGED]" if is_flip else " [CONFIRMED]")
    )
    parts = [header, verdict_line]

    if event.news_summary:
        parts.append(event.news_summary)

    if event.news_refs:
        ref_lines = "\n".join(
            f"  • {ref.title}" + (f" ({ref.source})" if ref.source else "")
            for ref in event.news_refs[:3]
        )
        parts.append(f"\nNew evidence:\n{ref_lines}")

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
