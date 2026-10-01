"""
Structured logging configuration.

Call configure_logging() once at application startup.
Then use standard logging.getLogger(__name__) in every module.
"""
import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Set up a consistent log format across the whole application."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Silence noisy third-party loggers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
