"""Fetch upstream mapping documents without writing local files."""

from ..utils.http_client import http_client_manager

BANGUMI_DATA_URL = "https://unpkg.com/bangumi-data@0.3/dist/data.json"
KOMETA_ANIME_IDS_URL = "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/master/anime_ids.json"


async def fetch_document(url: str):
    response = await http_client_manager.get_client().get(url, timeout=40)
    response.raise_for_status()
    return response.json()
