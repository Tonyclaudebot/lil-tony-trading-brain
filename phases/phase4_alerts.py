"""
Phase 4 — Final deep read → alerts.
Loads phase3_top10.json, runs full pick_best_plan (full options chain, earnings
intel, macro context, news) on each candidate, fires alerts for the top 3 plans.
"""
import dataclasses
import json
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from pathlib import Path

from brain import grader, learner
from brain.macro_filter import load_macro_calendar
from config import settings
from scanner.scan_writer import set_phase4_alerts
from scanner.strategies.base import CandidateStock
from scanner.strategies.selector import pick_best_plan
from send_alert import send_alert
from alerts.imessage import send_imessage, format_alert_dict
from paper_trading import engine as paper_engine

TOP10_PATH  = Path(__file__).parent.parent / "phase3_top10.json"
SEEN_PATH   = Path(__file__).parent.parent / "phase4_seen.json"

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(f"{settings.LOG_DIR}/lil-tony.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("phase4")

_MAX_ALERTS = 3


def _load_seen() -> set[str]:
    if SEEN_PATH.exists():
        try:
            data = json.loads(SEEN_PATH.read_text())
            date_str = data.get("date", "")
            today = datetime.now().strftime("%Y-%m-%d")
            if date_str == today:
                return set(data.get("contracts", []))
        except Exception:
            pass
    return set()


def _save_seen(seen: set[str]) -> None:
    SEEN_PATH.write_text(json.dumps({
        "date":      datetime.now().strftime("%Y-%m-%d"),
        "contracts": list(seen),
    }, indent=2))


def _to_candidate(pick: dict) -> CandidateStock:
    c = CandidateStock(
        ticker=pick["ticker"],
        spot=pick.get("spot", 0.0),
        ret_1d=pick.get("ret_1d", 0.0),
        ret_5d=pick.get("ret_5d", 0.0),
        rsi=pick.get("rsi", 50.0),
        volume_ratio=pick.get("volume_ratio", 1.0),
        ma20=pick.get("ma20", 0.0),
        ma20_pct=pick.get("ma20_pct", 0.0),
        momentum_score=pick.get("momentum_score", 0.0),
    )
    c.uoa_score       = pick.get("uoa_score", 0.0)
    c.composite_score = pick.get("composite_score", 0.0)
    return c


def _plan_to_alert(plan, score: float = 0.0) -> dict:
    exp_short     = plan.expiration[5:].replace("-", "/") if plan.expiration else "?"
    opt_label     = "C" if plan.opt_type == "call" else "P"
    contract_type = "CALL" if plan.opt_type == "call" else "PUT"
    alert = {
        "ticker":        plan.ticker,
        "contract_type": contract_type,
        "strike":        f"${plan.strike:.2f}",
        "expiry":        exp_short,
        "entry":         f"${plan.entry:.2f}",
        "stop":          f"${plan.stop:.2f}",
        "target":        f"${plan.target:.2f}",
        "score":         round(score),
        "contract":      f"${int(plan.strike)}{opt_label}",
    }
    if plan.or_high is not None:
        alert["or_high"]       = plan.or_high
        alert["or_low"]        = plan.or_low
        alert["or_t1"]         = plan.or_t1
        alert["or_t2"]         = plan.or_t2
        alert["or_t3"]         = plan.or_t3
        alert["or_target_hit"] = plan.or_target_hit
    if plan.risk_score:
        alert["risk_iv_rank"]       = plan.risk_iv_rank
        alert["risk_iv_label"]      = plan.risk_iv_label
        alert["risk_premium_label"] = plan.risk_premium_label
        alert["risk_score"]         = plan.risk_score
        alert["risk_summary"]       = plan.risk_summary
    return alert


def run() -> list[str]:
    if not TOP10_PATH.exists():
        logger.error("phase3_top10.json not found — run Phase 3 first")
        return []

    data      = json.loads(TOP10_PATH.read_text())
    picks     = data["picks"]
    weights   = learner.get_weights()
    macro     = load_macro_calendar(settings.MACRO_WARNING_DAYS)
    seen            = _load_seen()
    alerted: list[str] = []
    fired_records: list[dict] = []

    logger.info(f"Phase 4: final deep read on {len(picks)} candidates → firing top {_MAX_ALERTS} alerts")

    for pick in picks:
        if len(alerted) >= _MAX_ALERTS:
            break

        ticker    = pick["ticker"]
        candidate = _to_candidate(pick)

        logger.info(f"  Evaluating {ticker} (composite={candidate.composite_score:.1f})")
        plan = pick_best_plan(candidate, weights, macro_events=macro)

        if plan is None:
            logger.info(f"  {ticker} — no valid plan")
            continue
        if plan.contract in seen:
            logger.debug(f"  {ticker} — duplicate {plan.contract}, skipping")
            continue

        seen.add(plan.contract)
        alerted.append(plan.contract)

        alert_data = _plan_to_alert(plan, score=candidate.composite_score)
        logger.info(
            f"  ALERT: {ticker} | {plan.strategy_name} | "
            f"{alert_data['contract']} | entry {alert_data['entry']} "
            f"target {alert_data['target']} score={alert_data['score']}"
        )
        send_alert(alert_data)

        recipients = [r.strip() for r in os.getenv("IMESSAGE_RECIPIENT", "").split(",") if r.strip()]
        if recipients:
            try:
                imsg_data = {
                    "ticker":     plan.ticker,
                    "opt_type":   plan.opt_type,
                    "strike":     plan.strike,
                    "expiration": plan.expiration,
                    "entry":      plan.entry,
                    "stop":       plan.stop,
                    "target":     plan.target,
                    "score":      round(candidate.composite_score),
                }
                if plan.or_high is not None:
                    imsg_data.update({
                        "or_high":       plan.or_high,
                        "or_low":        plan.or_low,
                        "or_t1":         plan.or_t1,
                        "or_t2":         plan.or_t2,
                        "or_t3":         plan.or_t3,
                        "or_target_hit": plan.or_target_hit,
                    })
                if plan.risk_score:
                    imsg_data.update({
                        "risk_iv_rank":       plan.risk_iv_rank,
                        "risk_iv_label":      plan.risk_iv_label,
                        "risk_premium_label": plan.risk_premium_label,
                        "risk_score":         plan.risk_score,
                        "risk_summary":       plan.risk_summary,
                    })
                imsg = format_alert_dict(imsg_data)
                for recipient in recipients:
                    ok = send_imessage(recipient, imsg)
                    logger.info(f"  iMessage {'sent' if ok else 'FAILED'} → {ticker} → {recipient}")
            except Exception as e:
                logger.warning(f"  iMessage error for {ticker}: {e}")

        # Paper trade: open a simulated position from the plan. Wrapped so a
        # paper-trade failure can never block or kill a real alert (T1/T5).
        try:
            paper_engine.open_trade({
                "ticker":        plan.ticker,
                "opt_type":      plan.opt_type,
                "strike":        plan.strike,
                "expiration":    plan.expiration,
                "entry":         plan.entry,
                "stop":          plan.stop,
                "target":        plan.target,
                "spot":          plan.spot,
                "strategy":      plan.strategy_key,
                "strategy_name": plan.strategy_name,
                "contract":      plan.contract,
            })
        except Exception as e:
            logger.warning(f"  paper trade open failed for {ticker}: {e}")

        fired_records.append(alert_data)
        grader.log_alert(dataclasses.asdict(plan))
        learner.record_open(plan.strategy_key)

    _save_seen(seen)
    set_phase4_alerts(fired_records)

    if alerted:
        logger.info(f"Phase 4 complete: {len(alerted)} alert(s) sent — {alerted}")
    else:
        logger.info("Phase 4 complete: no contracts met all filters")

    return alerted


if __name__ == "__main__":
    run()
