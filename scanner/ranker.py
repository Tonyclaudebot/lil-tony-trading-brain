import logging

from data.market_feed import batch_download_history, get_options_chain
from data.universe import UNIVERSE
from data.watchlist import build_scan_universe
from scanner.momentum import score_momentum
from scanner.strategies.base import CandidateStock
from scanner.setups import find_unusual_volume
from scanner import scan_writer
from config import settings

logger = logging.getLogger(__name__)

_TOP_MOMENTUM = 20   # candidates that advance to options scan
_TOP_FINAL = 10      # candidates returned to strategy selector
_TOP_TRADE = 3       # candidates that become trade plans


def scan_universe(dynamic_tickers: list[str] | None = None) -> list[CandidateStock]:
    """
    Two-pass scan:
      Pass 1 — batch download history, score momentum for all tickers.
              Uses UNIVERSE + fixed WATCHLIST + dynamic_tickers (deduped).
      Pass 2 — fetch options chains for top-20, score UOA, finalize ranking.
    Returns top-10 CandidateStock objects sorted by composite_score desc.
    """
    tickers = build_scan_universe(UNIVERSE, settings.WATCHLIST, dynamic_tickers or [])
    scan_writer.start_scan(len(tickers))

    # Pass 1: momentum scan
    logger.info(f"Pass 1: downloading history for {len(tickers)} tickers "
                f"({len(dynamic_tickers or [])} dynamic added today)")
    histories = batch_download_history(tickers, period="1mo")

    candidates: list[CandidateStock] = []
    for i, (ticker, hist) in enumerate(histories.items()):
        m = score_momentum(hist)
        scan_writer.update_scan(i + 1, len(tickers), ticker, 'momentum', m["momentum_score"])
        if m["momentum_score"] < 5:
            continue
        candidates.append(CandidateStock(
            ticker=ticker,
            spot=m["spot"],
            ret_1d=m["ret_1d"],
            ret_5d=m["ret_5d"],
            rsi=m["rsi"],
            volume_ratio=m["volume_ratio"],
            ma20=m["ma20"],
            ma20_pct=m["ma20_pct"],
            momentum_score=m["momentum_score"],
            atr_ratio=m.get("atr_ratio", 1.0),
            range_5d_pct=m.get("range_5d_pct", 0.05),
        ))

    candidates.sort(key=lambda c: c.momentum_score, reverse=True)
    top_momentum = candidates[:_TOP_MOMENTUM]
    logger.info(f"Pass 1 complete: {len(candidates)} scored, top {len(top_momentum)} advance")

    # Pass 2: options UOA scan
    logger.info("Pass 2: fetching options chains for top candidates")
    for candidate in top_momentum:
        scan_writer.update_scan(len(tickers), len(tickers), candidate.ticker, 'unusual_options', candidate.momentum_score)
        chain = get_options_chain(
            candidate.ticker,
            max_dte=settings.MAX_DTE,
            min_dte=settings.MIN_DTE,
        )
        if chain.empty:
            continue
        uoa_hits = find_unusual_volume(chain, settings.MIN_OPTION_VOLUME, settings.MIN_VOLUME_TO_OI_RATIO)
        if uoa_hits:
            best_ratio = max(h.volume / h.open_interest for h in uoa_hits)
            candidate.uoa_score = min(100.0, best_ratio * 20)

        candidate.composite_score = _composite(candidate)

    top_momentum.sort(key=lambda c: c.composite_score, reverse=True)
    top10 = top_momentum[:_TOP_FINAL]
    logger.info(f"Pass 2 complete: {len(top10)} final candidates")

    for candidate in top10:
        scan_writer.add_alert({
            'ticker': candidate.ticker,
            'strategy': 'composite',
            'score': candidate.composite_score,
            'momentum_score': candidate.momentum_score,
            'uoa_score': candidate.uoa_score,
        })

    scan_writer.finish_scan([
        {'ticker': c.ticker, 'score': c.composite_score, 'strategy': 'composite'}
        for c in top10
    ])
    return top10


def select_top_candidates(candidates: list[CandidateStock]) -> list[CandidateStock]:
    """Return the top-3 from the ranked candidate list."""
    return candidates[:_TOP_TRADE]


def _composite(c: CandidateStock) -> float:
    return round(c.momentum_score * 0.6 + c.uoa_score * 0.4, 2)
