"""Synchronize community mappings directly into SQLite."""

import asyncio
import logging
import re
from threading import Lock

from backend import data
from ...clients import mapping_source

logger = logging.getLogger(__name__)

_sync_lock = Lock()
SYNC_INTERVAL_SECONDS = 24 * 60 * 60


def status() -> dict:
    count = data.mapping_count()
    return {"exists": count > 0, "count": count, "syncing": _sync_lock.locked()}


class MappingSyncBusy(RuntimeError):
    pass


def parse_documents(document, kometa: dict | None) -> dict[int, dict]:
    items = document.get("items") if isinstance(document, dict) else document
    if not isinstance(items, list) or not items:
        raise ValueError("Bangumi 数据为空或格式无效")
    references = {int(key): value for key, value in (kometa or {}).items()}
    records = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Bangumi 条目格式无效")
        ids = {}
        tmdb_season = None
        for site in item.get("sites", []):
            provider, sid = site.get("site"), str(site.get("id", ""))
            if provider in ("bangumi", "mikan", "anidb") and sid:
                ids[provider] = int(sid)
            elif provider == "tmdb" and sid:
                match = re.fullmatch(r"(?:tv|movie)/(\d+)(?:/season/(\d+))?", sid)
                if match:
                    ids[provider] = int(match[1])
                    tmdb_season = int(match[2]) if match[2] else None
        if "bangumi" not in ids:
            continue
        title = item.get("title", "")
        names = item.get("titleTranslate", {}).get("zh-Hans", [])
        if not isinstance(title, str) or not isinstance(names, list):
            raise ValueError("Bangumi 标题格式无效")
        entry = {"title_cn": names[0] if names else title, "title_jp": title,
                 "mikan_id": ids.get("mikan"),
                 "tmdb_id": ids.get("tmdb"), "tmdb_season": tmdb_season if tmdb_season is not None else 1}
        reference = references.get(ids.get("anidb"), {})
        for field in ("tvdb_id", "tvdb_season"):
            value = reference.get(field)
            entry[field] = int(value) if value is not None else None
        if not isinstance(entry["title_cn"], str) or ids["bangumi"] <= 0:
            raise ValueError("Bangumi 映射字段无效")
        records[ids["bangumi"]] = entry
    if not records:
        raise ValueError("上游数据没有有效的 Bangumi 映射")
    return records


def validate_kometa(document) -> dict:
    if not isinstance(document, dict) or not document:
        raise ValueError("Kometa 数据为空或格式无效")
    for key, entry in document.items():
        int(key)
        if not isinstance(entry, dict):
            raise ValueError("Kometa 条目格式无效")
        for field in ("tvdb_id", "tvdb_season"):
            if entry.get(field) is not None:
                int(entry[field])
    return document


async def _download() -> tuple[dict, str | None]:
    logger.info("开始更新社区映射")
    document = await mapping_source.fetch_document(mapping_source.BANGUMI_DATA_URL)
    warning = None
    try:
        kometa = validate_kometa(await mapping_source.fetch_document(mapping_source.KOMETA_ANIME_IDS_URL))
    except Exception:
        logger.warning("Kometa 数据获取失败，保留已有 TVDB 映射", exc_info=True)
        warning = "Kometa 获取失败，已保留已有 TVDB 映射；请稍后重试完整更新。"
        kometa = None
    return parse_documents(document, kometa), warning


async def _update() -> dict:
    records, warning = await asyncio.wait_for(_download(), timeout=120)
    # Wait for a started transaction even on shutdown, so the lock is not
    # released while its database write is still running in another thread.
    commit = asyncio.create_task(asyncio.to_thread(
        data.replace_mappings, records,
        preserve_fields=("tvdb_id", "tvdb_season") if warning else (),
    ))
    try:
        await asyncio.shield(commit)
    except asyncio.CancelledError:
        await commit
        raise
    output = f"已更新 {len(records)} 条 Bangumi 映射。"
    if warning:
        output += "\n" + warning
    logger.info("社区映射更新完成: count=%d partial=%s", len(records), warning is not None)
    return {"ok": True, "output": output, "count": len(records), "warning": warning}


async def synchronize(*, only_if_needed=False) -> dict:
    if not _sync_lock.acquire(blocking=False):
        raise MappingSyncBusy("映射正在更新，请稍后重试")
    try:
        if only_if_needed and data.mapping_count() > 0:
            return {"ok": True, "output": "已有社区映射，无需初始化。"}
        return await _update()
    except asyncio.CancelledError:
        logger.warning("映射更新已中断")
        raise
    except Exception:
        logger.exception("社区映射更新失败，原有数据已保留")
        raise
    finally:
        _sync_lock.release()


async def run_scheduler() -> None:
    """Initialize an empty database, then refresh community mappings daily."""
    only_if_needed = True
    while True:
        try:
            await synchronize(only_if_needed=only_if_needed)
        except MappingSyncBusy:
            logger.info("已有映射更新任务，跳过本次自动同步")
        except Exception:
            logger.warning("社区映射自动更新未完成，将在下一轮重试")
        only_if_needed = False
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
