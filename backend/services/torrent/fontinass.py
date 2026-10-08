"""Durable ASS processing after subtitle copies, preserving source downloads."""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import tempfile
import traceback
from pathlib import Path
from time import perf_counter

from ... import config
from ...clients import fontinass as client
from ...db import torrents as store
from ...logging.logging_config import current_operation_id, new_operation_id, operation_context, safe_url

logger = logging.getLogger(__name__)
_semaphore = asyncio.Semaphore(1)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _state(info_hash: str) -> dict:
    record = store.get_torrent(info_hash)
    return (record or {}).get("fontinass") or {"status": "waiting", "files": []}


def _checkpoint(info_hash: str, state: dict) -> None:
    started = perf_counter()
    try:
        store.save_fontinass(info_hash, state)
    except Exception as error:
        # SQL exception text/parameters can contain service addresses and messages.
        logger.error("fontinass.checkpoint.failed torrent=%s status=%s error_type=%s stack=%s",
                     info_hash, state.get("status"), type(error).__name__, "".join(traceback.format_tb(error.__traceback__)))
        raise RuntimeError("FontInAss 状态保存失败") from None
    logger.debug("fontinass.checkpoint.saved torrent=%s status=%s files=%d elapsed_ms=%.1f",
                 info_hash, state.get("status"), len(state.get("files", [])), (perf_counter() - started) * 1000)


def copy_subtitle(info_hash: str, source: Path, target: Path) -> None:
    """Checkpoint ASS copies; recovery never overwrites processed/edited targets."""
    logger.debug("fontinass.copy.start torrent=%s source=%r target=%r extension=%s", info_hash, str(source), str(target), target.suffix.lower())
    try:
        _copy_subtitle(info_hash, source, target)
    except OSError:
        logger.exception("fontinass.copy.failed torrent=%s source=%r target=%r", info_hash, str(source), str(target))
        raise


def _copy_subtitle(info_hash: str, source: Path, target: Path) -> None:
    if target.suffix.lower() != ".ass":
        logger.debug("fontinass.copy.skip_processing torrent=%s target=%r reason=not_ass", info_hash, str(target))
        shutil.copy2(source, target)
        logger.debug("fontinass.copy.completed torrent=%s target=%r tracked=false", info_hash, str(target))
        return
    state = _state(info_hash)
    if not config.FONTINASS_ENABLED and not state["files"]:
        logger.debug("fontinass.copy.skip_processing torrent=%s target=%r reason=disabled", info_hash, str(target))
        shutil.copy2(source, target)
        logger.debug("fontinass.copy.completed torrent=%s target=%r tracked=false", info_hash, str(target))
        return
    path = str(target.absolute())
    entry = next((item for item in state["files"] if item["path"] == path), None)
    if entry:
        if target.is_file():
            current = digest(target.read_bytes())
            logger.debug("fontinass.copy.recovery_check torrent=%s target=%r current_hash=%s input_hash=%s output_hash=%s",
                         info_hash, path, current[:12], entry.get("input_hash", "")[:12], entry.get("output_hash", "")[:12])
            if current == entry.get("output_hash"):
                logger.debug("fontinass.copy.recovered torrent=%s target=%r reason=output_hash_matches", info_hash, path)
                entry["status"] = "success"
                _checkpoint(info_hash, state)
                return
            if current == entry.get("input_hash"):
                logger.debug("fontinass.copy.skip_recopy torrent=%s target=%r status=%s reason=input_hash_matches", info_hash, path, entry["status"])
                if entry["status"] == "copying":
                    entry["status"] = "pending"
                    _checkpoint(info_hash, state)
                return
        if entry["status"] != "copying":
            logger.warning("fontinass.copy.conflict torrent=%s target=%r reason=changed_or_missing", info_hash, path)
            entry.update(status="failed", error="目标字幕已改变或丢失，未重新复制或覆盖")
            _checkpoint(info_hash, state)
            return
    else:
        entry = {"path": path, "status": "copying", "attempts": 0,
                 "input_hash": digest(source.read_bytes())}
        state["files"].append(entry)
        _checkpoint(info_hash, state)
    shutil.copy2(source, target)
    entry.update(status="pending", input_hash=digest(target.read_bytes()))
    _checkpoint(info_hash, state)
    logger.info("fontinass.copy.ready torrent=%s target=%r", info_hash, path)


