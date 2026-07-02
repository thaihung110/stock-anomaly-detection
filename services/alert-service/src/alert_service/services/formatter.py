import html
from datetime import datetime

from alert_service.core.schema import (
    AlertEvent,
    AlertSeverity,
    ConfirmedAlertEvent,
    CustomAlertEvent,
    FollowUpEvent,
    LLMJudgement,
    NewsRef,
    RuleName,
)

_SEVERITY_EMOJI = {AlertSeverity.HIGH: "🔴", AlertSeverity.MEDIUM: "🟡"}

_BATCH_DATA_NOTE = "\n⚠️ _Note: this indicator reflects end-of-previous-day batch data, not real-time intraday values._"

# HTML variant of the batch note for confirmed / follow-up messages (parse_mode=HTML).
_BATCH_DATA_NOTE_HTML = (
    "\n⚠️ <i>Note: this indicator reflects end-of-previous-day batch data, "
    "not real-time intraday values.</i>"
)


def _esc(text: object) -> str:
    """HTML-escape any value for safe embedding in a Telegram HTML message.

    Escapes &, <, >, and quotes so LLM-generated text / news titles can never
    break the HTML parse. quote=True also makes the result safe inside an
    <a href="..."> attribute.
    """
    return html.escape(str(text), quote=True)


# Context-snapshot keys that represent a USD price (rendered as $X.XX).
_CURRENCY_CTX: frozenset[str] = frozenset(
    {
        "price",
        "prev_close",
        "open",
        "high",
        "low",
        "close",
        "vwap_5d_avg",
        "bb_upper_20d",
        "bb_lower_20d",
        "bb_mid_20d",
    }
)


def _fmt_timestamp(ts: str) -> str:
    """Human-friendly UTC timestamp: ``2026-06-30 16:50 UTC``.

    Drops the ISO ``T`` separator and seconds and replaces ``Z`` with ``UTC``.
    Falls back to the raw string if it cannot be parsed.
    """
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return ts


def _fmt_number(value: float) -> str:
    """Lossless, readable number: thousands separators, no trailing ``.0``.

    ``56326864.0`` → ``56,326,864`` · ``198.03`` → ``198.03`` · ``4.8`` → ``4.8``.
    Exact value is preserved (no rounding/abbreviation) so insight is intact.
    """
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _fmt_context_value(key: str, value: float) -> str:
    """Render a context-snapshot value with a unit inferred from its key.

    Units only where unambiguous; unitless indicators (RSI, Bollinger position)
    are left bare so no misleading unit is shown.
    """
    k = key.lower()
    if k in _CURRENCY_CTX:
        return f"${value:,.2f}"
    if "volume" in k:
        return f"{_fmt_number(value)} shares"
    if "ratio" in k:
        return f"{value:.2f}×"
    if k.startswith("z_") or "zscore" in k:
        return f"{value:.2f}σ"
    if "rsi" in k:  # RSI is a 0-100 index — no unit
        return f"{value:.1f}"
    return _fmt_number(value)


def _fmt_metric(rule: RuleName, value: float) -> str:
    """Render triggered_value / threshold with the unit implied by the rule.

    z-score rules → σ · volume ratio → × · intraday range → % (fraction ×100).
    RSI / Bollinger are unitless.
    """
    if rule in (RuleName.PRICE_ZSCORE, RuleName.VOLUME_ZSCORE):
        return f"{value:.2f}σ"
    if rule is RuleName.VOLUME_RATIO:
        return f"{value:.2f}×"
    if rule is RuleName.INTRADAY_RANGE:
        return f"{value * 100:.2f}%"
    return _fmt_number(value)


def _render_news_refs(refs: list[NewsRef], heading: str) -> str:
    """Render up to 3 news refs as an HTML bullet list with embedded links.

    A ref with a URL becomes a clickable <a href> on its title; the source name
    (if present) is appended in italic. Without a URL the title is shown plain.
    """
    lines: list[str] = []
    for ref in refs[:3]:
        title = _esc(ref.title)
        if ref.url:
            anchor = f'<a href="{_esc(ref.url)}">{title}</a>'
        else:
            anchor = title
        source = f" — <i>{_esc(ref.source)}</i>" if ref.source else ""
        lines.append(f"  • {anchor}{source}")
    return f"\n📰 <b>{heading}</b>\n" + "\n".join(lines)


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

    ctx_lines = "\n".join(
        f"  • `{k}`: `{v}`" for k, v in alert.context_snapshot.items()
    )
    ctx_block = f"Context:\n{ctx_lines}" if ctx_lines else ""

    parts = [header, rule_line, value_line, time_line]
    if ctx_block:
        parts.append(ctx_block)

    if alert.rule_name in (RuleName.RSI_EXTREME, RuleName.BOLLINGER_BREAKOUT):
        parts.append(_BATCH_DATA_NOTE)

    return "\n".join(parts)


