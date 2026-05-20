import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")
_STRATEGIES = ["momentum_breakout", "unusual_options_activity", "mean_reversion"]

# Bayesian-style prior: start at 0.5 win rate with this many "ghost" trades
_PRIOR_WEIGHT = 4


def load_state() -> dict:
    with open(_STATE_PATH) as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    state["last_updated"] = datetime.utcnow().isoformat()
    with open(_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def get_weights() -> dict[str, float]:
    return load_state()["weights"]


def record_outcome(strategy_key: str, outcome: str) -> None:
    """
    Update win/loss counts and recalculate weights.
    outcome: "win" | "loss" | "expired_worthless"
    """
    if strategy_key not in _STRATEGIES:
        return

    state = load_state()
    stats = state["strategy_stats"][strategy_key]

    if outcome == "win":
        stats["wins"] += 1
        if stats["open"] > 0:
            stats["open"] -= 1
    elif outcome in ("loss", "expired_worthless"):
        stats["losses"] += 1
        if stats["open"] > 0:
            stats["open"] -= 1

    state["weights"] = _recalculate_weights(state["strategy_stats"])
    _save_state(state)
    logger.info(f"Learner updated: {strategy_key} → {outcome}, new weights: {state['weights']}")


def record_open(strategy_key: str) -> None:
    """Increment open-trade count when an alert fires."""
    if strategy_key not in _STRATEGIES:
        return
    state = load_state()
    state["strategy_stats"][strategy_key]["open"] += 1
    _save_state(state)


def _win_rate(stats: dict) -> float:
    """Bayesian smoothed win rate: (wins + prior) / (total + 2*prior)."""
    wins = stats["wins"]
    total = stats["wins"] + stats["losses"]
    return (wins + _PRIOR_WEIGHT * 0.5) / (total + _PRIOR_WEIGHT)


def _recalculate_weights(strategy_stats: dict) -> dict[str, float]:
    """Normalize win rates to weights that sum to 1.0."""
    rates = {k: _win_rate(v) for k, v in strategy_stats.items()}
    total = sum(rates.values())
    return {k: round(r / total, 4) for k, r in rates.items()}
