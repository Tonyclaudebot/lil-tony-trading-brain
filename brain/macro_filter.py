"""
Cross-reference active setups against upcoming Fed meetings, CPI, and other
high-impact macro events from Forex Factory.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from data.forex_factory import get_key_macro_events, days_until

logger = logging.getLogger(__name__)

_BINARY_WINDOW_DAYS = 3    # events within this window = binary event risk
_WARNING_WINDOW_DAYS = 7   # events within this window = show in alert


def load_macro_calendar(days_ahead: int = 14) -> list[dict]:
    """Fetch the macro calendar once (call at startup, refresh hourly)."""
    events = get_key_macro_events(days_ahead)
    if events:
        logger.info(f"Macro calendar: {len(events)} high-impact event(s) loaded")
        for e in events:
            logger.info(f"  {e['date']} — {e['title']}")
    return events


def check_macro_risk(macro_events: list[dict]) -> tuple[bool, str | None]:
    """
    Evaluate upcoming macro events against the current setup.

    Returns:
        (is_binary_risk, warning_message)
        is_binary_risk=True means a market-wide binary event is within 3 days.
    """
    if not macro_events:
        return False, None

    near = [e for e in macro_events if days_until(e) <= _WARNING_WINDOW_DAYS]
    if not near:
        return False, None

    is_binary = any(days_until(e) <= _BINARY_WINDOW_DAYS for e in near)

    lines: list[str] = []
    for ev in near:
        d = days_until(ev)
        day_str = "TODAY" if d == 0 else f"in {d}d"
        lines.append(f"{ev['title']} {day_str}")

    if is_binary:
        lines.append("!! Market-wide binary event — elevated volatility expected !!")

    return is_binary, "\n".join(lines)


def get_events_within(macro_events: list[dict], days: int) -> list[dict]:
    """Return events occurring within the next `days` days."""
    return [e for e in macro_events if days_until(e) <= days]
