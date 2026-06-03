"""
outcome_tracker.py — Stock-proxy auto-grader for Lil Tony alerts.

Polls Polygon every 5 minutes. For each open alert in logs/alerts.jsonl,
walks the UNDERLYING stock's daily High/Low from send time forward and
re-prices the option each day with Black-Scholes (constant IV solved
from the entry premium). Wick rule: estimated option High >= target -> WIN,
estimated option Low <= stop -> LOSS. Theta decay is baked in via the
T-to-expiry term shrinking over time.

This grader is an ESTIMATE, not a confirmed fill. Caveats:
  - Constant IV — vol crush around binary events is not modeled
  - No bid/ask, no slippage
  - Daily H/L only (Polygon free tier blocks intraday option + stock minute)
  - Linear path assumption within a single trading day

Outcomes (existing schema, T10 preserved):
  WIN | LOSS | expired_worthless | open
Plus new field outcome_method = 'stock_proxy_bs_reprice' so any consumer
can tell these from manual grades.

Does NOT modify logs/alerts.jsonl. State goes in trade_outcomes.json.
Pushes only trade_outcomes.json + scoreboard.html to origin/main.

Run:
  python3 outcome_tracker.py                     # foreground, 5-min loop
  python3 outcome_tracker.py --once              # one pass then exit
  python3 outcome_tracker.py --once --dry-run    # one pass, no writes, no push
"""
from __future__ import annotations

import json
import logging
import math
import os
import signal
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas_market_calendars as mcal
import pytz
from dotenv import load_dotenv
load_dotenv()

from paper_trading import engine as paper_engine
from brain import learner

# ── Paths ─────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent
ALERTS_LOG    = ROOT / "logs" / "alerts.jsonl"      # READ-ONLY
OUTCOMES_FILE = ROOT / "trade_outcomes.json"         # state file
SCOREBOARD    = ROOT / "scoreboard.html"

# ── Config ────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 5 * 60
RISK_FREE_RATE        = 0.05          # ~current Fed rate, good enough for proxy
PACING_SECONDS        = 1             # Webull Open API — no restrictive rate limit
PUSH_FILES            = ["trade_outcomes.json", "scoreboard.html"]
_CENTRAL              = pytz.timezone("America/Chicago")
_FORCE_GRADE_HOUR_CT  = 15   # 3 PM CT
_FORCE_GRADE_MIN_CT   = 0    # :00 → 3:00 PM CT (options close = 4 PM ET)

# ── Market-calendar helpers ───────────────────────────────────────────────
_nyse = mcal.get_calendar("NYSE")
_ltd_cache: dict[date, date | None] = {}   # ref_week_monday → last trading day


def last_trading_day_of_week(ref: date) -> date | None:
    """
    Return the last NYSE trading day in the same Mon–Sun week as `ref`.
    Results are cached per week so the calendar is only queried once.
    """
    monday = ref - timedelta(days=ref.weekday())
    if monday in _ltd_cache:
        return _ltd_cache[monday]
    sunday = monday + timedelta(days=6)
    try:
        schedule = _nyse.schedule(start_date=str(monday), end_date=str(sunday))
        result = schedule.index[-1].date() if not schedule.empty else None
    except Exception:
        result = None
    _ltd_cache[monday] = result
    return result


def in_force_grade_window() -> bool:
    """
    True when it is currently >= 3:00 PM CT on the last NYSE trading day
    of this calendar week.
    """
    now_ct = datetime.now(_CENTRAL)
    today  = now_ct.date()
    ltd    = last_trading_day_of_week(today)
    if ltd is None or today != ltd:
        return False
    return (now_ct.hour, now_ct.minute) >= (_FORCE_GRADE_HOUR_CT, _FORCE_GRADE_MIN_CT)


# ── Logging ───────────────────────────────────────────────────────────────
# StreamHandler only — launchd captures stdout to logs/outcome-tracker.log
# (StandardOutPath in the plist). Using both FileHandler + StreamHandler on
# the same file caused duplicate lines when running under launchd.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("tracker")

class _WbBar:
    """Minimal bar object wrapping a Webull API bar dict."""
    __slots__ = ("timestamp", "open", "high", "low", "close", "volume")
    def __init__(self, d: dict):
        from datetime import datetime, timezone as _tz
        ts_dt = datetime.fromisoformat(d["time"].replace("+0000", "+00:00"))
        self.timestamp = int(ts_dt.timestamp() * 1000)
        self.open  = float(d.get("open",  0))
        self.high  = float(d.get("high",  0))
        self.low   = float(d.get("low",   0))
        self.close = float(d.get("close", 0))
        self.volume = int(d.get("volume", 0))


# ── Black-Scholes (self-contained, no scipy needed) ──────────────────────
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_price(S: float, K: float, T: float, r: float, sigma: float, opt_type: str) -> float:
    if T <= 0:
        return max(0.0, (S - K) if opt_type == "call" else (K - S))
    if sigma <= 0:
        # Degenerate — return intrinsic discounted
        return max(0.0, (S - K * math.exp(-r * T)) if opt_type == "call"
                                                   else (K * math.exp(-r * T) - S))
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if opt_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def implied_vol(S: float, K: float, T: float, r: float, premium: float, opt_type: str) -> float | None:
    """Bisection IV solver. Returns None if no root in [1%, 500%] range."""
    if T <= 0 or premium <= 0:
        return None
    lo, hi = 0.005, 5.0
    p_lo = bs_price(S, K, T, r, lo, opt_type)
    p_hi = bs_price(S, K, T, r, hi, opt_type)
    if premium < p_lo or premium > p_hi:
        # Premium outside BS range with these inputs (likely arbitrage-violating)
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        pm = bs_price(S, K, T, r, mid, opt_type)
        if abs(pm - premium) < 1e-5 or (hi - lo) < 1e-5:
            return mid
        if pm < premium:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ── State ─────────────────────────────────────────────────────────────────
