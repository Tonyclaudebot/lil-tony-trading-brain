"""
Risk assessment for Lil Tony trade alerts.

Called in selector.py after every build_plan, applies to all 4 strategies.
Results are stored on TradePlan and included in every alert format.
"""

from __future__ import annotations

from scanner.strategies.base import TradePlan

_SHORT_DTE  = 7     # DTE ≤ 7  → short-dated risk
_MEDIUM_DTE = 21    # DTE ≤ 21 → moderate theta decay


def assess(plan: TradePlan) -> None:
    """Compute risk fields and attach them to plan in-place."""
    iv_rank    = _resolve_iv_rank(plan)
    iv_label   = _iv_label(iv_rank)
    prem_label = _premium_label(plan.entry)

    risk_pts = 0
    reasons: list[str] = []

    # IV rank
    if iv_label == "HIGH":
        risk_pts += 2
        reasons.append(f"high IV rank ({iv_rank:.0f})")
    elif iv_label == "MEDIUM":
        risk_pts += 1

    # Premium cost
    if prem_label == "EXPENSIVE":
        risk_pts += 2
        reasons.append(f"expensive premium (${plan.entry:.2f})")
    elif prem_label == "FAIR":
        risk_pts += 1

    # Time decay
    if plan.dte <= _SHORT_DTE:
        risk_pts += 2
        reasons.append(f"short-dated ({plan.dte}d to expiry)")
    elif plan.dte <= _MEDIUM_DTE:
        risk_pts += 1
        reasons.append(f"moderate theta ({plan.dte}d)")

    # Strategy confidence
    if plan.score < 40:
        risk_pts += 2
        reasons.append(f"low score ({plan.score:.0f})")
    elif plan.score < 60:
        risk_pts += 1

    # Strike distance (OTM spread)
    otm_pct = abs(plan.strike - plan.spot) / plan.spot if plan.spot > 0 else 0
    if otm_pct > 0.05:
        risk_pts += 1
        reasons.append(f"strike {otm_pct*100:.0f}% OTM")

    overall = "HIGH" if risk_pts >= 5 else ("MEDIUM" if risk_pts >= 2 else "LOW")

    summary = (
        "Watch: " + "; ".join(reasons[:2])
        if reasons
        else "Clean setup — IV, premium, and time decay all in check"
    )

    plan.risk_iv_rank       = round(iv_rank, 1)
    plan.risk_iv_label      = iv_label
    plan.risk_premium_label = prem_label
    plan.risk_score         = overall
    plan.risk_summary       = summary


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_iv_rank(plan: TradePlan) -> float:
    """Use the stored IV rank (set by VB strategy) or derive a proxy from plan.iv."""
    if plan.risk_iv_rank is not None:
        return plan.risk_iv_rank
    # Proxy: plan.iv is stored as a percentage (e.g. 45.0 for 45% annualized IV).
    # Map to a 0-100 rank: IV 20% → ~30, IV 40% → ~60, IV 67% → ~100.
    return min(100.0, plan.iv * 1.5) if plan.iv else 50.0


def _iv_label(rank: float) -> str:
    if rank < 30:  return "LOW"
    if rank <= 60: return "MEDIUM"
    return "HIGH"


def _premium_label(entry: float) -> str:
    if entry <= 0.50: return "CHEAP"
    if entry <= 1.00: return "FAIR"
    return "EXPENSIVE"
