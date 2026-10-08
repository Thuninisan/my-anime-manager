"""ASS replacement, external response failures and crash recovery contracts."""

import asyncio
import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from backend.clients import fontinass as client
from backend.services.torrent import fontinass as service


ASS = b"""[Script Info]
ScriptType: v4.00+
[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Default,Arial,20
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello
"""
OUTPUT = ASS.replace(b"Arial", b"SubsetAlias")


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, response, server="https://example.test"):
        def handle(request):
            self.assertTrue(request.url.path.endswith("/api/subset"))
            self.assertEqual(request.headers["X-Fonts-Check"], "1")
            self.assertNotIn("x-clear-fonts", request.headers)
            self.assertIn(ASS, request.content)
            return response
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
            with patch.object(client.http_client_manager, "get_client", return_value=http_client):
                return await client.subset(server, 180, ASS)

    async def test_request_diagnostics_do_not_log_url_tokens_or_content(self):
        with self.assertLogs("backend.clients.fontinass", level="DEBUG") as captured:
            await self.request(httpx.Response(200, content=OUTPUT, headers={
                "X-Code": "200", "X-Message": base64.b64encode(b'["private-response-message"]').decode(),
            }), "https://example.test/private-url-token")
        logs = "\n".join(captured.output)
        self.assertIn("fontinass.request.start", logs)
        self.assertIn("fontinass.request.response", logs)
        self.assertIn("elapsed_ms=", logs)
        self.assertIn("code=200", logs)
        for private in ("private-url-token", "private-response-message", "Dialogue:", "Hello"):
            self.assertNotIn(private, logs)

    async def test_single_file_protocol(self):
        result = await self.request(httpx.Response(200, content=OUTPUT, headers={
            "X-Code": "200", "X-Message": base64.b64encode(json.dumps(["ok"]).encode()).decode(),
        }))
        self.assertEqual((result.code, result.messages, result.data), (200, ["ok"], OUTPUT))

    async def test_http_200_without_protocol_is_rejected(self):
        with self.assertRaises(client.SubsetError):
            await self.request(httpx.Response(200, text="<html>Proxy error</html>"))

    async def test_rate_limit_respects_retry_after(self):
        with self.assertRaises(client.SubsetError) as caught:
            await self.request(httpx.Response(429, headers={"Retry-After": "7"}))
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.retry_after, 7)


class ProcessingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.record = {"info_hash": "abc", "fontinass": {"status": "waiting", "files": []}}
        self.patches = [
            patch.object(service.store, "get_torrent", side_effect=lambda _: copy.deepcopy(self.record)),
            patch.object(service.store, "save_fontinass", side_effect=self.save),
            patch.object(service.config, "FONTINASS_ENABLED", True),
            patch.object(service.config, "FONTINASS_URL", "https://example.test"),
            patch.object(service.config, "FONTINASS_TIMEOUT", 180),
            patch.object(service, "_semaphore", asyncio.Semaphore(1)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def save(self, _, state):
        self.record["fontinass"] = copy.deepcopy(state)

    def copied(self, name="episode.ass"):
        source = self.root / ("source-" + name)
        target = self.root / name
        source.write_bytes(ASS)
        service.copy_subtitle("abc", source, target)
        return source, target

    async def test_only_ass_replaced_source_and_srt_unchanged(self):
        source, target = self.copied("episode.ASS")
        srt_source = self.root / "source.srt"
        srt_target = self.root / "episode.srt"
        srt_source.write_bytes(b"SRT original")
        service.copy_subtitle("abc", srt_source, srt_target)
        with patch.object(client, "subset", AsyncMock(return_value=client.SubsetResult(200, [], OUTPUT))) as subset:
            await service.process("abc")
            subset.assert_awaited_once()
        self.assertEqual(target.read_bytes(), OUTPUT)
        self.assertEqual(source.read_bytes(), ASS)
        self.assertEqual(srt_target.read_bytes(), b"SRT original")
        self.assertEqual(self.record["fontinass"]["status"], "success")
        self.assertEqual(list(self.root.glob(".mam-fontinass-*")), [])

    async def test_logs_correlate_all_processing_stages_and_separate_levels(self):
        from backend.logging.logging_config import configure_logging, operation_context
        configure_logging()
        with (operation_context("torrent-diagnostic"),
              self.assertLogs("backend.services.torrent.fontinass", level="DEBUG") as captured,
              patch.object(client, "subset", AsyncMock(return_value=client.SubsetResult(200, [], OUTPUT)))):
            self.copied()
            await service.process("abc")
        for event in ("copy.start", "copy.ready", "checkpoint.saved", "task.selected", "task.start",
                      "file.queued", "file.acquired", "file.attempt", "file.result", "validation.passed",
                      "replace.temp_written", "replace.completed", "replace.cleanup", "file.success", "task.finish"):
            self.assertTrue(any(f"fontinass.{event}" in record.getMessage() for record in captured.records), event)
        self.assertTrue(all(record.operation_id == "torrent-diagnostic" for record in captured.records))
        info = [record.getMessage() for record in captured.records if record.levelno == 20]
        self.assertTrue(any("task.finish" in message for message in info))
        self.assertFalse(any("file.attempt" in message or "checkpoint.saved" in message for message in info))

    async def test_business_failure_logs_phase_without_missing_character_payload(self):
        self.copied()
        with (self.assertLogs("backend.services.torrent.fontinass", level="DEBUG") as captured,
              patch.object(client, "subset", AsyncMock(return_value=client.SubsetResult(
                  300, ["Missing glyphs in [Arial]: PRIVATE-GLYPH-PAYLOAD"], b"")))):
            await service.process("abc")
        logs = "\n".join(captured.output)
        self.assertIn("phase=business_result", logs)
        self.assertIn("code=300", logs)
        self.assertIn("fonts=['Arial']", logs)
        self.assertIn("缺失字体或字形", logs)
        self.assertNotIn("PRIVATE-GLYPH-PAYLOAD", logs)

    async def test_filesystem_failure_has_error_stack_and_keeps_original(self):
        _, target = self.copied()
        with (self.assertLogs("backend.services.torrent.fontinass", level="DEBUG") as captured,
              patch.object(client, "subset", AsyncMock(return_value=client.SubsetResult(200, [], OUTPUT))),
              patch.object(service.os, "replace", side_effect=PermissionError("permission denied"))):
            await service.process("abc")
        errors = [record for record in captured.records if record.levelno == 40]
        self.assertTrue(any(record.exc_info and "phase=replace" in record.getMessage() for record in errors))
        self.assertEqual(target.read_bytes(), ASS)

    async def test_checkpoint_failure_logs_stack_without_sql_values(self):
        self.copied()
        with (self.assertLogs("backend.services.torrent.fontinass", level="DEBUG") as captured,
              patch.object(service.store, "save_fontinass", side_effect=RuntimeError("SQL private-service-token"))):
            with self.assertRaisesRegex(RuntimeError, "状态保存失败"):
                await service.process("abc")
        logs = "\n".join(captured.output)
        self.assertIn("fontinass.checkpoint.failed", logs)
        self.assertIn("stack=", logs)
        self.assertNotIn("private-service-token", logs)

    async def test_retry_diagnostics_and_new_attempt_clears_old_business_code(self):
        self.copied()
        entry = self.record["fontinass"]["files"][0]
        entry.update(status="failed", code=300, messages=["old failure"])
        with (self.assertLogs("backend.services.torrent.fontinass", level="DEBUG") as captured,
              patch.object(client, "subset", AsyncMock(side_effect=client.SubsetError("network failure", retryable=True))),
              patch.object(service.asyncio, "sleep", AsyncMock())):
            await service.process("abc", retry=True)
        warnings = [record.getMessage() for record in captured.records if record.levelno == 30]
        self.assertEqual(sum("fontinass.file.retry " in message for message in warnings), 2)
        failure = next(message for message in warnings if "fontinass.file.failed" in message)
        self.assertIn("phase=request", failure)
        self.assertIn("code=None", failure)
        self.assertNotIn("code", self.record["fontinass"]["files"][0])

    async def test_warning_and_missing_font_never_replace(self):
        for code in (201, 300, 999):
            with self.subTest(code=code):
                _, target = self.copied(f"episode-{code}.ass")
                with patch.object(client, "subset", AsyncMock(return_value=client.SubsetResult(code, ["missing glyphs"], OUTPUT))):
                    await service.process("abc")
                self.assertEqual(target.read_bytes(), ASS)
                self.assertEqual(self.record["fontinass"]["files"][-1]["status"], "failed")

    async def test_invalid_or_changed_timeline_never_replace(self):
        for index, output in enumerate((b"", b"<html>Error</html>", OUTPUT.replace(b"0:00:02.00", b"0:00:03.00"))):
            _, target = self.copied(f"episode-{index}.ass")
            with patch.object(client, "subset", AsyncMock(return_value=client.SubsetResult(200, [], output))):
                await service.process("abc")
            self.assertEqual(target.read_bytes(), ASS)

    async def test_target_modified_during_request_preserved(self):
        _, target = self.copied()
        async def subset(*args):
            target.write_bytes(b"User edit")
            return client.SubsetResult(200, [], OUTPUT)
        with patch.object(client, "subset", side_effect=subset):
            await service.process("abc")
        self.assertEqual(target.read_bytes(), b"User edit")
        self.assertEqual(self.record["fontinass"]["status"], "failed")
        self.assertEqual(list(self.root.glob(".mam-fontinass-*")), [])

    async def test_success_and_crash_checkpoint_skip_recopies_and_requests(self):
        source, target = self.copied()
        entry = self.record["fontinass"]["files"][0]
        entry.update(status="processing", output_hash=service.digest(OUTPUT))
        target.write_bytes(OUTPUT)  # Crash after replacement, before status=success.
        service.copy_subtitle("abc", source, target)
        with patch.object(client, "subset", AsyncMock()) as subset:
            await service.process("abc")
            subset.assert_not_awaited()
        self.assertEqual(target.read_bytes(), OUTPUT)
        self.assertEqual(self.record["fontinass"]["status"], "success")

    async def test_transient_retry_and_failure_isolation(self):
        _, first = self.copied("first.ass")
        _, second = self.copied("second.ass")
        with (patch.object(client, "subset", AsyncMock(side_effect=[
                client.SubsetError("busy", retryable=True, retry_after=3),
                client.SubsetResult(300, ["missing font"], b""),
                client.SubsetResult(200, [], OUTPUT)])) as subset,
              patch.object(service.asyncio, "sleep", AsyncMock()) as sleep):
            await service.process("abc")
        self.assertEqual(subset.await_count, 3)
        sleep.assert_awaited_once_with(3)
        self.assertEqual(first.read_bytes(), ASS)
        self.assertEqual(second.read_bytes(), OUTPUT)
        self.assertEqual(self.record["fontinass"]["status"], "partial")
        with patch.object(client, "subset", AsyncMock(return_value=client.SubsetResult(200, [], OUTPUT))) as subset:
            await service.process("abc", retry=True)
            subset.assert_awaited_once()
        self.assertEqual(self.record["fontinass"]["status"], "success")

    async def test_cancel_and_recover(self):
        source, target = self.copied()
        with patch.object(client, "subset", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await service.process("abc")
        self.assertEqual(target.read_bytes(), ASS)
        service.copy_subtitle("abc", source, target)
        with patch.object(client, "subset", AsyncMock(return_value=client.SubsetResult(200, [], OUTPUT))):
            await service.process("abc")
        self.assertEqual(target.read_bytes(), OUTPUT)

    async def test_disabled_no_upload_and_resume_after_enable(self):
        _, target = self.copied()
        with patch.object(service.config, "FONTINASS_ENABLED", False), patch.object(client, "subset", AsyncMock()) as subset:
            await service.process("abc")
            subset.assert_not_awaited()
        self.assertEqual(target.read_bytes(), ASS)
        self.assertEqual(self.record["fontinass"]["status"], "disabled")

    async def test_normal_and_recovered_torrent_process_after_copy(self):
        from backend.services.torrent import monitor
        from backend.api import routes_torrent as routes, state as api_state
        for recovered in (False, True):
            with self.subTest(recovered=recovered):
                self.record["fontinass"] = {"status": "waiting", "files": []}
                source_dir = self.root / f"downloads-{recovered}"
                source_dir.mkdir()
                (source_dir / "video.mkv").write_bytes(b"video")
                (source_dir / "video.ass").write_bytes(ASS)
                (source_dir / "video.srt").write_bytes(b"srt")
                library = self.root / f"library-{recovered}"
                context = {
                    "torrent_name": "Show", "hardlink_root": str(library),
                    "files": [{"torrent_path": "video.mkv"},
                              {"torrent_path": "video.ass", "is_subtitle": True},
                              {"torrent_path": "video.srt", "is_subtitle": True}],
                    "uploaded_subtitles": [], "series_name": "Show", "skip_nfo": True,
                    "movie_meta": {"tmdb_name": "Movie"},
                }
                record = {"info_hash": "abc", "torrent_name": "Show", "processing": monitor.build_processing(context)}
                async def subset(*args):
                    self.assertEqual((library / "Movie/Movie.ass").read_bytes(), ASS)
                    self.assertTrue((library / "Movie/Movie.mkv").is_file())
                    return client.SubsetResult(200, [], OUTPUT)
                with (patch.object(monitor, "qb_login", AsyncMock()),
                      patch.object(monitor, "get_torrents_by_hashes", AsyncMock(return_value={
                          "abc": {"progress": 1, "save_path": str(source_dir)}})),
                      patch.object(monitor.asyncio, "sleep", AsyncMock()),
                      patch.object(client, "subset", side_effect=subset) as request,
                      patch.object(routes.torrent_store, "finish_torrent") as finish):
                    routes._start_torrent_monitor(record, None if recovered else context)
                    await api_state._download_tasks["abc"]
                    request.assert_awaited_once()
                    finish.assert_called_once_with("abc", "completed")
                self.assertEqual((library / "Movie/Movie.ass").read_bytes(), OUTPUT)
                self.assertEqual((source_dir / "video.ass").read_bytes(), ASS)
                self.assertEqual((library / "Movie/Movie.srt").read_bytes(), b"srt")

    async def test_font_failure_does_not_fail_media_task(self):
        from backend.api import routes_torrent as routes, state as api_state
        _, target = self.copied()
        record = {"info_hash": "abc", "torrent_name": "Show", "processing": {
            "files": [{"action": "hardlink"}]}}
        with (patch.object(routes, "monitor_processing", AsyncMock(return_value={self.root / "video.mkv"})),
              patch.object(client, "subset", AsyncMock(return_value=client.SubsetResult(300, ["missing"], b""))),
              patch.object(routes.torrent_store, "finish_torrent") as finish):
            routes._start_torrent_monitor(record)
            await api_state._download_tasks["abc"]
            finish.assert_called_once_with("abc", "completed")
        self.assertEqual(target.read_bytes(), ASS)
        self.assertEqual(self.record["fontinass"]["status"], "failed")

    async def test_retry_is_checkpointed_before_background_dispatch(self):
        from backend.api import routes_torrent as routes
        self.copied()
        self.record["status"] = "completed"
        self.record["fontinass"]["files"][0]["status"] = "failed"
        with patch.object(routes, "_start_fontinass") as start:
            self.assertEqual(await routes.retry_fontinass("abc"), {"status": "accepted"})
            start.assert_called_once_with("abc")
        self.assertEqual(self.record["fontinass"]["status"], "processing")
        self.assertTrue(self.record["fontinass"]["ready"])
        self.assertEqual(self.record["fontinass"]["files"][0]["status"], "pending")

    async def test_completed_interrupted_font_job_recovers_without_qbittorrent(self):
        from backend.api import routes_torrent as routes
        self.record.update(status="completed")
        self.record["fontinass"].update(status="processing", ready=True)
        with (patch.object(routes.torrent_store, "list_torrents", return_value=[self.record]),
              patch.object(routes.torrent_store, "list_pending_torrents", return_value=[]),
              patch.object(routes, "_start_fontinass") as start,
              patch.object(routes, "qb_login", AsyncMock()) as qb):
            await routes.recover_torrent_monitors()
            start.assert_called_once_with("abc")
            qb.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
