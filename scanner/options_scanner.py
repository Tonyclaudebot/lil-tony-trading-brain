import logging

from config import settings
from data.market_feed import get_options_chain, get_spot_price
from scanner.setups import Setup, find_unusual_volume

logger = logging.getLogger(__name__)


def scan_ticker(ticker: str) -> list[Setup]:
    """Run all setup checks against a single ticker and return any hits."""
    chain = get_options_chain(ticker, max_dte=settings.MAX_DTE, min_dte=settings.MIN_DTE)
    if chain.empty:
        logger.debug(f"No options data for {ticker}")
        return []

    spot = get_spot_price(ticker)
    setups = find_unusual_volume(chain, settings.MIN_OPTION_VOLUME, settings.MIN_VOLUME_TO_OI_RATIO)

    for s in setups:
        s.spot_price = spot

    if setups:
        logger.info(f"{ticker}: {len(setups)} setup(s) found")
    return setups


def scan_watchlist(watchlist: list[str]) -> list[Setup]:
    """Scan every ticker in the watchlist and aggregate results."""
    results: list[Setup] = []
    for ticker in watchlist:
        try:
            results.extend(scan_ticker(ticker))
        except Exception as e:
            logger.error(f"Error scanning {ticker}: {e}")
    return results
