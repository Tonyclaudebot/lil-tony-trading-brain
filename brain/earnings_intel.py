from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from data.earnings import get_next_earnings_date, get_earnings_history

logger = logging.getLogger(__name__)

_HIGH_VOLATILITY_DAYS = 7
_ELEVATED_DAYS = 30


@dataclass
class EarningsIntel:
    ticker: str
    next_earnings_date: Optional[str] = None   # ISO date string
    days_to_earnings: Optional[int] = None
    proximity_risk: str = "UNKNOWN"             # HIGH | ELEVATED | STANDARD | UNKNOWN
    beat_rate: Optional[float] = None           # fraction of quarters beat (0.0–1.0)
    avg_move_abs: Optional[float] = None        # avg absolute % move on earnings day
    avg_beat_move: Optional[float] = None       # avg move when beat
    avg_miss_move: Optional[float] = None       # avg move when missed
    sample_size: int = 0
    binary_event: bool = False                  # True when earnings ≤ HIGH_VOLATILITY_DAYS
    warning: Optional[str] = None


def analyze_earnings(ticker: str) -> EarningsIntel:
    """
    Full earnings analysis: proximity classification, historical beat/miss rate,
    and average price move. Returns an EarningsIntel ready to attach to a TradePlan.
    """
    intel = EarningsIntel(ticker=ticker)

    next_date = get_next_earnings_date(ticker)
    if next_date is None:
        intel.proximity_risk = "UNKNOWN"
        return intel

    today = date.today()
    days = (next_date - today).days
    intel.next_earnings_date = next_date.isoformat()
    intel.days_to_earnings = days

    if days <= _HIGH_VOLATILITY_DAYS:
        intel.proximity_risk = "HIGH"
        intel.binary_event = True
    elif days <= _ELEVATED_DAYS:
        intel.proximity_risk = "ELEVATED"
    else:
        intel.proximity_risk = "STANDARD"

    # Historical behavior
    history = get_earnings_history(ticker)
    if history:
        _populate_history_stats(intel, history)

    intel.warning = _build_warning(intel)
    return intel


def _populate_history_stats(intel: EarningsIntel, history: list[dict]) -> None:
    graded = [h for h in history if h.get("beat") is not None]
    beats = [h for h in graded if h["beat"]]
    misses = [h for h in graded if not h["beat"]]
    moves = [h["next_day_move"] for h in history if h.get("next_day_move") is not None]

    intel.sample_size = len(graded)
    if graded:
        intel.beat_rate = round(len(beats) / len(graded), 2)
    if moves:
        intel.avg_move_abs = round(sum(abs(m) for m in moves) / len(moves), 1)
    if beats:
        beat_moves = [h["next_day_move"] for h in beats if h.get("next_day_move") is not None]
        if beat_moves:
            intel.avg_beat_move = round(sum(beat_moves) / len(beat_moves), 1)
    if misses:
        miss_moves = [h["next_day_move"] for h in misses if h.get("next_day_move") is not None]
        if miss_moves:
            intel.avg_miss_move = round(sum(miss_moves) / len(miss_moves), 1)


def _build_warning(intel: EarningsIntel) -> Optional[str]:
    if intel.proximity_risk in ("STANDARD", "UNKNOWN") or intel.days_to_earnings is None:
        return None

    days_str = f"{intel.days_to_earnings}d"
    lines: list[str] = []

    if intel.proximity_risk == "HIGH":
        lines.append(f"!! EARNINGS IN {days_str} — HIGH VOLATILITY !!")
        if intel.beat_rate is not None and intel.sample_size > 0:
            beat_n = round(intel.beat_rate * intel.sample_size)
            lines.append(
                f"Hist ({intel.sample_size} qtrs): beats {beat_n}/{intel.sample_size}"
                + (f", avg ±{intel.avg_move_abs}%" if intel.avg_move_abs else "")
            )
        lines.append("IV crush risk if holding through earnings")
        lines.append("!! Big Tony must confirm before trading !!")
    elif intel.proximity_risk == "ELEVATED":
        lines.append(f"Earnings in {days_str} — factor expected move into targets")
        if intel.avg_move_abs is not None:
            lines.append(f"Historical avg move: ±{intel.avg_move_abs}%")

    return "\n".join(lines)
