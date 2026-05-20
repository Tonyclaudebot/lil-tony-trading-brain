from datetime import date, timedelta
from unittest.mock import patch

import pytest

from brain.earnings_intel import EarningsIntel, analyze_earnings, _build_warning


def _mock_earnings(days_from_now: int, history: list[dict] | None = None):
    """Patch get_next_earnings_date and get_earnings_history for testing."""
    next_date = date.today() + timedelta(days=days_from_now)
    hist = history or []
    return (
        patch("brain.earnings_intel.get_next_earnings_date", return_value=next_date),
        patch("brain.earnings_intel.get_earnings_history", return_value=hist),
    )


def test_binary_event_within_7_days():
    p1, p2 = _mock_earnings(5)
    with p1, p2:
        intel = analyze_earnings("AAPL")
    assert intel.proximity_risk == "HIGH"
    assert intel.binary_event is True
    assert intel.days_to_earnings == 5


def test_elevated_risk_8_to_30_days():
    p1, p2 = _mock_earnings(15)
    with p1, p2:
        intel = analyze_earnings("AAPL")
    assert intel.proximity_risk == "ELEVATED"
    assert intel.binary_event is False


def test_standard_beyond_30_days():
    p1, p2 = _mock_earnings(45)
    with p1, p2:
        intel = analyze_earnings("AAPL")
    assert intel.proximity_risk == "STANDARD"
    assert intel.warning is None


def test_no_earnings_date_returns_unknown():
    with patch("brain.earnings_intel.get_next_earnings_date", return_value=None), \
         patch("brain.earnings_intel.get_earnings_history", return_value=[]):
        intel = analyze_earnings("AAPL")
    assert intel.proximity_risk == "UNKNOWN"
    assert intel.binary_event is False


def test_historical_beat_rate_calculated():
    history = [
        {"beat": True,  "next_day_move": 5.2},
        {"beat": True,  "next_day_move": 3.1},
        {"beat": False, "next_day_move": -4.5},
        {"beat": True,  "next_day_move": 6.0},
    ]
    p1, p2 = _mock_earnings(5, history)
    with p1, p2:
        intel = analyze_earnings("AAPL")
    assert intel.beat_rate == pytest.approx(0.75, abs=0.01)
    assert intel.sample_size == 4


def test_avg_move_calculated():
    history = [
        {"beat": True,  "next_day_move": 4.0},
        {"beat": False, "next_day_move": -6.0},
    ]
    p1, p2 = _mock_earnings(5, history)
    with p1, p2:
        intel = analyze_earnings("AAPL")
    assert intel.avg_move_abs == pytest.approx(5.0, abs=0.1)


def test_high_volatility_warning_contains_required_text():
    history = [{"beat": True, "next_day_move": 5.0}] * 6 + [{"beat": False, "next_day_move": -3.0}] * 2
    p1, p2 = _mock_earnings(4, history)
    with p1, p2:
        intel = analyze_earnings("TSLA")
    assert "EARNINGS" in intel.warning
    assert "Big Tony" in intel.warning
    assert "IV crush" in intel.warning


def test_elevated_warning_mentions_days():
    p1, p2 = _mock_earnings(20)
    with p1, p2:
        intel = analyze_earnings("MSFT")
    assert "20d" in intel.warning
