"""
Volatility Breakout strategy.

Signal: price coils (low ATR), IV rank is elevated but not extreme (<80),
then explodes with 2x+ volume. Direction follows the breakout bar.

Filters applied in build_plan:
  - volume_ratio >= 2.0 (expansion confirmation)
  - atr_ratio    <  0.75 (coiling — ATR5 compressed vs ATR20)
  - abs(ret_1d)  >= 0.01 (at least 1% breakout move)
  - iv_rank      <  80   (premiums not yet overpriced)
"""

import logging

from scanner.strategies.base import CandidateStock, TradePlan
from data.market_feed import get_options_chain
from config import settings

logger = logging.getLogger(__name__)

NAME = "Volatility Breakout"
KEY  = "volatility_breakout"

_MIN_VOLUME_RATIO  = 2.0
_MAX_IV_RANK       = 80.0
_COIL_ATR_RATIO    = 0.75
_MIN_BREAKOUT_MOVE = 0.01

_TARGET_MULT = 2.0
_STOP_MULT   = 0.5


def score(candidate: CandidateStock) -> float:
    """Rate how well this candidate fits a volatility breakout setup (0–100)."""
    s = 0.0

    # Volume explosion
    if candidate.volume_ratio >= _MIN_VOLUME_RATIO:
        s += 30 + min(20, (candidate.volume_ratio - 2.0) * 10)
    elif candidate.volume_ratio >= 1.5:
        s += 10

    # ATR coiling (lower = more compressed)
    if candidate.atr_ratio <= _COIL_ATR_RATIO:
        s += 25 + min(15, (0.75 - candidate.atr_ratio) * 100)
    elif candidate.atr_ratio <= 0.9:
        s += 8

    # Tight 5-day price range (< 4% = coiled)
    if candidate.range_5d_pct <= 0.04:
        s += 15 + min(10, (0.04 - candidate.range_5d_pct) * 500)
    elif candidate.range_5d_pct <= 0.06:
        s += 5

    # RSI neutral zone 35–65 (compressed, not extended)
    if 35 <= candidate.rsi <= 65:
        s += 10

    return round(min(100.0, s), 2)


def build_plan(candidate: CandidateStock, combined_score: float) -> TradePlan | None:
    """
    Build a Volatility Breakout trade plan.
    Applies all hard filters before selecting a contract.
    """
    # Hard filters
    if candidate.volume_ratio < _MIN_VOLUME_RATIO:
        logger.debug(f"{candidate.ticker} VB: volume_ratio {candidate.volume_ratio:.1f} < 2.0")
        return None
    if candidate.atr_ratio >= _COIL_ATR_RATIO:
        logger.debug(f"{candidate.ticker} VB: atr_ratio {candidate.atr_ratio:.2f} not coiled")
        return None
    if abs(candidate.ret_1d) < _MIN_BREAKOUT_MOVE:
        logger.debug(f"{candidate.ticker} VB: ret_1d {candidate.ret_1d:.3f} too small")
        return None

    opt_type = "call" if candidate.ret_1d > 0 else "put"

    chain = get_options_chain(candidate.ticker, max_dte=settings.MAX_DTE, min_dte=settings.MIN_DTE)
    if chain.empty:
        return None

    # IV Rank filter — skip overpriced premiums
    iv_rank = _compute_iv_rank(candidate.ticker, candidate.spot, chain)
    if iv_rank > _MAX_IV_RANK:
        logger.debug(f"{candidate.ticker} VB: IV Rank {iv_rank:.0f} > {_MAX_IV_RANK}")
        return None

    side = chain[chain["type"] == opt_type].copy()
    if side.empty:
        return None

    atm_candidates = side[
        (side["strike"] >= candidate.spot * 0.90)
        & (side["strike"] <= candidate.spot * 1.10)
        & (side["volume"] > 0)
    ]
    if atm_candidates.empty:
        return None

    preferred = atm_candidates[
        (atm_candidates["lastPrice"] >= 0.20)
        & (atm_candidates["lastPrice"] <= 1.00)
    ]
    fallback = atm_candidates[atm_candidates["lastPrice"] <= 3.00]
    window   = preferred if not preferred.empty else fallback
    if window.empty:
        return None

    row        = window.loc[window["volume"].idxmax()]
    entry      = float(row["lastPrice"])
    target     = round(entry * _TARGET_MULT, 2)
    stop       = round(entry * _STOP_MULT, 2)
    target_pct = round((_TARGET_MULT - 1) * 100, 1)
    confidence = "HIGH" if combined_score >= 75 else "MEDIUM"

    logger.info(
        f"{candidate.ticker} VB {opt_type.upper()} | "
        f"atr_ratio={candidate.atr_ratio:.2f} vol={candidate.volume_ratio:.1f}x "
        f"iv_rank={iv_rank:.0f} entry=${entry:.2f}"
    )

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
        risk_iv_rank=round(iv_rank, 1),
    )


def _compute_iv_rank(ticker: str, spot: float, chain) -> float:
    """
    IV rank proxy: ATM call IV from the options chain scaled to 0–100.
    No extra API calls — chain data is already fetched by build_plan.
    """
    calls  = chain[chain["type"] == "call"]
    atm    = calls[(calls["strike"] >= spot * 0.97) & (calls["strike"] <= spot * 1.03)]
    source = atm if not atm.empty else calls
    if source.empty:
        return 50.0
    current_iv = float(source["impliedVolatility"].mean())  # fractional, e.g. 0.45
    return round(min(100.0, current_iv * 150), 1)
