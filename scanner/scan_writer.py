import json
import os
from datetime import datetime, timezone

_STATUS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scan_status.json'))
_ALERTS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'alerts.json'))
_state: dict = {}


def _write() -> None:
    _state['last_updated'] = datetime.now(timezone.utc).isoformat()
    tmp = _STATUS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(_state, f)
    os.replace(tmp, _STATUS_FILE)


def _write_alerts() -> None:
    payload = {
        'alerts': _state.get('alerts', []),
        'top_picks': _state.get('top_picks', []),
        'scan_status': _state.get('status', 'idle'),
        'total_tickers': _state.get('total_tickers', 0),
        'scan_finished_at': _state.get('finished_at'),
        'last_updated': _state.get('last_updated', datetime.now(timezone.utc).isoformat()),
        'prices': _state.get('prices', {}),
    }
    tmp = _ALERTS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, _ALERTS_FILE)


def _fetch_price(ticker: str):
    try:
        from tradingview_ta import TA_Handler, Interval  # noqa
        for exchange in ('NASDAQ', 'NYSE', 'AMEX'):
            try:
                h = TA_Handler(
                    symbol=ticker,
                    screener='america',
                    exchange=exchange,
                    interval=Interval.INTERVAL_1_MINUTE,
                )
                return round(float(h.get_analysis().indicators['close']), 2)
            except Exception:
                continue
    except ImportError:
        pass
    return None


def update_prices(tickers: list) -> None:
    """Fetch current prices for the given tickers and persist to alerts.json."""
    prices = {}
    for ticker in tickers:
        price = _fetch_price(ticker)
        if price is not None:
            prices[ticker] = price
    _state['prices'] = prices
    _write_alerts()


def start_scan(total: int) -> None:
    global _state
    _state = {
        'status': 'scanning',
        'total_tickers': total,
        'current_index': 0,
        'pct_complete': 0,
        'current_ticker': '',
        'current_strategy': '',
        'current_score': 0,
        'tickers_done': [],
        'alerts': [],
        'top_picks': [],
        'finished_at': None,
    }
    _write()


def update_scan(index: int, total: int, ticker: str, strategy: str, score: float) -> None:
    prev = _state.get('current_ticker', '')
    if prev and prev not in _state['tickers_done']:
        _state['tickers_done'].append(prev)
    _state.update({
        'current_index': index,
        'total_tickers': total,
        'pct_complete': round(index / total * 100) if total else 0,
        'current_ticker': ticker,
        'current_strategy': strategy,
        'current_score': round(float(score), 1),
    })
    _write()


def add_alert(data: dict) -> None:
    _state.setdefault('alerts', []).append(data)
    _write()
    _write_alerts()


def finish_scan(top_picks: list) -> None:
    last = _state.get('current_ticker', '')
    if last and last not in _state['tickers_done']:
        _state['tickers_done'].append(last)
    _state.update({
        'status': 'idle',
        'top_picks': top_picks,
        'finished_at': datetime.now(timezone.utc).isoformat(),
        'current_ticker': '',
        'current_strategy': '',
        'current_score': 0,
        'pct_complete': 100,
    })
    _write()
    refresh_prices()  # fetches prices for phase4 tickers and writes alerts.json


def set_phase4_alerts(alert_list: list) -> None:
    """Persist Phase 4 fired alerts into alerts.json, then rebuild the active/history split."""
    try:
        with open(_ALERTS_FILE) as _f:
            existing = json.load(_f)
    except Exception:
        existing = {}
    existing['phase4_alerts'] = alert_list
    existing['last_updated'] = datetime.now(timezone.utc).isoformat()
    tmp = _ALERTS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(existing, f, indent=2)
    os.replace(tmp, _ALERTS_FILE)
    publish_trades()


def publish_trades() -> None:
    """
    Read logs/alerts.jsonl, split by expiration date into active vs history,
    and write both arrays to alerts.json['trades'].
    Active  = expiration >= today (contract still live).
    History = expiration <  today (contract expired).
    """
    from datetime import date as _date
    log_path = os.path.join(os.path.dirname(_ALERTS_FILE), 'logs', 'alerts.jsonl')
    trades: list[dict] = []
    try:
        with open(log_path) as lf:
            for line in lf:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    except Exception:
        pass

    today = _date.today().isoformat()
    active  = [t for t in trades if (t.get('expiration') or '9999') >= today]
    history = [t for t in trades if (t.get('expiration') or '9999') <  today]

    try:
        with open(_ALERTS_FILE) as _f:
            existing = json.load(_f)
    except Exception:
        existing = {}

    existing['trades'] = {'active': active, 'history': history}
    existing['last_updated'] = datetime.now(timezone.utc).isoformat()

    tmp = _ALERTS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(existing, f, indent=2)
    os.replace(tmp, _ALERTS_FILE)


def refresh_prices() -> None:
    """Re-fetch prices for active phase4 tickers and update alerts.json."""
    try:
        with open(_ALERTS_FILE) as _f:
            existing = json.load(_f)
        tickers = list({a['ticker'] for a in existing.get('phase4_alerts', [])})
        if tickers:
            update_prices(tickers)
            return
    except Exception:
        pass
    _write_alerts()
