FROM python:3.12-alpine

# Install git + Node.js for hot-update capability (git clone/pull + frontend rebuild)
RUN apk add --no-cache git nodejs npm

ENV PYTHONIOENCODING=utf-8
ENV MAM_DATA_DIR=/app/data
ENV MAM_SOURCE_DIR=/app/source
ENV MAM_REPO_URL=https://github.com/Thuninisan/my-anime-manager.git
ENV MAM_BRANCH=main
WORKDIR /app

# ── Pre-install Python dependencies (layer cache) ──
# Install the package to pull in all deps, then uninstall so the
# entrypoint script can pip install -e from the git-cloned source.
COPY pyproject.toml ./
COPY my_anime_manager/__init__.py ./my_anime_manager/__init__.py
RUN pip install --no-cache-dir . && pip uninstall -y my-anime-manager

# ── Entrypoint script ──
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh

# ── Ensure writable directories exist ──
RUN mkdir -p /app/data /app/source /app/frontend

EXPOSE 8000
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD []
