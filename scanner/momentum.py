import math

import pandas as pd


def rsi(closes: pd.Series, period: int = 14) -> float:
    """Wilder's RSI. Returns NaN if insufficient data."""
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    if loss.iloc[-1] == 0:
        return 100.0
    rs = gain.iloc[-1] / loss.iloc[-1]
    return round(float(100 - 100 / (1 + rs)), 2)


def score_momentum(history: pd.DataFrame) -> dict:
    """
    Score a stock's momentum from OHLCV history.
    Returns a dict of raw metrics plus a composite 0-100 score.
    """
    if len(history) < 22:
        return _empty_score()

    closes = history["Close"]
    volumes = history["Volume"]

    ret_1d = float((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2])
    ret_5d = float((closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6]) if len(closes) >= 6 else 0.0

    vol_20d_avg = float(volumes.iloc[-21:-1].mean())
    volume_ratio = float(volumes.iloc[-1] / vol_20d_avg) if vol_20d_avg > 0 else 1.0

    ma20 = float(closes.iloc[-20:].mean())
    spot = float(closes.iloc[-1])
    ma20_pct = (spot - ma20) / ma20

    rsi_val = rsi(closes)

    # Component scores (0–100 each)
    # Momentum: 5% 1d move = 100pts
    mom_1d_score = min(100.0, max(0.0, ret_1d * 2000))
    # 5d: 10% move = 100pts
    mom_5d_score = min(100.0, max(0.0, ret_5d * 1000))
    # Volume spike: 3x avg = 100pts
    vol_score = min(100.0, max(0.0, (volume_ratio - 1.0) * 50))
    # RSI momentum sweet spot 50-70 → 80-100, tapers outside
    rsi_score = _rsi_to_score(rsi_val)

    composite = (
        0.35 * mom_1d_score
        + 0.25 * mom_5d_score
        + 0.25 * vol_score
        + 0.15 * rsi_score
    )

    if "High" in history.columns and "Low" in history.columns:
        highs = history["High"].values
        lows  = history["Low"].values
        atr5  = _atr(highs, lows, closes.values, period=5)
        atr20 = _atr(highs, lows, closes.values, period=20)
        atr_ratio    = round(atr5 / atr20, 3) if (atr20 > 0 and not math.isnan(atr5)) else 1.0
        high_5d      = float(history["High"].iloc[-5:].max())
        low_5d       = float(history["Low"].iloc[-5:].min())
        range_5d_pct = round((high_5d - low_5d) / spot, 4) if spot > 0 else 0.05
    else:
        atr_ratio    = 1.0
        range_5d_pct = 0.05

    return {
        "spot": spot,
        "ret_1d": ret_1d,
        "ret_5d": ret_5d,
        "volume_ratio": round(volume_ratio, 2),
        "rsi": rsi_val,
        "ma20": round(ma20, 2),
        "ma20_pct": round(ma20_pct, 4),
        "momentum_score": round(min(100.0, max(0.0, composite)), 2),
        "atr_ratio":    atr_ratio,
        "range_5d_pct": range_5d_pct,
    }


def _rsi_to_score(rsi_val: float) -> float:
    """Map RSI to a momentum quality score. Best in 50-70 range."""
    if 50 <= rsi_val <= 70:
        return 80 + (rsi_val - 50) * 1.0
    if rsi_val > 70:
        return max(0, 100 - (rsi_val - 70) * 4)
    if rsi_val >= 35:
        return (rsi_val - 35) * (80 / 15)
    return 0.0


def _atr(highs, lows, closes, period: int) -> float:
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        for i in range(1, len(highs))
    ]
    return sum(trs[-period:]) / period if len(trs) >= period else float("nan")


def _empty_score() -> dict:
    return {
        "spot": 0.0, "ret_1d": 0.0, "ret_5d": 0.0,
        "volume_ratio": 1.0, "rsi": 50.0, "ma20": 0.0,
        "ma20_pct": 0.0, "momentum_score": 0.0,
        "atr_ratio": 1.0, "range_5d_pct": 0.05,
    }
