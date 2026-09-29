# AGENTS.md

My Anime Manager 是 FastAPI + React 的番剧管理应用：匹配 TMDB/Bangumi/TVDB 元数据、管理 qBittorrent 下载、生成 Jellyfin NFO，并处理 RSS 订阅与资源采集。

## 目录与技术栈

- `backend/`：Python 3.11+ 包。`api/` 提供 FastAPI 路由；`clients/` 封装外部服务；`services/` 包含 RSS 下载、资源采集、torrent 处理和 NFO；`db/` 使用 SQLAlchemy + SQLite；`data/` 负责 JSON 数据与社区映射；`utils/` 是路径、解析与 HTTP 工具。
- `frontend/`：React 19 + TypeScript + Vite + Tailwind CSS v4。`src/pages/` 是页面入口，`src/components/{rss,torrent,settings,shared,ui}/` 是组件，`src/api/` 是 API 调用层。
- `scripts/`：版本同步、映射下载与调试脚本；`tests/`：回归测试。
- `Dockerfile`：构建前端并打包后端；`docker-compose.yml`：本地构建与持久化挂载。

## 启动与验证

```bash
python run.py
uvicorn backend.api:app --host 0.0.0.0 --port 8000
cd frontend && npm ci && npm run build
docker compose up -d --build
```

后端测试：`python -m unittest discover -s tests -p 'test_*.py'`；安装 pytest 后可运行 `python -m pytest tests`。前端修改后运行 `cd frontend && npm run build`。

## 配置与数据

- `backend/config.py` 集中定义默认值、类型与范围；`GET/PATCH /api/settings` 读取或更新应用设置。敏感值在读取时脱敏，空字符串或 `***` 不覆盖已有密钥。
- 设置保存在 `MAM_DATA_DIR/settings.json`；首次启动且文件不存在时，同名环境变量仅用于初始化。旧 `rss_settings.json` 的排除词在首次读取时迁入。RSS 下载和资源采集轮询间隔也保存在设置中。
- 用户数据由 `MAM_DATA_DIR` 控制，Docker 默认 `/app/data`。订阅和历史仍在 JSON 文件；资源及种子关系数据在 `mam.sqlite3`。不要提交密钥、下载历史、数据库、日志、上传字幕等运行数据。
- 部署参数（如 `MAM_DATA_DIR`、`WATCH_DIR`、日志目录和级别）可由环境变量指定。私有仓库的 Docker 镜像已在构建时包含源码和网页，不在容器启动时拉取 Git。

## 主要流程

- Torrent：上传并解析 `.torrent` → 搜索/手动修正匹配 → 添加到 qBittorrent → 预生成 NFO 与图片 → 下载完成后整理媒体与字幕。
- RSS：创建订阅 → Bangumi/TMDB 丰富元数据 → 定时筛选 feed → 添加种子 → 生成 NFO 与图片 → 记录历史。
- 资源采集：定时读取配置来源的 feed，将资源和识别结果保存在 SQLite。

## 开发约定

- 前端组件按用途放入小写目录；组件文件使用 PascalCase，hook 使用 `use` + PascalCase。前端图标用 SVG，禁止 emoji。
- 异步 HTTP 使用 httpx，经 `backend/clients/` 或相关工具封装。业务流程使用标准 `logging`，避免 `print()`。
- Python 相对导入按包层级计算。保持 `backend` 包、`uvicorn backend.api:app`、Docker、测试及脚本中的路径一致。
- 更新版本运行 `python scripts/bump_version.py X.Y.Z`，同步 `backend/__init__.py`、`pyproject.toml`、`frontend/package.json` 和 `Dockerfile`；同时更新 `frontend/package-lock.json` 的根版本。
- Docker 更新在宿主机拉取源码后运行 `docker compose up -d --build`。网页中的 Updates 页只展示已安装版本。
