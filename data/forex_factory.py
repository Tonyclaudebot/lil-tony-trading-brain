"""
Macro calendar stub — Forex Factory removed per project rules.
Returns empty event lists so the rest of the system degrades gracefully.
"""
from datetime import date


def get_economic_calendar(days_ahead: int = 14) -> list[dict]:
    return []


def get_key_macro_events(days_ahead: int = 14) -> list[dict]:
    return []


def days_until(event: dict) -> int:
    return (date.fromisoformat(event["date"]) - date.today()).days


def _deduplicate(events: list[dict]) -> list[dict]:
    return events
