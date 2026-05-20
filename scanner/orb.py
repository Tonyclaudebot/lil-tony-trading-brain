"""
Opening Range Breakout (ORB) — confirmation filter for Momentum Breakout.

Captures the first-hour high/low (9:30–10:30 AM CT) once per day, caches it
in orb_ranges.json, then provides breakout confirmation and profit-target
calculations for call (above OR High) and put (below OR Low) setups.
"""

import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import TypedDict

import pytz

logger = logging.getLogger(__name__)

_CENTRAL = pytz.timezone("America/Chicago")
_CACHE_PATH = Path(__file__).parent.parent / "orb_ranges.json"
_VOL_CONFIRM_MULT = 1.5   # breakout bar must be ≥ 1.5× average OR-window bar volume


class OrbStatus(TypedDict):
    confirmed: bool
    direction: str | None      # "call" | "put" | None
    or_high: float | None
    or_low: float | None
    current_close: float | None
    current_volume: float | None
    t1: float | None
    t2: float | None
    t3: float | None
    reason: str | None         # why not confirmed (if confirmed=False)


def _ct_now() -> datetime:
    return datetime.now(_CENTRAL)


def is_orb_window_closed() -> bool:
    """True if it is past 10:30 AM CT — the ORB window has finished forming."""
    now = _ct_now()
    cutoff = now.replace(hour=10, minute=30, second=0, microsecond=0)
    return now >= cutoff


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            data = json.loads(_CACHE_PATH.read_text())
            if data.get("date") == date.today().isoformat():
                return data.get("ranges", {})
        except Exception:
            pass
    return {}


def _save_cache(ranges: dict) -> None:
    _CACHE_PATH.write_text(json.dumps({
        "date":   date.today().isoformat(),
        "ranges": ranges,
    }, indent=2))


# ── Opening range capture ──────────────────────────────────────────────────────

def _or_window() -> tuple[datetime, datetime]:
    today = date.today()
    start = _CENTRAL.localize(datetime(today.year, today.month, today.day, 9, 30))
    end   = _CENTRAL.localize(datetime(today.year, today.month, today.day, 10, 30))
    return start, end


def _capture_opening_range(ticker: str) -> dict | None:
    """Fetch 9:30–10:30 CT minute bars and compute OR high/low/avg_volume."""
    from data.market_feed import get_intraday_bars
    start, end = _or_window()
    bars = get_intraday_bars(ticker, start, end)
    if not bars:
        logger.debug(f"ORB: no bars for {ticker} in 9:30–10:30 CT")
        return None
    return {
        "high":       round(max(b["high"] for b in bars), 4),
        "low":        round(min(b["low"]  for b in bars), 4),
        "avg_volume": round(sum(b["volume"] for b in bars) / len(bars), 1),
    }


def get_opening_range(ticker: str) -> dict | None:
    """Return today's OR for ticker (cached). Returns None if window not yet closed."""
    if not is_orb_window_closed():
        return None
    cache = _load_cache()
    if ticker in cache:
        return cache[ticker]
    orb = _capture_opening_range(ticker)
    if orb:
        cache[ticker] = orb
        _save_cache(cache)
    return orb


# ── Target calculation ────────────────────────────────────────────────────────

def calc_orb_targets(
    or_high: float, or_low: float, direction: str
) -> tuple[float, float, float]:
    """
    Calculate three stock-price profit targets from OR range size.
    Calls → above OR High; Puts → below OR Low.
    """
    rng = or_high - or_low
    if direction == "call":
        return (
            round(or_high + rng * 1.0, 2),
            round(or_high + rng * 2.0, 2),
            round(or_high + rng * 3.0, 2),
        )
    else:
        return (
            round(or_low - rng * 1.0, 2),
            round(or_low - rng * 2.0, 2),
            round(or_low - rng * 3.0, 2),
        )


def _which_target_hit(
    direction: str, close: float, t1: float, t2: float, t3: float
) -> str | None:
    """Return the highest ORB target already cleared by the current close."""
    if direction == "call":
        if close >= t3: return "T3"
        if close >= t2: return "T2"
        if close >= t1: return "T1"
    else:
        if close <= t3: return "T3"
        if close <= t2: return "T2"
        if close <= t1: return "T1"
    return None


# ── Main check ────────────────────────────────────────────────────────────────

def get_orb_status(ticker: str, spot: float, volume_ratio: float) -> OrbStatus:
    """
    Full ORB status for ticker: confirmed, direction, OR levels, and forward targets.

    spot and volume_ratio come from the CandidateStock (already fetched by the
    scanner) — no extra API call is made here beyond the OR window bar fetch,
    which is cached after the first call per day per ticker.
    """
    _empty: OrbStatus = {
        "confirmed": False, "direction": None,
        "or_high": None, "or_low": None,
        "current_close": None, "current_volume": None,
        "t1": None, "t2": None, "t3": None,
        "reason": None,
    }

    if not is_orb_window_closed():
        return {**_empty, "reason": "ORB window not yet closed (before 10:30 AM CT)"}

    orb = get_opening_range(ticker)
    if orb is None:
        return {**_empty, "reason": "opening range data unavailable"}

    or_high = orb["high"]
    or_low  = orb["low"]

    # Use spot (daily close) for direction check; volume_ratio for volume confirmation
    current_close = spot
    vol_ok        = volume_ratio >= _VOL_CONFIRM_MULT

    # Determine direction
    if current_close > or_high:
        direction = "call"
    elif current_close < or_low:
        direction = "put"
    else:
        return {**_empty,
                "or_high": or_high, "or_low": or_low,
                "current_close": current_close,
                "reason": f"price ${current_close:.2f} inside OR (${or_low:.2f}–${or_high:.2f})"}

    if not vol_ok:
        return {**_empty,
                "or_high": or_high, "or_low": or_low,
                "current_close": current_close,
                "reason": (f"breakout direction={direction} but volume_ratio "
                           f"{volume_ratio:.1f}x < {_VOL_CONFIRM_MULT}x required")}

    t1, t2, t3 = calc_orb_targets(or_high, or_low, direction)
    target_hit  = _which_target_hit(direction, current_close, t1, t2, t3)

    return {
        "confirmed":      True,
        "direction":      direction,
        "or_high":        or_high,
        "or_low":         or_low,
        "current_close":  current_close,
        "current_volume": None,
        "t1":             t1,
        "t2":             t2,
        "t3":             t3,
        "reason":         None,
        "_target_hit":    target_hit,
    }
