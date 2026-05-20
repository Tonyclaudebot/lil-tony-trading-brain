import pandas as pd
from scanner.setups import find_unusual_volume


def _row(ticker="AAPL", volume=2000, oi=500, iv=0.30, last=1.50,
         strike=180.0, exp="2024-03-15", dte=30, opt_type="call",
         symbol="AAPL240315C00180000"):
    return {
        "ticker": ticker, "contractSymbol": symbol,
        "volume": volume, "openInterest": oi,
        "impliedVolatility": iv, "lastPrice": last,
        "strike": strike, "expiration": exp, "dte": dte, "type": opt_type,
    }


def test_unusual_volume_detected():
    chain = pd.DataFrame([_row(volume=2000, oi=500)])
    setups = find_unusual_volume(chain, min_volume=500, min_ratio=2.0)
    assert len(setups) == 1
    assert setups[0].setup_type == "unusual_volume"
    assert setups[0].ticker == "AAPL"


def test_volume_below_minimum_filtered():
    chain = pd.DataFrame([_row(volume=100, oi=500)])
    setups = find_unusual_volume(chain, min_volume=500, min_ratio=2.0)
    assert setups == []


def test_ratio_below_threshold_filtered():
    chain = pd.DataFrame([_row(volume=600, oi=1000)])
    setups = find_unusual_volume(chain, min_volume=500, min_ratio=2.0)
    assert setups == []


def test_empty_chain_returns_empty():
    assert find_unusual_volume(pd.DataFrame(), min_volume=500, min_ratio=2.0) == []


def test_zero_open_interest_skipped():
    chain = pd.DataFrame([_row(volume=5000, oi=0)])
    setups = find_unusual_volume(chain, min_volume=500, min_ratio=2.0)
    assert setups == []
