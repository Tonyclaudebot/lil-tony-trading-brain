"""
review_trades.py — Big Tony's private trade grader.

Usage:
  python3 review_trades.py        # review all ungraded alerts
  python3 review_trades.py --all  # re-review everything including graded

Keys: W = win  L = loss  S = skip  Q = quit
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ALERTS_LOG = Path("logs/alerts.jsonl")
SCOREBOARD  = Path("scoreboard.html")


def load_alerts():
    if not ALERTS_LOG.exists():
        return []
    return [json.loads(l) for l in ALERTS_LOG.read_text().splitlines() if l.strip()]


def save_alerts(alerts):
    ALERTS_LOG.write_text("\n".join(json.dumps(a) for a in alerts) + "\n")


def build_scoreboard(alerts):
    from collections import defaultdict
    wins = losses = pending = 0
    by_strategy = defaultdict(lambda: {"wins": 0, "losses": 0, "pending": 0})

    for a in alerts:
        s = a.get("strategy_name", "Unknown")
        if a["outcome"] == "WIN":
            wins += 1
            by_strategy[s]["wins"] += 1
        elif a["outcome"] == "LOSS":
            losses += 1
            by_strategy[s]["losses"] += 1
        else:
            pending += 1
            by_strategy[s]["pending"] += 1

    total_graded = wins + losses
    win_rate = round(wins / total_graded * 100, 1) if total_graded else 0

    rows = ""
    for strat, counts in sorted(by_strategy.items()):
        graded = counts["wins"] + counts["losses"]
        rate = round(counts["wins"] / graded * 100, 1) if graded else 0
        rows += f"""
        <tr>
          <td>{strat}</td>
          <td class="win">{counts['wins']}</td>
          <td class="loss">{counts['losses']}</td>
          <td class="pending">{counts['pending']}</td>
          <td>{"—" if not graded else f"{rate}%"}</td>
        </tr>"""

    updated = datetime.now().strftime("%b %d, %Y %I:%M %p")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Lil Tony — Scoreboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0d0d0d;
      color: #e0e0e0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      padding: 24px 16px;
      max-width: 600px;
      margin: 0 auto;
    }}
    h1 {{ font-size: 1.3rem; color: #ff6b00; letter-spacing: 1px; margin-bottom: 4px; }}
    .updated {{ font-size: 0.75rem; color: #555; margin-bottom: 24px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 28px;
    }}
    .card {{
      background: #1a1a1a;
      border-radius: 10px;
      padding: 16px 12px;
      text-align: center;
    }}
    .card .label {{ font-size: 0.7rem; color: #666; text-transform: uppercase; letter-spacing: 1px; }}
    .card .value {{ font-size: 2rem; font-weight: 700; margin-top: 4px; }}
    .win {{ color: #00c853; }}
    .loss {{ color: #ff3d3d; }}
    .pending {{ color: #888; }}
    .rate {{ color: #ff6b00; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }}
    th {{
      text-align: left;
      padding: 8px 10px;
      color: #555;
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 1px;
      border-bottom: 1px solid #222;
    }}
    td {{
      padding: 10px 10px;
      border-bottom: 1px solid #1a1a1a;
    }}
    tr:last-child td {{ border-bottom: none; }}
    h2 {{ font-size: 0.8rem; color: #444; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }}
  </style>
</head>
<body>
  <h1>🔥 LIL TONY — SCOREBOARD</h1>
  <div class="updated">Updated {updated} · Strategies under review</div>

  <div class="cards">
    <div class="card">
      <div class="label">Signals</div>
      <div class="value" style="color:#e0e0e0">{wins + losses + pending}</div>
    </div>
    <div class="card">
      <div class="label">Wins</div>
      <div class="value win">{wins}</div>
    </div>
    <div class="card">
      <div class="label">Losses</div>
      <div class="value loss">{losses}</div>
    </div>
    <div class="card">
      <div class="label">Win Rate</div>
      <div class="value rate">{"—" if not total_graded else f"{win_rate}%"}</div>
    </div>
  </div>

  <h2>By Strategy</h2>
  <table>
    <thead>
      <tr>
        <th>Strategy</th>
        <th>Wins</th>
        <th>Losses</th>
        <th>Pending</th>
        <th>Win Rate</th>
      </tr>
    </thead>
    <tbody>{rows}
    </tbody>
  </table>
</body>
</html>"""

    SCOREBOARD.write_text(html)
    print(f"\n✅ Scoreboard updated → {SCOREBOARD}")


def fmt_alert(a):
    return (
        f"\n{'─'*50}\n"
        f"  {a['ticker']} | {a['strategy_name']} | {a['opt_type'].upper()}\n"
        f"  Contract : {a['contract']}\n"
        f"  Expiry   : {a['expiration']} ({a['dte']}d)\n"
        f"  Entry    : ${a['entry']}  →  Target ${a['target']} (+{a['target_pct']}%)  Stop ${a['stop']}\n"
        f"  Score    : {a['score']}  Confidence: {a['confidence']}\n"
        f"  Sent     : {a['timestamp'][:16].replace('T',' ')} UTC\n"
        f"  Outcome  : {a.get('outcome') or 'PENDING'}\n"
    )


def main():
    review_all = "--all" in sys.argv
    alerts = load_alerts()
    queue = [a for a in alerts if review_all or not a.get("outcome")]

    if not queue:
        print("No ungraded alerts. Run with --all to re-review everything.")
        build_scoreboard(alerts)
        return

    print(f"\n🔥 LIL TONY TRADE REVIEW — {len(queue)} alert(s) to grade\n")
    print("  W = Win   L = Loss   S = Skip   Q = Quit\n")

    changed = False
    for alert in queue:
        print(fmt_alert(alert))
        while True:
            choice = input("  Grade [W/L/S/Q]: ").strip().upper()
            if choice == "Q":
                if changed:
                    save_alerts(alerts)
                    build_scoreboard(alerts)
                print("\nSaved. Bye.")
                return
            if choice == "S":
                break
            if choice in ("W", "L"):
                exit_price = input("  Exit price (or Enter to skip): ").strip()
                idx = next(i for i, a in enumerate(alerts) if a["alert_id"] == alert["alert_id"])
                alerts[idx]["outcome"] = "WIN" if choice == "W" else "LOSS"
                alerts[idx]["graded_at"] = datetime.now(timezone.utc).isoformat()
                if exit_price:
                    try:
                        alerts[idx]["exit_price"] = float(exit_price)
                    except ValueError:
                        pass
                changed = True
                print(f"  Marked {'✅ WIN' if choice == 'W' else '❌ LOSS'}")
                break
            print("  Invalid — type W, L, S, or Q")

    if changed:
        save_alerts(alerts)

    build_scoreboard(alerts)
    print("\nAll done.")


if __name__ == "__main__":
    main()