def load_alerts() -> list[dict]:
    if not ALERTS_LOG.exists():
        return []
    return [json.loads(l) for l in ALERTS_LOG.read_text().splitlines() if l.strip()]


def load_outcomes() -> dict:
    if not OUTCOMES_FILE.exists():
        return {}
    try:
        return json.loads(OUTCOMES_FILE.read_text())
    except Exception:
        log.warning("trade_outcomes.json malformed — starting fresh state")
        return {}


def save_outcomes(state: dict) -> None:
    OUTCOMES_FILE.write_text(json.dumps(state, indent=2))


def _init_entry(alert: dict) -> dict:
    return {
        "ticker":     alert["ticker"],
        "contract":   alert["contract"],
        "strategy":   alert.get("strategy_name", "Unknown"),
        "opt_type":   alert["opt_type"],
        "strike":     float(alert["strike"]),
        "expiration": alert.get("expiration"),
        "sent_at":    alert["timestamp"],
        "spot_at_send": float(alert.get("spot", 0)),
        "entry":      float(alert.get("entry", 0)),
        "target":     float(alert["target"]),
        "stop":       float(alert["stop"]),
        "outcome":            "open",
        "outcome_method":     "stock_proxy_bs_reprice",
        "exit_price":         None,
        "graded_at":          None,
        "outcome_at":         None,     # date the proxy triggered
        "implied_vol":        None,
        "last_underlying":    None,
        "last_polled_at":     None,
        "poll_count":         0,
    }


