"""Short-lived remote metadata reuse for one subscription poll or NFO batch."""


class MetadataContext:
    def __init__(self):
        self.tmdb_details = {}
        self.tmdb_season_maps = {}
        self.tmdb_images = {}
        self.tvdb_series = {}
        self.bgm_episodes = {}
        self.bgm_subjects = {}
        self.show_assets_done = set()
        self.season_assets_done = set()

    async def get_tmdb_detail(self, tmdb_id: int, language: str = "") -> dict:
        from ...clients import tmdb as client
        key = (tmdb_id, language)
        if key not in self.tmdb_details:
            self.tmdb_details[key] = (await client.get_tv_detail(tmdb_id, language=language)).json()
        return self.tmdb_details[key]

    async def get_tmdb_season_map(self, tmdb_id: int, language: str) -> dict:
        from .. import tmdb as service
        key = (tmdb_id, language)
        if key not in self.tmdb_season_maps:
            detail = await self.get_tmdb_detail(tmdb_id)
            self.tmdb_season_maps[key] = await service.build_season_episode_map(
                tmdb_id, language=language, tv_detail=detail,
            )
        return self.tmdb_season_maps[key]

    async def get_bgm_episodes(self, bangumi_id: int) -> list[dict]:
        from ..enrich import _get_bangumi_episodes
        if bangumi_id not in self.bgm_episodes:
            self.bgm_episodes[bangumi_id] = await _get_bangumi_episodes(bangumi_id)
        return self.bgm_episodes[bangumi_id]

    async def get_bgm_subject(self, bangumi_id: int) -> dict:
        from ...clients import bangumi as client
        if bangumi_id not in self.bgm_subjects:
            self.bgm_subjects[bangumi_id] = await client.get_subject(bangumi_id)
        return self.bgm_subjects[bangumi_id]

    async def get_tvdb_series(self, tvdb_id: int, language: str = "jpn") -> dict:
        key = (tvdb_id, language)
        if key not in self.tvdb_series:
            if language == "jpn":
                from ..tvdb import fetch_tvdb_series_episodes
                self.tvdb_series[key] = await fetch_tvdb_series_episodes(tvdb_id)
            else:
                from ...clients import tvdb as client
                response = await client.get_series_episodes(tvdb_id, language=language)
                payload = response.json()
                self.tvdb_series[key] = payload.get("data", payload)
        return self.tvdb_series[key]
