from datetime import datetime

from scanner.strategies.base import TradePlan

_SEP = "─" * 30


def _webull_link(ticker: str) -> str:
    return f"https://www.webull.com/quote/{ticker}"


def _fmt_date(iso_date: str) -> str:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        return iso_date


def format_trade_alert(plan: TradePlan) -> str:
    direction = plan.opt_type.upper()
    exp_fmt = _fmt_date(plan.expiration)
    target_pct = f"+{plan.target_pct:.0f}%"

    lines = [
        "LIL TONY ALERT",
        f"{plan.ticker} — {plan.strategy_name}",
        f"Buy ${plan.strike:.0f} {direction} exp {exp_fmt}",
        f"Entry: ${plan.entry:.2f}",
        f"Target: ${plan.target:.2f} ({target_pct})",
        f"Stop: ${plan.stop:.2f}",
        f"Confidence: {plan.confidence}",
        f"Open in Webull: {_webull_link(plan.ticker)}",
    ]

    # ── Risk scale ────────────────────────────────────────────────────────────
    if plan.risk_score:
        _scales = {"LOW": "▰▰▱▱▱", "MEDIUM": "▰▰▰▱▱", "HIGH": "▰▰▰▰▰"}
        scale = _scales.get(plan.risk_score, "▱▱▱▱▱")
        lines.append(f"Risk: {scale} {plan.risk_score}  |  IV: {plan.risk_iv_label}  |  Premium: {plan.risk_premium_label}")

    # ── ORB data (Momentum Breakout only) ────────────────────────────────────
    if plan.or_high is not None and plan.or_low is not None:
        hit_str = f"  ← {plan.or_target_hit} already hit" if plan.or_target_hit else ""
        lines += [
            "",
            _SEP,
            "Opening Range Breakout",
            f"OR High: ${plan.or_high:.2f}  |  OR Low: ${plan.or_low:.2f}",
        ]
        if plan.or_t1 is not None:
            lines.append(
                f"Targets: T1 ${plan.or_t1:.2f}  T2 ${plan.or_t2:.2f}  T3 ${plan.or_t3:.2f}{hit_str}"
            )

    # ── Earnings warning ─────────────────────────────────────────────────────
    if plan.earnings_warning:
        lines += ["", _SEP, plan.earnings_warning]

    # ── Macro warning ─────────────────────────────────────────────────────────
    if plan.macro_warning and plan.macro_warning not in (plan.earnings_warning or ""):
        lines += ["", _SEP, "MACRO EVENT ALERT", plan.macro_warning]

    # ── Binary event confirmation request (Big Tony House Rules) ─────────────
    if plan.binary_event_flag and plan.binary_event_detail:
        lines += [
            "",
            _SEP,
            "!! BINARY EVENT — CONFIRMATION REQUIRED !!",
            plan.binary_event_detail,
            "Reply to confirm or skip this trade.",
        ]
    elif plan.binary_event_flag:
        lines += [
            "",
            _SEP,
            "!! BINARY EVENT — CONFIRMATION REQUIRED !!",
            "Reply to confirm or skip this trade.",
        ]

    # ── News context ─────────────────────────────────────────────────────────
    if plan.news_context:
        lines += ["", _SEP, "News:"]
        for headline in plan.news_context:
            lines.append(f"  • {headline}")

    return "\n".join(lines)
