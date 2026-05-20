import logging

from scanner.strategies.base import CandidateStock, TradePlan
from scanner.orb import get_orb_status
from data.market_feed import get_options_chain
from config import settings

logger = logging.getLogger(__name__)

NAME = "Momentum Breakout"
KEY = "momentum_breakout"

_MIN_RET_1D = 0.015   # 1.5% 1-day move
_MIN_RET_5D = 0.03    # 3% 5-day move
_MIN_VOL_RATIO = 1.8  # volume at least 1.8x average

_TARGET_MULT = 2.5    # option premium target multiplier (stop: 0.5×)
_STOP_MULT = 0.5


def score(candidate: CandidateStock) -> float:
    """Rate how well this candidate fits a momentum breakout setup (0–100)."""
    s = 0.0
    if candidate.ret_1d >= _MIN_RET_1D:
        s += 35 + min(30, (candidate.ret_1d - _MIN_RET_1D) * 2000)
    if candidate.ret_5d >= _MIN_RET_5D:
        s += 25 + min(20, (candidate.ret_5d - _MIN_RET_5D) * 500)
    if candidate.volume_ratio >= _MIN_VOL_RATIO:
        s += min(15, (candidate.volume_ratio - _MIN_VOL_RATIO) * 15)
    # RSI sweet spot: 55-70 (trending but not exhausted)
    if 55 <= candidate.rsi <= 70:
        s += 10
    elif 45 <= candidate.rsi < 55:
        s += 5
    return round(min(100.0, s), 2)


def build_plan(candidate: CandidateStock, combined_score: float) -> TradePlan | None:
    """
    Build a Momentum Breakout trade plan with ORB confirmation.

    Direction (call/put) is determined by the ORB breakout: only fires when
    price has closed above OR High (call) or below OR Low (put) with confirming
    volume after the 9:30–10:30 AM CT opening range window.
    Returns None if ORB not confirmed or no qualifying contract found.
    """
    orb = get_orb_status(candidate.ticker, candidate.spot, candidate.volume_ratio)

    if orb["confirmed"]:
        opt_type   = orb["direction"]
        or_high    = orb["or_high"]
        or_low     = orb["or_low"]
        t1, t2, t3 = orb["t1"], orb["t2"], orb["t3"]
        target_hit = orb.get("_target_hit")
    elif orb.get("reason", "").startswith("opening range data unavailable"):
        # Polygon free tier doesn't serve minute bars — fall back to momentum direction
        logger.debug(f"  {candidate.ticker} ORB unavailable — falling back to momentum direction")
        opt_type   = "call" if candidate.ret_1d > 0 else "put"
        or_high    = None
        or_low     = None
        t1 = t2 = t3 = target_hit = None
    else:
        logger.debug(f"  {candidate.ticker} ORB not confirmed: {orb['reason']}")
        return None

    chain = get_options_chain(candidate.ticker, max_dte=settings.MAX_DTE, min_dte=settings.MIN_DTE)
    if chain.empty:
        return None

    side = chain[chain["type"] == opt_type].copy()
    if side.empty:
        return None

    # Near-ATM window: ±10% of spot (slightly wider for puts)
    atm_candidates = side[
        (side["strike"] >= candidate.spot * 0.90)
        & (side["strike"] <= candidate.spot * 1.10)
        & (side["volume"] > 0)
    ]
    if atm_candidates.empty:
        return None

    # Prefer $0.20–$1.00 premium; fall back up to $3.00 ceiling
    preferred = atm_candidates[
        (atm_candidates["lastPrice"] >= 0.20)
        & (atm_candidates["lastPrice"] <= 1.00)
    ]
    fallback  = atm_candidates[atm_candidates["lastPrice"] <= 3.00]
    window    = preferred if not preferred.empty else fallback
    if window.empty:
        return None

    row    = window.loc[window["volume"].idxmax()]
    entry  = float(row["lastPrice"])
    target = round(entry * _TARGET_MULT, 2)
    stop   = round(entry * _STOP_MULT, 2)
    target_pct = round((_TARGET_MULT - 1) * 100, 1)
    confidence = "HIGH" if combined_score >= 75 else "MEDIUM"

    plan = TradePlan(
        ticker=candidate.ticker,
        contract=str(row["contractSymbol"]),
        opt_type=opt_type,
        strike=float(row["strike"]),
        expiration=str(row["expiration"]),
        dte=int(row["dte"]),
        strategy_key=KEY,
        strategy_name=NAME,
        spot=candidate.spot,
        entry=entry,
        target=target,
        stop=stop,
        target_pct=target_pct,
        iv=round(float(row["impliedVolatility"]) * 100, 1),
        volume=int(row["volume"]),
        open_interest=int(row["openInterest"]),
        score=combined_score,
        confidence=confidence,
        # ORB fields
        or_high=or_high,
        or_low=or_low,
        or_t1=t1,
        or_t2=t2,
        or_t3=t3,
        or_target_hit=target_hit,
    )
    if or_high is not None:
        logger.info(
            f"  {candidate.ticker} ORB {opt_type.upper()} confirmed | "
            f"OR {or_low:.2f}–{or_high:.2f} | targets {t1}/{t2}/{t3}"
        )
    else:
        logger.info(f"  {candidate.ticker} momentum {opt_type.upper()} (no ORB data)")
    return plan
