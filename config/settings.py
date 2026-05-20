import os
from dotenv import load_dotenv

load_dotenv()

# Alert recipient (email address)
ALERT_RECIPIENT: str = os.environ["ALERT_RECIPIENT"]

# SMTP / email
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAIL_SUBJECT: str = os.getenv("ALERT_EMAIL_SUBJECT", "Lil Tony Alert")

# iMessage recipient (phone number with country code, e.g. "+12145551234", or Apple ID)
IMESSAGE_RECIPIENT: str = os.getenv("IMESSAGE_RECIPIENT", "")

# Watchlist
WATCHLIST: list[str] = os.getenv("WATCHLIST", "SPY,QQQ,AAPL,TSLA,NVDA").split(",")

# Scan cadence
SCAN_INTERVAL_SECONDS: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))

# Options filters
MIN_OPTION_VOLUME: int = int(os.getenv("MIN_OPTION_VOLUME", "500"))
MIN_VOLUME_TO_OI_RATIO: float = float(os.getenv("MIN_VOLUME_TO_OI_RATIO", "2.0"))
MAX_DTE: int = int(os.getenv("MAX_DTE", "45"))
MIN_DTE: int = int(os.getenv("MIN_DTE", "1"))

# Earnings intelligence
EARNINGS_LOOKBACK: int = int(os.getenv("EARNINGS_LOOKBACK", "8"))

# Macro calendar
MACRO_WARNING_DAYS: int = int(os.getenv("MACRO_WARNING_DAYS", "14"))
MACRO_REFRESH_SECONDS: int = int(os.getenv("MACRO_REFRESH_SECONDS", "3600"))

# News feed (MarketWatch RSS by default; override with NEWS_FEED_URL in .env)
FINANCIAL_JUICE_FEED_URL: str = os.getenv("FINANCIAL_JUICE_FEED_URL", "")
BENZINGA_FEED_URL: str = os.getenv(
    "NEWS_FEED_URL",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
)

# Logging
LOG_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
