"""Process logging: one stdout stream, JSON lines in production.

Lifecycle records carry their context as ``extra`` fields (``event``,
``match_id``, ``player_id``, ...), which this formatter emits as top-level
JSON keys (or ``key=value`` pairs in text mode) so operators can filter with
``docker compose logs backend | jq 'select(.match_id == "...")'``. Session
tokens are never passed to a logger anywhere in the backend.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    | {"message", "asctime", "color_message"}
)


class StructuredFormatter(logging.Formatter):
    def __init__(self, *, json_lines: bool) -> None:
        super().__init__()
        self._json_lines = json_lines

    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, Any] = {
            key: value for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS
        }
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        if self._json_lines:
            payload: dict[str, Any] = {
                "ts": timestamp,
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                **fields,
            }
            if record.exc_info:
                payload["exc"] = self.formatException(record.exc_info)
            return json.dumps(payload, default=str)
        line = f"{timestamp} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if fields:
            line += " " + " ".join(f"{key}={value}" for key, value in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str, log_format: str) -> None:
    """Route the app's and uvicorn's loggers through one stdout handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(json_lines=log_format == "json"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
