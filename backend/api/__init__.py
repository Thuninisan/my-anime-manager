"""My Anime Manager API package.

The FastAPI application is assembled in ``app.py`` and re-exported here so
``uvicorn backend.api:app`` keeps working.
"""

from .app import app

__all__ = ["app"]
