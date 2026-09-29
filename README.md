# My Anime Manager

My Anime Manager 是面向 Jellyfin 的番剧管理网页。它可解析 `.torrent`、匹配 TMDB/Bangumi/TVDB 元数据、管理 qBittorrent 下载、生成 NFO 与图片，并通过 RSS 自动追番和采集资源。

## 功能

- 上传种子，预览文件与剧集匹配，手动调整集数、下载选择和外挂字幕。
- 订阅字幕组 RSS，设置主/备 feed、标签和排除词，记录下载历史。
- 在下载前生成节目、季、剧集或电影的 NFO 与图片；下载完成后整理硬链接和字幕。
- 从资源来源采集并识别番剧资源，使用 SQLite 保存资源与种子关系。
- 在设置页配置 API Key、qBittorrent、路径、RSS 与资源轮询间隔。

## 项目结构

```text
backend/                 FastAPI 应用、外部 API 客户端、业务服务、SQLite 与 JSON 数据
frontend/                React 19 + TypeScript + Vite 前端
scripts/                 版本、映射下载和调试脚本
tests/                   后端回归测试
Dockerfile               前端构建 + Python 运行镜像
docker-compose.yml       本地构建及持久化挂载
```

后端 Python 包名是 `backend`。`backend/api/` 定义 API，`backend/services/` 处理下载、元数据和资源采集，`backend/db/` 管理 SQLite，`backend/data/` 管理订阅、历史和映射数据。设置由 `backend/config.py` 定义，并通过 `GET/PATCH /api/settings` 读写。

## Docker 安装

需要 Docker Compose、可访问的 qBittorrent Web UI，以及自己的 [TMDB API Key](https://www.themoviedb.org/settings/api)。若使用 TVDB 或 DeepSeek，请另行准备对应的密钥。RSS 功能还需访问 Bangumi 与蜜柑计划。

此仓库是私有仓库，先用有权限的 GitHub 账号克隆，再在本地构建镜像：

```bash
git clone git@github.com:Thuninisan/my-anime-manager.git
cd my-anime-manager
docker compose up -d --build
```

启动前编辑 `docker-compose.yml`：填入 qBittorrent 地址与密码、TMDB 密钥，并将种子目录和下载目录的宿主机占位路径改为实际路径。不使用代理时保持 `PROXY_HOST` 为空。`./mam-data:/app/data` 用于持久化设置、订阅、历史、数据库和日志。

应用与 qBittorrent 必须能访问同一份下载文件；跨容器时，建议两边使用相同的容器内路径，例如 `/downloads`。硬链接要求下载目录和目标媒体目录处于同一文件系统。启动后访问 `http://你的服务器地址:8000`，排查启动问题可运行 `docker compose logs -f my-anime-manager`。

Docker 镜像在构建时包含后端和已编译前端，运行中的容器无需连接 GitHub。更新时在宿主机运行：

```bash
git pull
docker compose up -d --build
```

## 本地开发

```bash
python -m pip install -e .
cd frontend && npm ci && npm run build && cd ..
python run.py
```

也可直接运行 `uvicorn backend.api:app --host 0.0.0.0 --port 8000`。前端开发服务器在 `frontend/` 目录执行 `npm run dev`。后端测试可运行 `python -m unittest discover -s tests -p 'test_*.py'`；安装 pytest 后可运行 `python -m pytest tests`。

## 设置与数据

首次打开网页后，在设置页填写 TMDB API Key、qBittorrent 连接信息、下载及整理路径。路径应填写**应用容器内**看到的路径。TVDB 和 DeepSeek 密钥是可选项。设置保存到 `MAM_DATA_DIR/settings.json`（Docker 中为 `/app/data/settings.json`）；环境变量仅在设置文件不存在的首次启动时初始化同名设置，之后以网页保存值为准。敏感字段在 API 中显示为 `***`。

RSS 排除词、RSS 下载轮询间隔和资源采集轮询间隔也保存在同一设置文件。旧版 `rss_settings.json` 的排除词会自动迁入。订阅和下载历史保存在数据目录的 JSON 文件，资源与种子关系保存在 `mam.sqlite3`。

如果启用 `WATCH_DIR=/torrents`，应用会扫描该目录下的 `.torrent` 文件。处理成功后文件会删除，失败时移到 `failed/`。RSS 下载器的开关是运行状态；重启后需在设置页重新开启，轮询间隔仍会保留。

## 日志与版本

日志输出到终端和 `MAM_DATA_DIR/logs/YYYY-MM-DD.log`。`TZ` 控制日期时区；`MAM_LOG_RETENTION_DAYS`、`MAM_LOG_DIR`、`MAM_LOG_LEVEL` 可调整保留期、位置与级别。网页的 Updates 页显示已安装版本；Docker 更新通过重建镜像完成。

版本由 `python scripts/bump_version.py X.Y.Z` 同步到后端、Python 包、前端和 Docker 镜像标签。项目在 `pyproject.toml` 中声明 ISC 许可。
