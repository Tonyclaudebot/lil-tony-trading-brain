from unittest.mock import MagicMock, patch

from alerts.imessage import format_alert, send_imessage
from scanner.setups import Setup


def _setup(**kwargs):
    defaults = dict(
        ticker="AAPL", contract="AAPL240315C00180000",
        setup_type="unusual_volume", strike=180.0,
        expiration="2024-03-15", dte=30, opt_type="call",
        volume=2000, open_interest=500, iv=35.0,
        last_price=2.50, spot_price=175.50,
        detail="Vol/OI 4.0x  (2000 vol / 500 OI)",
    )
    return Setup(**{**defaults, **kwargs})


def test_format_alert_contains_key_fields():
    alert = format_alert(_setup())
    assert "AAPL" in alert
    assert "CALL" in alert
    assert "180" in alert
    assert "unusual_volume" in alert
    assert "175.50" in alert
    assert "--" in alert  # stop/target show -- when not on Setup


def test_format_alert_put_label():
    alert = format_alert(_setup(opt_type="put"))
    assert "PUT" in alert


def test_format_alert_no_spot_price():
    alert = format_alert(_setup(spot_price=None))
    assert "Spot" not in alert


def test_send_imessage_success():
    mock = MagicMock(returncode=0)
    with patch("alerts.imessage.subprocess.run", return_value=mock) as run:
        assert send_imessage("+15555555555", "hello") is True
    run.assert_called_once()


def test_send_imessage_applescript_failure():
    mock = MagicMock(returncode=1, stderr="AppleScript error")
    with patch("alerts.imessage.subprocess.run", return_value=mock):
        assert send_imessage("+15555555555", "hello") is False


def test_send_imessage_timeout():
    from subprocess import TimeoutExpired
    with patch("alerts.imessage.subprocess.run", side_effect=TimeoutExpired("osascript", 10)):
        assert send_imessage("+15555555555", "hello") is False
