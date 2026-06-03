"""
paper_trading/engine.py — local paper-trade ledger.

Simulated options positions only. Nothing here ever contacts a broker or
places a real order (House Rules T1: paper by default).

  open_trade(trade)                       -> open a sized position under the $100 cap
  close_trade(ticker, exit_price, outcome) -> settle a position and compute P&L

State lives in data/paper_trades.json. The ledger is touched by phase4_alerts
(main thread, on open) and the tracker daemon (on close), so writes are locked.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "paper_trades.json"

MAX_TRADE_USD        = 100.0   # T2 position-size cap
_CONTRACT_MULTIPLIER = 100     # one option contract = 100 shares

_lock = threading.Lock()


def _load() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    try:
        return json.loads(LEDGER_PATH.read_text())
    except Exception:
        logger.warning("paper_trades.json malformed — starting fresh ledger")
        return []


def _save(trades: list[dict]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(trades, indent=2))
    tmp.replace(LEDGER_PATH)


def _to_float(v) -> float:
    """Coerce a price that may be a float or a '$1.23' string to float."""
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace("$", "").replace(",", "").strip())


def open_trade(trade: dict) -> dict | None:
    """
    Record a paper trade. Returns the stored record, or None if rejected
    (size cap exceeded, duplicate position, or bad input).

    Expected keys: ticker, opt_type ('call'/'put') or direction ('CALL'/'PUT'),
    entry, stop, target, strike, expiration (ISO 'YYYY-MM-DD'), spot, strategy.
    Prices may be floats or '$'-prefixed strings.
    """
    try:
        ticker     = trade["ticker"]
        opt_type   = (trade.get("opt_type") or trade.get("direction") or "call").lower()
        opt_type   = "put" if opt_type.startswith("p") else "call"
        direction  = "PUT" if opt_type == "put" else "CALL"
        entry      = _to_float(trade["entry"])
        stop       = _to_float(trade["stop"])
        target     = _to_float(trade["target"])
        strike     = _to_float(trade["strike"])
        expiration = trade.get("expiration") or ""
        spot       = _to_float(trade.get("spot", 0) or 0)
        strategy   = trade.get("strategy") or trade.get("strategy_key") or "unknown"
        strat_name = trade.get("strategy_name", strategy)
        contract   = trade.get("contract") or f"{ticker} {strike:g}{direction[0]}"
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"open_trade: bad trade dict ({e}) — not opening")
        return None

    if entry <= 0:
        logger.error(f"open_trade: {ticker} entry={entry} <= 0 — not opening")
        return None

    # T2: position sizing under the $100 cap
    cost_per_contract = entry * _CONTRACT_MULTIPLIER
    contracts = int(MAX_TRADE_USD // cost_per_contract)
    if contracts < 1:
        logger.warning(
            f"open_trade: {ticker} {contract} — 1 contract costs "
            f"${cost_per_contract:.2f} > ${MAX_TRADE_USD:.0f} cap, skipping"
        )
        return None
    size = round(contracts * cost_per_contract, 2)

    with _lock:
        trades = _load()
        # T2: duplicate-order guard — one open position per contract
        for t in trades:
            if (t.get("outcome") == "open" and t.get("ticker") == ticker
                    and t.get("strike") == strike and t.get("opt_type") == opt_type
                    and t.get("expiration") == expiration):
                logger.info(f"open_trade: {ticker} {contract} already open — skipping duplicate")
                return None

        record = {
            "id":            uuid.uuid4().hex[:12],
            "ticker":        ticker,
            "direction":     direction,
            "opt_type":      opt_type,
            "strike":        strike,
            "expiration":    expiration,
            "entry":         entry,
            "stop":          stop,
            "target":        target,
            "spot_at_open":  spot,
            "implied_vol":   None,    # solved lazily by the tracker on first poll
            "peak_profit_pct": 0.0,   # ratcheted up by the tracker; trails kick in at >= 30
            "contracts":     contracts,
            "size":          size,
            "open_time":     datetime.now(timezone.utc).isoformat(),
            "exit_price":    None,
            "exit_time":     None,
            "pnl":           None,
            "outcome":       "open",
            "strategy":      strategy,
            "strategy_name": strat_name,
            "contract":      contract,
        }
        trades.append(record)
        _save(trades)

    logger.info(
        f"PAPER OPEN  {ticker} {direction} {strike:g} x{contracts} "
        f"@ ${entry:.2f} (${size:.2f}) stop ${stop:.2f} target ${target:.2f}"
    )
    return record


def close_trade(ticker: str, exit_price: float, outcome: str,
                contract_id: str | None = None) -> dict | None:
    """
    Settle the open paper trade for `ticker` (or a specific one via contract_id).
    outcome: 'win' | 'loss' | 'expired_worthless'. Returns the closed record,
    or None if no matching open trade was found.
    """
    outcome = outcome.lower()
    with _lock:
        trades = _load()
        match = None
        for t in trades:
            if t.get("outcome") != "open" or t.get("ticker") != ticker:
                continue
            if contract_id is not None and t.get("id") != contract_id:
                continue
            match = t
            break
        if match is None:
            logger.warning(
                f"close_trade: no open trade for {ticker}"
                f"{' id=' + contract_id if contract_id else ''}"
            )
            return None

        exit_price = float(exit_price)
        contracts  = match.get("contracts", 1)
        match["exit_price"] = round(exit_price, 4)
        match["exit_time"]  = datetime.now(timezone.utc).isoformat()
        match["pnl"]        = round((exit_price - match["entry"]) * _CONTRACT_MULTIPLIER * contracts, 2)
        match["outcome"]    = outcome
        _save(trades)

    logger.info(
        f"PAPER CLOSE {ticker} {match['direction']} {match['strike']:g} "
        f"{outcome.upper()} exit ${exit_price:.2f} pnl ${match['pnl']:+.2f}"
    )
    return match


def set_implied_vol(contract_id: str, iv: float) -> None:
    """Persist the IV the tracker solved from the entry premium (one-time cache)."""
    with _lock:
        trades = _load()
        for t in trades:
            if t.get("id") == contract_id:
                t["implied_vol"] = iv
                _save(trades)
                return


def set_peak_profit_pct(contract_id: str, pct: float) -> None:
    """Persist the running peak profit % (trailing-stop ratchet — only moves up)."""
    with _lock:
        trades = _load()
        for t in trades:
            if t.get("id") == contract_id:
                t["peak_profit_pct"] = round(float(pct), 4)
                _save(trades)
                return


def get_open_trades() -> list[dict]:
    return [t for t in _load() if t.get("outcome") == "open"]


def get_all_trades() -> list[dict]:
    return _load()
