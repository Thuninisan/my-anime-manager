"""Translation service — Japanese → Chinese via DeepSeek.

Used by the episode-plot fallback chain when neither TMDB nor TVDB
have a Chinese plot available.
"""

import logging

from ...clients.deepseek import chat

logger = logging.getLogger(__name__)

# Simple in-memory cache keyed by original Japanese text.
# Avoids translating the same description multiple times within a session.
_translation_cache: dict[str, str] = {}

ANIME_TRANSLATION_SYSTEM = (
    "You are a professional anime subtitle translator. "
    "Translate the following Japanese anime episode synopsis into "
    "Simplified Chinese (zh-CN).\n\n"
    "Rules:\n"
    "- Preserve character names and proper nouns as-is (katakana → "
    "official Chinese transliteration if known, otherwise keep)\n"
    "- Keep anime-specific terminology accurate\n"
    "- Output ONLY the Chinese translation — no explanations, no "
    "furigana, no romanized readings\n"
    "- If the input is already mostly Chinese, return it unchanged"
)


async def translate_ja_to_zh(text: str) -> str:
    """Translate a Japanese episode description to Chinese via DeepSeek.

    Args:
        text: Japanese text to translate.

    Returns:
        Chinese translation, or ``""`` on failure / empty input.
    """
    text = text.strip()
    if not text:
        return ""

    if text in _translation_cache:
        return _translation_cache[text]

    try:
        result = await chat(
            prompt=text,
            system=ANIME_TRANSLATION_SYSTEM,
            temperature=0.2,
            max_tokens=min(len(text) * 3, 600),
        )
        translated = result.strip()
    except Exception:
        logger.exception("DeepSeek translation failed")
        return ""

    if not translated:
        return ""

    _translation_cache[text] = translated
    logger.info(
        "DeepSeek translated episode plot (%d → %d chars)",
        len(text), len(translated),
    )
    return translated
