#!/usr/bin/env python3
"""
paper_dashboard.py — local arcade-style watch screen for the paper-trade bot.

READ-ONLY. Serves a neon/CRT web page plus a /api/state JSON feed describing:
  - bot vitals (daytime_runner + outcome_tracker process state, market open, last poll)
  - open paper positions with a LIVE Black-Scholes mark and unrealized P&L
  - closed positions, realized P&L, and the win/loss record

It never mutates data/paper_trades.json, never places an order, never writes to
the bot's logs. Webull spot prices are cached 60s so mashing refresh on your
phone doesn't hammer the API.

Run:  python3 paper_dashboard.py
Then open the printed http://<lan-ip>:8787 on your Mac, iPhone, or iPad (same WiFi).
Ctrl-C to stop.
"""
from __future__ import annotations

import json
import math
import socket
import subprocess
import threading
import time
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytz

ROOT        = Path(__file__).resolve().parent
LEDGER_PATH = ROOT / "data" / "paper_trades.json"
DAYTIME_LOG = ROOT / "logs" / "daytime.log"

PORT             = 8787
RISK_FREE_RATE   = 0.05
SPOT_TTL         = 60          # seconds to cache a Webull spot price
_CONTRACT_MULT   = 100
_CENTRAL         = pytz.timezone("America/Chicago")
_MARKET_OPEN_CT  = (8, 30)     # 9:30 AM ET
_MARKET_CLOSE_CT = (15, 0)     # 4:00 PM ET

# Live spot price comes from the project's Webull feed. Kept optional so the
# dashboard still renders (marks show "—") if the feed can't be imported.
try:
    from data.market_feed import get_spot_price
except Exception:
    get_spot_price = None

_spot_cache: dict[str, tuple[float | None, float]] = {}
_iv_cache:   dict[str, float | None]               = {}
_lock = threading.Lock()


# ── Black-Scholes (read-only mirror of paper_trading/tracker.py) ───────────
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_price(S: float, K: float, T: float, r: float, sigma: float, opt_type: str) -> float:
    if T <= 0:
        return max(0.0, (S - K) if opt_type == "call" else (K - S))
    if sigma <= 0:
        return max(0.0, (S - K * math.exp(-r * T)) if opt_type == "call"
                                                    else (K * math.exp(-r * T) - S))
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if opt_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _implied_vol(S: float, K: float, T: float, r: float, premium: float, opt_type: str) -> float | None:
    if T <= 0 or premium <= 0:
        return None
    lo, hi = 0.005, 5.0
    if premium < _bs_price(S, K, T, r, lo, opt_type) or premium > _bs_price(S, K, T, r, hi, opt_type):
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        pm = _bs_price(S, K, T, r, mid, opt_type)
        if abs(pm - premium) < 1e-5 or (hi - lo) < 1e-5:
            return mid
        if pm < premium:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _years_to_expiry(expiration: str, ref: datetime) -> float:
    exp = date.fromisoformat(expiration)
    exp_close_utc = datetime(exp.year, exp.month, exp.day, 21, 0, tzinfo=timezone.utc)
    return (exp_close_utc - ref).total_seconds() / (365.25 * 86400)


def market_is_open(now_ct: datetime | None = None) -> bool:
    now_ct = now_ct or datetime.now(_CENTRAL)
    if now_ct.weekday() >= 5:
        return False
    hm = (now_ct.hour, now_ct.minute)
    return _MARKET_OPEN_CT <= hm <= _MARKET_CLOSE_CT


# ── data gathering ─────────────────────────────────────────────────────────
def _cached_spot(ticker: str) -> float | None:
    if get_spot_price is None:
        return None
    now = time.time()
    with _lock:
        hit = _spot_cache.get(ticker)
        if hit and now - hit[1] < SPOT_TTL:
            return hit[0]
    try:
        px = get_spot_price(ticker)
    except Exception:
        px = None
    with _lock:
        _spot_cache[ticker] = (px, now)
    return px


def _mark_for(t: dict) -> float | None:
    """Read-only Black-Scholes mark for an open trade. Never mutates the ledger."""
    exp = t.get("expiration")
    if not exp:
        return None
    spot = _cached_spot(t["ticker"])
    if spot is None:
        return None
    opt_type = t["opt_type"]
    K        = t["strike"]
    entry    = t["entry"]
    now_utc  = datetime.now(timezone.utc)
    T_rem    = _years_to_expiry(exp, now_utc)
    if T_rem <= 0:
        return max(0.0, (spot - K) if opt_type == "call" else (K - spot))

    iv = t.get("implied_vol")
    if iv is None:
        with _lock:
            iv = _iv_cache.get(t["id"])
    if iv is None:
        spot0 = t.get("spot_at_open") or spot
        try:
            open_ref = datetime.fromisoformat(t["open_time"])
        except Exception:
            open_ref = now_utc
        T0 = max(_years_to_expiry(exp, open_ref), 1e-6)
        iv = _implied_vol(spot0, K, T0, RISK_FREE_RATE, entry, opt_type)
        with _lock:
            _iv_cache[t["id"]] = iv
    if iv is None:
        return None
    return _bs_price(spot, K, max(T_rem, 1e-6), RISK_FREE_RATE, iv, opt_type)


def _proc_running(pattern: str) -> bool:
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _last_paper_poll() -> str | None:
    if not DAYTIME_LOG.exists():
        return None
    try:
        with DAYTIME_LOG.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            chunk = f.read().decode("utf-8", "ignore")
    except Exception:
        return None
    ts = None
    for line in chunk.splitlines():
        if "paper-trade poll" in line or "PAPER OPEN" in line or "PAPER CLOSE" in line:
            ts = line[:19]
    return ts


