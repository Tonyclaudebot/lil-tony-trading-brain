import logging

from scanner.strategies.base import CandidateStock, TradePlan
from data.market_feed import get_options_chain
from config import settings

logger = logging.getLogger(__name__)

NAME = "Mean Reversion"
KEY = "mean_reversion"

# RSI thresholds
_RSI_OVERSOLD = 35    # → buy call (bounce play)
_RSI_OVERBOUGHT = 65  # → buy put (pullback play)
_MA_DEVIATION = 0.04  # price must be >4% from 20-day MA

_TARGET_MULT = 1.35
_STOP_MULT = 0.5

# Re-enabled 2026-05-27 at Big Tony's direction with a 60 quality gate (mirrors
# Momentum). Note: the 2026-05-21 backtest found NO out-of-sample edge for this
# strategy (original RSI bands, RSI<=25/>=75, and MA-stretch>=6% variants all
# failed test-set validation — see scripts/backtest_sweep.py). Re-enabled despite
# that, so watch its graded win rate closely. Raise back toward 999 to re-bench.
MIN_SCORE = 60.0


def score(candidate: CandidateStock) -> float:
    """Rate how well this candidate fits a mean reversion setup (0–100)."""
    score = 0.0
    ma_deviation = abs(candidate.ma20_pct)

    if candidate.rsi <= _RSI_OVERSOLD:
        score += 50 + min(30, (_RSI_OVERSOLD - candidate.rsi) * 2)
    elif candidate.rsi >= _RSI_OVERBOUGHT:
        score += 50 + min(30, (candidate.rsi - _RSI_OVERBOUGHT) * 2)
    else:
        return 0.0  # Not a reversion candidate

    if ma_deviation >= _MA_DEVIATION:
        score += min(20, ma_deviation * 200)

    return round(min(100.0, score), 2)


def _direction(candidate: CandidateStock) -> str:
    """Oversold → call (bounce). Overbought → put (fade)."""
    return "call" if candidate.rsi <= _RSI_OVERSOLD else "put"


def build_plan(candidate: CandidateStock, combined_score: float) -> TradePlan | None:
    """
    Build a Mean Reversion trade plan.
    Oversold (RSI < 35) → ATM call. Overbought (RSI > 65) → ATM put.
    Returns None if no qualifying contract is found.
    """
    opt_type = _direction(candidate)

    chain = get_options_chain(candidate.ticker, max_dte=settings.MAX_DTE, min_dte=settings.MIN_DTE)
    if chain.empty:
        return None

    side = chain[chain["type"] == opt_type].copy()
    if side.empty:
        return None

    # ATM-ish window: within ±5% of spot
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
    fallback = atm_candidates[atm_candidates["lastPrice"] <= 3.00]
    window = preferred if not preferred.empty else fallback
    if window.empty:
        return None

    row = window.loc[window["volume"].idxmax()]
    entry = float(row["lastPrice"])
    target = round(entry * _TARGET_MULT, 2)
    stop = round(entry * _STOP_MULT, 2)
    target_pct = round((_TARGET_MULT - 1) * 100, 1)
    confidence = "HIGH" if combined_score >= 75 else "MEDIUM"

    return TradePlan(
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
    )
