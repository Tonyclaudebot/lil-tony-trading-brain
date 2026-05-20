# Lil Tony Trading Brain — First Live Run Checklist
**Phase 1 | Market Hours: 8:30 AM – 3:00 PM CT**

---

## Night Before
- [ ] Mac plugged in and set to never sleep
- [ ] `.env` file has App Key + App Secret for Webull
- [ ] Gmail SMTP credentials in `.env`
- [ ] Project folder open and ready in terminal
- [ ] ROADMAP.md and CLAUDE.md in project root

---

## Morning of (Before 8:30 AM CT)

- [ ] Mac is awake
- [ ] Open terminal in project root
- [ ] Run the bot:
  ```bash
  python3 daytime_runner.py
  ```
- [ ] Confirm you see the thread startup log:
  ```
  [tracker] daemon thread started
  ```
- [ ] Confirm holiday guard clears (no early exit)
- [ ] Bot is sitting in schedule loop — waiting for 8:30 AM

---

## During Market Hours (8:30 AM – 3:00 PM CT)

### Phase Scans
- [ ] Scans firing on schedule (check terminal output)
- [ ] Top 3 picks being scored each pass
- [ ] Alerts sending — check email and iMessage

### Paper Trades
- [ ] At least one paper trade opens (`data/paper_trades.json` gets created)
- [ ] Tracker polling every 5 min (watch for reprice logs)
- [ ] At least one paper trade closes → check for:
  ```
  PAPER CLOSE WIN/LOSS pnl +/- $XX
  ```
- [ ] Learner updates strategy weight after close
- [ ] Scoreboard regenerates with new data

---

## After Market Close (After 3:00 PM CT)

- [ ] Bot exits cleanly (exit 0)
- [ ] Check `data/paper_trades.json` — trades recorded
- [ ] Check `brain/state.json` — weights updated from closes
- [ ] Check scoreboard — win rate and P&L showing
- [ ] No runaway errors in terminal log

---

## Red Flags — Stop and Check
- ❌ Thread startup log never appears → tracker not running
- ❌ No scans firing after 8:30 AM → schedule loop issue
- ❌ `paper_trades.json` never created → phase4 hook not firing
- ❌ Weights not updating → learner not getting fed from tracker
- ❌ Bot crashes before 3:00 PM → check terminal for error

---

## If Something Breaks
1. Hit **CTRL+C** to stop
2. Note the error in terminal
3. Do NOT manually edit `state.json` or `paper_trades.json`
4. Bring the error to Claude Code — paste exact output

---

## Emergency Commands
| Command | Action |
|---|---|
| `KILL` | Instant full stop |
| `ENDGAME` | Full wipe — confirmation required |

---

*First live run validates the one remaining unproven path: tracker closing a real position with live Webull quotes during market hours. Everything else is confirmed.*
