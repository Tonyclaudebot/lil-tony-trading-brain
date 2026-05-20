#!/bin/bash
# sync_dashboard.sh — push dashboard files + live JSON to GitHub after each scan.
# Called automatically by daytime_runner.py after every alert window.

set -euo pipefail

BRAIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DASH_DIR="$HOME/lil-tony-dashboard"

if [ ! -d "$DASH_DIR/.git" ]; then
  echo "[sync] Dashboard repo not initialized — skipping push (run setup first)"
  exit 0
fi

# Copy dashboard HTML files
for f in index.html admin.html alert_log.html live_feed.html scoreboard.html; do
  [ -f "$BRAIN_DIR/$f" ] && cp "$BRAIN_DIR/$f" "$DASH_DIR/$f"
done

# Copy live data JSON
cp "$BRAIN_DIR/alerts.json"      "$DASH_DIR/alerts.json"
cp "$BRAIN_DIR/scan_status.json" "$DASH_DIR/scan_status.json"

cd "$DASH_DIR"

git pull --rebase --autostash origin main 2>/dev/null || true

git add -A

# Only commit if there are actual changes
if git diff --cached --quiet; then
  echo "[sync] No changes — skipping push"
  exit 0
fi

TIMESTAMP=$(date "+%Y-%m-%d %H:%M")
git commit -m "scan: $TIMESTAMP"
git push origin main

echo "[sync] ✓ Dashboard pushed to GitHub at $TIMESTAMP"
