"""
DEPRECATED — use `python runner.py 2 3 4` instead.
This runs the old single-phase monolith; kept for reference only.
"""
import dataclasses
import logging

from brain import grader, learner
from brain.macro_filter import load_macro_calendar
from config import settings
from data.watchlist import fetch_dynamic_tickers
from scanner.ranker import scan_universe, select_top_candidates
from scanner.strategies.selector import pick_best_plan
from send_alert import send_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(f"{settings.LOG_DIR}/lil-tony.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("force_scan")


def _plan_to_alert_data(plan) -> dict:
    exp_short     = plan.expiration[5:].replace("-", "/") if plan.expiration else "?"
    opt_label     = "C" if plan.opt_type == "call" else "P"
    contract_type = "CALL" if plan.opt_type == "call" else "PUT"
    contract_str  = f"${int(plan.strike)}{opt_label}"
    pct           = f"+{int(plan.target_pct)}%" if plan.target_pct else ""
    return {
        "ticker":        plan.ticker,
        "strategy":      plan.strategy_name,
        "signal":        "BUY",
        "contract_type": contract_type,
        "contract":      contract_str,
        "expiry":        f"exp {exp_short}",
        "entry":         f"${plan.entry:.2f}",
        "target":        f"${plan.target:.2f}",
        "pct_gain":      pct,
        "stop":          f"${plan.stop:.2f}",
        "confidence":    plan.confidence,
        "broker_link":   f"https://robinhood.com/options/{plan.ticker}",
    }


logger.info("=== FORCE SCAN (market-hours bypass) ===")

macro_events = load_macro_calendar(settings.MACRO_WARNING_DAYS)
logger.info(f"Macro events loaded: {len(macro_events)}")

logger.info("Fetching dynamic watchlist (top movers)...")
dynamic_tickers = fetch_dynamic_tickers(n=20)
logger.info(f"Dynamic tickers: {dynamic_tickers}")

weights = learner.get_weights()
logger.info(f"Strategy weights: {weights}")

logger.info("Scanning universe...")
candidates = scan_universe(dynamic_tickers=dynamic_tickers)

if not candidates:
    logger.warning("No candidates found — market may be closed, data may be stale")
else:
    logger.info(f"Found {len(candidates)} candidates")
    top3 = select_top_candidates(candidates)
    logger.info(f"Top 3: {[c.ticker for c in top3]}")

    seen: set[str] = set()
    alerted = []

    for candidate in top3:
        plan = pick_best_plan(candidate, weights, macro_events=macro_events)
        if plan is None:
            logger.info(f"{candidate.ticker} — no valid plan built")
            continue
        if plan.contract in seen:
            continue
        seen.add(plan.contract)
        alerted.append(plan.contract)

        alert_data = _plan_to_alert_data(plan)
        logger.info(
            f"Alert: {alert_data['ticker']} | {alert_data['strategy']} | "
            f"{alert_data['contract']} | entry {alert_data['entry']} "
            f"target {alert_data['target']} {alert_data['pct_gain']}"
        )

        send_alert(alert_data)
        grader.log_alert(dataclasses.asdict(plan))
        learner.record_open(plan.strategy_key)

    if alerted:
        logger.info(f"Alerts sent: {alerted}")
    else:
        logger.info("No contracts met all filters — try again at market open")
