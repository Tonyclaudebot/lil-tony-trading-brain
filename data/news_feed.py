"""
News feed — Webull unofficial app API only.
"""

import logging
import requests

logger = logging.getLogger(__name__)

_news_cache: dict[str, tuple[float, list[str]]] = {}
_CACHE_TTL = 300  # seconds


def _wb_headers() -> dict:
    from webull import webull
    return webull().build_req_headers()


def _wb_ticker_id(ticker: str) -> int | None:
    try:
        from webull import webull
        return int(webull().get_ticker(stock=ticker))
    except Exception:
        return None


def get_stock_news(ticker: str, max_items: int = 3) -> list[str]:
    """Return recent news headlines for a ticker from Webull."""
    import time
    cached = _news_cache.get(ticker)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1][:max_items]

    try:
        from webull import webull
        wb = webull()
        ticker_id = wb.get_ticker(stock=ticker)
        url = wb._urls.news(ticker_id, 0, max_items)
        r = requests.get(url, headers=wb.build_req_headers(), timeout=10)
        items = r.json() if r.status_code == 200 else []
        headlines = [item.get("title", "") for item in items if item.get("title")]
        _news_cache[ticker] = (time.time(), headlines)
        return headlines[:max_items]
    except Exception as e:
        logger.debug(f"Webull news failed for {ticker}: {e}")
        return []


def get_ticker_headlines(ticker: str) -> list[str]:
    """Return headlines that mention this ticker (from Webull per-ticker news)."""
    return get_stock_news(ticker, max_items=5)


# Backward-compat stub — Phase 2 imported this; now returns empty
def get_financial_juice_headlines(max_items: int = 100) -> list[str]:
    return []
