"""
Centralized logging configuration for PMC Overwatch.

Import and call ``setup_logging()`` once at application startup (in main.py).
Every other module should create its own logger with:

    import logging
    logger = logging.getLogger(__name__)
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per log file
_BACKUP_COUNT = 3             # Keep 3 rotated copies


def setup_logging() -> None:
    """Configure the root logger based on the LOG_LEVEL env var.

    Defaults to INFO.  Set ``LOG_LEVEL=DEBUG`` in .env for verbose output.
    Logs to both stdout and a rotating file in logs/.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on repeated calls
    if not root.handlers:
        root.addHandler(console)

        # File handler with rotation
        try:
            os.makedirs(_LOG_DIR, exist_ok=True)
            file_handler = RotatingFileHandler(
                os.path.join(_LOG_DIR, "pmc_overwatch.log"),
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            pass  # Can't write to logs/ — continue with console only
