import logging

from scanner.strategies.base import CandidateStock, TradePlan
from scanner.setups import find_unusual_volume
from data.market_feed import get_options_chain
from config import settings

logger = logging.getLogger(__name__)

NAME = "Unusual Options Activity"
KEY = "unusual_options_activity"

_TARGET_MULT = 2.0
_STOP_MULT = 0.5


def score(candidate: CandidateStock) -> float:
    """Rate how well this candidate fits a UOA setup (0–100)."""
    # UOA score already computed by the ranker
    base = candidate.uoa_score
    # Bonus: strong 1d move confirms the smart-money signal
    if candidate.ret_1d > 0.01:
        base = min(100.0, base + 10)
    if candidate.volume_ratio > 2.5:
        base = min(100.0, base + 10)
    return round(base, 2)


def build_plan(candidate: CandidateStock, combined_score: float) -> TradePlan | None:
    """
    Build a UOA trade plan — follow the unusual flow direction (call or put).
    Returns None if no qualifying contract is found.
    """
    chain = get_options_chain(candidate.ticker, max_dte=settings.MAX_DTE, min_dte=settings.MIN_DTE)
    if chain.empty:
        return None

    hits = find_unusual_volume(chain, settings.MIN_OPTION_VOLUME, settings.MIN_VOLUME_TO_OI_RATIO)
    if not hits:
        return None

    # Follow the dominant flow direction
    call_hits = [h for h in hits if h.opt_type == "call"]
    put_hits = [h for h in hits if h.opt_type == "put"]
    dominant = call_hits if len(call_hits) >= len(put_hits) else put_hits
    opt_type = "call" if dominant is call_hits else "put"

    # Prefer $0.20–$1.00 premium; fall back up to $3.00 ceiling
    preferred = [h for h in dominant if 0.20 <= h.last_price <= 1.00]
    fallback = [h for h in dominant if h.last_price <= 3.00]
    qualifying = preferred if preferred else fallback
    if not qualifying:
        return None

    best = max(qualifying, key=lambda h: h.volume / max(h.open_interest, 1))

    # Pull the full row to get all fields
    side = chain[chain["type"] == opt_type]
    row_match = side[side["contractSymbol"] == best.contract]
    if row_match.empty:
        return None
    row = row_match.iloc[0]

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