# Judgement → (emoji, short label). Used in the HTML "AI Analysis" header line.
_JUDGEMENT_LABEL: dict[LLMJudgement, tuple[str, str]] = {
    LLMJudgement.EXPLAINED: ("✅", "EXPLAINED — news context found"),
    LLMJudgement.UNEXPLAINED: ("❓", "UNEXPLAINED — no news backing detected"),
    LLMJudgement.UNCERTAIN: ("⏳", "UNCERTAIN — AI analysis unavailable"),
}


def format_confirmed_message(event: ConfirmedAlertEvent) -> str:
    """Return a Telegram **HTML** message for a confirmed (LLM-validated) alert.

    Uses parse_mode=HTML so key numbers can be bold (<b>) and news refs can embed
    clickable links (<a href>). All LLM-generated text and news titles are
    HTML-escaped via _esc() so they can never break the parse.
    """
    emoji = _SEVERITY_EMOJI[event.severity]
    header = f"{emoji} <b>{_esc(event.severity.value)} ALERT</b> · <b>{_esc(event.symbol)}</b>"
    rule_line = f"📋 Rule: <b>{_esc(event.rule_name.value.replace('_', ' ').title())}</b>"
    value_line = (
        f"📊 Value: <b>{_esc(_fmt_metric(event.rule_name, event.triggered_value))}</b> "
        f"(threshold <b>{_esc(_fmt_metric(event.rule_name, event.threshold))}</b>)"
    )
    time_line = f"🕐 {_esc(_fmt_timestamp(event.event_ts))}"

    ctx_lines = "\n".join(
        f"  • {_esc(k)}: <b>{_esc(_fmt_context_value(k, v))}</b>"
        for k, v in event.context_snapshot.items()
    )
    ctx_block = f"\n<b>Context</b>\n{ctx_lines}" if ctx_lines else ""

    j_emoji, j_label = _JUDGEMENT_LABEL[event.llm_judgement]
    judgement_line = f"\n🤖 <b>AI Analysis: {j_emoji} {_esc(j_label)}</b>"

    parts = [header, rule_line, value_line, time_line]
    if ctx_block:
        parts.append(ctx_block)

    if event.rule_name in (RuleName.RSI_EXTREME, RuleName.BOLLINGER_BREAKOUT):
        parts.append(_BATCH_DATA_NOTE_HTML)

    parts.append(judgement_line)

    if event.final_explanation:
        parts.append(_esc(event.final_explanation))

    if event.news_refs:
        parts.append(_render_news_refs(event.news_refs, "Related news"))

    return "\n".join(parts)


def format_followup_message(event: FollowUpEvent) -> str:
    """Return a Telegram **HTML** follow-up message when a re-check updates a verdict.

    Uses parse_mode=HTML for bold verdicts and embedded news links. All
    LLM-generated text is HTML-escaped via _esc().
    """
    is_flip = event.prev_judgement != event.new_judgement
    badge = "🔄 <b>VERDICT CHANGED</b>" if is_flip else "✅ <b>CONFIRMED</b>"
    header = f"📰 <b>Follow-up Update · {_esc(event.symbol)}</b>"
    verdict_line = (
        f"{_esc(event.prev_judgement.value)} → "
        f"<b>{_esc(event.new_judgement.value)}</b>  {badge}"
    )
    parts = [header, verdict_line]

    if event.news_summary:
        parts.append(_esc(event.news_summary))

    if event.news_refs:
        parts.append(_render_news_refs(event.news_refs, "New evidence"))

    return "\n".join(parts)


def format_custom_message(event: CustomAlertEvent) -> str:
    """Return a plain-text message string for a custom user alert.

    Uses parse_mode=None (not MarkdownV2) to avoid escaping issues with
    user-supplied field/operator strings.
    """
    batch_note = (
        " ⚠️ (end-of-previous-day)"
        if event.field in _BATCH_DAILY_FIELDS
        else ""
    )
    return (
        f"⚡ Custom Alert: {event.symbol}\n"
        f"Field: {event.field}{batch_note}\n"
        f"Condition: {event.field} {event.operator} {event.threshold}\n"
        f"Current value: {event.triggered_value:.4f}\n"
        f"Time: {event.triggered_at}"
    )
