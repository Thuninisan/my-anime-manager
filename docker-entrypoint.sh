#!/bin/sh
set -e

REPO_URL="${MAM_REPO_URL:-https://github.com/karlkono/my-anime-manager.git}"
SOURCE_DIR="${MAM_SOURCE_DIR:-/app/source}"
BRANCH="${MAM_BRANCH:-main}"
DATA_DIR="${MAM_DATA_DIR:-/app/data}"

mkdir -p "$DATA_DIR"

echo "[entrypoint] === My Anime Manager ==="
echo "[entrypoint] Repo:    $REPO_URL"
echo "[entrypoint] Branch:  $BRANCH"
echo "[entrypoint] Source:  $SOURCE_DIR"
echo "[entrypoint] Data:    $DATA_DIR"

while true; do
    echo "[entrypoint] --- Cycle start ---"

    # ── 0. Configure proxy (if set) ──
    if [ -n "$PROXY_HOST" ] && [ -n "$PROXY_PORT" ]; then
        PROXY_URL="http://${PROXY_HOST}:${PROXY_PORT}"
        export HTTP_PROXY="$PROXY_URL"
        export HTTPS_PROXY="$PROXY_URL"
        export http_proxy="$PROXY_URL"
        export https_proxy="$PROXY_URL"
        export NO_PROXY="localhost,127.0.0.1,.local"
        export no_proxy="$NO_PROXY"

        git config --global http.proxy "$PROXY_URL" 2>/dev/null || true
        git config --global https.proxy "$PROXY_URL" 2>/dev/null || true

        npm config set proxy "$PROXY_URL" 2>/dev/null || true
        npm config set https-proxy "$PROXY_URL" 2>/dev/null || true

        echo "[entrypoint] Proxy configured: $PROXY_URL"
    else
        unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy 2>/dev/null || true
        git config --global --unset http.proxy 2>/dev/null || true
        git config --global --unset https.proxy 2>/dev/null || true
        npm config delete proxy 2>/dev/null || true
        npm config delete https-proxy 2>/dev/null || true
    fi

    # ── 1. Clone or pull source ──
    if [ -d "$SOURCE_DIR/.git" ]; then
        echo "[entrypoint] Pulling latest source..."
        cd "$SOURCE_DIR"
        git fetch origin "$BRANCH" --depth=1 2>&1 || echo "[entrypoint] WARNING: git fetch failed, using cached source"
        git reset --hard "origin/$BRANCH" 2>&1 || echo "[entrypoint] WARNING: git reset failed, using cached source"
    else
        echo "[entrypoint] Cloning source from $REPO_URL ($BRANCH)..."
        git clone --depth=1 --branch "$BRANCH" "$REPO_URL" "$SOURCE_DIR" 2>&1 || {
            echo "[entrypoint] ERROR: git clone failed"
            exit 1
        }
        cd "$SOURCE_DIR"
    fi

    # ── 2. Install Python dependencies (editable mode) ──
    echo "[entrypoint] Installing Python dependencies..."
    pip install --no-cache-dir -e "$SOURCE_DIR" --quiet 2>&1

    # ── 3. Build frontend ──
    echo "[entrypoint] Building frontend..."
    cd "$SOURCE_DIR/frontend"
    npm ci --prefer-offline --quiet 2>/dev/null || npm install --quiet 2>&1
    npm run build 2>&1
    echo "[entrypoint] Frontend build complete."

    # ── 4. Symlink frontend dist so FastAPI finds it ──
    mkdir -p /app/frontend
    ln -sfn "$SOURCE_DIR/frontend/dist" /app/frontend/dist
    echo "[entrypoint] Frontend dist linked: /app/frontend/dist -> $SOURCE_DIR/frontend/dist"

    # ── 5. Start application ──
    echo "[entrypoint] Starting uvicorn..."
    cd "$SOURCE_DIR"
    uvicorn my_anime_manager.api:app --host 0.0.0.0 --port 8000

    EXIT_CODE=$?
    echo "[entrypoint] uvicorn exited with code $EXIT_CODE"

    if [ "$EXIT_CODE" = "42" ]; then
        echo "[entrypoint] Update requested — restarting with latest code..."
        continue
    fi

    echo "[entrypoint] Shutting down container."
    exit $EXIT_CODE
done
