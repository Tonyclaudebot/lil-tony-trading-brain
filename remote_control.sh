#!/bin/zsh
# Keeps claude --remote-control "lil-tony" alive in a persistent tmux session.
# Managed by launchd — restarts automatically on crash or reboot.

SESSION="lil-tony"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    exit 0
fi

tmux new-session -d -s "$SESSION" "claude --remote-control $SESSION --permission-mode bypassPermissions"
