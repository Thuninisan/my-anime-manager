"""One application log file per calendar day, with bounded retention."""

import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Callable, TextIO


_DAILY_LOG_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.log$")


class DailyLogHandler(logging.Handler):
    """Append to today's file and remove files outside the retention window.

    The application currently runs one Uvicorn worker per checkout. Like the
    standard library's rotating handlers, this handler is thread-safe within
    a process but is not intended for concurrent processes writing one file.
    """

    def __init__(
        self,
        directory: Path,
        timezone: tzinfo,
        retention_days: int = 10,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__()
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        self.directory = Path(directory)
        self.timezone = timezone
        self.retention_days = retention_days
        self._now = now or (lambda: datetime.now(timezone))
        self._open_date: date | None = None
        self._stream: TextIO | None = None
        self._warned = False
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._delete_expired(self._today())

    def _today(self) -> date:
        return self._now().astimezone(self.timezone).date()

    def _open_for_date(self, day: date) -> None:
        self._close_stream()
        path = self.directory / f"{day.isoformat()}.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            self._stream = os.fdopen(fd, "a", encoding="utf-8", buffering=1)
        except Exception:
            os.close(fd)
            raise
        self._open_date = day
        self._delete_expired(day)

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except OSError as exc:
                self._warn_once(f"File log close failed: {exc}")

    def _delete_expired(self, today: date) -> None:
        oldest_to_keep = today - timedelta(days=self.retention_days - 1)
        for path in self.directory.iterdir():
            if not path.is_file() or not _DAILY_LOG_NAME.fullmatch(path.name):
                continue
            try:
                file_day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if file_day < oldest_to_keep:
                try:
                    path.unlink()
                except OSError as exc:
                    self._warn_once(f"Could not delete old log file {path.name}: {exc}")

    def _warn_once(self, message: str) -> None:
        if not self._warned:
            sys.stderr.write(f"[logging] {message}\n")
            self._warned = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            today = self._today()
            if today != self._open_date or self._stream is None:
                self._open_for_date(today)
            assert self._stream is not None
            self._stream.write(self.format(record) + "\n")
            self._warned = False
        except (OSError, ValueError) as exc:
            self._warn_once(f"File log write failed: {exc}")
            self._close_stream()

    def close(self) -> None:
        self.acquire()
        try:
            self._close_stream()
            super().close()
        finally:
            self.release()
