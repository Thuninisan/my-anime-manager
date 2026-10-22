"""在线作品候选搜索接口。"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services.discovery import search

router = APIRouter()


class SearchResult(BaseModel):
    provider: Literal["tmdb", "tvdb"]
    provider_id: int
    media_type: Literal["tv", "movie"]
    name: str
    name_original: str = ""
    date: str = ""
    poster_url: str = ""


@router.get("/api/discovery/search", response_model=list[SearchResult])
async def search_candidates(
    provider: Literal["tmdb", "tvdb"],
    media_type: Literal["tv", "movie"] = "tv",
    q: str = Query(default="", max_length=300),
):
    if provider == "tvdb" and media_type == "movie":
        raise HTTPException(422, "TVDB 暂不支持电影候选")
    try:
        return await search.search_candidates(provider, media_type, q)
    except search.SearchUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
