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


_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> None:
    """Configure the root logger based on the LOG_LEVEL env var.

    Defaults to INFO.  Set ``LOG_LEVEL=DEBUG`` in .env for verbose output.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on repeated calls
    if not root.handlers:
        root.addHandler(handler)