def build_state() -> dict:
    try:
        trades = json.loads(LEDGER_PATH.read_text())
    except Exception:
        trades = []

    now_ct = datetime.now(_CENTRAL)
    open_rows, closed_rows = [], []
    realized = unrealized = 0.0
    wins = losses = 0

    for t in trades:
        out = t.get("outcome")
        if out == "open":
            contracts = t.get("contracts", 1)
            entry     = t["entry"]
            mark      = _mark_for(t)
            upnl      = round((mark - entry) * _CONTRACT_MULT * contracts, 2) if mark is not None else None
            if upnl is not None:
                unrealized += upnl
            span = (t["target"] - t["stop"]) or 1e-9
            prog = None if mark is None else max(0.0, min(100.0, (mark - t["stop"]) / span * 100.0))
            open_rows.append({
                "ticker":   t["ticker"],
                "side":     t["direction"],
                "open_time": t.get("open_time"),
                "strike":   t["strike"],
                "exp":      t.get("expiration"),
                "entry":    entry,
                "mark":     round(mark, 2) if mark is not None else None,
                "target":   t["target"],
                "stop":     t["stop"],
                "contracts": contracts,
                "size":     t.get("size"),
                "upnl":     upnl,
                "prog":     None if prog is None else round(prog, 1),
                "strategy": t.get("strategy_name") or t.get("strategy") or "?",
            })
        else:
            pnl = t.get("pnl") or 0.0
            realized += pnl
            if out == "win":
                wins += 1
            elif out in ("loss", "expired_worthless"):
                losses += 1
            closed_rows.append({
                "ticker":   t["ticker"],
                "side":     t.get("direction"),
                "open_time": t.get("open_time"),
                "strike":   t.get("strike"),
                "outcome":  out,
                "entry":    t.get("entry"),
                "exit":     t.get("exit_price"),
                "pnl":      round(pnl, 2),
                "exit_time": t.get("exit_time"),
                "strategy": t.get("strategy_name") or t.get("strategy") or "?",
            })

    closed_rows.sort(key=lambda r: r.get("exit_time") or "", reverse=True)
    decided = wins + losses
    return {
        "server_time_ct": now_ct.strftime("%Y-%m-%d %H:%M:%S CT"),
        "market_open":    market_is_open(now_ct),
        "vitals": {
            "runner":    _proc_running("daytime_runner.py"),
            "tracker":   _proc_running("outcome_tracker.py"),
            "last_poll": _last_paper_poll(),
            "feed":      get_spot_price is not None,
        },
        "totals": {
            "open":       len(open_rows),
            "wins":       wins,
            "losses":     losses,
            "win_rate":   round(wins / decided * 100, 1) if decided else None,
            "realized":   round(realized, 2),
            "unrealized": round(unrealized, 2),
        },
        "open":   open_rows,
        "closed": closed_rows,
    }