def _ass_events(data: bytes) -> list[tuple[str, str]]:
    text = None
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "big5"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeError:
            continue
    if text is None:
        raise ValueError("字幕编码无法识别")
    section = ""
    sections = set()
    fields: list[str] = []
    style_fields: list[str] = []
    valid_style = False
    events = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line.lower()
            sections.add(section)
        elif section == "[v4+ styles]":
            key, sep, value = line.partition(":")
            if sep and key.lower() == "format":
                style_fields = [field.strip().lower() for field in value.split(",")]
            elif sep and key.lower() == "style":
                if "name" not in style_fields or "fontname" not in style_fields or len(value.split(",")) != len(style_fields):
                    raise ValueError("ASS 字幕样式无效")
                valid_style = True
        elif section == "[events]":
            key, sep, value = line.partition(":")
            if not sep:
                continue
            if key.lower() == "format":
                fields = [field.strip().lower() for field in value.split(",")]
            elif key.lower() == "dialogue":
                if "start" not in fields or "end" not in fields or fields[-1:] != ["text"]:
                    raise ValueError("ASS 事件格式无效")
                parts = value.split(",", len(fields) - 1)
                if len(parts) != len(fields):
                    raise ValueError("ASS 事件内容无效")
                events.append((parts[fields.index("start")].strip(), parts[fields.index("end")].strip()))
    if not {"[script info]", "[v4+ styles]", "[events]"}.issubset(sections) or not events or not valid_style:
        raise ValueError("返回内容不是包含字幕事件的有效 ASS")
    return events


def validate_result(original: bytes, result: bytes) -> None:
    logger.debug("fontinass.validation.start input_bytes=%d output_bytes=%d", len(original), len(result))
    if not result:
        raise ValueError("FontInAss 返回字幕的事件数量或时间轴不一致")
    original_events, result_events = _ass_events(original), _ass_events(result)
    logger.debug("fontinass.validation.events input_events=%d output_events=%d", len(original_events), len(result_events))
    if original_events != result_events:
        raise ValueError("FontInAss 返回字幕的事件数量或时间轴不一致")
    logger.debug("fontinass.validation.passed events=%d", len(result_events))


def _replace(target: Path, data: bytes, expected_hash: str) -> None:
    logger.debug("fontinass.replace.start target=%r output_bytes=%d", str(target), len(data))
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".mam-fontinass-", delete=False) as temporary:
            name = temporary.name
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        logger.debug("fontinass.replace.temp_written target=%r temporary=%r output_bytes=%d", str(target), name, len(data))
        current_hash = digest(target.read_bytes())
        logger.debug("fontinass.replace.check target=%r current_hash=%s expected_hash=%s",
                     str(target), current_hash[:12], expected_hash[:12])
        if target.is_symlink() or current_hash != expected_hash:
            raise ValueError("处理期间目标字幕发生变化，保留当前文件")
        shutil.copystat(target, name)
        os.replace(name, target)
        logger.debug("fontinass.replace.completed target=%r", str(target))
    finally:
        if name:
            Path(name).unlink(missing_ok=True)
            logger.debug("fontinass.replace.cleanup temporary=%r", name)


async def process(info_hash: str, *, retry: bool = False) -> None:
    operation_id = current_operation_id()
    with operation_context(operation_id if operation_id != "-" else new_operation_id("fontinass")):
        try:
            await _process(info_hash, retry=retry)
        except asyncio.CancelledError:
            logger.info("fontinass.task.cancelled torrent=%s recovery=persisted_checkpoint", info_hash)
            raise
        except Exception as error:
            logger.error("fontinass.task.exception torrent=%s error_type=%s stack=%s",
                         info_hash, type(error).__name__, "".join(traceback.format_tb(error.__traceback__)))
            raise


