"""
auto_scheduler.py — El Piloto Automático / The Autopilot.

Runs the DegenRadar pipeline every X hours, 24/7.
Perfect for leaving it running on a server or home PC.

Ejecuta el pipeline de DegenRadar cada X horas, 24/7.
Perfecto para dejarlo corriendo en un servidor o PC de casa.

Features / Características:
  - Adaptive intervals: 2h normal, 4h when nothing new found (saves RPC)
  - Rotating log files (5MB × 3 backups = 20MB max) to prevent disk bloat
  - Separate log for bot subprocess output
  - Automatic timeout (60 min max per cycle)

Logs rotate automatically to prevent disk bloat.
Los logs rotan automáticamente para prevenir acumulación en disco.
"""
import os
import time
import subprocess
import sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

from MemeAlphaCrew_Auto.cache import CycleCache

# ── Configuration ──────────────────────────────────────────────
INTERVAL_SECONDS = 7200       # 2 hours between runs (normal)
EXTENDED_INTERVAL = 14400     # 4 hours (when nothing new found)

# Command to run (full pipeline)
COMMAND = [sys.executable, "-m", "MemeAlphaCrew_Auto.main", "--seeds", "--deep-dive", "--momentum"]

# ── Log Paths ────────────────────────────────────────────────
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULER_LOG = os.path.join(_PROJECT_DIR, "scheduler.log")
BOT_OUTPUT_LOG = os.path.join(_PROJECT_DIR, "bot_execution.log")

# ── Logging Setup (with rotation) ────────────────────────────
# Max 5 MB per log file, keep 3 backups → 20 MB total max
_LOG_FORMAT = '%(asctime)s │ %(levelname)-7s │ [SCHEDULER] %(message)s'
_LOG_DATEFMT = '%Y-%m-%d %H:%M:%S'
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3

logger = logging.getLogger("scheduler")
logger.setLevel(logging.INFO)

# Rotating file handler
_file_handler = RotatingFileHandler(
    SCHEDULER_LOG, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
logger.addHandler(_file_handler)

# Console handler (so you still see output if running interactively)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
logger.addHandler(_console_handler)

# Separate rotating logger for bot subprocess output
_bot_logger = logging.getLogger("bot_output")
_bot_logger.setLevel(logging.INFO)
_bot_file_handler = RotatingFileHandler(
    BOT_OUTPUT_LOG, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT
)
_bot_file_handler.setFormatter(logging.Formatter('%(asctime)s │ %(message)s', datefmt=_LOG_DATEFMT))
_bot_logger.addHandler(_bot_file_handler)


def run_cycle():
    """Executes one full autonomous cycle."""
    start_time = datetime.now()
    logger.info("Starting new autonomous cycle...")

    try:
        result = subprocess.run(
            COMMAND,
            capture_output=True,
            text=True,
            timeout=3600,  # 60 min max per cycle
        )

        # Log bot output through the rotating handler
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                _bot_logger.info(line)

        if result.stderr:
            for line in result.stderr.strip().splitlines():
                _bot_logger.info(line)

        duration = datetime.now() - start_time

        if result.returncode == 0:
            logger.info(f"Cycle completed successfully in {duration}")
        else:
            logger.error(f"Bot exited with code {result.returncode} after {duration}")

    except subprocess.TimeoutExpired:
        logger.error("Cycle timed out after 60 minutes — killed subprocess")
    except Exception as e:
        logger.error(f"Unexpected scheduler error: {e}")


def main():
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║      MEMEALPHACREW AUTO — AUTOPILOT              ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  Mode: Discovery + Seeds + Deep Dive + Momentum")
    print(f"  Interval: {INTERVAL_SECONDS // 60} minutes")
    print(f"  Logs: {SCHEDULER_LOG} (max {_MAX_BYTES // (1024*1024)} MB × {_BACKUP_COUNT + 1})")
    print(f"  Bot output: {BOT_OUTPUT_LOG}")
    print()

    logger.info(f"Autopilot started. Interval: {INTERVAL_SECONDS}s. Command: {' '.join(COMMAND)}")

    while True:
        run_cycle()

        # Adaptive interval: if last 3 cycles found nothing, slow down
        cache = CycleCache()
        if cache.should_skip_cycle():
            interval = EXTENDED_INTERVAL
            logger.info(
                f"Last 3 cycles found nothing new — "
                f"extending interval to {interval // 60} min"
            )
        else:
            interval = INTERVAL_SECONDS

        next_run = datetime.fromtimestamp(time.time() + interval)
        logger.info(f"Sleeping. Next run at: {next_run.strftime('%H:%M:%S')}")

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user.")
            break


if __name__ == "__main__":
    main()
