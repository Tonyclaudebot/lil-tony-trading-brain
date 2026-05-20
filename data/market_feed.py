"""
Market data feed — Webull only.

Stock prices / bars : Webull Open API (HMAC-SHA1 signed)
Options chains      : Webull unofficial app API
"""

import base64
import hashlib
import hmac as hmac_module
import logging
import math
import os
import socket
import time
import uuid
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Webull Open API constants ──────────────────────────────────────────────────

_WB_HOST    = "api.webull.com"
_WB_BASE    = f"https://{_WB_HOST}"
_WB_KEY     = os.getenv("WEBULL_APP_KEY", "")
_WB_SECRET  = os.getenv("WEBULL_APP_SECRET", "")

# Known ETF symbols — snapshot requires category=US_ETF for these
_ETF_SYMBOLS = frozenset({
    "SPY", "QQQ", "IWM", "GLD", "SLV", "TLT", "LQD", "HYG",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLU", "XLP", "XLRE",
    "XLC", "XLB", "XLY", "ARKK", "ARKG", "ARKW", "ARKF",
    "UVXY", "SVXY", "VXX", "VIXM", "VIXY",
    "SQQQ", "TQQQ", "SPXU", "SPXS", "SPXL", "UPRO",
    "SOXL", "SOXS", "LABU", "LABD", "FNGU", "FNGD",
    "DIA", "MDY", "VTI", "VEA", "VWO", "EFA", "EEM",
    "GDX", "GDXJ", "SLX", "USO", "UNG", "PDBC",
    "IBB", "XBI", "FXI", "KWEB", "MCHI",
    "TNA", "TZA", "FAZ", "FAS", "TECL", "TECS",
    "HIBL", "HIBS", "NAIL", "CURE", "WANT",
})

_category_cache: dict[str, str] = {}


# ── Webull Open API signing ────────────────────────────────────────────────────

def _iso_ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _nonce() -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, socket.gethostname() + str(uuid.uuid1())))


def _sign(uri: str, params: dict) -> dict:
    """Build signed headers for a Webull Open API GET request."""
    if not _WB_KEY or not _WB_SECRET:
        raise RuntimeError("WEBULL_APP_KEY / WEBULL_APP_SECRET not set in .env")

    headers: dict[str, str] = {
        "x-version":            "v1",
        "x-app-key":            _WB_KEY,
        "x-timestamp":          _iso_ts(),
        "x-signature-version":  "1.0",
        "x-signature-algorithm": "HMAC-SHA1",
        "x-signature-nonce":    _nonce(),
    }

    # Sign params = 5 signing headers (not x-version) + host + query params
    sp: dict[str, str] = {
        "x-app-key":            headers["x-app-key"],
        "x-timestamp":          headers["x-timestamp"],
        "x-signature-version":  headers["x-signature-version"],
        "x-signature-algorithm": headers["x-signature-algorithm"],
        "x-signature-nonce":    headers["x-signature-nonce"],
        "host":                 _WB_HOST,
    }
    for k, v in params.items():
        existing = sp.get(k)
        if existing is not None:
            sp[k] = str(existing) + "&" + str(v)
        else:
            sp[k] = str(v)

    from urllib.parse import quote
    sts = uri + "&" + "&".join(f"{k}={v}" for k, v in sorted(sp.items()))
    quoted = quote(sts, safe="")
    key = (_WB_SECRET + "&").encode()
    sig = hmac_module.new(key, quoted.encode(), hashlib.sha1)
    headers["x-signature"] = base64.b64encode(sig.digest()).decode().strip()
    return headers


