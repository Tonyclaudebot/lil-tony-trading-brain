import json
import os
import tempfile
from unittest.mock import patch

import brain.grader as grader_mod


def test_log_alert_appends(tmp_path):
    log_file = str(tmp_path / "alerts.jsonl")
    plan = {"alert_id": "abc", "ticker": "AAPL", "contract": "AAPL240315C00180000"}
    with patch.object(grader_mod, "_LOG_PATH", log_file):
        grader_mod.log_alert(plan)
        grader_mod.log_alert({**plan, "alert_id": "def"})

    lines = [l for l in open(log_file).read().splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["alert_id"] == "abc"
    assert json.loads(lines[1])["alert_id"] == "def"


def test_log_alert_does_not_overwrite(tmp_path):
    log_file = str(tmp_path / "alerts.jsonl")
    existing = {"alert_id": "existing", "ticker": "MSFT"}
    with open(log_file, "w") as f:
        f.write(json.dumps(existing) + "\n")

    new_plan = {"alert_id": "new", "ticker": "NVDA"}
    with patch.object(grader_mod, "_LOG_PATH", log_file):
        grader_mod.log_alert(new_plan)

    lines = [l for l in open(log_file).read().splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["alert_id"] == "existing"
    assert json.loads(lines[1])["alert_id"] == "new"