async def _process(info_hash: str, *, retry: bool = False) -> None:
    started = perf_counter()
    state = _state(info_hash)
    logger.debug("fontinass.task.selected torrent=%s ass_files=%d enabled=%s previous_status=%s retry=%s",
                 info_hash, len(state["files"]), config.FONTINASS_ENABLED, state.get("status"), retry)
    if retry:
        state.pop("settings", None)
        for entry in state["files"]:
            if entry["status"] == "failed":
                entry.update(status="pending", error="")
    if not state["files"]:
        state["status"] = "not_needed" if config.FONTINASS_ENABLED else "disabled"
        _checkpoint(info_hash, state)
        logger.debug("fontinass.task.skipped torrent=%s reason=%s", info_hash, state["status"])
        return
    state.setdefault("settings", {"server": config.FONTINASS_URL, "timeout": config.FONTINASS_TIMEOUT})
    state["ready"] = True
    state["status"] = "processing"
    _checkpoint(info_hash, state)
    logger.info("fontinass.task.start torrent=%s ass_files=%d", info_hash, len(state["files"]))
    logger.debug("fontinass.task.settings torrent=%s origin=%s timeout_s=%d strict=true concurrency=1",
                 info_hash, safe_url(state["settings"]["server"]), state["settings"]["timeout"])
    skipped = 0
    for entry in state["files"]:
        if entry["status"] == "failed":
            skipped += 1
            logger.debug("fontinass.file.skipped torrent=%s target=%r reason=previous_failure", info_hash, entry["path"])
            continue
        file_started = perf_counter()
        phase = "read"
        try:
            target = Path(entry["path"])
            logger.info("fontinass.file.start torrent=%s target=%r", info_hash, str(target))
            if target.is_symlink():
                raise ValueError("目标字幕是符号链接，未处理")
            original = target.read_bytes()
            current_hash = digest(original)
            logger.debug("fontinass.file.read torrent=%s target=%r input_bytes=%d current_hash=%s input_hash=%s output_hash=%s",
                         info_hash, str(target), len(original), current_hash[:12], entry["input_hash"][:12], entry.get("output_hash", "")[:12])
            if current_hash == entry.get("output_hash"):
                skipped += 1
                logger.debug("fontinass.file.skipped torrent=%s target=%r reason=already_processed", info_hash, str(target))
                entry["status"] = "success"
                _checkpoint(info_hash, state)
                continue
            if current_hash != entry["input_hash"]:
                raise ValueError("目标字幕已改变，未覆盖")
            if not config.FONTINASS_ENABLED:
                entry["status"] = "pending"
                state["status"] = "disabled"
                _checkpoint(info_hash, state)
                logger.info("fontinass.task.paused torrent=%s reason=disabled", info_hash)
                return
            entry["status"] = "processing"
            entry.pop("code", None)
            entry.pop("messages", None)
            _checkpoint(info_hash, state)
            phase = "queue"
            queued_at = perf_counter()
            logger.debug("fontinass.file.queued torrent=%s target=%r", info_hash, str(target))
            async with _semaphore:
                logger.debug("fontinass.file.acquired torrent=%s target=%r queue_ms=%.1f", info_hash, str(target), (perf_counter() - queued_at) * 1000)
                result = None
                for attempt in range(3):
                    if not config.FONTINASS_ENABLED:
                        entry["status"] = "pending"
                        state["status"] = "disabled"
                        _checkpoint(info_hash, state)
                        logger.info("fontinass.task.paused torrent=%s reason=disabled", info_hash)
                        return
                    entry["attempts"] += 1
                    _checkpoint(info_hash, state)
                    phase = "request"
                    logger.debug("fontinass.file.attempt torrent=%s target=%r attempt=%d max_attempts=3 cumulative_attempts=%d",
                                 info_hash, str(target), attempt + 1, entry["attempts"])
                    try:
                        result = await client.subset(state["settings"]["server"], state["settings"]["timeout"], original)
                        break
                    except client.SubsetError as error:
                        if not error.retryable or attempt == 2:
                            logger.debug("fontinass.file.no_retry torrent=%s target=%r attempt=%d retryable=%s", info_hash, str(target), attempt + 1, error.retryable)
                            raise
                        # A very long Retry-After is left for explicit later retry.
                        if error.retry_after > 60:
                            logger.warning("fontinass.file.retry_deferred torrent=%s target=%r retry_after_s=%.1f", info_hash, str(target), error.retry_after)
                            raise client.SubsetError(f"服务要求等待 {error.retry_after:.0f} 秒后重试") from error
                        delay = max(2 ** (attempt + 1), error.retry_after)
                        logger.warning("fontinass.file.retry torrent=%s target=%r attempt=%d wait_s=%.1f reason=%s",
                                       info_hash, str(target), attempt + 1, delay, str(error))
                        await asyncio.sleep(delay)
            entry["code"] = result.code
            entry["messages"] = [message[:2000] for message in result.messages][:20]
            logger.debug("fontinass.file.result torrent=%s target=%r code=%d message_count=%d output_bytes=%d",
                         info_hash, str(target), result.code, len(result.messages), len(result.data))
            missing_glyph_fonts = [match.group(1) for message in result.messages
                                   if (match := re.match(r"Missing glyphs in \[([^\]\r\n]{1,200})\]:", message))][:20]
            if missing_glyph_fonts:
                logger.debug("fontinass.file.missing_glyph_fonts torrent=%s target=%r fonts=%r",
                             info_hash, str(target), missing_glyph_fonts)
            phase = "business_result"
            if result.code != 200:
                raise ValueError(f"FontInAss 业务码 {result.code}：" + ("；".join(entry["messages"]) or "处理未完整成功"))
            phase = "validation"
            validate_result(original, result.data)
            entry["output_hash"] = digest(result.data)
            # Save the expected output before replacement to recover a crash between these steps.
            _checkpoint(info_hash, state)
            phase = "replace"
            _replace(target, result.data, current_hash)
            entry.update(status="success", error="")
            logger.info("fontinass.file.success torrent=%s target=%r input_bytes=%d output_bytes=%d elapsed_ms=%.1f",
                        info_hash, str(target), len(original), len(result.data), (perf_counter() - file_started) * 1000)
        except asyncio.CancelledError:
            logger.debug("fontinass.file.cancelled torrent=%s target=%r phase=%s attempts=%d",
                         info_hash, entry["path"], phase, entry["attempts"])
            raise
        except (OSError, ValueError, client.SubsetError) as error:
            entry.update(status="failed", error=str(error)[:2000])
            # Business messages can include the entire missing character set.
            business_reason = {201: "服务返回警告，未完整处理", 300: "缺失字体或字形", 400: "服务拒绝字幕输入", 500: "服务内部处理错误"}.get(entry.get("code"), "未知业务结果")
            reason = f"{business_reason}（完整消息见任务详情）" if phase == "business_result" else str(error)
            logger.warning("fontinass.file.failed torrent=%s target=%r phase=%s error_type=%s code=%s reason=%s elapsed_ms=%.1f original_preserved=true",
                           info_hash, entry["path"], phase, type(error).__name__, entry.get("code"), reason, (perf_counter() - file_started) * 1000)
            if isinstance(error, OSError):
                logger.exception("fontinass.file.filesystem_error torrent=%s target=%r phase=%s", info_hash, entry["path"], phase)
        _checkpoint(info_hash, state)
    succeeded = sum(entry["status"] == "success" for entry in state["files"])
    state["status"] = "success" if succeeded == len(state["files"]) else "partial" if succeeded else "failed"
    _checkpoint(info_hash, state)
    logger.info("fontinass.task.finish torrent=%s status=%s total=%d success=%d failed=%d skipped=%d elapsed_ms=%.1f",
                info_hash, state["status"], len(state["files"]), succeeded, len(state["files"]) - succeeded, skipped, (perf_counter() - started) * 1000)