def _wb_get(uri: str, params: dict) -> Optional[object]:
    """Make a signed GET request to the Webull Open API."""
    from urllib.parse import urlencode
    url = f"{_WB_BASE}{uri}?" + urlencode(params)
    try:
        r = requests.get(url, headers=_sign(uri, params), timeout=12)
        if r.status_code == 200:
            return r.json()
        logger.debug(f"Webull API {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        logger.debug(f"Webull API error ({uri}): {e}")
        return None


# ── Category detection ────────────────────────────────────────────────────────

def _get_category(ticker: str) -> str:
    """Return 'US_STOCK' or 'US_ETF' for a ticker (cached)."""
    if ticker in _category_cache:
        return _category_cache[ticker]
    category = "US_ETF" if ticker in _ETF_SYMBOLS else "US_STOCK"
    # Try the detected category; if it fails, flip and try the other
    data = _wb_get("/market-data/snapshot", {"symbols": ticker, "category": category})
    if data and isinstance(data, list):
        _category_cache[ticker] = category
        return category
    other = "US_STOCK" if category == "US_ETF" else "US_ETF"
    data2 = _wb_get("/market-data/snapshot", {"symbols": ticker, "category": other})
    if data2 and isinstance(data2, list):
        _category_cache[ticker] = other
        return other
    _category_cache[ticker] = "US_STOCK"
    return "US_STOCK"


# ── Webull unofficial client ──────────────────────────────────────────────────

_wb_unofficial: Optional[object] = None


def _wb_client():
    global _wb_unofficial
    if _wb_unofficial is None:
        from webull import webull
        _wb_unofficial = webull()
    return _wb_unofficial


# ── Strike helpers (same as before) ───────────────────────────────────────────

def _strike_increment(spot: float) -> float:
    if spot < 25:  return 0.5
    if spot < 50:  return 1.0
    if spot < 200: return 2.5
    return 5.0


def _estimate_option_price(spot: float, strike: float, dte: int, iv: float, opt_type: str) -> float:
    """Simplified Black-Scholes ATM approximation for options without real price data."""
    t = max(dte / 252.0, 0.001)
    if iv <= 0:
        iv = 0.30
    try:
        d1 = (math.log(spot / strike) + 0.5 * iv**2 * t) / (iv * math.sqrt(t))
        d2 = d1 - iv * math.sqrt(t)
        # Normal CDF via error function
        def _ncdf(x: float) -> float:
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))
        if opt_type == "call":
            price = spot * _ncdf(d1) - strike * _ncdf(d2)
        else:
            price = strike * _ncdf(-d2) - spot * _ncdf(-d1)
        return max(0.01, round(price, 2))
    except Exception:
        return 0.50


# ── Public API ────────────────────────────────────────────────────────────────

def get_spot_price(ticker: str) -> float | None:
    """Return the most recent trade price via Webull Open API."""
    try:
        category = _get_category(ticker)
        data = _wb_get("/market-data/snapshot", {"symbols": ticker, "category": category})
        if data and isinstance(data, list) and data:
            return float(data[0]["price"])
        return None
    except Exception as e:
        logger.error(f"Spot price failed for {ticker}: {e}")
        return None


def _parse_wb_bars(raw_bars: list, tz: str = "America/New_York") -> pd.DataFrame:
    """Convert Webull bar list to OHLCV DataFrame with tz-aware DatetimeIndex."""
    import pytz
    tz_obj = pytz.timezone(tz)
    rows = []
    for b in raw_bars:
        try:
            ts = datetime.fromisoformat(b["time"].replace("+0000", "+00:00"))
            ts_local = ts.astimezone(tz_obj)
            rows.append({
                "Date":   ts_local,
                "Open":   float(b.get("open", 0)),
                "High":   float(b.get("high", 0)),
                "Low":    float(b.get("low", 0)),
                "Close":  float(b.get("close", 0)),
                "Volume": int(b.get("volume", 0)),
            })
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("Date")
    df.index.name = "Date"
    return df.dropna(subset=["Close"]).sort_index()


def _fetch_wb_daily_bars(ticker: str, count: int = 40) -> pd.DataFrame:
    """Fetch daily OHLCV bars from Webull Open API."""
    try:
        category = _get_category(ticker)
        data = _wb_get("/market-data/bars", {
            "symbol":   ticker,
            "category": category,
            "timespan": "D",
            "count":    str(count),
        })
        if not data or not isinstance(data, list):
            return pd.DataFrame()
        return _parse_wb_bars(data)
    except Exception as e:
        logger.debug(f"Webull daily bars failed for {ticker}: {e}")
        return pd.DataFrame()


def batch_download_history(tickers: list[str], period: str = "1mo") -> dict[str, pd.DataFrame]:
    """Download daily OHLCV for all tickers via Webull Open API."""
    if not tickers:
        return {}
    count = 40 if "1mo" in period else 15
    result: dict[str, pd.DataFrame] = {}
    for i, ticker in enumerate(tickers):
        df = _fetch_wb_daily_bars(ticker, count=count)
        if not df.empty:
            result[ticker] = df
        else:
            logger.debug(f"No Webull bars for {ticker}")
        if i < len(tickers) - 1:
            time.sleep(0.3)
    logger.info(f"Webull history: {len(result)}/{len(tickers)} tickers loaded")
    return result


def get_price_history(ticker: str, days: int = 35) -> pd.DataFrame | None:
    """Return daily OHLCV bars for the past `days` calendar days, or None."""
    # Convert calendar days to approximate trading sessions
    trading_count = max(10, int(days * 0.7))
    df = _fetch_wb_daily_bars(ticker, count=trading_count)
    return df if not df.empty else None


