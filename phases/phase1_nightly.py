"""
Phase 1 — Nightly universe scan.
Runs after market close (~6 PM ET). Downloads history for all tickers in the
static universe, scores momentum, saves top 100 to nightly_picks.json.
"""
import json
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from pathlib import Path

from config import settings
from data.market_feed import batch_download_history
from data.universe import UNIVERSE
from data.watchlist import build_scan_universe
from scanner.momentum import score_momentum

OUTPUT_PATH = Path(__file__).parent.parent / "nightly_picks.json"

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(f"{settings.LOG_DIR}/lil-tony.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("phase1")


def run() -> list[dict]:
    tickers = build_scan_universe(UNIVERSE, settings.WATCHLIST, [])
    logger.info(f"Phase 1: scanning {len(tickers)} tickers (static universe, no dynamic)")

    histories = batch_download_history(tickers, period="1mo")

    picks = []
    for ticker, hist in histories.items():
        m = score_momentum(hist)
        if m["momentum_score"] < 1:
            continue
        picks.append({
            "ticker":         ticker,
            "spot":           m["spot"],
            "ret_1d":         m["ret_1d"],
            "ret_5d":         m["ret_5d"],
            "rsi":            m["rsi"],
            "volume_ratio":   m["volume_ratio"],
            "ma20":           m["ma20"],
            "ma20_pct":       m["ma20_pct"],
            "momentum_score": m["momentum_score"],
        })

    picks.sort(key=lambda p: p["momentum_score"], reverse=True)
    top100 = picks[:100]

    output = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "total_scanned":  len(histories),
        "total_scored":   len(picks),
        "picks":          top100,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    logger.info(f"Phase 1 complete: {len(top100)} picks saved → {OUTPUT_PATH.name}")
    return top100


if __name__ == "__main__":
    run()
