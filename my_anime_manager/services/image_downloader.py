"""Image downloading utilities for TMDB and Bangumi."""

from pathlib import Path

import httpx

from ..clients.tmdb import TMDB_IMAGE_BASE, get_tv_images
from .. import config


def _get_proxy() -> str | None:
    if config.PROXY_HOST:
        return f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"
    return None


async def _download_image(url: str, file_path_no_ext: str) -> str | None:
    """Download an image and save it, detecting extension from Content-Type.

    Args:
        url: Image URL
        file_path_no_ext: Save path without file extension

    Returns:
        Full saved path with extension, or None on failure
    """
    if not url:
        return None

    # Skip if any file with same base name already exists (any extension)
    parent = Path(file_path_no_ext).parent
    base = Path(file_path_no_ext).name
    existing = list(parent.glob(base + ".*"))
    if existing:
        print(f"   ⏭️ 图片已存在，跳过: {existing[0]}")
        return str(existing[0])

    try:
        async with httpx.AsyncClient(proxy=_get_proxy(), timeout=30.0) as client:
            res = await client.get(url)
            res.raise_for_status()

        # Infer extension from Content-Type
        content_type = res.headers.get("content-type", "")
        if "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"
        elif "gif" in content_type:
            ext = ".gif"
        else:
            ext = ".jpg"

        full_path = Path(file_path_no_ext + ext)
        full_path.write_bytes(res.content)
        return str(full_path)
    except Exception as e:
        print(f"   ⚠️ 图片下载失败: {url} — {e}")
        return None


def _pick_best_image(
    images: list[dict],
    lang_prefs: list[str | None] | None = None,
) -> dict | None:
    """Pick the best image by language preference and vote average.

    Args:
        images: TMDB image list [{file_path, iso_639_1, vote_average, ...}]
        lang_prefs: Language priority list, default ['ja', 'zh', None]

    Returns:
        Best matching image dict or None
    """
    if lang_prefs is None:
        lang_prefs = ["ja", "zh", None]
    if not images:
        return None

    for lang in lang_prefs:
        matches = [
            img for img in images
            if (img.get("iso_639_1") or None) == lang
        ]
        if matches:
            matches.sort(key=lambda img: img.get("vote_average") or 0, reverse=True)
            return matches[0]

    # No language match: pick highest rated
    sorted_imgs = sorted(
        images, key=lambda img: img.get("vote_average") or 0, reverse=True
    )
    return sorted_imgs[0]


async def download_episode_thumb(
    still_path: str,
    output_dir: str,
    base_filename: str,
) -> str | None:
    """Download episode still image as thumbnail.

    Args:
        still_path: TMDB still_path
        output_dir: Output directory
        base_filename: Base filename without extension

    Returns:
        Saved file path or None
    """
    if not still_path:
        return None
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    url = f"{TMDB_IMAGE_BASE}{still_path}"
    file_path = str(Path(output_dir) / f"{base_filename}-thumb")
    return await _download_image(url, file_path)


async def download_show_images(
    tv_id: int, output_dir: str
) -> dict[str, str | None]:
    """Download show-level images (backdrop, poster, landscape, logo).

    Language priority: Japanese(ja) > Chinese(zh) > no language.

    Args:
        tv_id: TMDB show ID
        output_dir: Output directory (the show name folder)

    Returns:
        dict with keys: backdrop, folder, landscape, logo → paths or None
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    try:
        res = await get_tv_images(tv_id, "ja,zh,null")
        images = res.json()
    except Exception as e:
        print(f"   ⚠️ 获取 TMDB 图片列表失败: {e}")
        return {}

    results = {}

    # Backdrop — prefer no-language backdrop (generic background)
    backdrops = images.get("backdrops", [])
    no_lang_backdrop = next(
        (b for b in backdrops if not b.get("iso_639_1")), None
    ) or (backdrops[0] if backdrops else None)
    if no_lang_backdrop:
        results["backdrop"] = await _download_image(
            f"{TMDB_IMAGE_BASE}{no_lang_backdrop['file_path']}",
            str(Path(output_dir) / "backdrop"),
        )

    # Folder — use poster
    poster = _pick_best_image(images.get("posters", []))
    if poster:
        results["folder"] = await _download_image(
            f"{TMDB_IMAGE_BASE}{poster['file_path']}",
            str(Path(output_dir) / "folder"),
        )

    # Landscape — language-prioritized backdrop (ja > zh > null, best rated)
    landscape = _pick_best_image(images.get("backdrops", []))
    if landscape:
        results["landscape"] = await _download_image(
            f"{TMDB_IMAGE_BASE}{landscape['file_path']}",
            str(Path(output_dir) / "landscape"),
        )

    # Logo — Japanese preferred
    logo = _pick_best_image(images.get("logos", []))
    if logo:
        results["logo"] = await _download_image(
            f"{TMDB_IMAGE_BASE}{logo['file_path']}",
            str(Path(output_dir) / "logo"),
        )
    else:
        print("   ℹ️ TMDB 无 logo 图片")

    return results


async def download_season_poster(
    bgm_subject: dict,
    output_dir: str,
    season_number: int,
) -> str | None:
    """Download season poster from Bangumi subject's cover image.

    Args:
        bgm_subject: Full Bangumi subject data (with images field)
        output_dir: Output directory (Season X folder)
        season_number: Season number

    Returns:
        Saved file path or None
    """
    if not bgm_subject.get("images"):
        return None
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Bangumi images: large > common > medium
    imgs = bgm_subject["images"]
    url = imgs.get("large") or imgs.get("common") or imgs.get("medium")
    if not url:
        return None

    season_str = f"{season_number:02d}"
    file_path = str(Path(output_dir) / f"season{season_str}-poster")
    return await _download_image(url, file_path)
