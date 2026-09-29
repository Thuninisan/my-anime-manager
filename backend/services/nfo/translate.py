"""Translation service — Japanese → Chinese via DeepSeek.

Used by the episode-plot fallback chain when neither TMDB nor TVDB
have a Chinese plot available.

Every translation result is verified before it is returned: an output
that still looks like the original Japanese (an echo of the input, or
high kana density) is logged and retried with a stricter system
prompt.  Only verified Chinese translations are cached.
"""

import asyncio
import difflib
import logging
import unicodedata

from ...clients.deepseek import chat

logger = logging.getLogger(__name__)

# Simple in-memory cache keyed by original Japanese text.
# Only verified Chinese translations are stored here — never echoes.
_translation_cache: dict[str, str] = {}

# Titles have a different prompt and include show context; keep their cache separate.
_title_translation_cache: dict[tuple[str, str], str] = {}


async def resolve_episode_title(
    bangumi_name_cn: str, bangumi_name: str, tmdb_name: str,
    *, show_name: str = "", context: str = "episode.nfo",
    selected_source: list[str] | None = None,
) -> str:
    """Prefer Bangumi Chinese, then translated original, then TMDB verbatim.

    Translation failures are not cached. Bound title translation latency so
    an unavailable server does not prevent NFO generation and downloading.
    """
    if bangumi_name_cn.strip():
        if selected_source is not None:
            selected_source.append("Bangumi 中文")
        logger.info("NFO [%s 标题] Bangumi 中文：命中", context)
        return bangumi_name_cn.strip()
    logger.info("NFO [%s 标题] Bangumi 中文：无标题", context)
    source = bangumi_name.strip()
    fallback = tmdb_name.strip()
    if not source:
        if selected_source is not None:
            selected_source.append("TMDB" if fallback else "空标题")
        logger.info("NFO [%s 标题] Bangumi 原名：无标题；最终使用：%s", context,
                    "TMDB" if fallback else "空标题")
        return fallback
    key = (show_name, source)
    if key in _title_translation_cache:
        if selected_source is not None:
            selected_source.append("Bangumi 译文（缓存）")
        logger.info("NFO [%s 标题] Bangumi 原名 → DeepSeek：命中翻译缓存", context)
        return _title_translation_cache[key]
    logger.info("NFO [%s 标题] Bangumi 原名：尝试 DeepSeek 翻译", context)
    try:
        result = await asyncio.wait_for(chat(
            prompt=f"Anime: {show_name}\nEpisode title: {source}",
            system=(
                "Translate the Japanese anime episode title into Simplified Chinese. "
                "Use the anime name only as context. Preserve the title's meaning, "
                "tone and punctuation; do not expand it into a synopsis. "
                "Translate or transliterate Japanese kana into Chinese. "
                "Return ONLY the translated title on one line, without labels, "
                "explanations or surrounding quotation marks."
            ),
            temperature=0.2,
            max_tokens=512,
        ), timeout=20.0)
        title = result.strip()
        # Short titles can legitimately retain Latin names or unchanged Han text.
        if (not title or _kana_chars(title) or "\n" in title or "\r" in title
                or not any("\u3400" <= ch <= "\u9fff" for ch in title)):
            logger.warning("Invalid episode title translation; using TMDB title")
            if selected_source is not None:
                selected_source.append("TMDB" if fallback else "空标题")
            logger.info("NFO [%s 标题] DeepSeek：译文无效；最终使用：%s", context,
                        "TMDB" if fallback else "空标题")
            return fallback
    except Exception:
        logger.warning("Episode title translation failed; using TMDB title", exc_info=True)
        if selected_source is not None:
            selected_source.append("TMDB" if fallback else "空标题")
        logger.info("NFO [%s 标题] DeepSeek：翻译失败；最终使用：%s", context,
                    "TMDB" if fallback else "空标题")
        return fallback
    _title_translation_cache[key] = title
    if selected_source is not None:
        selected_source.append("Bangumi 译文")
    logger.info("NFO [%s 标题] DeepSeek：翻译成功；最终使用：Bangumi 译文", context)
    return title

