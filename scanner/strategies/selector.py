import logging
from typing import Optional

from scanner.strategies.base import CandidateStock, TradePlan
from scanner.strategies import (
    momentum_breakout, unusual_options_activity, mean_reversion, volatility_breakout,
)
from scanner import risk
from brain.earnings_intel import analyze_earnings
from brain.macro_filter import check_macro_risk
from data.news_feed import get_stock_news, get_ticker_headlines

logger = logging.getLogger(__name__)

_STRATEGIES = [momentum_breakout, unusual_options_activity, mean_reversion, volatility_breakout]


def pick_best_plan(
    candidate: CandidateStock,
    weights: dict[str, float],
    macro_events: Optional[list[dict]] = None,
) -> TradePlan | None:
    """
    Score all three strategies, apply learner weights, build the winning plan,
    then enrich it with earnings intelligence, macro context, and news.
    Returns None if no qualifying contract is found.
    """
    raw_scores: dict[str, float] = {
        strat.KEY: strat.score(candidate) for strat in _STRATEGIES
    }

    weighted: dict[str, float] = {
        key: raw * (weights.get(key, 0.333) * 3)
        for key, raw in raw_scores.items()
    }

    ranked = sorted(weighted.items(), key=lambda kv: kv[1], reverse=True)
    logger.debug(f"{candidate.ticker} strategy scores: {ranked}")

    _strategy_map = {strat.KEY: strat for strat in _STRATEGIES}
    plan: TradePlan | None = None

    for strategy_key, weighted_score in ranked:
        if weighted_score < 10:
            continue
        strat = _strategy_map[strategy_key]
        combined = round((candidate.composite_score + raw_scores[strategy_key]) / 2, 2)
        plan = strat.build_plan(candidate, combined)
        if plan is not None:
            logger.info(
                f"{candidate.ticker}: strategy={strategy_key} "
                f"entry=${plan.entry} target=${plan.target} conf={plan.confidence}"
            )
            break

    if plan is None:
        logger.debug(f"{candidate.ticker}: no qualifying contract found")
        return None

    # ── Risk assessment (all strategies) ─────────────────────────────────────
    risk.assess(plan)

    # ── Enrich: earnings intelligence ────────────────────────────────────────
    _attach_earnings_intel(plan)

    # ── Enrich: macro events ─────────────────────────────────────────────────
    _attach_macro_context(plan, macro_events or [])

    # ── Enrich: news context ─────────────────────────────────────────────────
    _attach_news(plan)

    # ── House Rules: flag binary events to Big Tony ───────────────────────────
    _apply_binary_event_rules(plan)

    return plan


def _attach_earnings_intel(plan: TradePlan) -> None:
    try:
        intel = analyze_earnings(plan.ticker)
        plan.earnings_date = intel.next_earnings_date
        plan.days_to_earnings = intel.days_to_earnings
        plan.earnings_proximity_risk = intel.proximity_risk
        plan.earnings_beat_rate = intel.beat_rate
        plan.earnings_avg_move = intel.avg_move_abs
        plan.earnings_warning = intel.warning

        # Mark binary event if earnings fall before contract expiration
        if intel.binary_event and intel.next_earnings_date:
            if plan.expiration >= intel.next_earnings_date:
                plan.binary_event_flag = True
                plan.binary_event_detail = intel.warning

    except Exception as e:
        logger.warning(f"Earnings intel failed for {plan.ticker}: {e}")


def _attach_macro_context(plan: TradePlan, macro_events: list[dict]) -> None:
    if not macro_events:
        return
    try:
        from data.forex_factory import days_until
        near = [e for e in macro_events if days_until(e) <= 7]
        plan.macro_events = [e["title"] for e in near]
        is_binary, warning = check_macro_risk(macro_events)
        plan.macro_warning = warning
        if is_binary:
            plan.binary_event_flag = True
    except Exception as e:
        logger.warning(f"Macro context failed: {e}")


def _attach_news(plan: TradePlan) -> None:
    try:
        yf_news = get_stock_news(plan.ticker, max_items=2)
        fj_news = get_ticker_headlines(plan.ticker)[:2]
        plan.news_context = (fj_news + yf_news)[:3]
    except Exception as e:
        logger.debug(f"News fetch failed for {plan.ticker}: {e}")


def _apply_binary_event_rules(plan: TradePlan) -> None:
    """
    House Rules compliance: never send a naked options alert into a binary
    event without prominently flagging it for Big Tony.
    """
    if not plan.binary_event_flag:
        return

    parts: list[str] = []
    if plan.earnings_proximity_risk == "HIGH":
        parts.append(plan.earnings_warning or "Earnings within 7 days")
    if plan.macro_warning:
        parts.append(plan.macro_warning)

    if parts:
        plan.binary_event_detail = "\n\n".join(parts)

    # Downgrade confidence — Big Tony should manually verify
    if plan.confidence == "HIGH":
        plan.confidence = "MEDIUM"
        logger.info(f"{plan.ticker}: confidence downgraded to MEDIUM due to binary event")
