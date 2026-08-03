"""
Structured logging using Loguru.
Replaces Winston + daily-rotate from Node.js.
"""

import sys
from pathlib import Path

from loguru import logger

# Remove the default handler so we can configure our own
logger.remove()

# Log directory
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Console handler — human-readable format
logger.add(
    sys.stderr,
    format="[{time:YYYY-MM-DD HH:mm:ss}] {level}: {message}",
    level="DEBUG",
    colorize=True,
)

# Error file handler — daily rotation, JSON format
logger.add(
    str(LOG_DIR / "error-{time:YYYY-MM-DD}.log"),
    format="{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | {message}",
    level="ERROR",
    rotation="1 day",
    retention="14 days",
    compression="zip",
    serialize=True,
)

# Combined file handler — daily rotation, JSON format
logger.add(
    str(LOG_DIR / "combined-{time:YYYY-MM-DD}.log"),
    format="{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | {message}",
    level="DEBUG",
    rotation="1 day",
    retention="14 days",
    compression="zip",
    serialize=True,
)

# Re-export logger for use throughout the application
__all__ = ["logger"]
