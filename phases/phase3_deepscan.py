"""
Phase 3 — Deep scan (9:30 AM ET, market open).
Loads morning_40.json, refreshes spot prices, scores all strategies using
price/volume data only, saves top 10 to phase3_top10.json.

No options chain is fetched here — that costs ~30 API calls per ticker and
exhausts Polygon free-tier limits before Phase 4 can run. Phase 4 does the
full chain scan on the final 10 candidates only.
"""
import json
import logging
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from pathlib import Path

from config import settings
from data.market_feed import get_spot_price
from scanner.strategies import momentum_breakout, mean_reversion, unusual_options_activity
from scanner.strategies.base import CandidateStock

MORNING_PATH  = Path(__file__).parent.parent / "morning_40.json"
OUTPUT_PATH   = Path(__file__).parent.parent / "phase3_top10.json"

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(f"{settings.LOG_DIR}/lil-tony.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("phase3")

_STRATEGIES = [momentum_breakout, unusual_options_activity, mean_reversion]


def _to_candidate(pick: dict, spot: float | None) -> CandidateStock:
    c = CandidateStock(
        ticker=pick["ticker"],
        spot=spot or pick.get("spot", 0.0),
        ret_1d=pick.get("ret_1d", 0.0),
        ret_5d=pick.get("ret_5d", 0.0),
        rsi=pick.get("rsi", 50.0),
        volume_ratio=pick.get("volume_ratio", 1.0),
        ma20=pick.get("ma20", 0.0),
        ma20_pct=pick.get("ma20_pct", 0.0),
        momentum_score=pick.get("momentum_score", 0.0),
    )
    c.uoa_score = 0.0
    c.composite_score = 0.0
    return c


def run() -> list[dict]:
    if not MORNING_PATH.exists():
        logger.error("morning_40.json not found — run Phase 2 first")
        return []

    data   = json.loads(MORNING_PATH.read_text())
    picks  = data["picks"][:40]
    logger.info(f"Phase 3: deep scanning {len(picks)} tickers (spot refresh only — chain deferred to Phase 4)")

    results: list[dict] = []

    for i, pick in enumerate(picks):
        ticker = pick["ticker"]
        logger.info(f"  [{i+1}/{len(picks)}] {ticker}")

        if i > 0:
            time.sleep(2)   # ~2 calls/sec stays within Polygon free-tier burst limit

        spot = get_spot_price(ticker)
        if not spot:
            logger.debug(f"    {ticker}: no spot price, skipping")
            continue

        candidate = _to_candidate(pick, spot)

        # Score all strategies using price/volume data only
        strategy_scores: dict[str, float] = {}
        for strat in _STRATEGIES:
            strategy_scores[strat.KEY] = round(strat.score(candidate), 2)

        best_strategy  = max(strategy_scores, key=strategy_scores.__getitem__)
        best_raw_score = strategy_scores[best_strategy]
        candidate.composite_score = round(
            candidate.momentum_score * 0.6 + best_raw_score * 0.4, 2
        )

        results.append({
            **pick,
            "spot":            spot,
            "uoa_score":       0.0,
            "composite_score": candidate.composite_score,
            "strategy_scores": strategy_scores,
            "best_strategy":   best_strategy,
        })

    results.sort(key=lambda r: r["composite_score"], reverse=True)
    top10 = results[:10]

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scanned":      len(results),
        "picks":        top10,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    logger.info(f"Phase 3 complete: top 10 saved → {OUTPUT_PATH.name}")
    for r in top10:
        logger.info(f"  {r['ticker']:6s}  composite={r['composite_score']:5.1f}  best={r['best_strategy']}")
    return top10


if __name__ == "__main__":
    run()