def get_options_chain(
    ticker: str,
    max_dte: int = 45,
    min_dte: int = 1,
    n_strikes: int = 5,
) -> pd.DataFrame:
    """
    Fetch options chain via the Webull app API.

    The nearest active expiration returns real market data (IV, price, volume, OI).
    Further expirations return contract metadata only — those rows will have
    volume=0 so strategies (which filter volume>0) ignore them for trading.
    """
    try:
        wb = _wb_client()
        spot = get_spot_price(ticker)
        if not spot:
            logger.debug(f"No spot price for {ticker} — skipping options")
            return pd.DataFrame()

        headers = wb.build_req_headers()
        ticker_id = wb.get_ticker(ticker)
        url = wb._urls.options_exp_dat_new()
        resp = requests.post(
            url,
            json={"count": -1, "direction": "all", "tickerId": ticker_id},
            headers=headers,
            timeout=15,
        )
        res = resp.json()
        if "expireDateList" not in res:
            logger.debug(f"No expireDateList for {ticker}")
            return pd.DataFrame()

        # ATM strike window
        inc = _strike_increment(spot)
        atm = round(spot / inc) * inc

        # vol1y as fallback IV (annualized historical vol, e.g. 0.32 = 32%)
        vol1y = float(res.get("vol1y", 0.30))
        ref_iv: Optional[float] = None  # set from first expiration that has IV data

        rows = []
        today = date.today()

        for entry in res["expireDateList"]:
            days = entry["from"]["days"]
            exp_date = entry["from"]["date"]

            if days < min_dte or days > max_dte:
                continue

            for contract in entry["data"]:
                strike = float(contract["strikePrice"])
                opt_type = contract["direction"]  # 'call' or 'put'

                # ATM ± n_strikes filter
                if abs(strike - atm) > n_strikes * inc * 1.5:
                    continue

                has_full = "impVol" in contract and contract.get("close")

                if has_full:
                    iv = float(contract["impVol"])
                    if ref_iv is None:
                        ref_iv = iv
                    last_price = float(contract.get("close") or contract.get("preClose") or 0)
                    volume = int(contract.get("volume", 0))
                    oi = int(contract.get("openInterest", 0))
                else:
                    # Metadata-only — estimated values (won't be traded: volume=0)
                    iv = ref_iv if ref_iv is not None else vol1y
                    last_price = _estimate_option_price(spot, strike, days, iv, opt_type)
                    volume = 0
                    oi = 0

                in_the_money = (strike <= spot) if opt_type == "call" else (strike >= spot)
                rows.append({
                    "contractSymbol":    contract.get("symbol", ""),
                    "type":              opt_type,
                    "strike":            strike,
                    "expiration":        exp_date,
                    "dte":               days,
                    "ticker":            ticker,
                    "lastPrice":         last_price,
                    "volume":            volume,
                    "openInterest":      oi,
                    "impliedVolatility": iv,
                    "inTheMoney":        in_the_money,
                })

        if not rows:
            logger.debug(f"No option contracts found for {ticker}")
            return pd.DataFrame()

        return pd.DataFrame(rows)

    except Exception as e:
        logger.error(f"Options chain failed for {ticker}: {e}")
        return pd.DataFrame()


def get_current_contract_price(
    ticker: str,
    expiration: str,
    contract_symbol: str,
) -> float | None:
    """
    Look up the current last price for a specific options contract.
    Uses the app API's nearest-expiration data if the contract is in the chain.
    """
    try:
        chain = get_options_chain(ticker, min_dte=0, max_dte=5)
        if chain.empty:
            return None
        match = chain[chain["contractSymbol"] == contract_symbol]
        if not match.empty:
            return float(match.iloc[0]["lastPrice"])
        return None
    except Exception as e:
        logger.debug(f"Contract price lookup failed for {contract_symbol}: {e}")
        return None


def get_intraday_bars(
    ticker: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[dict]:
    """
    Fetch 1-minute bars for ticker between from_dt and to_dt (tz-aware datetimes).
    Returns list of {high, low, close, volume, timestamp_ms}.
    """
    try:
        # How many M1 bars back from now to cover the from_dt window?
        now_utc = datetime.now(timezone.utc)
        from_utc = from_dt.astimezone(timezone.utc)
        to_utc = to_dt.astimezone(timezone.utc)
        minutes_back = max(int((now_utc - from_utc).total_seconds() / 60) + 5, 90)
        count = min(minutes_back, 390)  # cap at full trading day

        category = _get_category(ticker)
        data = _wb_get("/market-data/bars", {
            "symbol":   ticker,
            "category": category,
            "timespan": "M1",
            "count":    str(count),
        })
        if not data or not isinstance(data, list):
            return []

        result = []
        for bar in data:
            try:
                ts = datetime.fromisoformat(bar["time"].replace("+0000", "+00:00"))
                if from_utc <= ts <= to_utc:
                    result.append({
                        "high":         float(bar["high"]),
                        "low":          float(bar["low"]),
                        "close":        float(bar["close"]),
                        "volume":       int(bar.get("volume", 0)),
                        "timestamp_ms": int(ts.timestamp() * 1000),
                    })
            except Exception:
                continue

        return sorted(result, key=lambda b: b["timestamp_ms"])

    except Exception as e:
        logger.debug(f"Intraday bars failed for {ticker}: {e}")
        return []
