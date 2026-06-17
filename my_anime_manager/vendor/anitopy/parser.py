# -*- coding: utf-8 -*-

from __future__ import unicode_literals, absolute_import

import re

from . import parser_helper, parser_number
from .element import ElementCategory, Elements
from .keyword import keyword_manager
from .token import TokenCategory, TokenFlags, Tokens

# ========== Chinese subtitle/language decomposition ==========

_CHINESE_LANG_MAP = {
    "简": "简体中文", "简体": "简体中文",
    "繁": "繁体中文", "繁体": "繁体中文",
    "日": "日语",
    "英": "英语",
    "韩": "韩语",
}

_CHINESE_SUB_PATTERNS = [
    (re.compile(r"内封"), "内封字幕"),
    (re.compile(r"内嵌"), "内嵌字幕"),
    (re.compile(r"外[挂]?"), "外挂字幕"),
    (re.compile(r"字幕"), None),  # generic subtitle marker
]


def _parse_chinese_subtitle_language(word: str) -> bool:
    """Try to decompose a Chinese subtitle/language compound token.

    e.g. "简繁日内封字幕" → Language: ["简体中文","繁体中文","日语"],
                         Subtitles: ["内封字幕"]

    Returns True if the word was successfully parsed.
    """
    # Check if the word contains Chinese subtitle/language patterns
    has_lang = any(k in word for k in _CHINESE_LANG_MAP)
    has_sub = any(pat.search(word) for pat, _ in _CHINESE_SUB_PATTERNS)

    if not has_lang and not has_sub:
        return False

    # Decompose: extract language abbreviations
    for abbr, full in _CHINESE_LANG_MAP.items():
        if abbr in word:
            Elements.insert(ElementCategory.LANGUAGE, full)

    # Decompose: extract subtitle types
    for pattern, label in _CHINESE_SUB_PATTERNS:
        if pattern.search(word):
            if label:
                Elements.insert(ElementCategory.SUBTITLES, label)

    return has_lang or has_sub


