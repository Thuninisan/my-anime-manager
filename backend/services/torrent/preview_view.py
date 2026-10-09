"""The sole private snapshot → public matcher/UI projection."""
import copy
from ...domain.preview import PreviewContextSnapshot, TorrentPreviewResponse, PreviewParsedFileView, PreviewSearchEntry


def search_views(snapshot: PreviewContextSnapshot) -> dict[str, PreviewSearchEntry]:
    return copy.deepcopy(snapshot["series_contexts"])


def build_preview_view(snapshot: PreviewContextSnapshot, preview_id: str, revision: int, expires_at: str) -> TorrentPreviewResponse:
    def file_view(item) -> PreviewParsedFileView:
        return {"file_id": item["file_id"], "file_name": item["file_name"], "torrent_path": item["torrent_path"],
                "show_name": item["show_key"], "parsed_episode": dict(item["parsed"])}
    return {
            "preview_id": preview_id, "revision": revision, "expires_at": expires_at,
            "torrent_name": snapshot["torrent"]["name"],
            "resource_id": snapshot["torrent"]["resource_id"], "episode_match_source": snapshot["episode_match_source"],
            "parsed_files": [file_view(f) for f in snapshot["parsed_files"] if f["kind"] == "video"],
            "specials": [file_view(f) for f in snapshot["parsed_files"] if f["kind"] == "special"],
            "subtitles": [f["torrent_path"] for f in snapshot["parsed_files"] if f["kind"] == "subtitle"],
            "subtitle_files": [file_view(f) for f in snapshot["parsed_files"] if f["kind"] == "subtitle"],
            "skipped_files": copy.deepcopy(snapshot["skipped_files"]),
            "search_results": search_views(snapshot), "episode_catalog": copy.deepcopy(snapshot["episode_catalog"])}


def session_view(row) -> dict:
    from .preview_session import load_preview_session
    row, snapshot = load_preview_session(row.id)
    return build_preview_view(snapshot, row.id, row.revision, row.expires_at)
