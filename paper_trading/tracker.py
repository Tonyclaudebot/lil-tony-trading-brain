"""
paper_trading/tracker.py — background poller that closes open paper trades.

Every 5 minutes during NYSE hours (8:30 AM-3:00 PM CT = 9:30 AM-4:00 PM ET),
each open paper trade is re-priced with Black-Scholes from the current
underlying price (IV solved once from the entry premium, then cached). Exit
policy is a trailing stop (applies to all strategies; ignores per-trade target/
stop on the record):

  Before peak profit >= 30%: safety floor at -25% on premium -> close 'loss'
  Once peak profit >= 30%:   floor lifts to (peak - 15%); profit falling to
                             that level -> close 'win' at the trail price
  Past expiration:           close 'expired_worthless' at intrinsic

Peak profit % is ratcheted up on every poll and persisted to the trade record.

Runs as a daemon thread via start_tracker() — not a separate process. No broker.

The BS/IV helpers are duplicated from outcome_tracker.py on purpose: importing
that module would run its top-level signal.signal() / logging.basicConfig() /
load_dotenv() side effects, which we don't want inside a worker thread.
"""
from __future__ import annotations

import logging
import math
import threading
from datetime import date, datetime, timezone

import pytz

from data.market_feed import get_spot_price
from paper_trading import engine

logger = logging.getLogger(__name__)

_CENTRAL              = pytz.timezone("America/Chicago")
POLL_INTERVAL_SECONDS = 5 * 60
RISK_FREE_RATE        = 0.05
_MARKET_OPEN_CT       = (8, 30)   # 9:30 AM ET
_MARKET_CLOSE_CT      = (15, 0)   # 4:00 PM ET

_thread: threading.Thread | None = None
_stop_event = threading.Event()


# ── Black-Scholes (self-contained; mirrors outcome_tracker) ────────────────
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_price(S: float, K: float, T: float, r: float, sigma: float, opt_type: str) -> float:
    if T <= 0:
        return max(0.0, (S - K) if opt_type == "call" else (K - S))
    if sigma <= 0:
        return max(0.0, (S - K * math.exp(-r * T)) if opt_type == "call"
                                                    else (K * math.exp(-r * T) - S))
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if opt_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _implied_vol(S: float, K: float, T: float, r: float, premium: float, opt_type: str) -> float | None:
    """Bisection IV solver. None if premium is outside the BS range for these inputs."""
    if T <= 0 or premium <= 0:
        return None
    lo, hi = 0.005, 5.0
    if premium < _bs_price(S, K, T, r, lo, opt_type) or premium > _bs_price(S, K, T, r, hi, opt_type):
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        pm = _bs_price(S, K, T, r, mid, opt_type)
        if abs(pm - premium) < 1e-5 or (hi - lo) < 1e-5:
            return mid
        if pm < premium:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _years_to_expiry(expiration: str, ref: datetime) -> float:
    """Fractional years from `ref` to the 4 PM ET (21:00 UTC) close on expiry day."""
    exp = date.fromisoformat(expiration)
    exp_close_utc = datetime(exp.year, exp.month, exp.day, 21, 0, tzinfo=timezone.utc)
    return (exp_close_utc - ref).total_seconds() / (365.25 * 86400)


def _record_outcome(closed: dict | None) -> None:
    """No-op. The learner is now trained from a single source of truth — the
    alert grader in outcome_tracker.py. Feeding it here too would double-count
    the same signal (it is graded by both paths). Kept for call-site stability.
    """
    return


def market_is_open(now_ct: datetime | None = None) -> bool:
    now_ct = now_ct or datetime.now(_CENTRAL)
    if now_ct.weekday() >= 5:   # Saturday / Sunday
        return False
    hm = (now_ct.hour, now_ct.minute)
    return _MARKET_OPEN_CT <= hm <= _MARKET_CLOSE_CT