# ── Webull stock fetch ────────────────────────────────────────────────────
def fetch_stock_bars(ticker: str, start: date, end: date) -> list:
    """Daily bars in [start, end]. Returns list of _WbBar-compatible objects."""
    from data.market_feed import _wb_get, _get_category
    try:
        count = (end - start).days + 5
        category = _get_category(ticker)
        data = _wb_get("/market-data/bars", {
            "symbol":   ticker,
            "category": category,
            "timespan": "D",
            "count":    str(min(count, 365)),
        })
        if not data or not isinstance(data, list):
            return []
        bars = [_WbBar(d) for d in data]
        start_ms = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
        end_ms   = int(datetime(end.year,   end.month,   end.day,   23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
        return [b for b in bars if start_ms <= b.timestamp <= end_ms]
    except Exception as e:
        log.warning(f"  Webull daily fetch failed for {ticker}: {e}")
        return []


# ── Grading core ─────────────────────────────────────────────────────────
def _bar_date(b) -> date:
    """Polygon bar timestamp is ms-since-epoch UTC. Convert to ET-ish date."""
    return datetime.fromtimestamp(b.timestamp / 1000, tz=timezone.utc).date()


# Display name (as stored in trade_outcomes.json) -> learner strategy key.
_STRATEGY_NAME_TO_KEY = {
    "Momentum Breakout":        "momentum_breakout",
    "Mean Reversion":           "mean_reversion",
    "Unusual Options Activity": "unusual_options_activity",
    "Volatility Breakout":      "volatility_breakout",
}


def _feed_learner(entry: dict) -> None:
    """Train the strategy learner on a freshly-graded alert.

    This grader is the single source of truth for the learner — the paper
    tracker no longer feeds it, to avoid double-counting the same signal.
    """
    key = _STRATEGY_NAME_TO_KEY.get(entry.get("strategy", ""))
    if not key:
        return
    o = (entry.get("outcome") or "").upper()
    if o == "WIN":
        outcome = "win"
    elif o == "LOSS":
        outcome = "loss"
    elif o == "EXPIRED_WORTHLESS":
        outcome = "expired_worthless"
    else:
        return
    try:
        learner.record_outcome(key, outcome)
    except Exception as e:
        log.warning(f"  learner feed failed for {key}: {e}")


def grade_via_stock_proxy(entry: dict, bars: list, force_grade: bool = False) -> bool:
    """
    Walk daily stock bars after send date and BS-reprice the option.
    Return True if outcome changed to terminal this pass.

    force_grade: when True and the contract expires this week, settle it
    immediately using the last available bar (3:55 PM CT force-close rule).
    """
    if entry["outcome"] != "open":
        return False

    K        = entry["strike"]
    target   = entry["target"]
    stop     = entry["stop"]
    opt_type = entry["opt_type"]
    exp_str  = entry.get("expiration") or ""
    if not exp_str:
        return False
    exp = date.fromisoformat(exp_str)

    spot0 = entry["spot_at_send"]
    prem0 = entry["entry"]
    send_dt = datetime.fromisoformat(entry["sent_at"])
    # Use full-precision time-to-expiry against 4pm ET close (21:00 UTC).
    # Day-level rounding zeroed out 0-DTE alerts and broke IV solving.
    exp_close_utc = datetime(exp.year, exp.month, exp.day, 21, 0, tzinfo=timezone.utc)
    T0 = max((exp_close_utc - send_dt).total_seconds() / (365.25 * 86400), 1e-6)

    # Solve IV from entry premium (one-time)
    iv = entry.get("implied_vol")
    if iv is None:
        iv = implied_vol(spot0, K, T0, RISK_FREE_RATE, prem0, opt_type)
        if iv is None:
            log.warning(f"  IV solve failed for {entry['contract']} — entry premium "
                        f"${prem0} outside BS range. Marking outcome=unsolvable")
            entry["outcome_method"] = "stock_proxy_unsolvable"
            return False
        entry["implied_vol"] = iv

    today = date.today()
    last_underlying = None

    # Force-grade: settle at 3:55 PM CT on the last trading day of expiry week.
    if force_grade:
        ltd = last_trading_day_of_week(exp)
        if ltd is not None and exp <= ltd:
            # Use last available bar's close as the underlying settlement price
            settle_bar = bars[-1] if bars else None
            settle_price = settle_bar.close if settle_bar else None
            if settle_price is not None:
                T_rem = 0.0  # treat as expiration
                if opt_type == "call":
                    est_val = bs_price(settle_price, K, T_rem, RISK_FREE_RATE, iv, "call")
                else:
                    est_val = bs_price(settle_price, K, T_rem, RISK_FREE_RATE, iv, "put")
                if est_val >= target:
                    outcome, exit_p = "WIN", target
                elif est_val <= stop:
                    outcome, exit_p = "LOSS", stop
                else:
                    outcome, exit_p = "expired_worthless", round(est_val, 4)
                entry["outcome"]         = outcome
                entry["exit_price"]      = exit_p
                entry["outcome_at"]      = ltd.isoformat()
                entry["graded_at"]       = datetime.now(tz=timezone.utc).isoformat()
                entry["last_underlying"] = settle_price
                entry["outcome_method"]  = "force_grade_last_trading_day"
                log.info(f"  FORCE-GRADED {outcome:16} {entry['contract']} "
                         f"(last trading day {ltd}, settle ${settle_price:.2f} "
                         f"→ est ${est_val:.2f})")
                return True

    # Walk bars whose trading session began AFTER the alert was sent.
    # A bar's session opens at 13:30 UTC (9:30 AM ET). If send_dt < session_open,
    # the full day's H/L is post-send and grading is valid for that bar.
    for b in bars:
        bd = _bar_date(b)
        session_open_utc = datetime(bd.year, bd.month, bd.day, 13, 30, tzinfo=timezone.utc)
        if send_dt >= session_open_utc:
            continue
        if bd > exp:
            break

        # T-remaining at bar's market close, in fractional years
        bar_close_utc = datetime(bd.year, bd.month, bd.day, 21, 0, tzinfo=timezone.utc)
        T_rem = max((exp_close_utc - bar_close_utc).total_seconds() / (365.25 * 86400), 0)
        high  = b.high if b.high is not None else b.close
        low   = b.low  if b.low  is not None else b.close
        last_underlying = b.close

        # For a call, option max at stock high, min at stock low.
        # For a put, flipped.
        if opt_type == "call":
            est_opt_high = bs_price(high, K, T_rem, RISK_FREE_RATE, iv, "call")
            est_opt_low  = bs_price(low,  K, T_rem, RISK_FREE_RATE, iv, "call")
        else:
            est_opt_high = bs_price(low,  K, T_rem, RISK_FREE_RATE, iv, "put")
            est_opt_low  = bs_price(high, K, T_rem, RISK_FREE_RATE, iv, "put")

        hit_target = est_opt_high >= target
        hit_stop   = est_opt_low  <= stop

        if hit_target and hit_stop:
            entry["outcome"]    = "LOSS"
            entry["exit_price"] = stop
            entry["outcome_at"] = bd.isoformat()
            entry["graded_at"]  = datetime.now(tz=timezone.utc).isoformat()
            entry["last_underlying"] = last_underlying
            log.info(f"  GRADED LOSS  {entry['contract']} (both tagged {bd}, "
                     f"est H=${est_opt_high:.2f} L=${est_opt_low:.2f})")
            return True
        if hit_target:
            entry["outcome"]    = "WIN"
            entry["exit_price"] = target
            entry["outcome_at"] = bd.isoformat()
            entry["graded_at"]  = datetime.now(tz=timezone.utc).isoformat()
            entry["last_underlying"] = last_underlying
            log.info(f"  GRADED WIN   {entry['contract']} on {bd} "
                     f"(est option high ${est_opt_high:.2f} >= target ${target})")
            return True
        if hit_stop:
            entry["outcome"]    = "LOSS"
            entry["exit_price"] = stop
            entry["outcome_at"] = bd.isoformat()
            entry["graded_at"]  = datetime.now(tz=timezone.utc).isoformat()
            entry["last_underlying"] = last_underlying
            log.info(f"  GRADED LOSS  {entry['contract']} on {bd} "
                     f"(est option low ${est_opt_low:.2f} <= stop ${stop})")
            return True

    entry["last_underlying"] = last_underlying
    entry["last_polled_at"]  = datetime.now(tz=timezone.utc).isoformat()
    entry["poll_count"]     += 1

    # No hit yet — if past expiration, settle as expired_worthless
    if exp < today:
        # Reprice at expiration with T=0 and last bar's close
        if last_underlying is not None:
            intrinsic = max(0.0, (last_underlying - K) if opt_type == "call"
                                                       else (K - last_underlying))
            entry["exit_price"] = round(intrinsic, 4)
        else:
            entry["exit_price"] = 0.0
        entry["outcome"]    = "expired_worthless"
        entry["outcome_at"] = exp.isoformat()
        entry["graded_at"]  = datetime.now(tz=timezone.utc).isoformat()
        log.info(f"  GRADED EXPIRED {entry['contract']} settle ${entry['exit_price']}")
        return True

    return False


# ── Scoreboard regen ──────────────────────────────────────────────────────
def regenerate_scoreboard(state: dict) -> None:
    # ── Signal stats ──────────────────────────────────────────────────
    by_strat = defaultdict(lambda: {"wins": 0, "losses": 0, "pending": 0, "expired": 0})
    wins = losses = pending = expired = 0
    for k, v in state.items():
        if k.startswith("_"):
            continue
        s = v.get("strategy", "Unknown")
        o = v.get("outcome", "open")
        if o == "WIN":                 wins += 1;     by_strat[s]["wins"]    += 1
        elif o == "LOSS":              losses += 1;   by_strat[s]["losses"]  += 1
        elif o == "expired_worthless": expired += 1;  by_strat[s]["expired"] += 1
        else:                          pending += 1;  by_strat[s]["pending"] += 1

    total_graded = wins + losses
    win_rate     = round(wins / total_graded * 100, 1) if total_graded else 0

    strat_rows = ""
    for strat, c in sorted(by_strat.items()):
        graded = c["wins"] + c["losses"]
        rate = round(c["wins"] / graded * 100, 1) if graded else 0
        strat_rows += (
            f'\n        <tr><td>{strat}</td>'
            f'<td class="win">{c["wins"]}</td>'
            f'<td class="loss">{c["losses"]}</td>'
            f'<td class="pending">{c["pending"]}</td>'
            f'<td class="expired">{c["expired"]}</td>'
            f'<td>{"—" if not graded else f"{rate}%"}</td></tr>'
        )

    # ── Paper trading aggregate ────────────────────────────────────────
    paper       = paper_engine.get_all_trades()
    p_total     = len(paper)
    p_open      = sum(1 for t in paper if t.get("outcome") == "open")
    p_wins      = sum(1 for t in paper if t.get("outcome") == "win")
    p_losses    = sum(1 for t in paper if t.get("outcome") in ("loss", "expired_worthless"))
    p_closed    = p_wins + p_losses
    p_win_rate  = round(p_wins / p_closed * 100, 1) if p_closed else 0
    p_pnl       = round(sum((t.get("pnl") or 0) for t in paper), 2)
    p_pnl_color = "#00c853" if p_pnl >= 0 else "#ff3d3d"
    p_pnl_pfx   = "−$" if p_pnl < 0 else "$"
    p_pnl_str   = p_pnl_pfx + format(abs(p_pnl), ",.0f")

    # ── Current week stats ─────────────────────────────────────────────
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end   = week_start + timedelta(days=6)

    def _open_date(t):
        ts = t.get("open_time") or ""
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except Exception:
            return None

    week_trades = []
    for t in paper:
        d = _open_date(t)
        if d is not None and week_start <= d <= week_end:
            week_trades.append(t)

    wk_wins      = sum(1 for t in week_trades if t.get("outcome") == "win")
    wk_losses    = sum(1 for t in week_trades if t.get("outcome") in ("loss", "expired_worthless"))
    wk_closed    = wk_wins + wk_losses
    wk_rate      = round(wk_wins / wk_closed * 100, 1) if wk_closed else 0
    wk_pnl       = round(sum((t.get("pnl") or 0) for t in week_trades), 2)
    wk_pnl_color = "#00c853" if wk_pnl >= 0 else "#ff3d3d"
    wk_pnl_pfx   = "−$" if wk_pnl < 0 else "$"
    wk_pnl_str   = wk_pnl_pfx + format(abs(wk_pnl), ",.0f")
    wk_label     = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d')}"

    # ── Trade table rows ───────────────────────────────────────────────
    def _fmt_ts(ts):
        if not ts:
            return "—"
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%m/%d %H:%M")
        except Exception:
            return "—"

    def _week_of_month(d):
        return (d.day - 1) // 7 + 1

    open_trade_rows   = ""
    closed_trade_rows = ""
    for t in sorted(paper, key=lambda x: x.get("open_time") or "", reverse=True):
        ot = t.get("open_time") or ""
        try:
            ot_date    = datetime.fromisoformat(ot.replace("Z", "+00:00")).date()
            data_month = ot_date.strftime("%Y-%m")
            data_week  = str(_week_of_month(ot_date))
        except Exception:
            data_month, data_week = "unknown", "0"

        outcome = t.get("outcome", "open")
        if outcome == "open":
            sc  = "dir-put" if (t.get("opt_type") or "").lower().startswith("p") else "dir-call"
            sid = (t.get("direction") or t.get("opt_type") or "—").upper()
            open_trade_rows += (
                f'\n      <tr class="open-row" data-id="{t.get("id", "")}">'
                f'<td class="td-contract">{t.get("ticker", "—")}</td>'
                f'<td class="{sc}">{sid}</td>'
                f'<td>{t.get("strike", "—")}</td>'
                f'<td>${float(t.get("entry", 0) or 0):.2f}</td>'
                f'<td class="td-mark">—</td>'
                f'<td class="td-upnl">—</td>'
                f'<td class="td-dim">{t.get("expiration", "—")}</td>'
                f'<td class="td-time">{_fmt_ts(ot)}</td>'
                f'<td class="td-strat">{t.get("strategy_name", t.get("strategy", "—"))}</td>'
                f'</tr>'
            )
        else:
            if outcome == "win":
                o_cls, o_lbl, row_cls = "win",     "WIN",  "trade-win"
            elif outcome == "expired_worthless":
                o_cls, o_lbl, row_cls = "expired", "EXP",  "trade-loss"
            else:
                o_cls, o_lbl, row_cls = "loss",    "LOSS", "trade-loss"

            pnl     = t.get("pnl")
            pnl_str = "—" if pnl is None else (f"+${pnl:.2f}" if pnl >= 0 else f"−${abs(pnl):.2f}")
            pnl_cls = "" if pnl is None else ("win" if pnl >= 0 else "loss")

            closed_trade_rows += (
                f'\n      <tr class="trade-row {row_cls}"'
                f' data-month="{data_month}" data-week="{data_week}">'
                f'<td class="td-contract">{t.get("contract", "—")}</td>'
                f'<td class="td-strat">{t.get("strategy_name", t.get("strategy", "—"))}</td>'
                f'<td class="td-time">{_fmt_ts(ot)}</td>'
                f'<td class="td-time">{_fmt_ts(t.get("exit_time"))}</td>'
                f'<td class="{o_cls}">{o_lbl}</td>'
                f'<td class="{pnl_cls}">{pnl_str}</td>'
                f'</tr>'
            )

    # ── Month tabs ─────────────────────────────────────────────────────
    months_seen: list[str] = []
    _seen_m: set[str] = set()
    for t in paper:
        ot = t.get("open_time") or ""
        try:
            m = datetime.fromisoformat(ot.replace("Z", "+00:00")).strftime("%Y-%m")
            if m not in _seen_m:
                _seen_m.add(m)
                months_seen.append(m)
        except Exception:
            pass
    months_seen.sort(reverse=True)

    month_tabs = '    <button class="tab active" data-month="all">All</button>'
    for m in months_seen:
        try:
            lbl = datetime.strptime(m, "%Y-%m").strftime("%b %Y")
        except Exception:
            lbl = m
        month_tabs += f'\n    <button class="tab" data-month="{m}">{lbl}</button>'

    def _wr(val, has_data):
        if not has_data:
            return "—"
        return f'<span data-countup="{val}" data-suffix="%" data-decimals="1">0%</span>'

    updated  = datetime.now().strftime("%b %d, %Y %I:%M %p")
    sig_total = wins + losses + pending + expired

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lil Tony — Scoreboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.1/anime.min.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #0d0d0d; color: #e0e0e0;
       font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       padding: 24px 16px; max-width: 700px; margin: 0 auto; }}
