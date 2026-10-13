"""Collected resource search and polling endpoints."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..db import resources
from ..db import resource_recognitions
from ..db.resource_sources import list_sources
from ..services.resources import worker, sources, preview
from ..services.resources.recognition import recognize_resource
from pydantic import BaseModel


class SourceInput(BaseModel):
    name: str
    rss_url: str
    downloadtag: dict
    index_type: str


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
        return await sources.add_source(body.name, body.rss_url, body.downloadtag, body.index_type)
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
    try:
        return await preview.preview_cached_torrent(resource_id)
    except preview.CachedTorrentMissing as exc:
        raise HTTPException(404, str(exc)) from exc
    except preview.MetadataProviderUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
