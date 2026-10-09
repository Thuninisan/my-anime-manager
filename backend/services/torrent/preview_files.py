"""One record per torrent file, independent of matching and display groups."""
from pathlib import PurePosixPath
import re

SUBTITLE_EXTENSIONS = {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt", ".ttml", ".sbv", ".dfxp"}
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".m2ts", ".webm", ".wmv", ".mpg", ".mpeg", ".vob", ".ogm", ".flv"}
FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".woff", ".woff2", ".zip", ".7z", ".rar", ".tar", ".gz"}
AUDIO_EXTENSIONS = {".mka", ".flac", ".mp3", ".aac", ".wav", ".ogg", ".m4a", ".ape", ".opus"}


def file_type(path: str) -> str:
    extension = PurePosixPath(path).suffix.lower()
    for kind, extensions in (("video", VIDEO_EXTENSIONS), ("subtitle", SUBTITLE_EXTENSIONS),
                             ("font", FONT_EXTENSIONS), ("audio", AUDIO_EXTENSIONS)):
        if extension in extensions:
            return kind
    return "other"


def exclude_paths(files: list[dict], patterns: str) -> set[str]:
    words = [word.strip().lower() for word in patterns.split(",") if word.strip()]
    return {item["name"] for item in files if any(
        re.search(rf"(?:^|[^a-zA-Z]){re.escape(word)}(?:$|[^a-zA-Z])", item["name"].lower())
        for word in words)}


def unify_files(result: dict, original_files: list[dict] | None = None, excluded_paths=()) -> dict:
    """Collapse private parser groups; retain filtered files without duplicate records."""
    if result.get("parsed_files") and "type" in result["parsed_files"][0]:
        return result
    regular = {f["torrent_path"]: f for f in result.get("parsed_files", [])}
    special = {f["torrent_path"]: f for f in result.get("specials", [])}
    skipped = {f["torrent_path"]: f for f in result.get("skipped_files", [])}
    subtitles = set(result.get("subtitles", []))
    paths = [f["name"] for f in original_files] if original_files is not None else list(dict.fromkeys(
        [*regular, *special, *result.get("subtitles", []), *skipped]))
    files = []
    for path in dict.fromkeys(paths):
        item = regular.get(path) or special.get(path) or skipped.get(path) or {}
        kind = file_type(path)
        category = "special" if path in special and kind == "video" else "regular" if kind == "video" else None
        status, reason = "ignored", "unsupported_processing"
        if path in excluded_paths:
            reason = "exclude_pattern"
        elif kind == "subtitle" or path in subtitles:
            kind, status, reason = "subtitle", "associate", None
        elif kind == "video" and path in regular:
            status, reason = "automatic", None
        elif kind == "video" and path in special:
            status, reason = "manual", None
        elif path in skipped:
            reason = item.get("reason") or item.get("skip_reason") or "parse_failure"
        coordinate = item.get("parsed_episode") or {
            "season_number": item.get("season"), "episode_number": item.get("episode")}
        files.append({"file_name": item.get("file_name") or PurePosixPath(path).name,
            "torrent_path": path, "type": kind, "category": category,
            "show_name": item.get("show_name") or "", "parsed_episode": coordinate,
            "processing_status": status, "skip_reason": reason})
    return {**{k: v for k, v in result.items() if k not in (
        "specials", "subtitles", "subtitle_files", "skipped_files")}, "parsed_files": files}