h1 {{ font-size: 1.3rem; color: #ff6b00; letter-spacing: 1px; margin-bottom: 4px; }}
.updated {{ font-size: 0.75rem; color: #555; margin-bottom: 6px; }}
.method  {{ font-size: 0.7rem;  color: #b48a00; margin-bottom: 24px; }}
h2 {{ font-size: 0.8rem; color: #444; text-transform: uppercase;
      letter-spacing: 1px; margin-bottom: 12px; }}
.cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 28px; }}
.card {{ background: #1a1a1a; border-radius: 10px; padding: 16px 12px;
         text-align: center; opacity: 0; }}
.card .label {{ font-size: 0.7rem; color: #666; text-transform: uppercase; letter-spacing: 1px; }}
.card .value {{ font-size: 2rem; font-weight: 700; margin-top: 4px; }}
.win {{ color: #00c853; }} .loss {{ color: #ff3d3d; }}
.pending {{ color: #888; }} .expired {{ color: #b48a00; }}
.rate {{ color: #ff6b00; }}
.week-panel {{ background: #111; border: 1px solid #1f1f1f; border-radius: 12px;
               padding: 16px; margin-bottom: 28px; opacity: 0; }}
.week-panel .card {{ opacity: 1; }}
.wp-header {{ display: flex; justify-content: space-between; align-items: baseline;
              margin-bottom: 10px; }}
.wp-title {{ font-size: 0.7rem; color: #ff6b00; text-transform: uppercase; letter-spacing: 1px; }}
.wp-date  {{ font-size: 0.65rem; color: #555; }}
.week-panel .cards {{ margin-bottom: 0; }}
.tab-group {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }}
.tab {{ background: #1a1a1a; border: none; color: #555; padding: 5px 13px;
        border-radius: 20px; font-size: 0.72rem; cursor: pointer; }}
.tab:hover {{ color: #aaa; }}
.tab.active {{ background: #ff6b00; color: #fff; }}
#week-tabs {{ display: none; margin-bottom: 14px; }}
.trades-wrap {{ overflow-x: auto; margin-top: 8px; }}
table.trades-tbl {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
table.trades-tbl th {{ text-align: left; padding: 6px 8px; color: #555; font-size: 0.68rem;
                        text-transform: uppercase; letter-spacing: 0.8px;
                        border-bottom: 1px solid #222; white-space: nowrap; }}
table.trades-tbl td {{ padding: 9px 8px; border-bottom: 1px solid #141414; }}
table.trades-tbl tr:last-child td {{ border-bottom: none; }}
.td-contract {{ font-weight: 600; font-size: 0.82rem; }}
.td-strat    {{ font-size: 0.7rem; color: #666; max-width: 110px; }}
.td-time     {{ font-size: 0.7rem; color: #555; white-space: nowrap;
                font-variant-numeric: tabular-nums; }}
table.strat-tbl {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
table.strat-tbl th {{ text-align: left; padding: 8px 10px; color: #555; font-size: 0.7rem;
                       text-transform: uppercase; letter-spacing: 1px;
                       border-bottom: 1px solid #222; }}
table.strat-tbl td {{ padding: 10px 10px; border-bottom: 1px solid #1a1a1a; }}
table.strat-tbl tr:last-child td {{ border-bottom: none; }}
@keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}
.trade-open {{ animation: pulse 2.8s ease-in-out infinite; }}
.dir-call {{ color: #00c853; }}
.dir-put  {{ color: #ff6b00; }}
.td-mark  {{ color: #e0e0e0; font-variant-numeric: tabular-nums; }}
.td-upnl  {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
.td-dim   {{ color: #555; font-size: 0.75rem; }}
.td-empty {{ color: #555; padding: 14px 8px; text-align: center; font-size: 0.75rem; }}
.open-row {{ animation: pulse 2.8s ease-in-out infinite; }}
</style></head><body>
<h1>LIL TONY — SCOREBOARD</h1>
<div class="updated">Updated {updated}</div>
<div class="method">Auto-graded via stock-proxy BS reprice (estimate, not confirmed fill)</div>

<div id="week-panel" class="week-panel">
  <div class="wp-header">
    <span class="wp-title">This Week</span>
    <span class="wp-date">{wk_label}</span>
  </div>
  <div class="cards">
    <div class="card"><div class="label">Win Rate</div>
      <div class="value rate">{_wr(wk_rate, wk_closed > 0)}</div></div>
    <div class="card"><div class="label">P&amp;L</div>
      <div class="value" style="color:{wk_pnl_color}"
           data-countup="{abs(wk_pnl):.0f}" data-prefix="{wk_pnl_pfx}">{wk_pnl_str}</div></div>
    <div class="card"><div class="label">Wins</div>
      <div class="value win" data-countup="{wk_wins}">{wk_wins}</div></div>
    <div class="card"><div class="label">Losses</div>
      <div class="value loss" data-countup="{wk_losses}">{wk_losses}</div></div>
  </div>
</div>

<div class="cards" id="signal-cards">
  <div class="card"><div class="label">Signals</div>
    <div class="value" style="color:#e0e0e0" data-countup="{sig_total}">{sig_total}</div></div>
  <div class="card"><div class="label">Wins</div>
    <div class="value win" data-countup="{wins}">{wins}</div></div>
  <div class="card"><div class="label">Losses</div>
    <div class="value loss" data-countup="{losses}">{losses}</div></div>
  <div class="card"><div class="label">Win Rate</div>
    <div class="value rate">{_wr(win_rate, total_graded > 0)}</div></div>
</div>

<h2>Paper Trading (simulated)</h2>
<div class="cards" id="paper-cards">
  <div class="card"><div class="label">Trades</div>
    <div class="value" style="color:#e0e0e0" data-countup="{p_total}">{p_total}</div></div>
  <div class="card"><div class="label">Win Rate</div>
    <div class="value rate">{_wr(p_win_rate, p_closed > 0)}</div></div>
  <div class="card"><div class="label">Total P&amp;L</div>
    <div class="value" style="color:{p_pnl_color}"
         data-countup="{abs(p_pnl):.0f}" data-prefix="{p_pnl_pfx}">{p_pnl_str}</div></div>
  <div class="card"><div class="label">Open</div>
    <div class="value pending" data-countup="{p_open}">{p_open}</div></div>
</div>

<h2>Open Positions</h2>
<div class="trades-wrap">
<table class="trades-tbl" id="open-tbl"><thead><tr>
  <th>Ticker</th><th>Side</th><th>Strike</th><th>Entry</th>
  <th>Current</th><th>U-P&amp;L</th><th>Exp</th><th>Opened</th><th>Strategy</th>
</tr></thead><tbody id="open-body">{open_trade_rows}
</tbody></table>
</div>

<br>
<h2>Closed Trades</h2>
<div id="month-tabs" class="tab-group">
{month_tabs}
</div>
<div id="week-tabs" class="tab-group"></div>
<div class="trades-wrap">
<table class="trades-tbl"><thead><tr>
  <th>Contract</th><th>Strategy</th><th>Opened</th><th>Closed</th><th>Result</th><th>P&amp;L</th>
</tr></thead><tbody id="trades-body">{closed_trade_rows}
</tbody></table>
</div>

<br>
<h2>By Strategy</h2>
<table class="strat-tbl"><thead><tr>
  <th>Strategy</th><th>Wins</th><th>Losses</th><th>Open</th><th>Expired</th><th>Win Rate</th>
</tr></thead><tbody>{strat_rows}
</tbody></table>

<script>
(function () {{
  var anime = window.anime;
  if (!anime) return;

  // Staggered card entrance (signal + paper card groups only; week panel animates separately)
  anime({{
    targets: '#signal-cards .card, #paper-cards .card',
    translateY: [20, 0],
    opacity: [0, 1],
    delay: anime.stagger(65),
    duration: 480,
    easing: 'easeOutCubic'
  }});

  // Week panel slide down
  anime({{
    targets: '#week-panel',
    translateY: [-16, 0],
    opacity: [0, 1],
    duration: 550,
    easing: 'easeOutCubic'
  }});

  // Number countup — handles data-countup + optional data-prefix / data-suffix / data-decimals
  document.querySelectorAll('[data-countup]').forEach(function (el) {{
    var target   = parseFloat(el.dataset.countup) || 0;
    var prefix   = el.dataset.prefix   || '';
    var suffix   = el.dataset.suffix   || '';
    var decimals = parseInt(el.dataset.decimals || '0', 10);
    var obj = {{ val: 0 }};
    anime({{
      targets: obj,
      val: target,
      duration: 1100,
      easing: 'easeOutExpo',
      update: function () {{
        el.textContent = prefix + obj.val.toFixed(decimals) + suffix;
      }}
    }});
  }});

  // Win row flash green
  anime({{
    targets: '.trade-win',
    backgroundColor: ['rgba(0,200,83,0.14)', 'rgba(0,200,83,0)'],
    duration: 900,
    delay: anime.stagger(22, {{start: 500}}),
    easing: 'easeOutQuad'
  }});
  // Loss row flash red
  anime({{
    targets: '.trade-loss',
    backgroundColor: ['rgba(255,61,61,0.14)', 'rgba(255,61,61,0)'],
    duration: 900,
    delay: anime.stagger(22, {{start: 600}}),
    easing: 'easeOutQuad'
  }});

  // ── Tab filtering ──────────────────────────────────────────────────
  var currentMonth = 'all';
  var currentWeek  = 'all';

  function filterTrades(month, week) {{
    var rows    = document.querySelectorAll('.trade-row');
    var showing = [];
    rows.forEach(function (r) {{
      var ok = (month === 'all' || r.dataset.month === month) &&
               (week  === 'all' || r.dataset.week  === week);
      if (ok) {{
        r.style.display = '';
        showing.push(r);
      }} else {{
        r.style.display = 'none';
      }}
    }});
    if (showing.length) {{
      anime({{
        targets: showing,
        opacity: [0, 1],
        translateX: [6, 0],
        delay: anime.stagger(12),
        duration: 240,
        easing: 'easeOutCubic'
      }});
    }}
  }}

  function buildWeekTabs(month) {{
    var rows  = document.querySelectorAll('.trade-row');
    var weeks = [];
    var seen  = {{}};
    rows.forEach(function (r) {{
      if (r.dataset.month === month && !seen[r.dataset.week]) {{
        seen[r.dataset.week] = true;
        weeks.push(r.dataset.week);
      }}
    }});
    weeks.sort();
    var container = document.getElementById('week-tabs');
    container.innerHTML = '<button class="tab active" data-week="all">All Weeks</button>';
    weeks.forEach(function (w) {{
      var btn = document.createElement('button');
      btn.className    = 'tab';
      btn.dataset.week = w;
      btn.textContent  = 'Week ' + w;
      container.appendChild(btn);
    }});
    container.querySelectorAll('.tab').forEach(function (btn) {{
      btn.addEventListener('click', function () {{
        container.querySelectorAll('.tab').forEach(function (t) {{ t.classList.remove('active'); }});
        this.classList.add('active');
        currentWeek = this.dataset.week;
        filterTrades(currentMonth, currentWeek);
        anime({{ targets: this, scale: [0.88, 1], duration: 200, easing: 'easeOutBack' }});
      }});
    }});
  }}

  document.querySelectorAll('#month-tabs .tab').forEach(function (tab) {{
    tab.addEventListener('click', function () {{
      document.querySelectorAll('#month-tabs .tab').forEach(function (t) {{
        t.classList.remove('active');
      }});
      this.classList.add('active');
      currentMonth = this.dataset.month;
      currentWeek  = 'all';
      var weekTabsEl = document.getElementById('week-tabs');
      if (currentMonth === 'all') {{
        weekTabsEl.style.display = 'none';
      }} else {{
        buildWeekTabs(currentMonth);
        weekTabsEl.style.display = 'flex';
        anime({{
          targets: weekTabsEl,
          opacity: [0, 1],
          translateY: [-6, 0],
          duration: 220,
          easing: 'easeOutCubic'
        }});
      }}
      filterTrades(currentMonth, currentWeek);
      anime({{ targets: this, scale: [0.88, 1], duration: 200, easing: 'easeOutBack' }});
    }});
  }});

  // ── Live open positions (polls paper_dashboard :8787) ──────────────
  function fmtTime(ts) {{
    if (!ts) return '—';
    try {{
      var d = new Date(ts);
      return String(d.getMonth()+1).padStart(2,'0') + '/' +
             String(d.getDate()).padStart(2,'0') + ' ' +
             String(d.getHours()).padStart(2,'0') + ':' +
             String(d.getMinutes()).padStart(2,'0');
    }} catch(e) {{ return '—'; }}
  }}

  function pollOpenPositions() {{
    fetch('http://localhost:8787/api/state', {{cache: 'no-store'}})
      .then(function(r) {{ return r.json(); }})
      .then(function(s) {{
        var tbody = document.getElementById('open-body');
        if (!tbody) return;
        var open = s.open || [];
        if (!open.length) {{
          tbody.innerHTML = '<tr><td colspan="9" class="td-empty">no open positions</td></tr>';
          return;
        }}
        var html = '';
        for (var i = 0; i < open.length; i++) {{
          var r = open[i];
          var markStr = (r.mark == null) ? '—' : '$' + r.mark.toFixed(2);
          var upnlStr, upnlCls;
          if (r.upnl == null) {{
            upnlStr = '—'; upnlCls = '';
          }} else {{
            var upnlPct = (r.mark != null && r.entry > 0)
              ? ' (' + ((r.mark - r.entry) / r.entry * 100).toFixed(1) + '%)'
              : '';
            upnlStr = (r.upnl >= 0 ? '+$' : '-$') + Math.abs(r.upnl).toFixed(2) + upnlPct;
            upnlCls = r.upnl >= 0 ? 'win' : 'loss';
          }}
          var sc = (r.side || '').toUpperCase() === 'PUT' ? 'dir-put' : 'dir-call';
          html += '<tr class="open-row">' +
            '<td class="td-contract">' + (r.ticker || '—') + '</td>' +
            '<td class="' + sc + '">' + (r.side || '—').toUpperCase() + '</td>' +
            '<td>' + (r.strike || '—') + '</td>' +
            '<td>$' + (r.entry || 0).toFixed(2) + '</td>' +
            '<td class="td-mark">' + markStr + '</td>' +
            '<td class="td-upnl ' + upnlCls + '">' + upnlStr + '</td>' +
            '<td class="td-dim">' + (r.exp || '—') + '</td>' +
            '<td class="td-time">' + fmtTime(r.open_time) + '</td>' +
            '<td class="td-strat">' + (r.strategy || '—') + '</td>' +
            '</tr>';
        }}
        tbody.innerHTML = html;
        anime({{targets: '#open-tbl .open-row', opacity: [0, 1], duration: 300,
               delay: anime.stagger(12), easing: 'easeOutCubic'}});
      }})
      .catch(function() {{/* paper_dashboard offline — keep static rows */}});
  }}
  pollOpenPositions();
  setInterval(pollOpenPositions, 30000);

}})();
</script>
</body></html>"""
    SCOREBOARD.write_text(html)


# ── Git push ─────────────────────────────────────────────────────────────
def git_commit_and_push(grade_summary: str, dry_run: bool) -> None:
    if dry_run:
        log.info(f"  [dry-run] would: git add {PUSH_FILES} && commit && push")
        return
    try:
        for f in PUSH_FILES:
            if (ROOT / f).exists():
                subprocess.run(["git", "add", f], cwd=ROOT, check=True,
                               capture_output=True, text=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"],
                              cwd=ROOT, capture_output=True)
        if diff.returncode == 0:
            log.info("  No changes to push")
            return
        msg = f"outcome update: {grade_summary}"
        subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True,
                       capture_output=True, text=True)

        # Push; if it fails (e.g. scanner bot pushed concurrently), rebase + retry once
        push = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True)
        if push.returncode != 0:
            log.info(f"  push rejected — rebasing on remote and retrying: {push.stderr.strip().splitlines()[-1] if push.stderr else ''}")
            pull = subprocess.run(
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                cwd=ROOT, capture_output=True, text=True,
            )
            if pull.returncode != 0:
                log.warning(f"  git pull --rebase FAILED: {pull.stderr.strip()}")
                return
            push = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True)
        if push.returncode != 0:
            log.warning(f"  git push FAILED after retry: {push.stderr.strip()}")
        else:
            log.info(f"  pushed: {msg}")
    except subprocess.CalledProcessError as e:
        log.warning(f"  git step failed: {e.stderr if e.stderr else e}")


# ── One pass ─────────────────────────────────────────────────────────────
def poll_once(dry_run: bool) -> int:
    alerts = load_alerts()
    state  = load_outcomes()

    new = 0
    for a in alerts:
        aid = a["alert_id"]
        if aid not in state:
            state[aid] = _init_entry(a)
            new += 1
    if new:
        log.info(f"  Seeded {new} new alert(s) into state")

    open_entries = [(aid, v) for aid, v in state.items()
                    if not aid.startswith("_") and v["outcome"] == "open"]
    if not open_entries:
        log.info("  No open alerts to track")
        if new and not dry_run:
            _write_state(state); regenerate_scoreboard(state)
            git_commit_and_push(f"seeded {new} new alert(s)", dry_run)
        return 0

    # Group by ticker so we fetch each underlying once
    by_ticker: dict[str, list] = defaultdict(list)
    for aid, v in open_entries:
        by_ticker[v["ticker"]].append((aid, v))

    today      = date.today()
    force_week = in_force_grade_window()
    if force_week:
        ltd = last_trading_day_of_week(today)
        log.info(f"  *** force-grade window active — last trading day of week: {ltd} ***")
    new_grades = 0
    for n, (ticker, items) in enumerate(by_ticker.items()):
        earliest_send = min(date.fromisoformat(v["sent_at"][:10]) for _, v in items)
        latest_exp_str = max((v.get("expiration") or "9999-12-31") for _, v in items)
        try:
            latest_exp = date.fromisoformat(latest_exp_str)
        except Exception:
            latest_exp = today
        window_end = min(today, latest_exp)
        # 1 trading day buffer on each side
        start = earliest_send - timedelta(days=2)
        end   = window_end  + timedelta(days=1)

        log.info(f"  [{n+1}/{len(by_ticker)}] {ticker}: fetching daily bars "
                 f"{start} → {end} for {len(items)} open alert(s)")
        bars = fetch_stock_bars(ticker, start, end)
        if not bars:
            log.warning(f"    no bars returned for {ticker}, skipping this pass")
            continue

        for aid, entry in items:
            if grade_via_stock_proxy(entry, bars, force_grade=force_week):
                new_grades += 1
                _feed_learner(entry)

        if n < len(by_ticker) - 1:
            time.sleep(PACING_SECONDS)

    if new_grades or new:
        summary = f"{new_grades} grade(s), {new} new alert(s)"
        if dry_run:
            log.info(f"  [dry-run] would write state + scoreboard + push: {summary}")
        else:
            _write_state(state)
            regenerate_scoreboard(state)
            git_commit_and_push(summary, dry_run)

    log.info(f"  Pass complete: {new_grades} new grade(s) this cycle")
    return new_grades


def _write_state(state: dict) -> None:
    counts = {"wins": 0, "losses": 0, "expired": 0, "open": 0}
    for k, v in state.items():
        if k.startswith("_"):
            continue
        o = v.get("outcome", "open")
        if o == "WIN":   counts["wins"] += 1
        elif o == "LOSS": counts["losses"] += 1
        elif o == "expired_worthless": counts["expired"] += 1
        else: counts["open"] += 1
    state["_meta"] = {
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "method": "stock_proxy_bs_reprice",
        "risk_free_rate": RISK_FREE_RATE,
        **counts,
    }
    save_outcomes(state)


# ── Signals + main ───────────────────────────────────────────────────────
_should_stop = False
def _on_signal(sig, frame):
    global _should_stop
    log.info(f"Received signal {sig} — finishing current cycle then stopping")
    _should_stop = True

signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)


def main() -> int:
    once    = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv

    log.info("=" * 60)
    log.info(f"outcome_tracker starting (once={once}, dry_run={dry_run})")
    log.info(f"method: stock-proxy BS reprice")
    log.info(f"alerts log:    {ALERTS_LOG}")
    log.info(f"outcomes file: {OUTCOMES_FILE}")
    log.info("=" * 60)

    if not os.getenv("WEBULL_APP_KEY"):
        log.error("WEBULL_APP_KEY not in .env — exiting")
        return 2

    while not _should_stop:
        try:
            t0 = time.time()
            log.info(f"---- poll @ {datetime.now().isoformat(timespec='seconds')} ----")
            poll_once(dry_run)
            log.info(f"  cycle done in {time.time()-t0:.1f}s")
        except Exception as e:
            log.exception(f"poll cycle failed: {e}")

        if once:
            break

        for _ in range(POLL_INTERVAL_SECONDS):
            if _should_stop:
                break
            time.sleep(1)

    log.info("outcome_tracker stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
