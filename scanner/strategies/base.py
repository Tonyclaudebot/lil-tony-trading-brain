from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CandidateStock:
    ticker: str
    spot: float
    ret_1d: float
    ret_5d: float
    rsi: float
    volume_ratio: float
    ma20: float
    ma20_pct: float
    momentum_score: float
    uoa_score: float = 0.0
    composite_score: float = 0.0
    atr_ratio: float = 1.0       # ATR5 / ATR20 — <0.75 = coiling
    range_5d_pct: float = 0.05   # (5d high − low) / spot


@dataclass
class TradePlan:
    # Identity
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Stock + contract
    ticker: str = ""
    contract: str = ""
    opt_type: str = ""       # "call" | "put"
    strike: float = 0.0
    expiration: str = ""     # "YYYY-MM-DD"
    dte: int = 0

    # Strategy
    strategy_key: str = ""   # "momentum_breakout" | "unusual_options_activity" | "mean_reversion"
    strategy_name: str = ""  # Display name

    # Pricing
    spot: float = 0.0
    entry: float = 0.0
    target: float = 0.0
    stop: float = 0.0
    target_pct: float = 0.0  # e.g. 150.0 for +150%

    # Metadata
    iv: float = 0.0
    volume: int = 0
    open_interest: int = 0
    score: float = 0.0
    confidence: str = ""     # "HIGH" | "MEDIUM"

    # Earnings intelligence
    earnings_date: str | None = None
    days_to_earnings: int | None = None
    earnings_proximity_risk: str = "UNKNOWN"   # HIGH | ELEVATED | STANDARD | UNKNOWN
    earnings_beat_rate: float | None = None
    earnings_avg_move: float | None = None
    earnings_warning: str | None = None

    # Macro events
    macro_events: list = field(default_factory=list)   # list of event title strings
    macro_warning: str | None = None

    # Binary event flag (House Rules: never recommend without flagging)
    binary_event_flag: bool = False
    binary_event_detail: str | None = None

    # News context
    news_context: list = field(default_factory=list)   # list of headline strings

    # Risk assessment (computed by scanner/risk.py after build_plan)
    risk_iv_rank: float | None = None   # 0–100 IV rank (proxy or exact)
    risk_iv_label: str = ""             # LOW | MEDIUM | HIGH
    risk_premium_label: str = ""        # CHEAP | FAIR | EXPENSIVE
    risk_score: str = ""                # LOW | MEDIUM | HIGH
    risk_summary: str = ""              # one-line explanation

    # Opening Range Breakout confirmation data (Momentum Breakout only)
    or_high: float | None = None
    or_low: float | None = None
    or_t1: float | None = None
    or_t2: float | None = None
    or_t3: float | None = None
    or_target_hit: str | None = None   # "T1" | "T2" | "T3" | None at alert time

    # Grading (filled in by grader)
    outcome: str | None = None   # "win" | "loss" | "expired_worthless" | None
    graded_at: str | None = None
    exit_price: float | None = None
