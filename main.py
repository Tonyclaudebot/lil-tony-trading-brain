"""
DEPRECATED — superseded by daytime_runner.py + runner.py (phase pipeline).
Kept for reference only. Do not run directly.
"""
import dataclasses
import logging
import time
from datetime import date, datetime, time as dtime

import pytz

from brain import grader, learner
from brain.macro_filter import load_macro_calendar
from config import settings
from data.watchlist import fetch_dynamic_tickers
from scanner.ranker import scan_universe, select_top_candidates
from scanner.strategies.selector import pick_best_plan
from send_alert import send_alert

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(f"{settings.LOG_DIR}/lil-tony.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")

_EASTERN = pytz.timezone("America/New_York")
_MARKET_OPEN = dtime(9, 30)
_MARKET_CLOSE = dtime(16, 0)
_DYNAMIC_REFRESH_TIME = dtime(9, 0)   # 9:00 AM ET — before market open
_GRADE_EVERY = 5


def _is_market_hours() -> bool:
    now = datetime.now(_EASTERN).time()
    return _MARKET_OPEN <= now <= _MARKET_CLOSE


def _should_refresh_dynamic(last_refresh_date: date | None) -> bool:
    """True once per day at or after 9:00 AM ET."""
    now = datetime.now(_EASTERN)
    return (
        last_refresh_date != now.date()
        and now.time() >= _DYNAMIC_REFRESH_TIME
    )


def run_scan_cycle(
    seen: set[str],
    dynamic_tickers: list[str],
    macro_events: list[dict],
) -> list[str]:
    """Full scan → rank → strategy → enrich → alert cycle."""
    weights = learner.get_weights()
    logger.info(f"Strategy weights: {weights}")

    candidates = scan_universe(dynamic_tickers=dynamic_tickers)
    if not candidates:
        logger.warning("No candidates from universe scan")
        return []

    top3 = select_top_candidates(candidates)
    newly_alerted: list[str] = []

    for candidate in top3:
        plan = pick_best_plan(candidate, weights, macro_events=macro_events)
        if plan is None:
            continue
        if plan.contract in seen:
            logger.debug(f"Duplicate skipped: {plan.contract}")
            continue

        seen.add(plan.contract)
        newly_alerted.append(plan.contract)

        exp_short     = plan.expiration[5:].replace("-", "/") if plan.expiration else "?"
        opt_label     = "C" if plan.opt_type == "call" else "P"
        contract_type = "CALL" if plan.opt_type == "call" else "PUT"
        alert_data = {
            "ticker":        plan.ticker,
            "strategy":      plan.strategy_name,
            "signal":        "BUY",
            "contract_type": contract_type,
            "contract":      f"${int(plan.strike)}{opt_label}",
            "expiry":        f"exp {exp_short}",
            "entry":         f"${plan.entry:.2f}",
            "target":        f"${plan.target:.2f}",
            "pct_gain":      f"+{int(plan.target_pct)}%" if plan.target_pct else "",
            "stop":          f"${plan.stop:.2f}",
            "confidence":    plan.confidence,
            "broker_link":   f"https://robinhood.com/options/{plan.ticker}",
        }
        logger.info(
            f"Alert: {plan.ticker} | {plan.strategy_name} | "
            f"{alert_data['contract']} | entry {alert_data['entry']}"
        )
        send_alert(alert_data)
        grader.log_alert(dataclasses.asdict(plan))
        learner.record_open(plan.strategy_key)

    return newly_alerted


def main() -> None:
    logger.info("Lil Tony Trading Brain started")

    macro_events: list[dict] = load_macro_calendar(settings.MACRO_WARNING_DAYS)
    macro_last_loaded = time.time()

    dynamic_tickers: list[str] = []
    dynamic_last_date: date | None = None

    # Fetch dynamic list immediately if we're already at or past 9:00 AM ET
    if _should_refresh_dynamic(dynamic_last_date):
        dynamic_tickers = fetch_dynamic_tickers(n=20)
        dynamic_last_date = datetime.now(_EASTERN).date()
        logger.info(f"Dynamic watchlist loaded at startup: {len(dynamic_tickers)} tickers")

    seen: set[str] = set()
    cycle = 0

    while True:
        # Refresh dynamic watchlist at 9:00 AM ET each morning
        if _should_refresh_dynamic(dynamic_last_date):
            logger.info("9:00 AM ET — refreshing dynamic watchlist")
            dynamic_tickers = fetch_dynamic_tickers(n=20)
            dynamic_last_date = datetime.now(_EASTERN).date()
            seen.clear()   # Reset dedup set for the new trading day
            logger.info(f"Dynamic watchlist: {dynamic_tickers}")

        if not _is_market_hours():
            logger.debug("Outside market hours — sleeping 60s")
            time.sleep(60)
            continue

        # Refresh macro calendar hourly
        if time.time() - macro_last_loaded >= settings.MACRO_REFRESH_SECONDS:
            macro_events = load_macro_calendar(settings.MACRO_WARNING_DAYS)
            macro_last_loaded = time.time()

        cycle += 1
        logger.info(f"── Scan cycle {cycle} ──")

        try:
            alerted = run_scan_cycle(seen, dynamic_tickers, macro_events)
            if alerted:
                logger.info(f"Alerted: {alerted}")
            else:
                logger.info("No new setups this cycle")
        except Exception as e:
            logger.error(f"Scan cycle error: {e}", exc_info=True)

        # Grading handled by outcome_tracker.py daemon — nothing to do here

        time.sleep(settings.SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
