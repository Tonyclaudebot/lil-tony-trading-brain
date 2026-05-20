"""
reconcile_alerts.py — Walk every alert's underlying stock daily bars from
send time to now (or expiration close) and grade WIN / LOSS / expired_worthless
using Black-Scholes stock-proxy repricing (same approach as outcome_tracker.py).

Updates logs/alerts.jsonl in place.
Per house rule T10: full history must remain intact.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ALERTS_LOG = Path(__file__).parent / "logs" / "alerts.jsonl"
PACING_SECONDS = 1  # Webull Open API — no restrictive rate limit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
)
log = logging.getLogger("reconcile")

RISK_FREE_RATE = 0.05


def load_alerts() -> list[dict]:
    if not ALERTS_LOG.exists():
        return []
    return [json.loads(l) for l in ALERTS_LOG.read_text().splitlines() if l.strip()]


def save_alerts(alerts: list[dict]) -> None:
    ALERTS_LOG.write_text("\n".join(json.dumps(a) for a in alerts) + "\n")


# ── Black-Scholes helpers ─────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, r: float, sigma: float, opt_type: str) -> float:
    if T <= 0:
        return max(0.0, (S - K) if opt_type == "call" else (K - S))
    if sigma <= 0:
        return max(0.0, (S - K * math.exp(-r * T)) if opt_type == "call"
                   else (K * math.exp(-r * T) - S))
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if opt_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def implied_vol(S: float, K: float, T: float, r: float, prem: float, opt_type: str) -> float | None:
    if T <= 0 or prem <= 0:
        return None
    lo, hi = 0.005, 5.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        pm = bs_price(S, K, T, r, mid, opt_type)
        if abs(pm - prem) < 1e-5 or (hi - lo) < 1e-5:
            return mid
        if pm < prem:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ── Webull stock bar fetch ─────────────────────────────────────────────────

def fetch_stock_bars(ticker: str, start: date, end: date) -> list[dict]:
    from data.market_feed import _wb_get, _get_category
    try:
        count = (end - start).days + 5
        category = _get_category(ticker)
        data = _wb_get("/market-data/bars", {
            "symbol":   ticker,
            "category": category,
            "timespan": "D",
            "count":    str(min(count, 365)),
        })
        if not data or not isinstance(data, list):
            return []
        start_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
        end_ts   = int(datetime(end.year,   end.month,   end.day,   23, 59, tzinfo=timezone.utc).timestamp() * 1000)
        result = []
        for bar in data:
            ts = datetime.fromisoformat(bar["time"].replace("+0000", "+00:00"))
            ts_ms = int(ts.timestamp() * 1000)
            if start_ts <= ts_ms <= end_ts:
                result.append({
                    "ts_ms":  ts_ms,
                    "date":   ts.date().isoformat(),
                    "open":   float(bar.get("open",  0)),
                    "high":   float(bar.get("high",  0)),
                    "low":    float(bar.get("low",   0)),
                    "close":  float(bar.get("close", 0)),
                })
        return sorted(result, key=lambda b: b["ts_ms"])
    except Exception as e:
        log.warning(f"  Webull fetch failed for {ticker}: {e}")
        return []


# ── Per-alert grading ─────────────────────────────────────────────────────

def reconcile_alert(alert: dict, now_ms: int) -> dict:
    ticker   = alert["ticker"]
    K        = float(alert["strike"])
    target   = float(alert["target"])
    stop     = float(alert["stop"])
    opt_type = alert.get("opt_type", "call")
    expiration = alert.get("expiration", "")
    send_dt  = datetime.fromisoformat(alert["timestamp"])
    spot0    = float(alert.get("spot", 0))
    prem0    = float(alert.get("entry", 0))

    if not expiration or not spot0 or not prem0:
        return {"outcome": None, "outcome_src": "missing_data"}

    exp = date.fromisoformat(expiration)
    exp_close_utc = datetime(exp.year, exp.month, exp.day, 21, 0, tzinfo=timezone.utc)
    T0 = max((exp_close_utc - send_dt).total_seconds() / (365.25 * 86400), 1e-6)

    iv = implied_vol(spot0, K, T0, RISK_FREE_RATE, prem0, opt_type)
    if iv is None:
        return {"outcome": None, "outcome_src": "iv_solve_failed"}

    # Fetch underlying stock bars from send date to today or expiry
    start = send_dt.date()
    end   = min(date.today(), exp)
    bars  = fetch_stock_bars(ticker, start, end)

    last_close = None
    for bar in bars:
        bd = date.fromisoformat(bar["date"])
        session_open = datetime(bd.year, bd.month, bd.day, 13, 30, tzinfo=timezone.utc)
        if send_dt.replace(tzinfo=timezone.utc) >= session_open:
            continue
        if bd > exp:
            break

        bar_close_utc = datetime(bd.year, bd.month, bd.day, 21, 0, tzinfo=timezone.utc)
        T_rem = max((exp_close_utc - bar_close_utc).total_seconds() / (365.25 * 86400), 0)
        high, low = bar["high"], bar["low"]
        last_close = bar["close"]

        if opt_type == "call":
            est_high = bs_price(high, K, T_rem, RISK_FREE_RATE, iv, "call")
            est_low  = bs_price(low,  K, T_rem, RISK_FREE_RATE, iv, "call")
        else:
            est_high = bs_price(low,  K, T_rem, RISK_FREE_RATE, iv, "put")
            est_low  = bs_price(high, K, T_rem, RISK_FREE_RATE, iv, "put")

        if est_high >= target and est_low <= stop:
            return {"outcome": "LOSS", "exit_price": stop, "outcome_at": bar["date"], "outcome_src": "stock_proxy"}
        if est_high >= target:
            return {"outcome": "WIN",  "exit_price": target, "outcome_at": bar["date"], "outcome_src": "stock_proxy"}
        if est_low <= stop:
            return {"outcome": "LOSS", "exit_price": stop,   "outcome_at": bar["date"], "outcome_src": "stock_proxy"}

    if expiration < date.today().isoformat():
        return {"outcome": "expired_worthless", "exit_price": last_close or 0.0, "outcome_at": None, "outcome_src": "expired"}

    return {"outcome": None, "exit_price": last_close, "outcome_at": None, "outcome_src": "still_open"}


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    force = "--all" in sys.argv

    if not os.getenv("WEBULL_APP_KEY"):
        log.error("WEBULL_APP_KEY not in .env — exiting")
        return 2

    alerts = load_alerts()
    log.info(f"Loaded {len(alerts)} alerts from {ALERTS_LOG}")

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    queue_idx = [
        i for i, a in enumerate(alerts)
        if force or a.get("outcome") in (None, "")
    ]
    log.info(f"Reconciling {len(queue_idx)} alert(s)")

    summary = []
    for n, i in enumerate(queue_idx, start=1):
        a = alerts[i]
        log.info(f"[{n}/{len(queue_idx)}] {a['ticker']} {a.get('strategy_name','?')} sent {a['timestamp'][:19]}")
        result = reconcile_alert(a, now_ms)

        a["outcome"]     = result.get("outcome")
        a["exit_price"]  = result.get("exit_price")
        a["outcome_at"]  = result.get("outcome_at")
        a["outcome_src"] = result.get("outcome_src")
        a["graded_at"]   = date.today().isoformat() if a["outcome"] else None

        summary.append({
            "ticker":   a["ticker"],
            "contract": a.get("contract", ""),
            "sent":     a["timestamp"][:19],
            "target":   a.get("target"),
            "stop":     a.get("stop"),
            "outcome":  a["outcome"] or "OPEN",
            "outcome_at": a.get("outcome_at"),
        })

        if n < len(queue_idx):
            time.sleep(PACING_SECONDS)

    if not dry_run:
        save_alerts(alerts)
        log.info(f"Wrote {ALERTS_LOG}")
    else:
        log.info("Dry run — not writing file")

    wins    = sum(1 for s in summary if s["outcome"] == "WIN")
    losses  = sum(1 for s in summary if s["outcome"] == "LOSS")
    expired = sum(1 for s in summary if s["outcome"] == "expired_worthless")
    opens   = sum(1 for s in summary if s["outcome"] == "OPEN")
    log.info(f"WINS: {wins}  LOSSES: {losses}  EXPIRED: {expired}  OPEN: {opens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