def _evaluate_trade(t: dict, now_utc: datetime) -> None:
    """Reprice one open trade; close it if it hit target, stop, or expiration."""
    ticker   = t["ticker"]
    K        = t["strike"]
    opt_type = t["opt_type"]
    entry    = t["entry"]

    if not t.get("expiration"):
        logger.warning(f"  {ticker} {t.get('contract')}: no expiration — cannot track")
        return

    spot = get_spot_price(ticker)
    if spot is None:
        logger.warning(f"  {ticker}: no spot price this poll — skipping")
        return

    T_rem = _years_to_expiry(t["expiration"], now_utc)
    if T_rem <= 0:
        intrinsic = max(0.0, (spot - K) if opt_type == "call" else (K - spot))
        _record_outcome(
            engine.close_trade(ticker, round(intrinsic, 4), "expired_worthless", contract_id=t["id"])
        )
        return

    # Solve IV once from the entry premium, then cache it on the record.
    iv = t.get("implied_vol")
    if iv is None:
        spot0 = t.get("spot_at_open") or spot
        try:
            open_ref = datetime.fromisoformat(t["open_time"])
        except Exception:
            open_ref = now_utc
        T0 = max(_years_to_expiry(t["expiration"], open_ref), 1e-6)
        iv = _implied_vol(spot0, K, T0, RISK_FREE_RATE, entry, opt_type)
        if iv is None:
            logger.warning(
                f"  {ticker} {t['contract']}: IV solve failed "
                f"(entry ${entry} outside BS range) — cannot track"
            )
            return
        engine.set_implied_vol(t["id"], iv)

    est        = _bs_price(spot, K, max(T_rem, 1e-6), RISK_FREE_RATE, iv, opt_type)
    profit_pct = ((est - entry) / entry) * 100.0

    # Peak-profit ratchet (only moves up). Persist whenever it advances.
    prev_peak = t.get("peak_profit_pct", 0.0)
    peak      = max(prev_peak, profit_pct)
    if peak > prev_peak:
        engine.set_peak_profit_pct(t["id"], peak)

    # Trail engages once peak >= 30%; the static target/stop on the record are ignored.
    # Before trail: -25% on premium is the safety floor.
    # After trail: exit when current profit falls to (peak - 15%).
    if peak >= 30.0:
        trail_pct = peak - 15.0
        if profit_pct <= trail_pct:
            exit_px = round(entry * (1 + trail_pct / 100.0), 4)
            _record_outcome(engine.close_trade(ticker, exit_px, "win", contract_id=t["id"]))
    elif profit_pct <= -25.0:
        exit_px = round(entry * 0.75, 4)
        _record_outcome(engine.close_trade(ticker, exit_px, "loss", contract_id=t["id"]))
    # otherwise: still open, leave it


def poll_once() -> int:
    """One sweep over all open paper trades. Returns the number closed."""
    open_trades = engine.get_open_trades()
    if not open_trades:
        logger.debug("  no open paper trades")
        return 0
    now_utc = datetime.now(timezone.utc)
    for t in open_trades:
        try:
            _evaluate_trade(t, now_utc)
        except Exception as e:
            logger.warning(f"  evaluate failed for {t.get('ticker')}: {e}")
    closed = len(open_trades) - len(engine.get_open_trades())
    logger.info(f"  paper-trade poll: {len(open_trades)} open, {closed} closed this cycle")
    return closed


def _loop() -> None:
    logger.info("paper-trade tracker started (5-min poll, 8:30 AM-3:00 PM CT)")
    while not _stop_event.is_set():
        try:
            if market_is_open():
                poll_once()
        except Exception as e:
            logger.exception(f"paper-trade poll cycle failed: {e}")
        _stop_event.wait(POLL_INTERVAL_SECONDS)
    logger.info("paper-trade tracker stopped")


def start_tracker() -> threading.Thread:
    """Start the polling loop in a daemon thread. Idempotent."""
    global _thread
    if _thread is not None and _thread.is_alive():
        logger.info("paper-trade tracker already running")
        return _thread
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="paper-trade-tracker", daemon=True)
    _thread.start()
    return _thread


def stop_tracker() -> None:
    _stop_event.set()
