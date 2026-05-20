# Lil Tony Trading Brain — Roadmap
**Last Updated:** May 2026  
**Operator:** Big Tony | Dallas, TX  
**Governed by:** Big Tony's House Rules v2.1 (CLAUDE.md)

---

## The Philosophy
> Train first. Level up with data. Never risk real money without proven signal.

Small trades. Math-based. No speculation. Every phase must earn the next one.

---

## Phase 1 — Local Training (ACTIVE NOW)

**Goal:** Build a data foundation. Let the learner figure out what works.

**What's running:**
- Scanner pulls top-moving optionable stocks
- Scores and narrows to Top 3 picks per scan
- Three strategies scoring every pick:
  - Momentum Breakout
  - Unusual Options Activity
  - Mean Reversion
- HTML email + iMessage alerts with Webull deep links
- Local paper ledger (`data/paper_trades.json`) — no broker, pure math
- Black-Scholes repricing every 5 min (8:30 AM – 3:00 PM CT)
- Learner records every close → adjusts strategy weights
- Scoreboard regenerated each pass

**Level-Up Trigger:**
- Minimum 2 weeks of live runs
- At least 1 strategy showing consistent win rate > 55%
- Scoreboard data is clean (state.json reset before go-live)

---

## Phase 2 — Webull Paper Account Integration

**Goal:** Replace local math ledger with real Webull paper account. Real fills, real P&L.

**What gets added:**
- Wire official Webull OpenAPI keys (App Key + App Secret already in `.env`)
- `place_order()` → sends paper trades to Webull paper account
- `get_positions()` → reads real paper P&L instead of BS-estimated P&L
- Real options pricing from live market fills
- Scoreboard pulls actual account balance and positions

**Trade Rules (same as Phase 1):**
- Max $100 per trade
- Target trade size $20–$25
- Max 1 contract if premium > $100 (skip if 1 contract can't fit)
- No duplicate orders

**Level-Up Trigger:**
- Minimum 3 weeks on Webull paper
- Win rate holding > 55% on real fills (not just math)
- No runaway losses — max drawdown < 30% of paper balance
- At least 2 strategies with positive P&L track record

---

## Phase 3 — Live Trading

**Goal:** Real money. Small size. Let the system prove itself before scaling.

**What changes:**
- Single config flag: `PAPER_MODE = False`
- Same strategies, same rules, same scorer
- Same $20–$25 target, $100 hard cap (House Rules)
- Webull deep links still generated for manual review before execution (initially)

**Progression inside Phase 3:**
1. **3a — Manual Confirm:** Alerts fire, Big Tony clicks the deep link and places manually
2. **3b — Auto Execute:** After 3a proves consistent, enable auto order placement
3. **3c — Scale:** Increase position size only after sustained profitability — decided by Big Tony, not the bot

**Hard Rules (never overridden):**
- Never exceed $100 per trade — ever
- Never trade during first 30 min of market open without explicit approval
- KILL command = instant full stop, no questions
- ENDGAME command = full wipe with confirmation popup

---

## Strategy Definitions

| Strategy | Signal | Edge |
|---|---|---|
| Momentum Breakout | Price + volume surge above threshold | Catches early runners |
| Unusual Options Activity | Options volume spike vs open interest | Follows smart money |
| Mean Reversion | Oversold bounce setup | Fades overextended moves |

---

## Data Sources
- **Yahoo Finance** — stock data, options chains
- **Forex Factory** — macro calendar
- **Financial Juice** — news flow
- **Webull OpenAPI** — order execution + deep links (Phase 2+)

---

## Alert Delivery
- HTML email via Gmail SMTP
- iMessage alerts (Apple ID verification pending)
- Webull deep links embedded in every alert

---

## Emergency Commands
| Command | Action |
|---|---|
| `KILL` | Instant full stop — all processes halt immediately |
| `ENDGAME` | Full wipe — confirmation popup required before execution |

---

## Notes for Claude Code
- Always read `CLAUDE.md` (House Rules v2.1) before starting any session
- Never touch live trading config without explicit Big Tony approval
- Never exceed trade caps under any circumstances
- Phase gates are hard stops — do not advance phases without Big Tony sign-off
- Paper trading is always the default until Big Tony explicitly says otherwise
