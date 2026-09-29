"""RSS covers are served from the application's own persistent cache."""

import asyncio
from backend.services import rss_poster


def test_poster_cache_uses_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(rss_poster.data, "_USER_DATA_DIR", tmp_path)
    cached = tmp_path / "rss_posters" / "123.jpg"
    cached.parent.mkdir()
    cached.write_bytes(b"cached")

    async def unexpected_subject(_id):
        raise AssertionError("Cached cover should not call Bangumi")

    monkeypatch.setattr(rss_poster, "get_subject", unexpected_subject)
    assert asyncio.run(rss_poster.get_poster(123)) == cached


def test_poster_rejects_untrusted_url(monkeypatch, tmp_path):
    monkeypatch.setattr(rss_poster.data, "_USER_DATA_DIR", tmp_path)

    async def subject(_id):
        return {"images": {"common": "https://example.com/private.jpg"}}

    monkeypatch.setattr(rss_poster, "get_subject", subject)
    assert asyncio.run(rss_poster.get_poster(456)) is None
    assert not (tmp_path / "rss_posters").exists()


def test_poster_url_is_same_origin():
    assert rss_poster.poster_url(123) == "/api/rss/bangumi/123/poster"
    assert rss_poster._trusted_image_url("https://lain.bgm.tv/pic/cover.jpg")
    assert not rss_poster._trusted_image_url("https://lain.bgm.tv.evil.test/cover.jpg")