ANIME_TRANSLATION_SYSTEM = (
    "You are a professional anime subtitle translator. "
    "Translate the following Japanese anime episode synopsis into "
    "Simplified Chinese (zh-CN).\n\n"
    "Rules:\n"
    "- Preserve character names and proper nouns as-is (katakana → "
    "Chinese transliteration; never keep Japanese kana)\n"
    "- Keep anime-specific terminology accurate\n"
    "- Output ONLY the Chinese translation — no explanations, no "
    "furigana, no romanized readings"
)

# Stricter prompt used on retries after a failed verification pass.
# The "input is already Chinese" escape hatch is deliberately absent —
# the caller decides whether translation is needed, never the model.
ANIME_TRANSLATION_RETRY_SYSTEM = (
    "You are a translation engine. "
    "Translate the following synopsis into Simplified "
    "Chinese (zh-CN).\n\n"
    "Rules:\n"
    "- Output ONLY the Chinese translation\n"
    "- Never output the original Japanese text\n"
    "- No explanations, no furigana, no romanized readings\n"
    "- Translate or transliterate all names into Chinese; no Japanese kana"
)

MAX_ATTEMPTS = 3

# An output sharing more than this fraction of the input is treated as
# an echo of the original Japanese text, not a translation.
_ECHO_SIMILARITY = 0.85

def _kana_chars(text: str) -> int:
    """Count hiragana / katakana characters.

    Covers U+3040–U+30FF (hiragana + katakana) and U+FF66–U+FF9F
    (halfwidth katakana).  Japanese text always contains kana
    (particles, okurigana, verb endings) while Chinese never does.
    """
    return sum(
        1 for ch in text
        if "぀" <= ch <= "ヿ" or "ｦ" <= ch <= "ﾟ"
    )


def is_chinese_plot(text: str) -> bool:
    """Conservative script check, not a language detector.

    Used for metadata explicitly requested in Chinese. Han-only Japanese
    cannot be distinguished here, so unlabelled Bangumi text still goes
    through translation even if it passes this check.
    """
    text = unicodedata.normalize("NFKC", text)
    letters = [ch for ch in text if ch.isalpha()]
    han = sum("\u3400" <= ch <= "\u9fff" for ch in letters)
    return bool(letters) and not _kana_chars(text) and han / len(letters) >= 0.5


def _looks_untranslated(input_text: str, output_text: str) -> bool:
    if not is_chinese_plot(output_text):
        return True
    # Chinese input may legitimately be returned unchanged. Kana in the
    # source provides independent evidence that an echo is Japanese.
    return bool(_kana_chars(input_text)) and difflib.SequenceMatcher(
        None, input_text, output_text, autojunk=False,
    ).ratio() > _ECHO_SIMILARITY


async def translate_ja_to_zh(text: str) -> str:
    """Translate an unlabelled synopsis into Chinese.

    Return empty on exhausted retries; never publish the foreign source as
    a successful translation. Only accepted results enter the cache.
    """
    text = text.strip()
    if not text:
        return ""

    if text in _translation_cache:
        return _translation_cache[text]

    for attempt in range(1, MAX_ATTEMPTS + 1):
        system = (
            ANIME_TRANSLATION_SYSTEM
            if attempt == 1
            else ANIME_TRANSLATION_RETRY_SYSTEM
        )
        try:
            result = await chat(
                prompt=text,
                system=system,
                temperature=0.2,
                max_tokens=min(max(len(text) * 3, 600), 8192),
            )
            translated = result.strip()
        except Exception:
            logger.warning(
                "DeepSeek translation call failed (attempt %d/%d)",
                attempt, MAX_ATTEMPTS, exc_info=True,
            )
            continue

        if not translated:
            logger.warning(
                "DeepSeek returned empty output (attempt %d/%d) — retrying",
                attempt, MAX_ATTEMPTS,
            )
            continue

        if _looks_untranslated(text, translated):
            logger.warning(
                "DeepSeek output still looks Japanese on attempt %d/%d "
                "(%d input chars) — retrying with stricter prompt",
                attempt, MAX_ATTEMPTS, len(text),
            )
            continue

        # Verified Chinese translation — cache and return.
        _translation_cache[text] = translated
        logger.info(
            "DeepSeek translated episode plot (%d → %d chars)",
            len(text), len(translated),
        )
        return translated

    # Leave failures uncached so regeneration can retry.
    logger.error(
        "DeepSeek translation failed after %d attempts — "
        "no Chinese plot available",
        MAX_ATTEMPTS,
    )
    return ""
