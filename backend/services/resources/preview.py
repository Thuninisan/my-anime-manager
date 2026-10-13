"""Create a manual Torrent preview from a trusted collected resource."""

from pathlib import Path

from ...db import resources, resource_recognitions


class CachedTorrentMissing(FileNotFoundError):
    pass


class MetadataProviderUnavailable(RuntimeError):
    pass


async def preview_cached_torrent(resource_id: int) -> dict:
    resource = resources.get_resource(resource_id)
    if resource is None or not resource["torrent_path"]:
        raise CachedTorrentMissing("已保存的种子文件不存在")
    expected = resources.torrent_file_path(resource["source"], resource["source_id"])
    path = Path(resource["torrent_path"])
    if path != expected or not path.is_file():
        raise CachedTorrentMissing("已保存的种子文件不存在")
    from ..torrent.preview import parse_and_search
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
            raise MetadataProviderUnavailable(f"{provider} 数据获取失败，请检查网络或代理设置后重试") from error
        if str(error) == "resource_provider_request_failed":
            raise MetadataProviderUnavailable("外部元数据服务请求失败，请检查网络或代理设置后重试") from error
        raise
    result["resource_id"] = resource_id

    recognition = resource_recognitions.get(resource_id)
    candidates = (recognition or {}).get("resource_candidates", [])
    from ...domain.resource_resolution import normalized_title
    for candidate in candidates:
        matching = [entry for key, entry in result["search_results"].items()
                    if len(result["search_results"]) == 1 or normalized_title(key) in {
                        normalized_title(candidate["title"]), normalized_title(candidate["original_title"]),
                        *(normalized_title(title) for title in candidate["alternative_titles"])}]
        if not matching:
            continue
        from ..torrent.preview_session import deduplicate_candidates
        for context in matching:
            provider = candidate["provider"]
            if (context["media_type"] == "movie" and provider == "tvdb") or candidate["media_type"] not in (context["media_type"], "special", "unknown"):
                continue
            context["candidates"][provider] = deduplicate_candidates(context["candidates"][provider] + [candidate])
        # Historical candidates remain selectable; their directories load on
        # selection rather than extending the bounded initial discovery.
    from ..torrent.preview_session import create_preview_session
    from ..torrent.preview_view import session_view
    row = create_preview_session(result, str(path))
    view = session_view(row)
    return view
