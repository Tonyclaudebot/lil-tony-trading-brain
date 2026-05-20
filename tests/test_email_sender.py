import smtplib
from unittest.mock import MagicMock, patch

from alerts.email_sender import send_email_alert


def _patch_settings(**kwargs):
    defaults = dict(
        SMTP_HOST="smtp.gmail.com",
        SMTP_PORT=587,
        SMTP_USER="tonyclaudebot@gmail.com",
        SMTP_PASSWORD="test-app-password",
        ALERT_RECIPIENT="tonyclaudebot@gmail.com",
    )
    return patch.multiple("alerts.email_sender.settings", **{**defaults, **kwargs})


def test_send_success():
    mock_smtp = MagicMock()
    with _patch_settings(), \
         patch("alerts.email_sender.smtplib.SMTP", return_value=mock_smtp) as smtp_cls:
        mock_smtp.__enter__ = lambda s: s
        mock_smtp.__exit__ = MagicMock(return_value=False)
        result = send_email_alert("Lil Tony Alert", "Test body")
    assert result is True
    mock_smtp.sendmail.assert_called_once()


def test_returns_false_when_no_password():
    with _patch_settings(SMTP_PASSWORD=""):
        result = send_email_alert("Subject", "Body")
    assert result is False


def test_auth_failure_returns_false():
    mock_smtp = MagicMock()
    mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
    with _patch_settings(), \
         patch("alerts.email_sender.smtplib.SMTP", return_value=mock_smtp):
        mock_smtp.__enter__ = lambda s: s
        mock_smtp.__exit__ = MagicMock(return_value=False)
        result = send_email_alert("Subject", "Body")
    assert result is False


def test_smtp_error_returns_false():
    mock_smtp = MagicMock()
    mock_smtp.sendmail.side_effect = smtplib.SMTPException("server error")
    with _patch_settings(), \
         patch("alerts.email_sender.smtplib.SMTP", return_value=mock_smtp):
        mock_smtp.__enter__ = lambda s: s
        mock_smtp.__exit__ = MagicMock(return_value=False)
        result = send_email_alert("Subject", "Body")
    assert result is False


def test_network_error_returns_false():
    with _patch_settings(), \
         patch("alerts.email_sender.smtplib.SMTP", side_effect=ConnectionRefusedError):
        result = send_email_alert("Subject", "Body")
    assert result is False
