"""
Earnings data — Webull unofficial app API.
"""
import logging
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)


def _wb_fundamentals(ticker: str) -> dict:
    try:
        from webull import webull
        import requests
        wb = webull()
        ticker_id = wb.get_ticker(stock=ticker)
        url = wb._urls.fundamentals(ticker_id)
        r = requests.get(url, headers=wb.build_req_headers(), timeout=10)
        if r.status_code == 200:
            items = r.json()
            if isinstance(items, list) and items:
                return items[0]
    except Exception as e:
        logger.debug(f"Webull fundamentals failed for {ticker}: {e}")
    return {}


def get_next_earnings_date(ticker: str) -> date | None:
    """Return the next upcoming earnings date via Webull fundamentals."""
    try:
        fund = _wb_fundamentals(ticker)
        # Webull returns latestEarningsDate and nextEarningsDate
        ned = fund.get("nextEarningsDate") or fund.get("latestDividendDate")
        if ned:
            d = date.fromisoformat(ned[:10])
            if d >= date.today():
                return d
    except Exception as e:
        logger.debug(f"Webull earnings date for {ticker}: {e}")
    return None


def get_earnings_estimates(ticker: str) -> dict:
    """Return consensus EPS estimates (empty — not available without premium data)."""
    return {}


def get_earnings_history(ticker: str, lookback: int = 8) -> list[dict]:
    """Return recent earnings history from Webull fundamentals."""
    try:
        fund = _wb_fundamentals(ticker)
        raw = fund.get("fiscalYearEps") or fund.get("eps") or []
        if not raw:
            return []
        results = []
        for entry in raw[:lookback]:
            try:
                results.append({
                    "date":         str(entry.get("date") or entry.get("reportDate") or ""),
                    "eps_estimate": None,
                    "eps_reported": float(entry.get("eps") or entry.get("epsActual") or 0),
                    "surprise_pct": None,
                    "beat":         None,
                    "next_day_move": None,
                })
            except Exception:
                continue
        return results
    except Exception as e:
        logger.debug(f"Webull earnings history for {ticker}: {e}")
        return []
