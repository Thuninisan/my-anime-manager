"""Translation service — Japanese → Chinese via DeepSeek.

Used by the episode-plot fallback chain when neither TMDB nor TVDB
have a Chinese plot available.

Every translation result is verified before it is returned: an output
that still looks like the original Japanese (an echo of the input, or
high kana density) is logged and retried with a stricter system
prompt.  Only verified Chinese translations are cached.
"""

import difflib
import logging

from ...clients.deepseek import chat

logger = logging.getLogger(__name__)

# Simple in-memory cache keyed by original Japanese text.
# Only verified Chinese translations are stored here — never echoes.
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
    "furigana, no romanized readings"
)

# Stricter prompt used on retries after a failed verification pass.
# The "input is already Chinese" escape hatch is deliberately absent —
# the caller decides whether translation is needed, never the model.
ANIME_TRANSLATION_RETRY_SYSTEM = (
    "You are a translation engine. "
    "The following text is JAPANESE — translate it into Simplified "
    "Chinese (zh-CN).\n\n"
    "Rules:\n"
    "- Output ONLY the Chinese translation\n"
    "- Never output the original Japanese text\n"
    "- No explanations, no furigana, no romanized readings\n"
    "- Character names may use their official Chinese transliteration"
)

MAX_ATTEMPTS = 3

# An output sharing more than this fraction of the input is treated as
# an echo of the original Japanese text, not a translation.
_ECHO_SIMILARITY = 0.85

# A real zh-CN translation only keeps a handful of katakana names —
# more kana than this fraction means the text is still Japanese.
_KANA_RATIO = 0.2


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


def _looks_untranslated(input_text: str, output_text: str) -> bool:
    """True when the output still looks like the original Japanese text.

    Two independent signals:
      1. Echo — the output is (nearly) identical to the input, e.g. the
         model applied a stale "return unchanged" rule on kanji-heavy
         Japanese that it misjudged as Chinese.
      2. Kana density — a genuinely translated text drops almost all
         kana, so a kana-heavy output is still Japanese.
    """
    if not output_text:
        return False
    ratio = difflib.SequenceMatcher(
        None, input_text, output_text, autojunk=False,
    ).ratio()
    if ratio > _ECHO_SIMILARITY:
        return True
    if _kana_chars(output_text) / len(output_text) > _KANA_RATIO:
        return True
    return False


async def translate_ja_to_zh(text: str) -> str:
    """Translate a Japanese episode description to Chinese via DeepSeek.

    Args:
        text: Japanese text to translate.

    Returns:
        Chinese translation; ``""`` for empty input.  When every
        attempt fails (API down, empty output, or output that still
        looks Japanese) the original Japanese *text* is returned as a
        last resort — a Japanese plot is better than none.

    Failed translations (API error, empty output, or output that still
    looks Japanese) are logged and retried up to ``MAX_ATTEMPTS`` times
    with an escalating prompt, so a transient model mistake does not
    leave a Japanese plot in the NFO.
    """
    text = text.strip()
    if not text:
        return ""

    # No kana → already Chinese (Japanese always uses kana) — no API call.
    if _kana_chars(text) == 0:
        return text

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
                max_tokens=min(len(text) * 3, 600),
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

    # Last resort: keep the original Japanese text — not cached, so a
    # later regen can retry the translation.
    logger.error(
        "DeepSeek translation failed after %d attempts — "
        "falling back to original Japanese text",
        MAX_ATTEMPTS,
    )
    return text