class Parser:
    def __init__(self, options):
        self.options = options

    def parse(self):
        self.search_for_keywords()

        self.search_for_isolated_numbers()

        if self.options['parse_episode_number']:
            self.search_for_episode_number()

        self.search_for_anime_title()

        if self.options['parse_release_group'] and \
                not Elements.contains(ElementCategory.RELEASE_GROUP):
            self.search_for_release_group()

        if self.options['parse_episode_title'] and \
                Elements.contains(ElementCategory.EPISODE_NUMBER):
            self.search_for_episode_title()

        self.validate_elements()

        return not Elements.empty()

    def search_for_keywords(self):
        for token in Tokens.get_list(TokenFlags.UNKNOWN):
            word = token.content
            word = word.strip(' -')

            if not word:
                continue
            # Don't bother if the word is a number that cannot be CRC
            if len(word) != 8 and word.isdigit():
                continue

            category = ElementCategory.UNKNOWN
            keyword = keyword_manager.find(keyword_manager.normalize(word))
            decomposed = False  # True if Chinese sub/lang decomposition handled it
            if keyword:
                category = keyword.category
                if not self.options['parse_release_group'] and \
                        category == ElementCategory.RELEASE_GROUP:
                    continue
                if not ElementCategory.is_searchable(category) or \
                        not keyword.options.searchable:
                    continue
                if ElementCategory.is_singular(category) and \
                        Elements.contains(category):
                    continue

                if category == ElementCategory.ANIME_SEASON_PREFIX:
                    parser_helper.check_anime_season_keyword(token)
                    continue
                elif category == ElementCategory.EPISODE_PREFIX:
                    if keyword.options.valid:
                        parser_number.check_extent_keyword(
                            ElementCategory.EPISODE_NUMBER, token)
                    continue
                elif category == ElementCategory.RELEASE_VERSION:
                    word = word[1:]  # number without "v"
                elif category == ElementCategory.VOLUME_PREFIX:
                    parser_number.check_extent_keyword(
                        ElementCategory.VOLUME_NUMBER, token)
                    continue
            else:
                # Try Chinese subtitle/language decomposition first
                if _parse_chinese_subtitle_language(word):
                    category = ElementCategory.SUBTITLES
                    decomposed = True
                elif not Elements.contains(ElementCategory.FILE_CHECKSUM) and \
                        parser_helper.is_crc32(word):
                    category = ElementCategory.FILE_CHECKSUM
                elif not Elements.contains(ElementCategory.VIDEO_RESOLUTION) \
                        and parser_helper.is_resolution(word):
                    category = ElementCategory.VIDEO_RESOLUTION

            if category != ElementCategory.UNKNOWN:
                # For Chinese subtitle decomposition, individual parts were
                # already inserted; skip inserting the raw word.
                if not decomposed:
                    Elements.insert(category, word)
                if keyword is None or keyword.options.identifiable:
                    token.category = TokenCategory.IDENTIFIER

    def search_for_isolated_numbers(self):
        for token in Tokens.get_list(TokenFlags.UNKNOWN):
            if not token.content.isdigit() or \
                    not parser_helper.is_token_isolated(token):
                continue

            number = int(token.content)

            # Anime year
            if number >= parser_number.ANIME_YEAR_MIN and \
                    number <= parser_number.ANIME_YEAR_MAX:
                if not Elements.contains(ElementCategory.ANIME_YEAR):
                    Elements.insert(ElementCategory.ANIME_YEAR, token.content)
                    token.category = TokenCategory.IDENTIFIER
                    continue

            # Video resolution
            if number == 480 or number == 720 or number == 1080:
                # If these numbers are isolated, it's more likely for them to
                # be the video resolution rather than the episode number. Some
                # fansub groups use these without the "p" suffix.
                if not Elements.contains(ElementCategory.VIDEO_RESOLUTION):
                    Elements.insert(
                        ElementCategory.VIDEO_RESOLUTION, token.content)
                    token.category = TokenCategory.IDENTIFIER
                    continue

    def search_for_episode_number(self):
        # List all unknown tokens that contain a number (digit or Chinese numeral)
        tokens = [token for token in Tokens.get_list(TokenFlags.UNKNOWN)
                  if parser_helper.find_number_in_string(token.content) is not
                  None
                  or token.content.startswith('第')]

        if not tokens:
            return

        Elements.set_check_alt_number(
            Elements.contains(ElementCategory.EPISODE_NUMBER))

        # If a token matches a known episode pattern, it has to be the episode
        # number
        if parser_number.search_for_episode_patterns(tokens):
            return

        if Elements.contains(ElementCategory.EPISODE_NUMBER):
            return  # We have previously found an episode number via keywords

        # From now on, we're only interested in numeric tokens
        tokens = [token for token in tokens if token.content.isdigit()]

        if not tokens:
            return

        # e.g. "01 (176)", "29 (04)"
        if parser_number.search_for_equivalent_numbers(tokens):
            return

        # e.g. " - 08"
        if parser_number.search_for_separated_numbers(tokens):
            return

        # e.g. "[12]", "(2006)"
        if parser_number.search_for_isolated_numbers(tokens):
            return

        # Consider using the last number as a last resort
        parser_number.search_for_last_number(tokens)

    @staticmethod
    def _is_metadata_token(word: str) -> bool:
        """Check if a token looks like a metadata label rather than a title.

        e.g. '04月新番', '01月新番', '10月新番' — seasonal broadcast labels.
        """
        if not word:
            return False
        # Seasonal broadcast prefix: digits + 月新番
        if re.match(r'^\d{1,2}月新番$', word):
            return True
        # Pure decorative symbols
        if re.match(r'^[★☆♪♪♫※]+$', word):
            return True
        return False

    def search_for_anime_title(self):
        enclosed_title = False

        # Find the first non-enclosed unknown token
        token_begin = Tokens.find(TokenFlags.NOT_ENCLOSED | TokenFlags.UNKNOWN)

        # Skip metadata-like tokens (e.g. 04月新番) so the real title can
        # be found, either as the next non-enclosed token or via the
        # enclosed-title fallback.
        while token_begin is not None and self._is_metadata_token(token_begin.content):
            token_begin.category = TokenCategory.IDENTIFIER
            token_begin = Tokens.find(TokenFlags.NOT_ENCLOSED | TokenFlags.UNKNOWN)

        # If that doesn't work, find the first unknown token in the second
        # enclosed group, assuming that the first one is the release group
        if token_begin is None:
            enclosed_title = True
            token_begin = Tokens.get(0)
            skipped_previous_group = False
            while token_begin is not None:
                token_begin = Tokens.find_next(token_begin, TokenFlags.UNKNOWN)
                if token_begin is None:
                    break
                # Skip groups that are just numbers (episode/volume)
                if not token_begin.content.isdigit():
                    if skipped_previous_group:
                        break  # Found it
                # Get the first unknown token of the next group
                token_begin = Tokens.find_next(token_begin, TokenFlags.BRACKET)
                skipped_previous_group = True

        if token_begin is None:
            return

        # Continue until an identifier (or a bracket, if the title is enclosed)
        # is found
        token_end = Tokens.find_next(
            token_begin, TokenFlags.IDENTIFIER | (
                TokenFlags.BRACKET if enclosed_title else TokenFlags.NONE
            ))

        # If within the interval there's an open bracket without its matching
        # pair, move the upper endpoint back to the bracket
        if not enclosed_title:
            last_bracket = token_end
            bracket_open = False
            for token in Tokens.get_list(TokenFlags.BRACKET, begin=token_begin,
                                         end=token_end):
                last_bracket = token
                bracket_open = not bracket_open
            if bracket_open:
                token_end = last_bracket

        # If the interval ends with an enclosed group (e.g. "Anime Title
        # [Fansub]"), move the upper endpoint back to the beginning of the
        # group. We ignore parentheses in order to keep certain groups (e.g.
        # "(TV)") intact.
        if not enclosed_title:
            token = Tokens.find_previous(token_end, TokenFlags.NOT_DELIMITER)
            while token.category == TokenCategory.BRACKET and \
                    token.content != ')':
                token = Tokens.find_previous(token, TokenFlags.BRACKET)
                if token is not None:
                    token_end = token
                    token = Tokens.find_previous(
                        token_end, TokenFlags.NOT_DELIMITER)

        # Token end is a bracket, so we get the previous token to be included
        # in the element
        token_end = Tokens.find_previous(token_end, TokenFlags.VALID)
        parser_helper.build_element(ElementCategory.ANIME_TITLE, token_begin,
                                    token_end, keep_delimiters=False)

    def search_for_release_group(self):
        token_end = None
        while True:
            # Find the first enclosed unknown token
            if token_end:
                token_begin = Tokens.find_next(
                    token_end, TokenFlags.ENCLOSED | TokenFlags.UNKNOWN)
            else:
                token_begin = Tokens.find(
                    TokenFlags.ENCLOSED | TokenFlags.UNKNOWN)
            if token_begin is None:
                return

            # Continue until a bracket or identifier is found
            token_end = Tokens.find_next(
                token_begin, TokenFlags.BRACKET | TokenFlags.IDENTIFIER)
            if token_end is None:
                return
            if token_end.category != TokenCategory.BRACKET:
                continue

            # Ignore if it's not the first non-delimiter token in group
            previous_token = Tokens.find_previous(
                token_begin, TokenFlags.NOT_DELIMITER)
            if previous_token is not None and \
                    previous_token.category != TokenCategory.BRACKET:
                continue

            # Build release group, token end is a bracket, so we get the
            # previous token to be included in the element
            token_end = Tokens.find_previous(token_end, TokenFlags.VALID)
            parser_helper.build_element(
                ElementCategory.RELEASE_GROUP, token_begin, token_end,
                keep_delimiters=True)
            return

    def search_for_episode_title(self):
        token_end = None
        while True:
            # Find the first non-enclosed unknown token
            if token_end:
                token_begin = Tokens.find_next(
                    token_end, TokenFlags.NOT_ENCLOSED | TokenFlags.UNKNOWN)
            else:
                token_begin = Tokens.find(
                    TokenFlags.NOT_ENCLOSED | TokenFlags.UNKNOWN)
            if token_begin is None:
                return

            # Continue until a bracket or identifier is found
            token_end = Tokens.find_next(
                token_begin, TokenFlags.BRACKET | TokenFlags.IDENTIFIER)

            # Ignore if it's only a dash
            if Tokens.distance(token_begin, token_end) <= 2 and \
                    parser_helper.is_dash_character(token_begin.content):
                continue

            # If token end is a bracket, then we get the previous token to be
            # included in the element
            if token_end and token_end.category == TokenCategory.BRACKET:
                token_end = Tokens.find_previous(token_end, TokenFlags.VALID)
            # Build episode title
            parser_helper.build_element(
                ElementCategory.EPISODE_TITLE, token_begin, token_end,
                keep_delimiters=False)
            return

    def validate_elements(self):
        # Validate anime type and episode title
        if Elements.contains(ElementCategory.ANIME_TYPE) and \
                Elements.contains(ElementCategory.EPISODE_TITLE):
            # Here we check whether the episode title contains an anime type
            episode_title = Elements.get(ElementCategory.EPISODE_TITLE)[0]
            # Copy list because we may modify it
            anime_type_list = list(Elements.get(ElementCategory.ANIME_TYPE))
            for anime_type in anime_type_list:
                if anime_type == episode_title:
                    # Invalid episode title
                    Elements.erase(ElementCategory.EPISODE_TITLE)
                elif anime_type in episode_title:
                    norm_anime_type = keyword_manager.normalize(anime_type)
                    if keyword_manager.find(
                            norm_anime_type, ElementCategory.ANIME_TYPE):
                        Elements.remove(ElementCategory.ANIME_TYPE, anime_type)
                        continue
