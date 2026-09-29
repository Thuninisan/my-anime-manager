"""Regression tests for operation context and safe log output."""

import io
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.logging.daily_log_handler import DailyLogHandler
from backend.logging.logging_config import configure_logging, operation_context, safe_url


class LoggingConfigTests(unittest.TestCase):
    def test_http_response_exposes_request_id(self):
        from fastapi.testclient import TestClient
        from backend.api import app

        with self.assertLogs("backend.api.app", level="INFO") as captured:
            with TestClient(app) as client:
                response = client.get("/watch/status")
        self.assertEqual(response.status_code, 200)
        request_id = response.headers["X-Request-ID"]
        self.assertRegex(request_id, r"^http-[0-9a-f]{10}$")
        self.assertTrue(any(record.operation_id == request_id for record in captured.records))

    def test_operation_context_is_restored(self):
        configure_logging()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(operation_id)s %(message)s"))
        logger = logging.getLogger("test.mam.logging")
        logger.addHandler(handler)
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            logger.info("before")
            with operation_context("rss-123"):
                logger.info("inside")
                with operation_context("torrent-456"):
                    logger.info("nested")
                logger.info("restored")
            logger.info("after")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        self.assertEqual(
            stream.getvalue().splitlines(),
            ["- before", "rss-123 inside", "torrent-456 nested", "rss-123 restored", "- after"],
        )

    def test_safe_url_removes_credentials_and_query(self):
        self.assertEqual(
            safe_url("https://user:secret@example.com:8443/feed.xml?token=private#fragment"),
            "https://example.com:8443",
        )
        self.assertEqual(safe_url("not-a-url"), "<invalid-url>")

    def test_daily_files_append_and_remove_only_expired_logs(self):
        shanghai = ZoneInfo("Asia/Shanghai")
        current = [datetime(2026, 9, 26, 15, 59, tzinfo=timezone.utc)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "2026-09-16.log").write_text("expired\n")
            (directory / "2026-09-17.log").write_text("keep\n")
            (directory / "notes.txt").write_text("unrelated\n")

            handler = DailyLogHandler(directory, shanghai, now=lambda: current[0])
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.assertFalse((directory / "2026-09-16.log").exists())
            self.assertTrue((directory / "2026-09-17.log").exists())
            self.assertFalse((directory / "2026-09-26.log").exists())
            try:
                handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "before", (), None))
                current[0] = datetime(2026, 9, 26, 16, 1, tzinfo=timezone.utc)
                handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "after", (), None))
            finally:
                handler.close()

            self.assertEqual((directory / "2026-09-26.log").read_text(), "before\n")
            self.assertEqual((directory / "2026-09-27.log").read_text(), "after\n")
            self.assertFalse((directory / "2026-09-16.log").exists())
            self.assertFalse((directory / "2026-09-17.log").exists())
            self.assertTrue((directory / "notes.txt").exists())

            restarted = DailyLogHandler(directory, shanghai, now=lambda: current[0])
            restarted.setFormatter(logging.Formatter("%(message)s"))
            try:
                restarted.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "restart", (), None))
            finally:
                restarted.close()
            self.assertEqual((directory / "2026-09-27.log").read_text(), "after\nrestart\n")


if __name__ == "__main__":
    unittest.main()
