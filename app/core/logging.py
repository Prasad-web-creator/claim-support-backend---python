"""
Structured logging using Loguru.
Console-only logging (no log files generated on disk).
"""

import sys
from loguru import logger

# Remove the default handler so we can configure our own
logger.remove()

# Console handler — human-readable format
logger.add(
    sys.stderr,
    format="[{time:YYYY-MM-DD HH:mm:ss}] {level}: {message}",
    level="INFO",
    colorize=True,
)

# Re-export logger for use throughout the application
__all__ = ["logger"]
