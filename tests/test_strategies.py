from scanner.strategies.base import CandidateStock
from scanner.strategies import (
    momentum_breakout, mean_reversion, unusual_options_activity, volatility_breakout,
)


def _candidate(**kwargs) -> CandidateStock:
    defaults = dict(
        ticker="AAPL", spot=175.0, ret_1d=0.03, ret_5d=0.07,
        rsi=60.0, volume_ratio=3.0, ma20=170.0, ma20_pct=0.029,
        momentum_score=72.0, uoa_score=60.0, composite_score=67.0,
        atr_ratio=1.0, range_5d_pct=0.05,
    )
    return CandidateStock(**{**defaults, **kwargs})


# ── Momentum Breakout ─────────────────────────────────────────────────────────

def test_momentum_breakout_strong_candidate_scores_high():
    c = _candidate(ret_1d=0.04, ret_5d=0.10, volume_ratio=4.0, rsi=62.0)
    assert momentum_breakout.score(c) >= 70


def test_momentum_breakout_weak_returns_low():
    c = _candidate(ret_1d=0.001, ret_5d=0.005, volume_ratio=1.0, rsi=45.0)
    assert momentum_breakout.score(c) < 20


# ── Mean Reversion ────────────────────────────────────────────────────────────

def test_mean_reversion_oversold_scores_high():
    c = _candidate(rsi=28.0, ma20_pct=-0.08)
    assert mean_reversion.score(c) >= 50


def test_mean_reversion_overbought_scores_high():
    c = _candidate(rsi=72.0, ma20_pct=0.08)
    assert mean_reversion.score(c) >= 50


def test_mean_reversion_neutral_rsi_returns_zero():
    c = _candidate(rsi=50.0, ma20_pct=0.01)
    assert mean_reversion.score(c) == 0.0


def test_mean_reversion_direction_oversold_is_call():
    c = _candidate(rsi=30.0)
    from scanner.strategies.mean_reversion import _direction
    assert _direction(c) == "call"


def test_mean_reversion_direction_overbought_is_put():
    c = _candidate(rsi=70.0)
    from scanner.strategies.mean_reversion import _direction
    assert _direction(c) == "put"


# ── UOA ───────────────────────────────────────────────────────────────────────

def test_uoa_high_uoa_score_scores_high():
    c = _candidate(uoa_score=80.0, ret_1d=0.02, volume_ratio=3.0)
    assert unusual_options_activity.score(c) >= 80


def test_uoa_zero_uoa_score_returns_low():
    c = _candidate(uoa_score=0.0, ret_1d=0.001, volume_ratio=1.0)
    assert unusual_options_activity.score(c) <= 15


# ── Volatility Breakout ───────────────────────────────────────────────────────

def test_vb_coiled_high_volume_scores_high():
    c = _candidate(atr_ratio=0.55, range_5d_pct=0.02, volume_ratio=3.5, rsi=50.0)
    assert volatility_breakout.score(c) >= 60


def test_vb_expanded_atr_low_volume_scores_low():
    c = _candidate(atr_ratio=1.2, range_5d_pct=0.08, volume_ratio=1.1, rsi=72.0)
    assert volatility_breakout.score(c) < 20


def test_vb_moderate_coil_medium_volume_scores_medium():
    c = _candidate(atr_ratio=0.70, range_5d_pct=0.035, volume_ratio=2.5, rsi=55.0)
    s = volatility_breakout.score(c)
    assert 30 <= s <= 100


# ── Risk Assessment ───────────────────────────────────────────────────────────

def test_risk_cheap_low_iv_low_dte_scores_low():
    from scanner.risk import assess
    from scanner.strategies.base import TradePlan
    plan = TradePlan(
        ticker="AAPL", opt_type="call", strike=175.0, spot=174.0,
        entry=0.35, target=0.70, stop=0.18, dte=30, iv=18.0, score=72.0,
    )
    assess(plan)
    assert plan.risk_score == "LOW"
    assert plan.risk_premium_label == "CHEAP"
    assert plan.risk_iv_label == "LOW"


def test_risk_expensive_high_iv_short_dte_scores_high():
    from scanner.risk import assess
    from scanner.strategies.base import TradePlan
    plan = TradePlan(
        ticker="TSLA", opt_type="call", strike=210.0, spot=190.0,
        entry=1.80, target=3.60, stop=0.90, dte=4, iv=75.0, score=35.0,
    )
    assess(plan)
    assert plan.risk_score == "HIGH"
    assert plan.risk_premium_label == "EXPENSIVE"
    assert plan.risk_iv_label == "HIGH"


def test_risk_summary_populated():
    from scanner.risk import assess
    from scanner.strategies.base import TradePlan
    plan = TradePlan(
        ticker="SPY", opt_type="put", strike=520.0, spot=522.0,
        entry=0.65, target=1.30, stop=0.33, dte=14, iv=40.0, score=55.0,
    )
    assess(plan)
    assert len(plan.risk_summary) > 0
    assert plan.risk_iv_rank is not None
