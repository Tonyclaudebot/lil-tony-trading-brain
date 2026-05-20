"""
Dynamic watchlist: every morning before market open, pull the top movers
and most active options stocks from Yahoo Finance screeners and add them
to the day's scan list on top of the fixed .env WATCHLIST.
"""

import json
import logging
import ssl
import urllib.request

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

logger = logging.getLogger(__name__)

_YF_SCREENER = (
    "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LilTony/1.0)"}

# Pull from most active, biggest gainers, and biggest losers
_SCREENER_IDS = ["most_actives", "day_gainers", "day_losers"]


def fetch_dynamic_tickers(n: int = 20) -> list[str]:
    """
    Fetch the top n most active / most moved tickers from Yahoo Finance.
    Combines most_actives + day_gainers + day_losers in that priority order,
    deduplicates, and returns up to n symbols.
    Falls back to an empty list if all screeners fail.
    """
    seen: set[str] = set()
    tickers: list[str] = []

    for scr_id in _SCREENER_IDS:
        for symbol in _fetch_screener(scr_id, count=25):
            if symbol not in seen:
                seen.add(symbol)
                tickers.append(symbol)

    result = tickers[:n]
    if result:
        logger.info(f"Dynamic watchlist ({len(result)}): {', '.join(result)}")
    else:
        logger.warning("Dynamic watchlist fetch returned no tickers — using fixed list only")
    return result


def _fetch_screener(scr_id: str, count: int = 25) -> list[str]:
    """Fetch a Yahoo Finance predefined screener and return ticker symbols."""
    url = f"{_YF_SCREENER}?scrIds={scr_id}&count={count}&region=US&lang=en-US"
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
        quotes = (
            data.get("finance", {})
            .get("result", [{}])[0]
            .get("quotes", [])
        )
        symbols = [q["symbol"] for q in quotes if q.get("symbol")]
        logger.debug(f"Screener '{scr_id}': {len(symbols)} tickers")
        return symbols
    except Exception as e:
        logger.warning(f"Yahoo Finance screener '{scr_id}' failed: {e}")
        return []


def build_scan_universe(
    base: list[str],
    watchlist: list[str],
    dynamic: list[str],
) -> list[str]:
    """
    Merge base universe + fixed watchlist + dynamic tickers.
    Order: watchlist first (highest priority), then dynamic, then base.
    Deduplicates while preserving insertion order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for ticker in watchlist + dynamic + base:
        t = ticker.strip().upper()
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result
