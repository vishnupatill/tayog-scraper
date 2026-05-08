"""
Structured logging with JSON output for production log aggregation
(Datadog, CloudWatch, ELK Stack, etc.)
"""

import logging
import sys
from datetime import datetime, timezone

from app.core.config import settings


class JsonFormatter(logging.Formatter):
    """Emit log records as JSON for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def configure_logging() -> None:
    """Configure root logger. Call once at startup."""
    handler = logging.StreamHandler(sys.stdout)

    if settings.LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    root.handlers = [handler]

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "scrapy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
