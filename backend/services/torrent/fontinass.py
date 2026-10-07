"""Durable ASS processing after subtitle copies, preserving source downloads."""

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path

from ... import config
from ...clients import fontinass as client
from ...db import torrents as store

logger = logging.getLogger(__name__)
_semaphore = asyncio.Semaphore(1)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _state(info_hash: str) -> dict:
    record = store.get_torrent(info_hash)
    return (record or {}).get("fontinass") or {"status": "waiting", "files": []}


def copy_subtitle(info_hash: str, source: Path, target: Path) -> None:
    """Checkpoint ASS copies; recovery never overwrites processed/edited targets."""
    if target.suffix.lower() != ".ass":
        shutil.copy2(source, target)
        return
    state = _state(info_hash)
    if not config.FONTINASS_ENABLED and not state["files"]:
        shutil.copy2(source, target)
        return
    path = str(target.absolute())
    entry = next((item for item in state["files"] if item["path"] == path), None)
    if entry:
        if target.is_file():
            current = digest(target.read_bytes())
            if current == entry.get("output_hash"):
                entry["status"] = "success"
                store.save_fontinass(info_hash, state)
                return
            if current == entry.get("input_hash"):
                if entry["status"] == "copying":
                    entry["status"] = "pending"
                    store.save_fontinass(info_hash, state)
                return
        if entry["status"] != "copying":
            entry.update(status="failed", error="目标字幕已改变或丢失，未重新复制或覆盖")
            store.save_fontinass(info_hash, state)
            return
    else:
        entry = {"path": path, "status": "copying", "attempts": 0,
                 "input_hash": digest(source.read_bytes())}
        state["files"].append(entry)
        store.save_fontinass(info_hash, state)
    shutil.copy2(source, target)
    entry.update(status="pending", input_hash=digest(target.read_bytes()))
    store.save_fontinass(info_hash, state)


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
    if not result or _ass_events(original) != _ass_events(result):
        raise ValueError("FontInAss 返回字幕的事件数量或时间轴不一致")


def _replace(target: Path, data: bytes, expected_hash: str) -> None:
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".mam-fontinass-", delete=False) as temporary:
            name = temporary.name
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        if target.is_symlink() or digest(target.read_bytes()) != expected_hash:
            raise ValueError("处理期间目标字幕发生变化，保留当前文件")
        shutil.copystat(target, name)
        os.replace(name, target)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


async def process(info_hash: str, *, retry: bool = False) -> None:
    state = _state(info_hash)
    if retry:
        state.pop("settings", None)
        for entry in state["files"]:
            if entry["status"] == "failed":
                entry.update(status="pending", error="")
    if not state["files"]:
        state["status"] = "not_needed" if config.FONTINASS_ENABLED else "disabled"
        store.save_fontinass(info_hash, state)
        return
    state.setdefault("settings", {"server": config.FONTINASS_URL, "timeout": config.FONTINASS_TIMEOUT})
    state["ready"] = True
    state["status"] = "processing"
    store.save_fontinass(info_hash, state)
    for entry in state["files"]:
        if entry["status"] == "failed":
            continue
        try:
            target = Path(entry["path"])
            if target.is_symlink():
                raise ValueError("目标字幕是符号链接，未处理")
            original = target.read_bytes()
            current_hash = digest(original)
            if current_hash == entry.get("output_hash"):
                entry["status"] = "success"
                store.save_fontinass(info_hash, state)
                continue
            if current_hash != entry["input_hash"]:
                raise ValueError("目标字幕已改变，未覆盖")
            if not config.FONTINASS_ENABLED:
                entry["status"] = "pending"
                state["status"] = "disabled"
                store.save_fontinass(info_hash, state)
                return
            entry["status"] = "processing"
            store.save_fontinass(info_hash, state)
            async with _semaphore:
                result = None
                for attempt in range(3):
                    if not config.FONTINASS_ENABLED:
                        entry["status"] = "pending"
                        state["status"] = "disabled"
                        store.save_fontinass(info_hash, state)
                        return
                    entry["attempts"] += 1
                    store.save_fontinass(info_hash, state)
                    try:
                        result = await client.subset(state["settings"]["server"], state["settings"]["timeout"], original)
                        break
                    except client.SubsetError as error:
                        if not error.retryable or attempt == 2:
                            raise
                        # A very long Retry-After is left for explicit later retry.
                        if error.retry_after > 60:
                            raise client.SubsetError(f"服务要求等待 {error.retry_after:.0f} 秒后重试") from error
                        await asyncio.sleep(max(2 ** (attempt + 1), error.retry_after))
            entry["code"] = result.code
            entry["messages"] = [message[:2000] for message in result.messages][:20]
            if result.code != 200:
                raise ValueError(f"FontInAss 业务码 {result.code}：" + ("；".join(entry["messages"]) or "处理未完整成功"))
            validate_result(original, result.data)
            entry["output_hash"] = digest(result.data)
            # Save the expected output before replacement to recover a crash between these steps.
            store.save_fontinass(info_hash, state)
            _replace(target, result.data, current_hash)
            entry.update(status="success", error="")
            logger.info("FontInAss 字幕处理成功 [%s]: %s", info_hash[:8], target.name)
        except (OSError, ValueError, client.SubsetError) as error:
            entry.update(status="failed", error=str(error)[:2000])
            logger.warning("FontInAss 保留原字幕 [%s]: %s — %s", info_hash[:8], Path(entry["path"]).name, entry["error"])
        store.save_fontinass(info_hash, state)
    succeeded = sum(entry["status"] == "success" for entry in state["files"])
    state["status"] = "success" if succeeded == len(state["files"]) else "partial" if succeeded else "failed"
    store.save_fontinass(info_hash, state)
