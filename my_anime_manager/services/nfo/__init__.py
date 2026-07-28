"""NFO metadata sub-package — XML generation, image downloading, and orchestration.

Public API
----------
NFO XML generators:
  - :func:`.generate_episode_nfo`
  - :func:`.generate_tv_show_nfo`
  - :func:`.generate_season_nfo`

Image downloaders:
  - :func:`.download_episode_thumb`
  - :func:`.download_tvdb_episode_thumb`
  - :func:`.download_show_images`
  - :func:`.download_season_poster`
  - :func:`.get_subscription_poster_url`

Path & file generation:
  - :func:`.format_download_path`
  - :func:`.sanitize_path_name`
  - :func:`.write_episode_files`

Metadata orchestration:
  - :func:`.generate_metadata`
  - :func:`.batch_nfo_generator`
"""

from .generator import batch_nfo_generator, format_download_path, sanitize_path_name, write_episode_files
from .images import (
    download_episode_thumb,
    download_season_poster,
    download_show_images,
    download_tvdb_episode_thumb,
    get_subscription_poster_url,
)
from .metadata_builder import generate_metadata
from .nfo_xml import generate_episode_nfo, generate_season_nfo, generate_tv_show_nfo
