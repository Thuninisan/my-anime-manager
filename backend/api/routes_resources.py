"""Collected resource search and polling endpoints."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..db import resources
from ..db import resource_recognitions
from ..db.resource_sources import list_sources, save_source
from ..services.resource_monitor import worker
from ..services.resource_monitor.feeds import parse_feed
from ..services.resource_monitor.recognize import recognize_resource
from ..utils.http_retry import fetch_with_retry
from pydantic import BaseModel


class SourceInput(BaseModel):
    name: str
    rss_url: str
    downloadtag: dict
    index_type: str


class PollIntervalInput(BaseModel):
    minutes: int

router = APIRouter(prefix="/api/resources", tags=["resources"])


@router.get("")
def list_resources(q: str = "", source: str = "", status: str = "",
                   limit: int = Query(50, ge=1, le=200),
                   offset: int = Query(0, ge=0)):
    if source and source not in list_sources():
        raise HTTPException(400, "未知资源来源")
    if status and status not in {"pending", "complete", "failed"}:
        raise HTTPException(400, "未知采集状态")
    return resources.list_resources(q=q.strip(), source=source, status=status,
                                    limit=limit, offset=offset)


@router.get("/status")
def collection_status():
    return worker.status()


@router.get("/sources")
def get_sources():
    return list(list_sources().values())


@router.get("/bangumi")
def list_bangumi_resources():
    return resource_recognitions.list_bangumi_resources()


@router.post("/sources")
async def add_source(body: SourceInput):
    try:
        if body.index_type not in {"tmdb", "tvdb"}:
            raise ValueError("index_type 必须是 tmdb 或 tvdb")
        if not body.rss_url.startswith("https://"):
            raise ValueError("RSS URL 必须使用 HTTPS")
        response = await fetch_with_retry(body.rss_url, label=f"{body.name} feed")
        items = parse_feed(body.name, response.content, body.model_dump())
        if not items:
            raise ValueError("Feed 中没有可验证的条目")
        return save_source(body.name, body.rss_url, body.downloadtag, body.index_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/run-once")
async def collect_now():
    return worker.trigger()


@router.get("/{resource_id}")
def get_resource(resource_id: int):
    resource = resources.get_resource(resource_id)
    if resource is None:
        raise HTTPException(404, "资源不存在")
    return resource


@router.get("/{resource_id}/recognition")
def get_recognition(resource_id: int):
    if resources.get_resource(resource_id) is None:
        raise HTTPException(404, "资源不存在")
    return resource_recognitions.get(resource_id)


@router.post("/{resource_id}/recognize")
async def retry_recognition(resource_id: int):
    record = resources.get_resource(resource_id)
    if record is None:
        raise HTTPException(404, "资源不存在")
    return await recognize_resource(record)


@router.get("/{resource_id}/torrent")
def get_cached_torrent(resource_id: int):
    resource = resources.get_resource(resource_id)
    if resource is None or not resource["torrent_path"]:
        raise HTTPException(404, "种子文件不存在")
    path = Path(resource["torrent_path"])
    if not path.is_file():
        raise HTTPException(404, "种子文件不存在")
    return FileResponse(path, media_type="application/x-bittorrent",
                        filename=f"{resource['source']}-{resource_id}.torrent")


@router.post("/{resource_id}/torrent-preview")
async def preview_cached_torrent(resource_id: int):
    """Only a server-selected saved torrent may enter the manual matching flow."""
    resource = resources.get_resource(resource_id)
    if resource is None or not resource["torrent_path"]:
        raise HTTPException(404, "已保存的种子文件不存在")
    expected = resources.torrent_file_path(resource["source"], resource["source_id"])
    path = Path(resource["torrent_path"])
    if path != expected or not path.is_file():
        raise HTTPException(404, "已保存的种子文件不存在")
    from ..services.torrent.preview import parse_and_search
    try:
        result = await parse_and_search(str(path))
    except RuntimeError as error:
        provider_errors = {
            "preview_provider_fetch_failed: tmdb": "TMDB",
            "preview_provider_fetch_failed: bangumi": "Bangumi",
            "preview_provider_fetch_failed: tvdb": "TVDB",
        }
        provider = provider_errors.get(str(error))
        if provider:
            raise HTTPException(502, f"{provider} 数据获取失败，请检查网络或代理设置后重试") from error
        if str(error) == "resource_provider_request_failed":
            raise HTTPException(502, "外部元数据服务请求失败，请检查网络或代理设置后重试") from error
        raise
    result["resource_id"] = resource_id

    recognition = resource_recognitions.get(resource_id)
    candidates = (recognition or {}).get("resource_candidates", [])
    from ..services.resource_resolver import normalized_title
    for candidate in candidates:
        matching = [entry for key, entry in result["search_results"].items()
                    if len(result["search_results"]) == 1 or normalized_title(key) in {
                        normalized_title(candidate["title"]), normalized_title(candidate["original_title"]),
                        *(normalized_title(title) for title in candidate["alternative_titles"])}]
        if not matching:
            continue
        from ..services.torrent.preview_session import deduplicate_candidates
        for context in matching:
            provider = candidate["provider"]
            if (context["media_type"] == "movie" and provider == "tvdb") or candidate["media_type"] not in (context["media_type"], "special", "unknown"):
                continue
            context["candidates"][provider] = deduplicate_candidates(context["candidates"][provider] + [candidate])
        # Historical candidates remain selectable; their directories load on
        # selection rather than extending the bounded initial discovery.
    from ..services.torrent.preview_session import create_preview_session
    from ..services.torrent.preview_view import session_view
    row = create_preview_session(result, str(path))
    view = session_view(row)
    return view
