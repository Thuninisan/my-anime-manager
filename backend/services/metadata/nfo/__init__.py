"""Jellyfin NFO writers, image assets and batch generation.

路径模板规则归 media.paths；qBittorrent 重命名编排归 downloads.episode_submission。
"""

from .generator import batch_nfo_generator
from .images import (
    download_episode_thumb, download_season_poster, download_show_images,
    download_tvdb_episode_thumb, get_subscription_poster_url,
)
from .nfo_xml import (
    generate_episode_nfo, generate_season_nfo, generate_tv_show_nfo, generate_movie_nfo,
)
