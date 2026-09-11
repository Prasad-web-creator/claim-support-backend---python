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


def log_section(title: str, width: int = 88) -> None:
    """Logs a clean group separator line with normal developer English title."""
    clean_title = f" {title.strip()} "
    dashes_needed = max(8, width - len(clean_title))
    left_count = dashes_needed // 2
    right_count = dashes_needed - left_count
    logger.info(f"{'-' * left_count}{clean_title}{'-' * right_count}")


# Re-export logger and log_section for use throughout the application
__all__ = ["logger", "log_section"]

