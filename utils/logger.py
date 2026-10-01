"""
utils/logger.py — Coloured, dual-sink (console + file) logger.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    import colorlog
    _HAS_COLOR = True
except ImportError:
    _HAS_COLOR = False

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str, log_file: Path | None = None) -> logging.Logger:
    """
    Return a logger with a coloured console handler and optional rotating
    file handler.  Safe to call multiple times with the same name.
    """
    logger = logging.getLogger(name)

    if logger.handlers:          # already configured — avoid duplicates
        return logger

    logger.setLevel(logging.DEBUG)

    # ── Console handler ──────────────────────────────────────────────────────
    if _HAS_COLOR:
        console_fmt = colorlog.ColoredFormatter(
            fmt=(
                "%(log_color)s%(asctime)s | %(levelname)-8s%(reset)s"
                " | %(cyan)s%(name)-25s%(reset)s | %(message)s"
            ),
            datefmt=_DATE_FORMAT,
            log_colors={
                "DEBUG":    "white",
                "INFO":     "green",
                "WARNING":  "yellow",
                "ERROR":    "red",
                "CRITICAL": "bold_red",
            },
        )
    else:
        console_fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(console_fmt)
    logger.addHandler(ch)

    # ── File handler (optional) ───────────────────────────────────────────────
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,   # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(fh)

    return logger
