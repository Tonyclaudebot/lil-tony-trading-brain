from scanner.strategies.base import TradePlan
from alerts.formatter import format_trade_alert


def _plan(**kwargs) -> TradePlan:
    defaults = dict(
        ticker="NVDA", contract="NVDA240315C00500000",
        opt_type="call", strike=500.0,
        expiration="2024-03-15", dte=30,
        strategy_key="momentum_breakout",
        strategy_name="Momentum Breakout",
        spot=490.0, entry=0.75, target=1.88, stop=0.38,
        target_pct=150.0, iv=42.5, volume=3200,
        open_interest=800, score=81.0, confidence="HIGH",
    )
    return TradePlan(**{**defaults, **kwargs})


def test_format_contains_header():
    assert "LIL TONY ALERT" in format_trade_alert(_plan())


def test_format_ticker_and_strategy():
    msg = format_trade_alert(_plan())
    assert "NVDA" in msg
    assert "Momentum Breakout" in msg


def test_format_direction_call():
    assert "CALL" in format_trade_alert(_plan(opt_type="call"))


def test_format_direction_put():
    assert "PUT" in format_trade_alert(_plan(opt_type="put"))


def test_format_prices():
    msg = format_trade_alert(_plan())
    assert "$0.75" in msg
    assert "$1.88" in msg
    assert "$0.38" in msg


def test_format_target_pct():
    assert "+150%" in format_trade_alert(_plan())


def test_format_confidence():
    assert "HIGH" in format_trade_alert(_plan(confidence="HIGH"))
    assert "MEDIUM" in format_trade_alert(_plan(confidence="MEDIUM"))


def test_format_webull_link():
    msg = format_trade_alert(_plan())
    assert "https://www.webull.com/quote/NVDA" in msg


def test_format_date_human_readable():
    msg = format_trade_alert(_plan(expiration="2024-03-15"))
    assert "Mar" in msg
    assert "15" in msg
