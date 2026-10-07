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

后端 Python 包名是 `backend`。`backend/api/` 定义 API，`backend/services/` 处理下载、元数据和资源采集，`backend/db/` 管理 SQLite，`backend/data/` 提供数据访问接口并管理仍使用 JSON 的历史和设置。设置由 `backend/config.py` 定义，并通过 `GET/PATCH /api/settings` 读写。

## Docker 安装

需要 Docker Compose、可访问的 qBittorrent Web UI，以及自己的 [TMDB API Key](https://www.themoviedb.org/settings/api)。若使用 TVDB 或 DeepSeek，请另行准备对应的密钥。RSS 功能还需访问 Bangumi 与蜜柑计划。

公开仓库可直接克隆。首次启动会在本地构建基础镜像，容器随后从本仓库拉取源码、安装后端依赖并构建前端：

```bash
git clone https://github.com/Thuninisan/my-anime-manager.git
cd my-anime-manager
docker compose up -d --build
```

启动前编辑 `docker-compose.yml`：填入 qBittorrent 地址与密码、TMDB 密钥，并将种子目录和下载目录的宿主机占位路径改为实际路径。不使用代理时保持 `PROXY_HOST` 为空。`./mam-data:/app/data` 用于持久化设置、订阅、历史、数据库和日志；`mam-source` 卷保留容器拉取的源码。

代理地址可填写纯 IP/域名（如 `192.168.1.2`），配合 `PROXY_PORT` 使用；也可填写 `http://192.168.1.2:7890` 或 `https://proxy.example:8443`，地址中指定的端口优先。IPv6 地址同样支持。Docker 中的 `127.0.0.1` 指向容器自身，连接宿主机代理时需填写容器可访问的宿主机地址。环境变量只在首次启动且 `settings.json` 不存在时初始化设置；已有设置请通过网页 Settings 修改。

应用与 qBittorrent 必须能访问同一份下载文件；跨容器时，建议两边使用相同的容器内路径，例如 `/downloads`。硬链接要求下载目录和目标媒体目录处于同一文件系统。启动后访问 `http://你的服务器地址:8000`，排查启动问题可运行 `docker compose logs -f my-anime-manager`。

容器启动时需要访问 GitHub、Python 包仓库和 npm。网页“设置 → Updates”可以检查远端提交并触发拉取、前端重建和应用重启。更新基础镜像时，在宿主机运行：

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

在 Settings → Torrent 的“ASS 字体处理”中开启 FontInAss，可在 Torrent 下载完成、外挂字幕复制到媒体目录后，自动上传本次复制的 ASS 并嵌入子集字体。默认服务地址为 `https://font.anibt.net`，请求超时为 180 秒；该功能默认关闭，仅处理 `.ass`，SRT 和其他格式仍按原流程复制。使用严格检查，只有完整成功且字幕事件数量、时间轴验证通过时才原位替换目标字幕，下载目录中的源字幕保持不变。

FontInAss 网络故障、缺失字体、缺字形或返回警告时保留目标字幕。Torrent 卡片显示处理进度、失败原因，并可重试失败字幕；失败不影响视频下载和整理结果。处理记录保存在现有 SQLite 数据库，应用重启后恢复未完成处理，跳过已完成文件。FontInAss 请求使用应用已有的代理设置。开启该功能会将 ASS 字幕内容发送到配置的服务。

首次打开网页后，在设置页填写 TMDB API Key、qBittorrent 连接信息、下载及整理路径。路径应填写**应用容器内**看到的路径。TVDB 和 DeepSeek 密钥是可选项。设置保存到 `MAM_DATA_DIR/settings.json`（Docker 中为 `/app/data/settings.json`）；环境变量仅在设置文件不存在的首次启动时初始化同名设置，之后以网页保存值为准。敏感字段在 API 中显示为 `***`。

RSS 排除词、RSS 下载轮询间隔和资源采集轮询间隔也保存在同一设置文件。旧版 `rss_settings.json` 的排除词会自动迁入 `settings.json`。订阅、下载历史、Bangumi 映射、资源与种子关系均保存在 `MAM_DATA_DIR/mam.sqlite3`。首次使用新版本时，旧 `subscriptions.json`、`download_history.json`、`torrents.json` 和随程序提供的 `bangumi_mikan_map.json` 会自动导入数据库，原文件不会删除；此后以数据库为准。降级到旧版本前，先停止应用，运行 `python scripts/export_legacy_json.py /path/to/export`，将导出的用户数据 JSON 复制到旧版的数据目录、映射文件复制到旧版的 `backend/data/` 目录。

如果启用 `WATCH_DIR=/torrents`，应用会扫描该目录下的 `.torrent` 文件。处理成功后文件会删除，失败时移到 `failed/`。RSS 下载器的开关是运行状态；重启后需在设置页重新开启，轮询间隔仍会保留。

## 日志与版本

日志输出到终端和 `MAM_DATA_DIR/logs/YYYY-MM-DD.log`。`TZ` 控制日期时区；`MAM_LOG_RETENTION_DAYS`、`MAM_LOG_DIR`、`MAM_LOG_LEVEL` 可调整保留期、位置与级别。网页的 Updates 页可在 Docker 部署中检查并应用源码更新。

版本由 `python scripts/bump_version.py X.Y.Z` 同步到后端、Python 包、前端和 Docker 镜像标签。项目在 `pyproject.toml` 中声明 ISC 许可。
