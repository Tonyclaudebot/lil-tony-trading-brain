"""
Phase 2 — Morning news filter (8:00 AM ET).
Loads nightly_picks.json (top 100), cross-references with Financial Juice
headlines, Forex Factory events, and the live dynamic watchlist (top movers).
Boosts scores for tickers with news catalysts, saves top 40 to morning_40.json.
"""
import json
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from pathlib import Path

from brain.macro_filter import load_macro_calendar
from config import settings
from data.news_feed import get_financial_juice_headlines
from data.watchlist import fetch_dynamic_tickers

NIGHTLY_PATH = Path(__file__).parent.parent / "nightly_picks.json"
OUTPUT_PATH  = Path(__file__).parent.parent / "morning_40.json"

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(f"{settings.LOG_DIR}/lil-tony.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("phase2")

_NEWS_BOOST_PER_HIT  = 12.0   # per headline that mentions the ticker
_NEWS_BOOST_MAX      = 36.0   # cap news boost at 3 hits
_DYNAMIC_BOOST       = 15.0   # bonus for being a top mover today
_MACRO_PENALTY       = -20.0  # penalise tickers exposed to high-impact macro day


def _news_boost(ticker: str, headlines: list[str]) -> tuple[float, list[str]]:
    catalysts = [h for h in headlines if ticker.upper() in h.upper()]
    boost = min(_NEWS_BOOST_MAX, len(catalysts) * _NEWS_BOOST_PER_HIT)
    return boost, catalysts[:5]


def run() -> list[dict]:
    if not NIGHTLY_PATH.exists():
        logger.error(f"nightly_picks.json not found — run Phase 1 first")
        return []

    data = json.loads(NIGHTLY_PATH.read_text())
    picks = data["picks"]  # top 100
    logger.info(f"Phase 2: filtering {len(picks)} nightly picks with morning data")

    headlines     = get_financial_juice_headlines(max_items=200)
    macro_events  = load_macro_calendar(settings.MACRO_WARNING_DAYS)
    macro_titles  = [e.get("title", "") for e in macro_events]
    dynamic       = fetch_dynamic_tickers(n=20)
    dynamic_set   = set(dynamic)

    logger.info(f"  FJ headlines: {len(headlines)} | macro events: {len(macro_events)} | dynamic tickers: {len(dynamic)}")

    scored: list[dict] = []
    existing_tickers: set[str] = set()

    for p in picks:
        ticker = p["ticker"]
        existing_tickers.add(ticker)

        news_boost, catalysts = _news_boost(ticker, headlines)
        dynamic_boost = _DYNAMIC_BOOST if ticker in dynamic_set else 0.0
        combined = p["momentum_score"] + news_boost + dynamic_boost

        scored.append({
            **p,
            "news_boost":     round(news_boost, 2),
            "dynamic_boost":  dynamic_boost,
            "combined_score": round(combined, 2),
            "news_catalysts": catalysts,
        })

    # Inject dynamic tickers not already in the nightly list
    for ticker in dynamic:
        if ticker in existing_tickers:
            continue
        news_boost, catalysts = _news_boost(ticker, headlines)
        scored.append({
            "ticker":         ticker,
            "spot":           0.0,
            "ret_1d":         0.0,
            "ret_5d":         0.0,
            "rsi":            50.0,
            "volume_ratio":   1.0,
            "ma20":           0.0,
            "ma20_pct":       0.0,
            "momentum_score": 0.0,
            "news_boost":     round(news_boost, 2),
            "dynamic_boost":  _DYNAMIC_BOOST,
            "combined_score": round(_DYNAMIC_BOOST + news_boost, 2),
            "news_catalysts": catalysts,
        })

    scored.sort(key=lambda p: p["combined_score"], reverse=True)
    top40 = scored[:40]

    output = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "macro_events":  macro_titles,
        "headline_count": len(headlines),
        "picks":         top40,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    logger.info(f"Phase 2 complete: {len(top40)} picks saved → {OUTPUT_PATH.name}")
    return top40


if __name__ == "__main__":
    run()
