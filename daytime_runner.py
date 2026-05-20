"""
daytime_runner.py — Daily alert schedule for Lil Tony Trading Brain.

Launched at 7:00 AM CT by launchd (Mon–Fri). Exits immediately on market
holidays via NYSE calendar check. Three alert windows per market day:

  07:00 CT  → Phase 2 + 3 + 4  → morning scan, alerts before 9:30 AM CT
  11:09 CT  → Phase 2 + 3 + 4  → midday re-scan
  12:39 CT  → Phase 2 + 3 + 4  → afternoon re-scan
  14:30 CT  → stop

Phase 2: nightly top-100 × morning news → top-40 with catalysts
Phase 3: deep options-chain read on top-40 → top-10
Phase 4: full analysis on top-10 → fire top-3 alerts
"""
import importlib
import logging
import subprocess
import sys
import os
import time
from datetime import date, datetime

import pytz

sys.path.insert(0, os.path.dirname(__file__))

from config import settings

_CENTRAL = pytz.timezone("America/Chicago")

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(f"{settings.LOG_DIR}/lil-tony.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("daytime_runner")

# Each entry: (HH:MM CT to start, [phase keys to run])
_SCHEDULE = [
    ("07:00", ["2", "3", "4"]),   # → morning scan, alerts before 9:30 AM CT open
    ("11:09", ["2", "3", "4"]),   # → midday re-scan
    ("12:39", ["2", "3", "4"]),   # → afternoon re-scan
]
_STOP_TIME = "14:30"              # 2:30 PM CT — hard stop

_PHASE_MAP = {
    "2": ("phases.phase2_morning",  "run"),
    "3": ("phases.phase3_deepscan", "run"),
    "4": ("phases.phase4_alerts",   "run"),
}

_SYNC_SCRIPT = os.path.join(os.path.dirname(__file__), "scripts", "sync_dashboard.sh")


def _ct_hhmm() -> str:
    return datetime.now(_CENTRAL).strftime("%H:%M")


def _sync_dashboard() -> None:
    try:
        result = subprocess.run(
            ["bash", _SYNC_SCRIPT],
            capture_output=True, text=True, timeout=30
        )
        if result.stdout.strip():
            logger.info(result.stdout.strip())
        if result.returncode != 0:
            logger.warning(f"Dashboard sync failed: {result.stderr.strip()}")
    except Exception as e:
        logger.warning(f"Dashboard sync error: {e}")


def _run_phase(key: str) -> None:
    module_path, fn_name = _PHASE_MAP[key]
    mod = importlib.import_module(module_path)
    logger.info(f"── Phase {key} start ──")
    getattr(mod, fn_name)()
    logger.info(f"── Phase {key} done  ──")


def _is_market_day(today: date) -> bool:
    """Return False on NYSE holidays so the runner exits cleanly instead of scanning."""
    try:
        import pandas_market_calendars as mcal
        schedule = mcal.get_calendar("NYSE").schedule(
            start_date=str(today), end_date=str(today)
        )
        return not schedule.empty
    except Exception as e:
        logger.warning(f"Holiday check failed ({e}) — assuming market is open")
        return True  # fail open: don't silently skip on library error


def main() -> None:
    logger.info(f"Daytime runner started — CT now {_ct_hhmm()}")

    if not _is_market_day(date.today()):
        logger.info(f"Today ({date.today()}) is a market holiday — exiting")
        return

    logger.info(f"Schedule: {[s[0] for s in _SCHEDULE]} CT  |  stop {_STOP_TIME} CT")

    fired: set[str] = set()

    while True:
        now = _ct_hhmm()

        if now >= _STOP_TIME:
            logger.info(f"Reached stop time {_STOP_TIME} CT — shutting down")
            break

        for trigger_time, phases in _SCHEDULE:
            if now >= trigger_time and trigger_time not in fired:
                fired.add(trigger_time)
                logger.info(f"=== Alert window {trigger_time} CT — running phases {phases} ===")
                for p in phases:
                    try:
                        _run_phase(p)
                    except Exception as exc:
                        logger.error(f"Phase {p} failed: {exc}", exc_info=True)
                logger.info(f"=== Alert window {trigger_time} CT complete ===")
                _sync_dashboard()

        time.sleep(30)   # check every 30 s

    logger.info("Daytime runner exited cleanly.")


if __name__ == "__main__":
    main()
