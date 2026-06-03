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
  :root{
    --bg:#05060a; --cyan:#22f0ff; --mag:#ff2bd6; --grn:#39ff14;
    --yel:#fde047; --red:#ff3b56; --dim:#5a6b7a; --panel:#0a0e16;
  }
  *{box-sizing:border-box;}
  html,body{margin:0;background:var(--bg);}
  body{
    font-family:"Courier New",ui-monospace,Menlo,Consolas,monospace;
    color:var(--cyan); text-transform:uppercase; letter-spacing:.08em;
    padding:14px; min-height:100vh; -webkit-text-size-adjust:100%;
    animation:flick 5s infinite steps(60);
  }
  @keyframes flick{0%,97%{opacity:1}98%{opacity:.92}99%{opacity:.97}100%{opacity:1}}
  /* CRT scanlines */
  body::after{
    content:""; position:fixed; inset:0; pointer-events:none; z-index:99;
    background:repeating-linear-gradient(0deg,rgba(0,0,0,0) 0,rgba(0,0,0,0) 2px,rgba(0,0,0,.22) 3px);
    mix-blend-mode:multiply;
  }
  h1{
    font-size:clamp(18px,5vw,30px); margin:0 0 2px; color:var(--mag);
    text-shadow:0 0 6px var(--mag),0 0 16px var(--mag);
  }
  .sub{color:var(--dim); font-size:11px; margin-bottom:12px;}
  .glow-c{text-shadow:0 0 5px var(--cyan),0 0 12px var(--cyan);}
  .bar{display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px;}
  .chip{
    border:1px solid #14304a; background:var(--panel); padding:6px 10px;
    font-size:11px; border-radius:4px; box-shadow:0 0 8px rgba(34,240,255,.08) inset;
  }
  .on{color:var(--grn); text-shadow:0 0 6px var(--grn);}
  .off{color:var(--red); text-shadow:0 0 6px var(--red);}
  .scores{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:18px;}
  .score{border:1px solid #14304a; background:var(--panel); border-radius:6px; padding:12px;}
  .score .lab{font-size:10px; color:var(--dim); margin-bottom:6px;}
  .score .val{font-size:clamp(20px,6vw,30px); font-weight:bold;}
  h2{font-size:13px; color:var(--yel); text-shadow:0 0 6px var(--yel); margin:18px 0 8px; border-bottom:1px dashed #2a3a4a; padding-bottom:5px;}
  .scroll{overflow-x:auto;}
  table{border-collapse:collapse; width:100%; font-size:12px; min-width:560px;}
  th{color:var(--dim); font-size:10px; text-align:right; padding:6px 8px; border-bottom:1px solid #1b2c3c;}
  th:first-child,td:first-child{text-align:left;}
  td{padding:7px 8px; border-bottom:1px solid #101a26; text-align:right; white-space:nowrap;}
  .call{color:var(--cyan); text-shadow:0 0 5px var(--cyan);}
  .put{color:var(--mag); text-shadow:0 0 5px var(--mag);}
  .pos{color:var(--grn); text-shadow:0 0 6px var(--grn);}
  .neg{color:var(--red); text-shadow:0 0 6px var(--red);}
  .win{color:var(--grn);} .loss{color:var(--red);}
  .muted{color:var(--dim);}
  .gauge{width:80px; height:9px; background:#0d1622; border:1px solid #1b2c3c; border-radius:5px; overflow:hidden; display:inline-block; vertical-align:middle;}
  .gauge>i{display:block; height:100%; background:linear-gradient(90deg,var(--red),var(--yel),var(--grn));}
  .live{display:inline-block; width:9px; height:9px; border-radius:50%; background:var(--grn); box-shadow:0 0 8px var(--grn); animation:blink 1.1s infinite;}
  @keyframes blink{50%{opacity:.25}}
  .foot{color:var(--dim); font-size:10px; margin-top:18px; text-align:center;}
  .empty{color:var(--dim); padding:14px 4px;}
  /* Week panel */
  .week-panel{border:1px solid #14304a;background:var(--panel);border-radius:6px;padding:12px;margin-bottom:16px;}
  .wp-hdr{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;}
  .wp-title{font-size:11px;color:var(--yel);text-shadow:0 0 6px var(--yel);letter-spacing:.1em;}
  .wp-date{font-size:10px;color:var(--dim);}
  /* Month / week tabs */
  .tab-group{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;}
  .tab{background:var(--panel);border:1px solid #14304a;color:var(--dim);padding:4px 11px;
       border-radius:20px;font-family:inherit;font-size:10px;text-transform:uppercase;
       letter-spacing:.08em;cursor:pointer;}
  .tab:hover{color:var(--cyan);}
  .tab.active{border-color:var(--cyan);color:var(--cyan);text-shadow:0 0 5px var(--cyan);}
  #week-tabs{display:none;margin-bottom:12px;}
  /* Timestamps */
  .td-time{color:var(--dim);font-size:10px;white-space:nowrap;}
  /* Open-position pulse */
  @keyframes open-pulse{0%,100%{opacity:1}50%{opacity:.45}}
  .open-row{animation:open-pulse 2.8s ease-in-out infinite;}
</style></head>
<body>
  <h1>&#9646; LIL TONY &mdash; PAPER ARCADE &#9646;</h1>
  <div class="sub">SIMULATED LEDGER &middot; NO REAL MONEY &middot; MAX $100/TRADE</div>
  <div class="bar" id="bar"></div>
  <div id="week-panel" class="week-panel"></div>
  <div class="scores" id="scores"></div>
  <h2>&#9656; OPEN POSITIONS</h2>
  <div class="scroll"><table id="opentbl"></table></div>
  <h2>&#9656; CLOSED &mdash; RECENT</h2>
  <div id="month-tabs" class="tab-group"></div>
  <div id="week-tabs" class="tab-group"></div>
  <div class="scroll"><table id="closedtbl"></table></div>
  <div class="foot" id="foot">connecting&hellip;</div>

<script>
const money=(v)=>(v==null?"&mdash;":(v<0?"-$":"$")+Math.abs(v).toFixed(2));
const cls=(v)=>v==null?"muted":(v>=0?"pos":"neg");
function chip(label,ok,txt){return `<span class="chip">${label}: <b class="${ok?'on':'off'}">${txt||(ok?'ONLINE':'OFFLINE')}</b></span>`;}
function score(lab,val,c){return `<div class="score"><div class="lab">${lab}</div><div class="val ${c||''}">${val}</div></div>`;}

// Tab state — persists across 5s tick loop
let currentMonth='all', currentWeek='all';

function fmtTime(ts){
  if(!ts) return '&mdash;';
  try{
    const d=new Date(ts);
    return String(d.getMonth()+1).padStart(2,'0')+'/'+String(d.getDate()).padStart(2,'0')+
           ' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
  }catch(e){return '&mdash;';}
}
function monthKey(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');}
function monthLabel(k){
  const[y,m]=k.split('-');
  return ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'][parseInt(m)-1]+' '+y;
}
function weekOfMonth(d){return Math.ceil(d.getDate()/7);}

function buildWeekPanel(closed){
  const now=new Date();
  const dow=now.getDay();
  const mo=dow===0?6:dow-1;
  const wkS=new Date(now); wkS.setDate(now.getDate()-mo); wkS.setHours(0,0,0,0);
  const wkE=new Date(wkS); wkE.setDate(wkS.getDate()+6); wkE.setHours(23,59,59,999);
  const wkT=closed.filter(r=>{
    const ts=r.exit_time||r.open_time; if(!ts) return false;
    const d=new Date(ts); return d>=wkS&&d<=wkE;
  });
  const wkW=wkT.filter(r=>r.outcome==='win').length;
  const wkL=wkT.filter(r=>r.outcome!=='win').length;
  const wkC=wkW+wkL;
  const wkR=wkC?(wkW/wkC*100).toFixed(1):null;
  const wkP=wkT.reduce((s,r)=>s+(r.pnl||0),0);
  const fmtD=d=>['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()]+' '+d.getDate();
  document.getElementById('week-panel').innerHTML=
    `<div class="wp-hdr"><span class="wp-title">&#9658; THIS WEEK</span><span class="wp-date">${fmtD(wkS)} &ndash; ${fmtD(wkE)}</span></div>`+
    `<div class="scores" style="margin-bottom:0">`+
    score('WIN RATE',  wkR==null?'&mdash;':`<span data-cu="${wkR}" data-sfx="%">0%</span>`,'glow-c')+
    score('P&amp;L',  `<span class="${cls(wkP)}" data-cu="${Math.abs(wkP).toFixed(0)}" data-pfx="${wkP<0?'-$':'$'}">$0</span>`,'')+
    score('WINS',     `<span class="pos" data-cu="${wkW}">0</span>`,'')+
    score('LOSSES',   `<span class="neg" data-cu="${wkL}">0</span>`,'')+
    `</div>`;
  document.querySelectorAll('[data-cu]').forEach(el=>{
    const target=parseFloat(el.dataset.cu)||0;
    const pfx=el.dataset.pfx||''; const sfx=el.dataset.sfx||'';
    const dec=sfx==='%'?1:0;
    const obj={val:0};
    anime({targets:obj,val:target,duration:900,easing:'easeOutExpo',
      update(){el.textContent=pfx+obj.val.toFixed(dec)+sfx;}});
  });
}

function applyClosedFilter(){
  const rows=document.querySelectorAll('#closedtbl .trade-row');
  const showing=[];
  rows.forEach(r=>{
    const ok=(currentMonth==='all'||r.dataset.month===currentMonth)&&
             (currentWeek==='all' ||r.dataset.week===currentWeek);
    if(ok){r.style.display='';showing.push(r);}
    else r.style.display='none';
  });
  if(showing.length)
    anime({targets:showing,opacity:[0,1],translateX:[5,0],delay:anime.stagger(10),duration:200,easing:'easeOutCubic'});
}

function buildWeekTabs(closed){
  const el=document.getElementById('week-tabs');
  if(currentMonth==='all'){el.style.display='none';return;}
  const ws=[]; const seen={};
  closed.forEach(r=>{
    const ts=r.open_time||r.exit_time; if(!ts) return;
    const d=new Date(ts); if(monthKey(d)!==currentMonth) return;
    const w=weekOfMonth(d).toString();
    if(!seen[w]){seen[w]=true;ws.push(w);}
  });
  ws.sort();
  el.innerHTML='<button class="tab'+(currentWeek==='all'?' active':'')+'" data-week="all">ALL WEEKS</button>';
  ws.forEach(w=>{el.innerHTML+=`<button class="tab${currentWeek===w?' active':''}" data-week="${w}">WEEK ${w}</button>`;});
  el.style.display='flex';
  el.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',function(){
    el.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    this.classList.add('active'); currentWeek=this.dataset.week;
    applyClosedFilter();
    anime({targets:this,scale:[0.88,1],duration:180,easing:'easeOutBack'});
  }));
}

function buildMonthTabs(closed){
  const ms=[]; const seen={};
  closed.forEach(r=>{
    const ts=r.open_time||r.exit_time; if(!ts) return;
    const k=monthKey(new Date(ts));
    if(!seen[k]){seen[k]=true;ms.push(k);}
  });
  ms.sort().reverse();
  const el=document.getElementById('month-tabs');
  el.innerHTML='<button class="tab'+(currentMonth==='all'?' active':'')+'" data-month="all">ALL</button>';
  ms.forEach(m=>{el.innerHTML+=`<button class="tab${currentMonth===m?' active':''}" data-month="${m}">${monthLabel(m)}</button>`;});
  el.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',function(){
    el.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    this.classList.add('active'); currentMonth=this.dataset.month; currentWeek='all';
    buildWeekTabs(closed); applyClosedFilter();
    anime({targets:this,scale:[0.88,1],duration:180,easing:'easeOutBack'});
  }));
}

async function tick(){
  let s;
  try{s=await(await fetch('/api/state',{cache:'no-store'})).json();}
  catch(e){document.getElementById('foot').innerHTML='&#9888; lost connection to dashboard';return;}
  const v=s.vitals,t=s.totals;
  document.getElementById('bar').innerHTML=
      `<span class="chip">MARKET: <b class="${s.market_open?'on':'off'}">${s.market_open?'OPEN':'CLOSED'}</b></span>`
    +chip('RUNNER',v.runner)+chip('TRACKER',v.tracker)+chip('FEED',v.feed,v.feed?'LIVE':'NO DATA')
    +`<span class="chip">CLOCK: <b class="glow-c">${s.server_time_ct}</b></span>`
    +`<span class="chip">LAST POLL: <b>${v.last_poll||'&mdash;'}</b></span>`;

  buildWeekPanel(s.closed);

  document.getElementById('scores').innerHTML=
      score('REALIZED P&L',money(t.realized),cls(t.realized))
    +score('UNREALIZED P&L',money(t.unrealized),cls(t.unrealized))
    +score('RECORD W&ndash;L',`${t.wins}&ndash;${t.losses}`,'glow-c')
    +score('WIN RATE',t.win_rate==null?'&mdash;':t.win_rate+'%','glow-c')
    +score('OPEN',t.open,'glow-c');
  anime({targets:'#scores .score',translateY:[14,0],opacity:[0,1],delay:anime.stagger(55),duration:400,easing:'easeOutCubic'});

  // open table — OPENED column added
  let oh='<tr><th>TICKER</th><th>SIDE</th><th>STRIKE</th><th>EXP</th><th>OPENED</th><th>ENTRY</th><th>MARK</th><th>U-P&L</th><th>STOP&rarr;TGT</th><th>STRAT</th></tr>';
  if(!s.open.length){document.getElementById('opentbl').innerHTML=oh+`<tr><td colspan="10" class="empty">no open paper positions</td></tr>`;}
  else{
    for(const r of s.open){
      const sc=r.side==='PUT'?'put':'call';
      const g=r.prog==null?'<span class="muted">&mdash;</span>':`<span class="gauge"><i style="width:${r.prog}%"></i></span>`;
      oh+=`<tr class="open-row">
        <td class="${sc}">${r.ticker}</td><td class="${sc}">${r.side}</td>
        <td>${r.strike}</td><td class="muted">${r.exp||'&mdash;'}</td>
        <td class="td-time">${fmtTime(r.open_time)}</td>
        <td>$${r.entry.toFixed(2)}</td>
        <td>${r.mark==null?'<span class="muted">&mdash;</span>':'$'+r.mark.toFixed(2)}</td>
        <td class="${cls(r.upnl)}">${money(r.upnl)}</td>
        <td>${g}</td><td class="muted">${r.strategy}</td></tr>`;
    }
    document.getElementById('opentbl').innerHTML=oh;
  }

  // closed table — OPENED + CLOSED columns, data-month/data-week for filtering
  let ch='<tr><th>TICKER</th><th>SIDE</th><th>STRIKE</th><th>RESULT</th><th>OPENED</th><th>CLOSED</th><th>ENTRY</th><th>EXIT</th><th>P&L</th><th>STRAT</th></tr>';
  if(!s.closed.length){document.getElementById('closedtbl').innerHTML=ch+`<tr><td colspan="10" class="empty">nothing closed yet</td></tr>`;}
  else{
    for(const r of s.closed){
      const ts=r.open_time||r.exit_time;
      let dm='unknown',dw='0';
      if(ts){const d=new Date(ts);dm=monthKey(d);dw=weekOfMonth(d).toString();}
      const res=r.outcome==='win'?'<span class="win">WIN</span>':'<span class="loss">'+(r.outcome==='loss'?'LOSS':'EXPIRED')+'</span>';
      ch+=`<tr class="trade-row ${r.outcome==='win'?'trade-win':'trade-loss'}" data-month="${dm}" data-week="${dw}">
        <td class="${r.side==='PUT'?'put':'call'}">${r.ticker}</td>
        <td class="${r.side==='PUT'?'put':'call'}">${r.side||'&mdash;'}</td>
        <td>${r.strike==null?'&mdash;':r.strike}</td>
        <td>${res}</td>
        <td class="td-time">${fmtTime(r.open_time)}</td>
        <td class="td-time">${fmtTime(r.exit_time)}</td>
        <td>${r.entry==null?'&mdash;':'$'+r.entry.toFixed(2)}</td>
        <td>${r.exit==null?'&mdash;':'$'+r.exit.toFixed(2)}</td>
        <td class="${cls(r.pnl)}">${money(r.pnl)}</td>
        <td class="muted">${r.strategy}</td></tr>`;
    }
    document.getElementById('closedtbl').innerHTML=ch;
    buildMonthTabs(s.closed);
    buildWeekTabs(s.closed);
    applyClosedFilter();
    anime({targets:'.trade-win', backgroundColor:['rgba(57,255,20,.12)','rgba(57,255,20,0)'], duration:800,delay:anime.stagger(16,{start:200}),easing:'easeOutQuad'});
    anime({targets:'.trade-loss',backgroundColor:['rgba(255,59,86,.12)','rgba(255,59,86,0)'],duration:800,delay:anime.stagger(16,{start:300}),easing:'easeOutQuad'});
  }

  document.getElementById('foot').innerHTML=
    `<span class="live"></span> live &middot; refreshes every 5s &middot; marks via black-scholes${s.market_open?'':' (frozen — market closed)'}`;
}
tick(); setInterval(tick,5000);
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
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  arcade closed.\n")
        srv.shutdown()


if __name__ == "__main__":
    main()
