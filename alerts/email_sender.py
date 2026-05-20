import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

try:
    import certifi
    _SSL_CAFILE = certifi.where()
except ImportError:
    _SSL_CAFILE = None

from config import settings

logger = logging.getLogger(__name__)


def send_email_alert(subject: str, body: str, html_body: Optional[str] = None) -> bool:
    """
    Send an alert email via SMTP with optional HTML version.
    Falls back to plain text if html_body is not provided.
    Returns True on success, False on failure.
    """
    if not settings.SMTP_PASSWORD:
        logger.error("SMTP_PASSWORD not set — email alert not sent")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_USER
    msg["To"] = settings.ALERT_RECIPIENT

    msg.attach(MIMEText(body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context(cafile=_SSL_CAFILE)
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.sendmail(settings.SMTP_USER, settings.ALERT_RECIPIENT, msg.as_string())
        logger.info(f"Email alert sent to {settings.ALERT_RECIPIENT}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "SMTP authentication failed — check SMTP_USER and SMTP_PASSWORD. "
            "Gmail requires an App Password: myaccount.google.com/apppasswords"
        )
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        return False
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False
