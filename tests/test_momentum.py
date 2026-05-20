import numpy as np
import pandas as pd
import pytest

from scanner.momentum import rsi, score_momentum


def _make_history(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    vols = volumes or [1_000_000] * n
    return pd.DataFrame({"Close": closes, "Volume": vols})


def test_rsi_all_gains_returns_100():
    closes = list(range(1, 30))
    assert rsi(pd.Series(closes)) == 100.0


def test_rsi_all_losses_returns_0():
    closes = list(range(30, 0, -1))
    assert rsi(pd.Series(closes)) == 0.0


def test_rsi_midpoint():
    val = rsi(pd.Series([50.0] * 30))
    assert 0 <= val <= 100


def test_score_momentum_requires_22_rows():
    hist = _make_history([100.0] * 21)
    result = score_momentum(hist)
    assert result["momentum_score"] == 0.0


def test_score_momentum_strong_move():
    closes = [100.0] * 21 + [108.0]  # 8% 1d move (22 rows needed for vol avg)
    hist = _make_history(closes)
    result = score_momentum(hist)
    assert result["ret_1d"] == pytest.approx(0.08, abs=1e-4)
    assert result["momentum_score"] > 0


def test_score_momentum_volume_spike():
    closes = [100.0] * 22
    # Last bar has 5x volume vs prior 20-day avg
    vols = [1_000_000] * 21 + [5_000_000]
    hist = _make_history(closes, vols)
    result = score_momentum(hist)
    assert result["volume_ratio"] == pytest.approx(5.0, abs=0.1)
