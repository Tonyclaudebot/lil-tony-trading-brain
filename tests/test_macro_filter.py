from datetime import date, timedelta
from unittest.mock import patch

from brain.macro_filter import check_macro_risk, get_events_within


def _event(title: str, days_from_now: int) -> dict:
    return {
        "title": title,
        "date": (date.today() + timedelta(days=days_from_now)).isoformat(),
        "forecast": "",
        "previous": "",
    }


def test_no_events_returns_no_risk():
    is_binary, warning = check_macro_risk([])
    assert is_binary is False
    assert warning is None


def test_far_events_not_flagged():
    events = [_event("CPI m/m", 10)]
    is_binary, warning = check_macro_risk(events)
    assert is_binary is False
    assert warning is None


def test_near_event_produces_warning():
    events = [_event("CPI m/m", 5)]
    is_binary, warning = check_macro_risk(events)
    assert warning is not None
    assert "CPI" in warning


def test_binary_event_within_3_days():
    events = [_event("FOMC Rate Decision", 2)]
    is_binary, warning = check_macro_risk(events)
    assert is_binary is True
    assert "binary event" in warning.lower()


def test_binary_event_today():
    events = [_event("CPI m/m", 0)]
    is_binary, warning = check_macro_risk(events)
    assert is_binary is True
    assert "TODAY" in warning


def test_get_events_within_filters_correctly():
    events = [
        _event("CPI", 2),
        _event("NFP", 5),
        _event("GDP", 10),
    ]
    result = get_events_within(events, 5)
    titles = [e["title"] for e in result]
    assert "CPI" in titles
    assert "NFP" in titles
    assert "GDP" not in titles


def test_multiple_events_all_shown_in_warning():
    events = [_event("CPI m/m", 3), _event("FOMC Rate Decision", 6)]
    _, warning = check_macro_risk(events)
    assert "CPI" in warning
    assert "FOMC" in warning
