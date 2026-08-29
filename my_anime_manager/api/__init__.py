"""My Anime Manager API package.

The FastAPI application is assembled in ``app.py`` and re-exported here so
``uvicorn my_anime_manager.api:app`` keeps working.
"""

from .app import app

__all__ = ["app"]
