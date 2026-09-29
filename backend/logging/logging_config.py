"""Console and daily file logging with per-operation context."""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .daily_log_handler import DailyLogHandler


_operation_id: ContextVar[str] = ContextVar("mam_operation_id", default="-")
_configured = False


class TimezoneFormatter(logging.Formatter):
    def __init__(self, timezone_info, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timezone_info = timezone_info

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, self.timezone_info).strftime(
            datefmt or "%Y-%m-%dT%H:%M:%S%z"
        )


def _log_timezone():
    name = os.environ.get("TZ") or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        sys.stderr.write(f"[logging] Unknown timezone {name!r}; using Asia/Shanghai\n")
        try:
            return ZoneInfo("Asia/Shanghai")
        except ZoneInfoNotFoundError:
            return timezone(timedelta(hours=8))


def _retention_days() -> int:
    raw = os.environ.get("MAM_LOG_RETENTION_DAYS", "10")
    try:
        value = int(raw)
        if value >= 1:
            return value
    except ValueError:
        pass
    sys.stderr.write(f"[logging] Invalid MAM_LOG_RETENTION_DAYS={raw!r}; using 10\n")
    return 10


def _log_directory() -> Path:
    configured = os.environ.get("MAM_LOG_DIR")
    if configured:
        return Path(configured)
    data_dir = Path(os.environ["MAM_DATA_DIR"]) if os.environ.get("MAM_DATA_DIR") else Path(__file__).parent.parent / "data"
    return data_dir / "logs"


def configure_logging() -> None:
    """Configure application and Uvicorn console and daily file output."""
    global _configured
    if _configured:
        return

    level_name = os.environ.get("MAM_LOG_LEVEL", "INFO").upper()
    level = logging.getLevelNamesMapping().get(level_name)
    if not isinstance(level, int):
        level = logging.INFO

    timezone_info = _log_timezone()
    formatter = TimezoneFormatter(
        timezone_info,
        "%(asctime)s %(levelname)-7s [%(name)s] [task=%(operation_id)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    previous_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = previous_factory(*args, **kwargs)
        record.operation_id = _operation_id.get()
        return record

    logging.setLogRecordFactory(record_factory)
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    try:
        file_handler = DailyLogHandler(
            _log_directory(), timezone_info, retention_days=_retention_days()
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("File logging unavailable: %s", exc)
    else:
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        # Uvicorn's named loggers normally do not propagate to root.
        for name in ("uvicorn", "uvicorn.access"):
            uvicorn_logger = logging.getLogger(name)
            if not uvicorn_logger.propagate:
                uvicorn_logger.addHandler(file_handler)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        for existing_handler in uvicorn_logger.handlers:
            existing_handler.setFormatter(formatter)
    # The middleware writes an access record with the operation ID. Avoid a
    # second, uncorrelated access record from Uvicorn for the same request.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _configured = True


def new_operation_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def safe_url(url: str) -> str:
    """Keep only the destination origin; paths can contain feed tokens."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return "<invalid-url>"
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        return "<invalid-url>"
    netloc = host + (f":{port}" if port else "")
    return urlunsplit((parts.scheme, netloc, "", "", ""))


@contextmanager
def operation_context(operation_id: str):
    token = _operation_id.set(operation_id)
    try:
        yield
    finally:
        _operation_id.reset(token)
