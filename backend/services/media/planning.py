"""Build destinations and file operations from confirmed Torrent selections."""

import re
from pathlib import Path

from ... import config
from ...domain.episode_metadata_adapters import episode_path_parameters
from ...utils.paths import SUBTITLE_DIR


def subtitle_suffix(item: dict, fallback: str) -> str:
    suffix = item.get("subtitle_suffix", "")
    return suffix if re.fullmatch(r"(?:\.sub[1-9][0-9]*)?(?:\.zh-(?:CN|TW))?\.[a-zA-Z0-9]+", suffix) else fallback


def build_processing(context: dict) -> dict:
    """Resolve confirmed selections into file operations before download starts."""
    from .paths import format_download_path

    root = Path(context["hardlink_root"])
    movie_meta = context.get("movie_meta")
    torrent_name = context["torrent_name"]
    subtitle_dir = SUBTITLE_DIR / sanitize_torrent_name(torrent_name)
    operations: list[dict] = []

    def destination(item: dict, suffix: str, *, is_subtitle: bool) -> Path:
        if is_subtitle:
            suffix = subtitle_suffix(item, suffix)
        if movie_meta:
            title = movie_meta["tmdb_name"]
            return root / title / f"{title}{suffix}"
        sub = subscription_path_context(item, context.get("series_name", ""))
        rel = format_download_path(
            config.RSS_PATH_TEMPLATE, sub,
            **episode_path_parameters(item),
        ).lstrip("/")
        rel = str(Path(rel).with_suffix(suffix))
        target = root / item.get("replacement_target", rel)
        if item.get("replacement_target") and not is_subtitle:
            target = target.with_name(target.name + ".mam-bd-pending")
        return target

    for item in context["files"]:
        is_subtitle = bool(item.get("is_subtitle"))
        operation = {
            "torrent_path": item["torrent_path"],
            "target_path": str(destination(item, Path(item["torrent_path"]).suffix, is_subtitle=is_subtitle)),
            "action": "copy" if is_subtitle else "hardlink",
        }
        if not is_subtitle and item.get("episode_mapping") is not None:
            from ...domain.persistence import episode_mapping_snapshot
            operation["episode_mapping_snapshot"] = episode_mapping_snapshot(
                item["episode_mapping"], item.get("resource_identity"), revision=item.get("identity_revision"))
        if not is_subtitle and item.get("resource_identity") is not None:
            operation["resource_identity"] = item["resource_identity"]
        operations.append(operation)

    for item in context.get("uploaded_subtitles", []):
        stored = item.get("stored_filename", "")
        operations.append({
            "source_path": str(subtitle_dir / stored),
            "target_path": str(destination(item, Path(stored).suffix, is_subtitle=True)),
            "action": "copy",
        })

    return {
        "mode": "movie" if movie_meta else "tv",
        "replace_bangumi_id": context.get("replace_bangumi_id"),
        "replaced_history": context.get("replaced_history"),
        "files": operations,
    }


def sanitize_torrent_name(name: str) -> str:
    """Remove characters that are illegal in directory / file names."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def subscription_path_context(f: dict, series_name: str = "") -> dict:
    """Build a pseudo-subscription dict for :func:`format_download_path`."""
    mapping = f["episode_mapping"]
    bgm_name = f.get("bangumi_show_name", "")
    return {
        "name": bgm_name,
        "series_name": series_name or f.get("tmdb_show_name") or bgm_name,
        "bgm": {
            "subject_name": bgm_name,
            "season": 1,
        },
        "tvdb": {
            "season": mapping["tvdb"]["season_number"] if mapping["tvdb"]["season_number"] is not None else mapping["tmdb"]["season_number"],
        },
        "tmdb": {
            "season": mapping["tmdb"]["season_number"],
        },
    }