# ── web server ───────────────────────────────────────────────────────────
PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>LIL TONY // PAPER ARCADE</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.1/anime.min.js"></script>
<style>
:root{--bg:#05060a;--cyan:#22f0ff;--mag:#ff2bd6;--grn:#39ff14;--yel:#fde047;--red:#ff3b56;--dim:#5a6b7a;--panel:#0a0e16;}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
html,body{margin:0;background:var(--bg);}
body{font-family:"Courier New",ui-monospace,Menlo,Consolas,monospace;color:var(--cyan);text-transform:uppercase;letter-spacing:.06em;padding:14px;min-height:100vh;-webkit-text-size-adjust:100%;animation:flick 5s infinite steps(60);}
@keyframes flick{0%,97%{opacity:1}98%{opacity:.92}99%{opacity:.97}100%{opacity:1}}
body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:99;background:repeating-linear-gradient(0deg,transparent 0,transparent 2px,rgba(0,0,0,.18) 3px);mix-blend-mode:multiply;}
h1{font-size:clamp(15px,4.2vw,26px);margin:0 0 2px;color:var(--mag);text-shadow:0 0 6px var(--mag),0 0 16px var(--mag);}
.title-letter{display:inline-block;opacity:0;}
.sub{color:var(--dim);font-size:10px;margin-bottom:11px;}
.glow-c{text-shadow:0 0 5px var(--cyan),0 0 12px var(--cyan);}
.pos{color:var(--grn);text-shadow:0 0 4px var(--grn);}
.neg{color:var(--red);text-shadow:0 0 4px var(--red);}
.win{color:var(--grn);} .loss{color:var(--red);}
.muted{color:var(--dim);}
.call{color:var(--cyan);text-shadow:0 0 4px var(--cyan);}
.put{color:var(--mag);text-shadow:0 0 4px var(--mag);}
.on{color:var(--grn);text-shadow:0 0 5px var(--grn);}
.off{color:var(--red);text-shadow:0 0 4px var(--red);}
.empty{color:var(--dim);padding:12px 0;font-size:10px;}
h2{font-size:11px;color:var(--yel);text-shadow:0 0 5px var(--yel);margin:16px 0 7px;border-bottom:1px dashed #2a3a4a;padding-bottom:4px;}
/* Status bar */
.bar{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:11px;}
.chip{border:1px solid #14304a;background:var(--panel);padding:5px 8px;font-size:10px;border-radius:4px;min-height:34px;display:flex;align-items:center;}
/* Range tabs */
.range-wrap{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:12px;}
.range-tab{background:var(--panel);border:1px solid #14304a;color:var(--dim);font-family:inherit;font-size:9px;text-transform:uppercase;letter-spacing:.06em;padding:0 10px;border-radius:20px;cursor:pointer;min-height:30px;position:relative;overflow:hidden;}
.range-tab.active{border-color:var(--cyan);color:var(--cyan);text-shadow:0 0 4px var(--cyan);}
.range-uline{position:absolute;bottom:0;left:0;height:2px;background:var(--cyan);width:0%;}
/* Week panel */
.week-panel{border:1px solid #14304a;background:var(--panel);border-radius:8px;padding:11px;margin-bottom:14px;}
.wp-hdr{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:9px;}
.wp-title{font-size:10px;color:var(--yel);text-shadow:0 0 5px var(--yel);}
.wp-date{font-size:9px;color:var(--dim);}
.wp-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;}
.wp-card{background:#0d1420;border-radius:5px;padding:8px;text-align:center;}
.wp-lab{font-size:8px;color:var(--dim);margin-bottom:3px;}
.wp-val{font-size:clamp(16px,5vw,22px);font-weight:bold;}
/* Stat cards */
.scores{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:14px;}
.score{border:1px solid #14304a;background:var(--panel);border-radius:6px;padding:10px;opacity:0;}
.score .lab{font-size:9px;color:var(--dim);margin-bottom:4px;}
.score .val{font-size:clamp(16px,5vw,26px);font-weight:bold;}
.wr-cell{display:flex;align-items:center;gap:8px;}
.wr-svg{width:48px;height:48px;flex-shrink:0;}
/* Calendar */
.cal-wrap{margin-bottom:16px;overflow:hidden;}
#calendar{will-change:transform;}
.cal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;}
.cal-title{color:var(--yel);font-size:11px;text-shadow:0 0 5px var(--yel);}
.cal-nav{background:none;border:1px solid #1b2c3c;color:var(--dim);font-size:11px;width:34px;height:34px;border-radius:4px;cursor:pointer;font-family:inherit;display:flex;align-items:center;justify-content:center;}
.cal-nav:active{color:var(--cyan);border-color:var(--cyan);}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;}
.cal-dow{font-size:7px;color:var(--dim);text-align:center;padding:3px 0;}
.cal-day{min-height:44px;display:flex;flex-direction:column;align-items:center;justify-content:center;border-radius:4px;border:1px solid #181f2e;background:var(--panel);font-size:11px;position:relative;opacity:0;user-select:none;}
.cal-day.cal-empty{background:transparent;border-color:transparent;cursor:default;}
.cal-day.has-trades{cursor:pointer;}
.cal-day.pnl-win{border-color:rgba(57,255,20,.3);color:var(--grn);}
.cal-day.pnl-loss{border-color:rgba(255,59,86,.3);color:var(--red);}
.cal-day.pnl-flat{border-color:rgba(253,224,71,.2);color:var(--yel);}
.cal-dot{font-size:7px;color:var(--dim);line-height:1.2;}
.cal-pnl{font-size:8px;font-weight:700;line-height:1.1;}
.pnl-win .cal-pnl{color:var(--grn);}
.pnl-loss .cal-pnl{color:var(--red);}
.pnl-flat .cal-pnl{color:var(--yel);}
/* Open cards */
.open-cards{display:flex;flex-direction:column;gap:6px;margin-bottom:6px;}
@keyframes heartbeat{0%,100%{transform:scale(1)}50%{transform:scale(1.015)}}
.open-card{background:var(--panel);border:1px solid #14304a;border-radius:6px;padding:10px 12px;cursor:pointer;animation:heartbeat 3s ease-in-out infinite;transform-origin:center;}
.oc-main{display:flex;align-items:center;gap:6px;flex-wrap:nowrap;min-height:24px;}
.oc-ticker{font-size:13px;font-weight:bold;min-width:44px;}
.oc-side{font-size:10px;min-width:26px;}
.oc-strike{color:var(--dim);font-size:10px;}
.oc-mark{margin-left:auto;font-size:11px;}
.oc-upnl{font-size:11px;font-weight:bold;min-width:52px;text-align:right;}
.oc-chev{color:var(--dim);font-size:10px;margin-left:4px;}
.oc-detail{overflow:hidden;height:0;opacity:0;}
.oc-detail-inner{padding:8px 0 2px;font-size:10px;color:var(--dim);line-height:1.9;border-top:1px solid #1b2c3c;margin-top:8px;}
/* Closed table */
.tab-group{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px;}
.tab{background:var(--panel);border:1px solid #14304a;color:var(--dim);padding:4px 9px;border-radius:20px;font-family:inherit;font-size:9px;text-transform:uppercase;letter-spacing:.06em;cursor:pointer;min-height:28px;}
.tab.active{border-color:var(--cyan);color:var(--cyan);}
#week-tabs{display:none;margin-bottom:8px;}
.scroll{overflow-x:auto;}
table{border-collapse:collapse;width:100%;font-size:11px;min-width:500px;}
th{color:var(--dim);font-size:9px;text-align:right;padding:5px 6px;border-bottom:1px solid #1b2c3c;}
th:first-child,td:first-child{text-align:left;}
td{padding:6px 6px;border-bottom:1px solid #0e1622;text-align:right;white-space:nowrap;}
.td-time{color:var(--dim);font-size:9px;}
/* Footer */
.foot{color:var(--dim);font-size:9px;margin-top:16px;text-align:center;}
.live{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--grn);box-shadow:0 0 5px var(--grn);animation:blink 1.1s infinite;}
@keyframes blink{50%{opacity:.2}}
/* Modal backdrop */
#modal-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:200;}
/* Day modal */
#day-modal{position:fixed;bottom:0;left:0;right:0;background:#0c1120;border-top:2px solid var(--cyan);border-radius:14px 14px 0 0;z-index:201;max-height:78vh;display:none;flex-direction:column;box-shadow:0 -6px 32px rgba(34,240,255,.12);}
.modal-header{display:flex;justify-content:space-between;align-items:center;padding:13px 15px;border-bottom:1px solid #1b2c3c;flex-shrink:0;}
.modal-h-left{display:flex;flex-direction:column;gap:3px;}
.modal-date{color:var(--cyan);font-size:13px;}
.modal-pnl{font-size:11px;}
.modal-close{background:none;border:1px solid #1b2c3c;color:var(--dim);font-size:14px;width:34px;height:34px;border-radius:50%;cursor:pointer;font-family:inherit;flex-shrink:0;display:flex;align-items:center;justify-content:center;}
.modal-body{overflow-y:auto;padding:10px 14px 28px;}
.modal-trade{border:1px solid #1b2c3c;border-radius:6px;padding:10px;margin-bottom:8px;background:var(--bg);}
.mt-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;}
.mt-ticker{font-size:12px;font-weight:bold;}
.mt-outcome{font-size:10px;}
.mt-row{font-size:10px;color:var(--dim);margin-bottom:2px;}
.mt-pnl{font-size:12px;font-weight:bold;margin-top:5px;}
</style></head>
<body>
<h1 id="title">&#9646; LIL TONY &mdash; PAPER ARCADE &#9646;</h1>
<div class="sub">SIMULATED LEDGER &middot; NO REAL MONEY &middot; $100 MAX / TRADE</div>
<div class="bar" id="bar"></div>
<div id="range-wrap" class="range-wrap"></div>
<div id="week-panel" class="week-panel"></div>
<div class="scores" id="scores"></div>
<h2>&#9656; CALENDAR</h2>
<div class="cal-wrap"><div id="calendar"></div></div>
<h2>&#9656; OPEN POSITIONS</h2>
<div class="open-cards" id="open-cards"></div>
<h2>&#9656; CLOSED TRADES</h2>
<div id="month-tabs" class="tab-group"></div>
<div id="week-tabs" class="tab-group"></div>
<div class="scroll"><table id="closedtbl"></table></div>
<div class="foot" id="foot">connecting&hellip;</div>
<div id="modal-backdrop" onclick="closeModal()"></div>
<div id="day-modal">
  <div class="modal-header">
    <div class="modal-h-left">
      <div class="modal-date" id="modal-title"></div>
      <div class="modal-pnl" id="modal-pnl"></div>
    </div>
    <button class="modal-close" onclick="closeModal()">&#10005;</button>
  </div>
  <div class="modal-body" id="modal-body"></div>
</div>
<script>
// ── state ────────────────────────────────────────────────────────────
const MO=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
let STATE=null, initialized=false, prevClosedCount=-1;
let currentRange='week', currentMonth='__thisweek__', currentWeek='all';
let calMonth=new Date(); calMonth.setDate(1); calMonth.setHours(0,0,0,0);
let expandedIdx=null;

// ── helpers ──────────────────────────────────────────────────────────
const money=v=>v==null?'&mdash;':(v<0?'-$':'$')+Math.abs(v).toFixed(2);
const cls=v=>v==null?'muted':(v>=0?'pos':'neg');
function fmtTime(ts){
  if(!ts)return '&mdash;';
  try{const d=new Date(ts);return String(d.getMonth()+1).padStart(2,'0')+'/'+String(d.getDate()).padStart(2,'0')+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
  catch(e){return '&mdash;';}
}
function monthKey(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');}
function weekOfMonth(d){return Math.ceil(d.getDate()/7);}
function chip(label,ok,txt){return `<span class="chip">${label}: <b class="${ok?'on':'off'}">${txt||(ok?'ONLINE':'OFFLINE')}</b></span>`;}

// ── range filter ─────────────────────────────────────────────────────
function filterByRange(closed,range){
  const now=new Date();
  if(range==='all')return closed;
  if(range==='today'){const s=new Date(now);s.setHours(0,0,0,0);return closed.filter(r=>new Date(r.exit_time||r.open_time||0)>=s);}
  if(range==='week'){const dow=now.getDay(),mo=dow===0?6:dow-1;const s=new Date(now);s.setDate(now.getDate()-mo);s.setHours(0,0,0,0);return closed.filter(r=>new Date(r.exit_time||r.open_time||0)>=s);}
  if(range==='month'){return closed.filter(r=>new Date(r.exit_time||r.open_time||0)>=new Date(now.getFullYear(),now.getMonth(),1));}
  if(range==='3m'){const s=new Date(now);s.setMonth(s.getMonth()-3);return closed.filter(r=>new Date(r.exit_time||r.open_time||0)>=s);}
  if(range==='6m'){const s=new Date(now);s.setMonth(s.getMonth()-6);return closed.filter(r=>new Date(r.exit_time||r.open_time||0)>=s);}
  if(range==='ytd'){return closed.filter(r=>new Date(r.exit_time||r.open_time||0)>=new Date(now.getFullYear(),0,1));}
  return closed;
}

// ── title animation ──────────────────────────────────────────────────
function animateTitle(){
  const h1=document.getElementById('title');
  const txt=h1.textContent;
  h1.innerHTML=txt.split('').map(c=>`<span class="title-letter">${c===' '?'&nbsp;':c==='—'?'&mdash;':c}</span>`).join('');
  anime({targets:'.title-letter',opacity:[0,1],translateY:[16,0],delay:anime.stagger(36),duration:360,easing:'easeOutCubic'});
}

// ── chip glow loop ───────────────────────────────────────────────────
function startChipGlow(){
  anime({targets:'.chip',boxShadow:['0 0 0px rgba(34,240,255,0)','0 0 10px rgba(34,240,255,.3)','0 0 0px rgba(34,240,255,0)'],duration:2400,delay:anime.stagger(180),loop:true,easing:'easeInOutSine'});
}

// ── range selector ───────────────────────────────────────────────────
function buildRangeSelector(){
  const el=document.getElementById('range-wrap');
  const opts=[['TODAY','today'],['THIS WEEK','week'],['THIS MONTH','month'],['3M','3m'],['6M','6m'],['YTD','ytd'],['ALL','all']];
  el.innerHTML=opts.map(([l,r])=>`<button class="range-tab${r==='week'?' active':''}" data-r="${r}">${l}<span class="range-uline"></span></button>`).join('');
  el.querySelectorAll('.range-tab').forEach(btn=>{
    btn.addEventListener('click',function(){
      el.querySelectorAll('.range-tab').forEach(b=>b.classList.remove('active'));
      this.classList.add('active');
      currentRange=this.dataset.r;
      anime({targets:this.querySelector('.range-uline'),width:['0%','100%'],duration:260,easing:'easeOutCubic'});
      if(STATE) buildStats(STATE.closed,STATE.totals,true);
    });
  });
}

// ── week panel ───────────────────────────────────────────────────────
function buildWeekPanel(closed,doAnimate){
  const now=new Date();
  const dow=now.getDay(),mo=dow===0?6:dow-1;
  const wkS=new Date(now);wkS.setDate(now.getDate()-mo);wkS.setHours(0,0,0,0);
  const wkE=new Date(wkS);wkE.setDate(wkS.getDate()+6);wkE.setHours(23,59,59,999);
  const wkT=closed.filter(r=>{const ts=r.exit_time||r.open_time;if(!ts)return false;const d=new Date(ts);return d>=wkS&&d<=wkE;});
  const wkW=wkT.filter(r=>r.outcome==='win').length;
  const wkL=wkT.filter(r=>r.outcome!=='win').length;
  const wkC=wkW+wkL;
  const wkR=wkC?(wkW/wkC*100):null;
  const wkP=wkT.reduce((s,r)=>s+(r.pnl||0),0);
  const fmtD=d=>MO[d.getMonth()]+' '+d.getDate();
  const wrStr=wkR==null?'&mdash;':wkR.toFixed(1)+'%';
  const pStr=(wkP<0?'-$':'$')+Math.abs(wkP).toFixed(0);
  document.getElementById('week-panel').innerHTML=
    `<div class="wp-hdr"><span class="wp-title">&#9658; THIS WEEK</span><span class="wp-date">${fmtD(wkS)} &ndash; ${fmtD(wkE)}</span></div>`+
    `<div class="wp-grid">`+
    `<div class="wp-card"><div class="wp-lab">WIN RATE</div><div class="wp-val glow-c" id="wp-wr">${wrStr}</div></div>`+
    `<div class="wp-card"><div class="wp-lab">P&amp;L</div><div class="wp-val ${cls(wkP)}" id="wp-pnl">${pStr}</div></div>`+
    `<div class="wp-card"><div class="wp-lab">WINS</div><div class="wp-val pos" id="wp-w">${wkW}</div></div>`+
    `<div class="wp-card"><div class="wp-lab">LOSSES</div><div class="wp-val neg" id="wp-l">${wkL}</div></div>`+
    `</div>`;
  if(doAnimate){
    if(wkR!=null){const o={v:0};anime({targets:o,v:wkR,duration:900,easing:'easeOutExpo',update(){const e=document.getElementById('wp-wr');if(e)e.textContent=o.v.toFixed(1)+'%';}});}
    {const o={v:0};anime({targets:o,v:Math.abs(wkP),duration:900,easing:'easeOutExpo',update(){const e=document.getElementById('wp-pnl');if(e)e.textContent=(wkP<0?'-$':'$')+o.v.toFixed(0);}});}
    {const o={v:0};anime({targets:o,v:wkW,duration:800,easing:'easeOutExpo',update(){const e=document.getElementById('wp-w');if(e)e.textContent=Math.round(o.v);}});}
    {const o={v:0};anime({targets:o,v:wkL,duration:800,easing:'easeOutExpo',update(){const e=document.getElementById('wp-l');if(e)e.textContent=Math.round(o.v);}});}
  }
}

// ── stat cards ───────────────────────────────────────────────────────
function buildStats(closed,totals,doAnimate){
  const f=filterByRange(closed,currentRange);
  const w=f.filter(r=>r.outcome==='win').length;
  const l=f.filter(r=>['loss','expired_worthless'].includes(r.outcome)).length;
  const dec=w+l;
  const wr=dec?(w/dec*100):null;
  const realized=f.reduce((s,r)=>s+(r.pnl||0),0);
  const unrealized=totals.unrealized;
  const arcOff=wr==null?100:(100-wr);
  document.getElementById('scores').innerHTML=
    `<div class="score"><div class="lab">REALIZED P&amp;L</div><div class="val ${cls(realized)}" id="sc-rp">${money(realized)}</div></div>`+
    `<div class="score"><div class="lab">UNREALIZED P&amp;L</div><div class="val ${cls(unrealized)}" id="sc-up">${money(unrealized)}</div></div>`+
    `<div class="score"><div class="lab">WIN RATE</div><div class="wr-cell">`+
      `<svg class="wr-svg" viewBox="0 0 36 36" style="transform:rotate(-90deg)">`+
        `<circle cx="18" cy="18" r="15.9155" fill="none" stroke="#1b2c3c" stroke-width="2.5"/>`+
        `<circle id="wr-arc" cx="18" cy="18" r="15.9155" fill="none" stroke="var(--cyan)" stroke-width="2.5" stroke-dasharray="100" stroke-dashoffset="${doAnimate?100:arcOff}" stroke-linecap="round"/>`+
      `</svg><div class="val glow-c" id="sc-wr">${wr==null?'&mdash;':wr.toFixed(1)+'%'}</div></div></div>`+
    `<div class="score"><div class="lab">RECORD W&ndash;L</div><div class="val glow-c">${w}&ndash;${l}</div></div>`+
    `<div class="score"><div class="lab">OPEN</div><div class="val glow-c">${totals.open}</div></div>`;
  if(doAnimate){
    anime({targets:'#scores .score',translateY:[40,0],opacity:[0,1],delay:anime.stagger(80),duration:500,easing:'easeOutCubic'});
    anime({targets:'#wr-arc',strokeDashoffset:[100,arcOff],duration:1300,easing:'easeOutExpo',delay:320});
    if(wr!=null){const o={v:0};anime({targets:o,v:wr,duration:1100,easing:'easeOutExpo',update(){const e=document.getElementById('sc-wr');if(e)e.textContent=o.v.toFixed(1)+'%';}});}
    [{id:'sc-rp',val:realized},{id:'sc-up',val:unrealized}].forEach(({id,val})=>{
      const e=document.getElementById(id);if(!e||val==null)return;
      const o={v:0};
      anime({targets:o,v:Math.abs(val),duration:1100,easing:'easeOutExpo',update(){e.textContent=(val<0?'-$':'$')+o.v.toFixed(2);}});
    });
  } else {
    const arc=document.getElementById('wr-arc');
    if(arc)arc.setAttribute('stroke-dashoffset',arcOff);
    document.querySelectorAll('#scores .score').forEach(s=>s.style.opacity='1');
  }
}

// ── calendar ─────────────────────────────────────────────────────────
function buildCalendar(closed,doAnimate,slideDir){
  const byDate={};
  closed.forEach(r=>{
    const ts=r.exit_time||r.open_time;if(!ts)return;
    const d=new Date(ts);
    const k=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    if(!byDate[k])byDate[k]=[];
    byDate[k].push(r);
  });
  const y=calMonth.getFullYear(),m=calMonth.getMonth();
  const firstDow=new Date(y,m,1).getDay();
  const dim=new Date(y,m+1,0).getDate();
  let html=`<div class="cal-header"><button class="cal-nav" id="cal-prev">&#9664;</button><span class="cal-title">${MO[m]} ${y}</span><button class="cal-nav" id="cal-next">&#9654;</button></div><div class="cal-grid" id="cal-grid">`;
  ['SUN','MON','TUE','WED','THU','FRI','SAT'].forEach(d=>{html+=`<div class="cal-dow">${d}</div>`;});
  for(let i=0;i<firstDow;i++)html+='<div class="cal-day cal-empty"></div>';
  for(let d=1;d<=dim;d++){
    const k=y+'-'+String(m+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');
    const trs=byDate[k]||[];
    const pnl=trs.reduce((s,r)=>s+(r.pnl||0),0);
    const pc=trs.length===0?'':(pnl>0?'pnl-win':pnl<0?'pnl-loss':'pnl-flat');
    const pnlStr=trs.length===0?'':(pnl>=0?'+$':'-$')+Math.abs(pnl).toFixed(0);
    html+=`<div class="cal-day ${pc}${trs.length?' has-trades':''}" data-date="${k}">${d}${trs.length?`<div class="cal-dot">${trs.length}T</div><div class="cal-pnl">${pnlStr}</div>`:''}</div>`;
  }
  html+='</div>';
  const calEl=document.getElementById('calendar');
  if(slideDir){
    const outX=slideDir==='left'?'-100%':'100%';
    const inX=slideDir==='left'?'100%':'-100%';
    anime({targets:calEl,translateX:[0,outX],opacity:[1,0],duration:180,easing:'easeInCubic',complete:()=>{
      calEl.innerHTML=html;
      anime({targets:calEl,translateX:[inX,'0%'],opacity:[0,1],duration:300,easing:'easeOutCubic',complete:()=>wireCal(byDate,true)});
    }});
  } else {
    calEl.innerHTML=html;
    wireCal(byDate,doAnimate);
  }
}

function wireCal(byDate,doAnimate){
  const days=document.querySelectorAll('#cal-grid .cal-day:not(.cal-empty)');
  if(doAnimate){
    anime({targets:days,translateY:[-20,0],opacity:[0,1],delay:anime.stagger(18),duration:280,easing:'easeOutCubic'});
    setTimeout(()=>{
      const gw=document.querySelectorAll('#cal-grid .pnl-win');
      const gl=document.querySelectorAll('#cal-grid .pnl-loss');
      if(gw.length)anime({targets:gw,boxShadow:['0 0 0px rgba(57,255,20,0)','0 0 9px rgba(57,255,20,.55)','0 0 0px rgba(57,255,20,0)'],duration:1400,delay:anime.stagger(22),easing:'easeInOutSine'});
      if(gl.length)anime({targets:gl,boxShadow:['0 0 0px rgba(255,59,86,0)','0 0 9px rgba(255,59,86,.45)','0 0 0px rgba(255,59,86,0)'],duration:1400,delay:anime.stagger(22),easing:'easeInOutSine'});
    },480);
  } else {
    days.forEach(e=>e.style.opacity='1');
  }
  const prev=document.getElementById('cal-prev');
  const next=document.getElementById('cal-next');
  if(prev)prev.addEventListener('click',()=>navCal(-1));
  if(next)next.addEventListener('click',()=>navCal(1));
  document.querySelectorAll('#cal-grid .has-trades').forEach(el=>{
    el.addEventListener('click',function(){
      anime({targets:this,scale:[1,1.08,1],duration:260,easing:'easeOutBack'});
      const date=this.dataset.date;
      setTimeout(()=>openDayModal(date,byDate[date]||[]),100);
    });
  });
  let tx0=null;
  const grid=document.getElementById('cal-grid');
  if(grid){
    grid.addEventListener('touchstart',e=>{tx0=e.touches[0].clientX;},{passive:true});
    grid.addEventListener('touchend',e=>{if(tx0===null)return;const dx=e.changedTouches[0].clientX-tx0;tx0=null;if(Math.abs(dx)>44)navCal(dx<0?1:-1);},{passive:true});
  }
}

function navCal(dir){
  calMonth.setMonth(calMonth.getMonth()+dir);
  if(STATE)buildCalendar(STATE.closed,true,dir<0?'right':'left');
}

// ── day modal ────────────────────────────────────────────────────────
function openDayModal(dateStr,trades){
  const[y,m,d]=dateStr.split('-');
  const pnl=trades.reduce((s,r)=>s+(r.pnl||0),0);
  document.getElementById('modal-title').textContent=MO[parseInt(m)-1]+' '+parseInt(d)+', '+y;
  document.getElementById('modal-pnl').innerHTML=`<span class="${pnl>=0?'pos':'neg'}">${money(pnl)}</span>`;
  let rows='';
  [...trades].sort((a,b)=>(a.exit_time||'')>(b.exit_time||'')?-1:1).forEach(r=>{
    rows+=`<div class="modal-trade">
      <div class="mt-top"><span class="mt-ticker ${r.side==='PUT'?'put':'call'}">${r.ticker} ${r.side||''} ${r.strike||''}</span><span class="mt-outcome ${r.outcome==='win'?'pos':'neg'}">${r.outcome.toUpperCase()}</span></div>
      <div class="mt-row">ENTRY $${(r.entry||0).toFixed(2)} &rarr; EXIT $${(r.exit||0).toFixed(2)}</div>
      <div class="mt-row">STRATEGY: ${r.strategy||'&mdash;'}</div>
      <div class="mt-pnl ${r.pnl>=0?'pos':'neg'}">${money(r.pnl)}</div>
    </div>`;
  });
  document.getElementById('modal-body').innerHTML=rows||'<div class="empty">NO TRADES</div>';
  const bd=document.getElementById('modal-backdrop');
  const md=document.getElementById('day-modal');
  bd.style.display='block';bd.style.opacity='0';
  md.style.display='flex';md.style.transform='translateY(100%)';
  anime({targets:bd,opacity:[0,1],duration:240,easing:'easeOutCubic'});
  anime({targets:md,translateY:['100%','0%'],duration:420,easing:'easeOutExpo'});
}

function closeModal(){
  const bd=document.getElementById('modal-backdrop');
  const md=document.getElementById('day-modal');
  anime({targets:bd,opacity:[1,0],duration:200,easing:'easeInCubic',complete:()=>{bd.style.display='none';}});
  anime({targets:md,translateY:['0%','100%'],duration:280,easing:'easeInCubic',complete:()=>{md.style.display='none';}});
}

// ── open position cards ──────────────────────────────────────────────
function buildOpenCards(open,doAnimate){
  const el=document.getElementById('open-cards');
  if(!open.length){el.innerHTML='<div class="empty">NO OPEN POSITIONS</div>';return;}
  const prevTickers=new Set(Array.from(el.querySelectorAll('[data-tick]')).map(e=>e.dataset.tick));
  let html='';
  open.forEach((r,i)=>{
    const sc=r.side==='PUT'?'put':'call';
    const upnlPct=(r.mark!=null&&r.entry>0)?((r.mark-r.entry)/r.entry*100).toFixed(1)+'%':'&mdash;';
    const isNew=doAnimate&&!prevTickers.has(r.ticker);
    html+=`<div class="open-card${isNew?' oc-new':''}" data-tick="${r.ticker}" data-idx="${i}">
      <div class="oc-main">
        <span class="oc-ticker ${sc}">${r.ticker}</span>
        <span class="oc-side ${sc}">${r.side}</span>
        <span class="oc-strike muted">${r.strike}</span>
        <span class="oc-mark">${r.mark==null?'<span class="muted">&mdash;</span>':'$'+r.mark.toFixed(2)}</span>
        <span class="oc-upnl ${cls(r.upnl)}">${money(r.upnl)}</span>
        <span class="oc-chev" id="chev-${i}">&#9662;</span>
      </div>
      <div class="oc-detail" id="ocd-${i}"><div class="oc-detail-inner">
        ENTRY $${r.entry.toFixed(2)} &bull; U-P&amp;L% ${upnlPct}<br>
        EXP ${r.exp||'&mdash;'} &bull; CONTRACTS ${r.contracts||1}<br>
        OPENED ${fmtTime(r.open_time)}<br>
        STRATEGY ${r.strategy||'&mdash;'}
      </div></div>
    </div>`;
  });
  const prevExpanded=expandedIdx;
  expandedIdx=null;
  el.innerHTML=html;
  if(doAnimate){
    const news=el.querySelectorAll('.oc-new');
    if(news.length)anime({targets:news,translateX:[-30,0],opacity:[0,1],duration:400,easing:'easeOutCubic'});
  }
  el.querySelectorAll('.open-card').forEach((card,i)=>{
    card.addEventListener('click',()=>toggleCard(i));
  });
  // restore previously expanded card without animation so 5s refresh doesn't close it
  if(prevExpanded!==null){
    const det=document.getElementById('ocd-'+prevExpanded);
    const chv=document.getElementById('chev-'+prevExpanded);
    if(det){
      const inner=det.querySelector('.oc-detail-inner');
      const h=(inner?inner.scrollHeight:60)+14;
      det.style.height=h+'px'; det.style.opacity='1';
      if(chv)chv.innerHTML='&#9652;';
      expandedIdx=prevExpanded;
    }
  }
}

function toggleCard(i){
  const detail=document.getElementById('ocd-'+i);
  const chev=document.getElementById('chev-'+i);
  if(!detail)return;
  const isOpen=expandedIdx===i;
  if(isOpen){
    anime({targets:detail,height:[detail.scrollHeight,0],opacity:[1,0],duration:300,easing:'easeInOutQuart'});
    if(chev)chev.innerHTML='&#9662;';
    expandedIdx=null;
  } else {
    if(expandedIdx!==null){
      const prev=document.getElementById('ocd-'+expandedIdx);
      if(prev)anime({targets:prev,height:[prev.scrollHeight,0],opacity:[1,0],duration:200,easing:'easeInQuart'});
      const pc=document.getElementById('chev-'+expandedIdx);
      if(pc)pc.innerHTML='&#9662;';
    }
    expandedIdx=i;
    const inner=detail.querySelector('.oc-detail-inner');
    const h=(inner?inner.scrollHeight:60)+14;
    anime({targets:detail,height:[0,h],opacity:[0,1],duration:360,easing:'easeInOutQuart'});
    if(chev)chev.innerHTML='&#9652;';
  }
}

// ── closed trades ────────────────────────────────────────────────────
function _thisWeekBounds(){
  const now=new Date(),dow=now.getDay(),mo=dow===0?6:dow-1;
  const s=new Date(now);s.setDate(now.getDate()-mo);s.setHours(0,0,0,0);
  const e=new Date(s);e.setDate(s.getDate()+6);e.setHours(23,59,59,999);
  return{s,e};
}

function applyClosedFilter(){
  const rows=document.querySelectorAll('#closedtbl .trade-row');
  const showing=[];
  const{s:wkS,e:wkE}=_thisWeekBounds();
  rows.forEach(r=>{
    let ok;
    if(currentMonth==='__thisweek__'){
      ok=r.dataset.thisweek==='1';
    } else {
      ok=(currentMonth==='all'||r.dataset.month===currentMonth)&&(currentWeek==='all'||r.dataset.week===currentWeek);
    }
    if(ok){r.style.display='';showing.push(r);}else r.style.display='none';
  });
  if(showing.length)anime({targets:showing,opacity:[0,1],translateX:[4,0],delay:anime.stagger(7),duration:160,easing:'easeOutCubic'});
}

function buildWeekTabs(closed){
  const el=document.getElementById('week-tabs');
  if(currentMonth==='all'||currentMonth==='__thisweek__'){el.style.display='none';return;}
  const ws=[],seen={};
  closed.forEach(r=>{const ts=r.open_time||r.exit_time;if(!ts)return;const d=new Date(ts);if(monthKey(d)!==currentMonth)return;const w=weekOfMonth(d).toString();if(!seen[w]){seen[w]=true;ws.push(w);}});
  ws.sort();
  el.innerHTML='<button class="tab'+(currentWeek==='all'?' active':'')+'" data-week="all">ALL WEEKS</button>';
  ws.forEach(w=>{el.innerHTML+=`<button class="tab${currentWeek===w?' active':''}" data-week="${w}">WEEK ${w}</button>`;});
  el.style.display='flex';
  el.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',function(){
    el.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    this.classList.add('active');currentWeek=this.dataset.week;applyClosedFilter();
    anime({targets:this,scale:[.88,1],duration:160,easing:'easeOutBack'});
  }));
}

function buildMonthTabs(closed){
  const ms=[],seen={};
  closed.forEach(r=>{const ts=r.open_time||r.exit_time;if(!ts)return;const k=monthKey(new Date(ts));if(!seen[k]){seen[k]=true;ms.push(k);}});
  ms.sort().reverse();
  const el=document.getElementById('month-tabs');
  // THIS WEEK first, then months, then ALL
  el.innerHTML=`<button class="tab${currentMonth==='__thisweek__'?' active':''}" data-month="__thisweek__">THIS WEEK</button>`;
  ms.forEach(m=>{el.innerHTML+=`<button class="tab${currentMonth===m?' active':''}" data-month="${m}">${MO[parseInt(m.split('-')[1])-1]} ${m.split('-')[0]}</button>`;});
  el.innerHTML+=`<button class="tab${currentMonth==='all'?' active':''}" data-month="all">ALL</button>`;
  el.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',function(){
    el.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    this.classList.add('active');currentMonth=this.dataset.month;currentWeek='all';
    buildWeekTabs(closed);applyClosedFilter();
    anime({targets:this,scale:[.88,1],duration:160,easing:'easeOutBack'});
  }));
}

function buildClosed(closed,doAnimate){
  const{s:wkS,e:wkE}=_thisWeekBounds();
  let ch='<tr><th>TICKER</th><th>SIDE</th><th>STRIKE</th><th>RESULT</th><th>OPENED</th><th>CLOSED</th><th>ENTRY</th><th>EXIT</th><th>P&L</th><th>STRAT</th></tr>';
  if(!closed.length){document.getElementById('closedtbl').innerHTML=ch+'<tr><td colspan="10" class="empty">NOTHING CLOSED YET</td></tr>';return;}
  closed.forEach(r=>{
    const ts=r.open_time||r.exit_time;let dm='unknown',dw='0';
    if(ts){const d=new Date(ts);dm=monthKey(d);dw=weekOfMonth(d).toString();}
    const exitD=r.exit_time?new Date(r.exit_time):null;
    const isThisWeek=exitD&&exitD>=wkS&&exitD<=wkE?'1':'0';
    const res=r.outcome==='win'?'<span class="win">WIN</span>':'<span class="loss">'+(r.outcome==='loss'?'LOSS':'EXP')+'</span>';
    ch+=`<tr class="trade-row ${r.outcome==='win'?'trade-win':'trade-loss'}" data-month="${dm}" data-week="${dw}" data-thisweek="${isThisWeek}">
      <td class="${r.side==='PUT'?'put':'call'}">${r.ticker}</td>
      <td class="${r.side==='PUT'?'put':'call'}">${r.side||'&mdash;'}</td>
      <td>${r.strike==null?'&mdash;':r.strike}</td>
      <td>${res}</td>
      <td class="td-time">${fmtTime(r.open_time)}</td>
      <td class="td-time">${fmtTime(r.exit_time)}</td>
      <td>${r.entry==null?'&mdash;':'$'+r.entry.toFixed(2)}</td>
      <td>${r.exit==null?'&mdash;':'$'+r.exit.toFixed(2)}</td>
      <td class="${cls(r.pnl)}">${money(r.pnl)}</td>
      <td class="muted" style="font-size:9px">${r.strategy}</td></tr>`;
  });
  document.getElementById('closedtbl').innerHTML=ch;
  buildMonthTabs(closed);buildWeekTabs(closed);applyClosedFilter();
  if(doAnimate){
    anime({targets:'.trade-win',backgroundColor:['rgba(57,255,20,.12)','rgba(57,255,20,0)'],duration:800,delay:anime.stagger(12,{start:200}),easing:'easeOutQuad'});
    anime({targets:'.trade-loss',backgroundColor:['rgba(255,59,86,.12)','rgba(255,59,86,0)'],duration:800,delay:anime.stagger(12,{start:300}),easing:'easeOutQuad'});
  }
}

// ── main tick ────────────────────────────────────────────────────────
async function tick(){
  let s;
  if(window.__GH_STATE){ s=window.__GH_STATE; }
  else{
    try{s=await(await fetch('/api/state',{cache:'no-store'})).json();}
    catch(e){document.getElementById('foot').innerHTML='&#9888; LOST CONNECTION';return;}
  }
  STATE=s;
  const v=s.vitals,t=s.totals;

  document.getElementById('bar').innerHTML=
    `<span class="chip">MARKET: <b class="${s.market_open?'on':'off'}">${s.market_open?'OPEN':'CLOSED'}</b></span>`
    +chip('RUNNER',v.runner)+chip('TRACKER',v.tracker)+chip('FEED',v.feed,v.feed?'LIVE':'NO DATA')
    +`<span class="chip">CLOCK: <b class="glow-c">${s.server_time_ct}</b></span>`
    +`<span class="chip">LAST POLL: <b>${v.last_poll||'&mdash;'}</b></span>`;

  buildWeekPanel(s.closed,!initialized);
  buildStats(s.closed,t,!initialized);
  buildOpenCards(s.open,!initialized);

  if(!initialized||s.closed.length!==prevClosedCount){
    prevClosedCount=s.closed.length;
    buildClosed(s.closed,!initialized);
    buildCalendar(s.closed,!initialized,null);
  }

  document.getElementById('foot').innerHTML=
    `<span class="live"></span> LIVE &middot; REFRESHES EVERY 5S &middot; MARKS VIA BLACK-SCHOLES${s.market_open?'':' (FROZEN &mdash; MARKET CLOSED)'}`;

  if(!initialized)setTimeout(startChipGlow,900);
  initialized=true;
}

// ── boot ─────────────────────────────────────────────────────────────
animateTitle();
buildRangeSelector();
tick();
if(!window.__GH_STATE) setInterval(tick,5000);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence per-request console spam
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            try:
                body = json.dumps(build_state()).encode("utf-8")
                self._send(body, "application/json")
            except Exception as e:
                self._send(json.dumps({"error": str(e)}).encode(), "application/json", 500)
        else:
            self._send(b"not found", "text/plain", 404)


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # no traffic sent; just picks the route
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def main() -> None:
    port = PORT
    for attempt in range(10):
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
            break
        except OSError:
            port += 1
    else:
        raise SystemExit("could not bind a port in 8787-8796")
    srv.daemon_threads = True
    ip = _lan_ip()
    print("\n  LIL TONY PAPER ARCADE is live\n")
    print(f"    on this Mac:        http://localhost:{port}")
    print(f"    on iPhone / iPad:   http://{ip}:{port}   (same WiFi)\n")
    print("  read-only — never touches the ledger. Ctrl-C to stop.\n")
    save_gh_pages()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  arcade closed.\n")
        srv.shutdown()


def save_gh_pages() -> None:
    """Render PAGE with current ledger state baked in as window.__GH_STATE,
    then push only docs/paper-arcade/index.html. GitHub Pages is static —
    no /api/state — so the data has to be inlined or the page is blank."""
    dest = ROOT / "docs" / "paper-arcade" / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = build_state()
    except Exception as e:
        print(f"  [gh-pages] build_state failed ({e}) — snapshot not pushed")
        return
    # Escape </ in any string field so an embedded </script> can't break out.
    inline = json.dumps(state, default=str).replace("</", "<\\/")
    marker = "</body></html>"
    if marker not in PAGE:
        print("  [gh-pages] PAGE missing </body></html> marker — aborting")
        return
    rendered = PAGE.replace(
        marker,
        f"<script>window.__GH_STATE={inline};</script>\n</body></html>",
    )
    dest.write_text(rendered, encoding="utf-8")
    rel = str(dest.relative_to(ROOT))
    cmds = [
        ["git", "-C", str(ROOT), "add", rel],
        ["git", "-C", str(ROOT), "commit", "--allow-empty",
         "-m", "chore: update paper-arcade GitHub Pages snapshot"],
        ["git", "-C", str(ROOT), "push", "origin", "main"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [gh-pages] {cmd[-1]} failed: {result.stderr.strip()}")
            return
    print("  [gh-pages] docs/paper-arcade/index.html pushed.")


if __name__ == "__main__":
    main()
