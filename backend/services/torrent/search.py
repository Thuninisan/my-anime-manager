"""Alternative torrent search flow for ktnbytes / 343-Labs releases.

These releases have known-good titles parsed by anitopy — we search TMDB
directly, then pull episode data from TMDB + related Bangumi + TVDB
entries discovered through the mapping table.
"""

from ...domain.episode_adapters import episode_catalog, parsed_episode_ref

import logging
from collections import Counter
from pathlib import Path

from .preview import _parse_file, _deduplicate_show_names
from ...utils.torrent_file_reader import read_torrent_file_list, read_torrent_name
from ... import config

logger = logging.getLogger(__name__)


async def search_by_tmdb(
    torrent_path: str, torrent_name: str = "",
) -> dict:
    """Full pipeline for ktnbytes / 343-Labs torrents.

    Flow:
      1. Read torrent file list (and name if not provided)
      2. anitopy parse each file (reuses ``_parse_file``)
      3. Deduplicate show names
      4. Search TMDB with each show name
      5. Look up map.json for related Bangumi + TVDB IDs
      6. Fetch episode data: TMDB + Bangumi + TVDB
      7. Return structured result

    Args:
        torrent_path: Filesystem path to a .torrent file.
        torrent_name: Optional pre-read torrent name (avoids re-reading).

    Returns:
        Nested dict with the unified ``parsed_files`` inventory,
        ``show_names``, ``search_results``, and ``provider_catalogs``.

    Raises:
        RuntimeError: If no files can be parsed from the torrent.
    """
    from .preview_files import exclude_paths, file_type, unify_files

    # ── Step 1: Read torrent ──
    if not torrent_name:
        torrent_name = read_torrent_name(torrent_path)
    logger.info("torrent.tmdb_first start torrent=%r", torrent_name)

    logger.debug("📋 读取种子文件内容 (bencode)...")
    file_list: list[dict] = read_torrent_file_list(torrent_path)
    original_files = list(file_list)
    excluded_paths = exclude_paths(original_files, config.TORRENT_EXCLUDE_PATTERNS)
    file_list = [file for file in file_list if file["name"] not in excluded_paths]
    logger.debug(f"   → {len(file_list)} 个文件")

    # ── Collect subtitle files before anitopy parsing ──
    subtitle_files: list[str] = [
        f["name"]
        for f in file_list
        if Path(f["name"]).suffix.lower() in {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt", ".ttml", ".sbv", ".dfxp"}
    ]
    if subtitle_files:
        logger.debug(f"   📝 {len(subtitle_files)} 个字幕文件")

    # ── Filter subtitle / font-archive files ──
    before_ext = len(file_list)
    file_list = [
        f for f in file_list
        if file_type(f["name"]) == "video"
    ]
    ext_skipped = before_ext - len(file_list)
    if ext_skipped:
        logger.debug(f"   📎 非视频文件过滤: {ext_skipped} 个文件 (字幕/字体/音频)")

    # ── Step 2: anitopy parsing ──
    logger.debug("🔧 anitopy 逐文件解析...")
    parsed_results: list[dict] = [_parse_file(f) for f in file_list]

    parsed_files: list[dict] = [r for r in parsed_results if not r["is_extra"]]
    skipped_files: list[dict] = [
        {
            "file_name": r["file_name"],
            "torrent_path": r["torrent_path"],
            "skip_reason": r["skip_reason"],
        }
        for r in parsed_results if r["is_extra"]
    ]

    logger.debug(f"   合规剧集: {len(parsed_files)} 个")
    logger.debug(f"   跳过文件: {len(skipped_files)} 个")
    reason_counts = Counter(s["skip_reason"] for s in skipped_files)
    for reason, count in reason_counts.most_common():
        logger.debug(f"     - {reason}: {count}")

    if not parsed_files:
        raise RuntimeError("没有找到可处理的剧集文件")

    # ── Collect SP/Extra files ──
    specials: list[dict] = [
        {
            "file_name": r["file_name"],
            "torrent_path": r["torrent_path"],
        }
        for r in parsed_results if r["is_extra"]
    ]

    # ── Step 3: Deduplicate show names ──
    show_names: list[str] = _deduplicate_show_names(parsed_files)
    logger.debug(f"📛 去重节目名: {len(show_names)} 个")
    for i, name in enumerate(show_names):
        count = sum(
            1 for p in parsed_files
            if p.get("show_name", "").lower() == name.lower()
        )
        logger.debug(f"   [{i + 1}] {name} ({count} 个文件)")

    # ── Step 4 + 5 + 6: Search TMDB → map lookup → fetch episode data ──
    logger.debug("🔍 TMDB 直搜 + 关联数据获取...")

    # All torrent naming variants share discovery limits and concurrent catalogs.
    from .preview import _parallel_search, _organize, _fetch_provider_catalogs
    pairs = await _parallel_search(show_names, parsed_files)
    search_results = _organize(pairs)["search_results"]
    provider_catalogs = await _fetch_provider_catalogs(search_results, parsed_files)
    from .preview_session import candidate_context
    search_results = {key: candidate_context(key, entry, provider_catalogs) for key, entry in search_results.items()}

    return unify_files({
        "index": "tmdb" if provider_catalogs.get("tmdb") else "tvdb" if provider_catalogs.get("tvdb") else "tmdb",
        "torrent_name": torrent_name,
        "torrent_path": torrent_path,
        "total_files": len(file_list),
        "subtitles": subtitle_files,
        "parsed_files": [
            {
                "file_name": p["file_name"],
                "torrent_path": p["torrent_path"],
                "show_name": p["show_name"],
                "season": p["season"],
                "episode": p["episode"],
                "parsed": p["parsed"],
                "parsed_episode": parsed_episode_ref(p),
            }
            for p in parsed_files
        ],
        "specials": specials,
        "skipped_files": skipped_files,
        "show_names": show_names,
        "search_results": search_results,
        "provider_catalogs": provider_catalogs,
        "episode_catalog": episode_catalog(provider_catalogs),
    }, original_files, excluded_paths)
