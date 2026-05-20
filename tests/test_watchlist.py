from unittest.mock import patch

from data.watchlist import build_scan_universe, fetch_dynamic_tickers


# ── build_scan_universe ───────────────────────────────────────────────────────

def test_watchlist_comes_first():
    result = build_scan_universe(
        base=["AAA", "BBB"],
        watchlist=["ZZZ"],
        dynamic=["YYY"],
    )
    assert result[0] == "ZZZ"


def test_dynamic_comes_before_base():
    result = build_scan_universe(
        base=["AAA", "BBB"],
        watchlist=[],
        dynamic=["DYN"],
    )
    assert result.index("DYN") < result.index("AAA")


def test_deduplication():
    result = build_scan_universe(
        base=["AAPL", "MSFT"],
        watchlist=["AAPL"],
        dynamic=["MSFT"],
    )
    assert result.count("AAPL") == 1
    assert result.count("MSFT") == 1


def test_empty_dynamic_returns_watchlist_plus_base():
    result = build_scan_universe(
        base=["A", "B"],
        watchlist=["C"],
        dynamic=[],
    )
    assert set(result) == {"A", "B", "C"}


def test_lowercase_tickers_normalized():
    result = build_scan_universe(base=[], watchlist=["aapl"], dynamic=["nvda"])
    assert "AAPL" in result
    assert "NVDA" in result


def test_all_empty_returns_empty():
    assert build_scan_universe([], [], []) == []


# ── fetch_dynamic_tickers ─────────────────────────────────────────────────────

def _mock_screener(symbols: list[str]):
    quotes = [{"symbol": s} for s in symbols]
    return {"finance": {"result": [{"quotes": quotes}]}}


def test_fetch_combines_screeners():
    import json
    from unittest.mock import MagicMock

    def fake_urlopen(req, timeout=None, **kwargs):
        scr_id = req.full_url.split("scrIds=")[1].split("&")[0]
        mapping = {
            "most_actives": ["SPY", "QQQ", "AAPL"],
            "day_gainers":  ["NVDA", "AMD"],
            "day_losers":   ["META", "TSLA"],
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(_mock_screener(mapping.get(scr_id, []))).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("data.watchlist.urllib.request.urlopen", side_effect=fake_urlopen):
        result = fetch_dynamic_tickers(n=10)

    assert "SPY" in result
    assert "NVDA" in result
    assert "META" in result
    assert len(result) <= 10


def test_fetch_deduplicates_across_screeners():
    import json
    from unittest.mock import MagicMock

    def fake_urlopen(req, timeout=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(_mock_screener(["AAPL", "MSFT"])).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("data.watchlist.urllib.request.urlopen", side_effect=fake_urlopen):
        result = fetch_dynamic_tickers(n=20)

    assert result.count("AAPL") == 1
    assert result.count("MSFT") == 1


def test_fetch_returns_empty_on_total_failure():
    with patch("data.watchlist.urllib.request.urlopen", side_effect=Exception("timeout")):
        result = fetch_dynamic_tickers()
    assert result == []


def test_fetch_partial_failure_uses_successful_screeners():
    import json
    from unittest.mock import MagicMock

    call_count = 0

    def fake_urlopen(req, timeout=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("screener down")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(_mock_screener(["NVDA", "AMD"])).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("data.watchlist.urllib.request.urlopen", side_effect=fake_urlopen):
        result = fetch_dynamic_tickers(n=10)

    assert "NVDA" in result


# ── _should_refresh_dynamic (via main) ───────────────────────────────────────

def test_should_refresh_when_no_prior_refresh():
    from datetime import date, datetime, time as dtime
    from unittest.mock import patch
    import pytz
    from main import _should_refresh_dynamic

    eastern = pytz.timezone("America/New_York")
    mock_now = eastern.localize(datetime.combine(date.today(), dtime(9, 5)))
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_now
        mock_dt.combine = datetime.combine
        assert _should_refresh_dynamic(None) is True


def test_should_not_refresh_twice_same_day():
    from datetime import date, datetime, time as dtime
    from unittest.mock import patch
    import pytz
    from main import _should_refresh_dynamic

    eastern = pytz.timezone("America/New_York")
    today = date.today()
    mock_now = eastern.localize(datetime.combine(today, dtime(10, 0)))
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_now
        mock_dt.combine = datetime.combine
        assert _should_refresh_dynamic(today) is False


def test_should_not_refresh_before_9am():
    from datetime import date, datetime, time as dtime
    from unittest.mock import patch
    import pytz
    from main import _should_refresh_dynamic

    eastern = pytz.timezone("America/New_York")
    mock_now = eastern.localize(datetime.combine(date.today(), dtime(8, 55)))
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_now
        mock_dt.combine = datetime.combine
        assert _should_refresh_dynamic(None) is False
