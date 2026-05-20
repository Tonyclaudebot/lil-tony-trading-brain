"""
runner.py — 4-phase scan orchestrator.

Usage:
  python runner.py          # run all 4 phases in sequence
  python runner.py 1        # Phase 1 only (nightly universe scan)
  python runner.py 2        # Phase 2 only (morning news filter)
  python runner.py 3        # Phase 3 only (deep scan top 40)
  python runner.py 4        # Phase 4 only (final deep read + alerts)
  python runner.py 2 3 4    # chain specific phases

Schedule is managed by launchd:
  Phase 1 → 6:00 PM CT nightly via com.liltony.phase1.plist
  Phases 2+3+4 → 7:00, 11:09, 12:39 CT weekdays via com.liltony.daytime.plist
                 (daytime_runner.py handles the scheduling loop)
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(f"{settings.LOG_DIR}/lil-tony.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("runner")

_PHASE_MAP = {
    "1": ("phases.phase1_nightly",  "run"),
    "2": ("phases.phase2_morning",  "run"),
    "3": ("phases.phase3_deepscan", "run"),
    "4": ("phases.phase4_alerts",   "run"),
}


def _run_phase(key: str) -> None:
    module_path, fn_name = _PHASE_MAP[key]
    import importlib
    mod = importlib.import_module(module_path)
    logger.info(f"═══ Phase {key} start ═══")
    getattr(mod, fn_name)()
    logger.info(f"═══ Phase {key} done  ═══")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        phases = ["1", "2", "3", "4"]
    else:
        phases = []
        for a in args:
            if a in _PHASE_MAP:
                phases.append(a)
            else:
                logger.error(f"Unknown phase '{a}'. Choose from 1 2 3 4.")
                sys.exit(1)

    logger.info(f"Running phases: {' → '.join(phases)}")
    for p in phases:
        _run_phase(p)
    logger.info("All requested phases complete.")


if __name__ == "__main__":
    main()
