"""
brain/grader.py — alert log writer.

log_alert() is the only active function: it appends a fired alert to
logs/alerts.jsonl (T10 audit trail, append-only).

Grading is handled by outcome_tracker.py (stock-proxy BS reprice via
Polygon daily bars). Polygon free tier does NOT authorize options
endpoints, so any direct option-price grading here would silently no-op.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "alerts.jsonl")


def log_alert(plan_dict: dict) -> None:
    """Append a fired alert to logs/alerts.jsonl (T10 — append only, never overwrite)."""
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(plan_dict) + "\n")
