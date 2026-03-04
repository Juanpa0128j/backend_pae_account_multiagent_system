"""
Structured JSON logging configuration.
"""

import logging
import json
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Format logs as JSON for structured logging."""

    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance with JSON formatting.

    The logger's level is inherited from the root logger (configured in
    main.py via ``logging.basicConfig``).  ``propagate`` is disabled to
    avoid duplicate log lines from the root handler.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = JSONFormatter()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        # Inherit level from root config; don't hardcode.
        # Disable propagation to prevent duplicate lines with root handler.
        logger.propagate = False

    return logger
