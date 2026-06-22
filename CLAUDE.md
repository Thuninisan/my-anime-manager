# CLAUDE.md

My Anime Manager — TMDB + Bangumi + qBittorrent 联动工具，为 Jellyfin 自动生成 NFO 元数据并管理番剧下载。

## 技术栈

- **后端**: Python 3.11+, FastAPI + uvicorn, httpx (异步 HTTP)
- **前端**: React + TypeScript (Vite), shadcn/ui 组件
- **部署**: Docker 多阶段构建 (node:20-alpine → python:3.12-alpine)
- **包管理**: setuptools + pyproject.toml

## 项目结构

```
my_anime_manager/
├── main.py              # CLI 入口，支持四种模式
├── api.py               # FastAPI 服务端 (REST API + 静态文件)
├── config.py            # 配置管理 (环境变量 + 运行时覆盖 + 默认值)
├── data/                # 持久化数据 (SQLite?)
├── clients/             # 外部 API 客户端
│   ├── bangumi.py       # Bangumi API
│   ├── tmdb.py          # TMDB API
│   ├── qbittorrent.py   # qBittorrent Web API
│   └── mikan.py         # Mikanani (蜜柑计划) RSS
├── services/            # 业务逻辑
│   ├── bangumi.py       # Bangumi 搜索、续集链、剧集匹配
│   ├── tmdb.py          # TMDB 搜索、详情、季→集映射
│   ├── batch_service.py # Torrent 批量处理 (预览→确认→执行)
│   ├── mapper.py        # 季/集映射逻辑
│   ├── nfo_generator.py # Jellyfin NFO 文件生成
│   ├── image_downloader.py # TMDB/Bangumi 图片下载
│   ├── rss.py           # RSS 订阅管理
│   └── downloader.py    # 下载器服务
├── utils/               # 工具函数
│   ├── parser.py        # 用户输入解析 (ShowName SXXEXX)
│   ├── formatter.py     # 终端输出格式化
│   ├── torrent_parser.py
│   ├── torrent_file_reader.py
│   └── torrent_hash.py
└── vendor/anitopy/      # 内嵌的 anime 文件名解析器
frontend/
├── src/
│   ├── App.tsx          # 前端主组件
│   ├── components/      # React 组件
│   │   ├── ui/          # shadcn/ui 基础组件
│   │   ├── Cards/       # 业务卡片组件
│   │   ├── TorrentUpload.tsx
│   │   ├── PreviewDashboard.tsx
│   │   ├── RssManager.tsx
│   │   ├── SettingsModal.tsx
│   │   └── ...
│   ├── api/             # 前端 API 调用层
│   ├── hooks/           # 自定义 hooks
│   ├── types/           # TypeScript 类型定义
│   └── lib/             # 工具函数
└── package.json
```

## CLI 四种模式

| 模式 | 命令 | 说明 |
|------|------|------|
| 单集查询 | `anime-manager "Show S01E12" [--nfo]` | TMDB→Bangumi 联动，可选生成 NFO |
| Torrent 处理 | `anime-manager --torrent <path>` | 处理单个 .torrent 文件 |
| 目录扫描 | `anime-manager --scan [dir]` | 每 30s 扫描目录，自动处理并删除成功文件 |
| API 服务 | `anime-manager --serve [host:port]` | 启动 FastAPI + 前端界面 |

## 单集查询流程 (核心逻辑)

1. **解析输入**: `ShowName SXXEXX` → show_name, season, episode
2. **TMDB 搜索**: 搜索 TV show → 获取详情 → 构建季→集映射
3. **Bangumi 搜索**: 用日文名→中文名→TMDB 名依次重试搜索
4. **续集链遍历**: 找到第一个条目 → 遍历 sequel 链 → 备选按日期排序
5. **条目匹配**: 在 chain 中找到对应季/集的目标 subject
6. **剧集匹配**: 获取 Bangumi 剧集列表 → 匹配目标集
7. **输出结果**: 打印映射关系，可选生成 NFO + 下载图片

## 配置系统

`config.py` 使用三层优先级: 运行时覆盖 > 环境变量 > 默认值。

通过 `__getattr__` 实现模块级属性访问 (`from .config import TMDB_API_KEY`)。

敏感键 (`TMDB_API_KEY`, `QBITTORRENT_PASSWORD`) 在 `get_all()` 中自动脱敏。

可通过 API (`PUT /config`) 在运行时修改配置。

## API 端点

- `POST /api/torrent/preview` — 上传 .torrent 返回预览 JSON
- `POST /api/torrent/confirm` — 确认执行
- `POST /scan` — 后台扫描目录
- `GET /scan/status` — 扫描进度
- `GET /watch/status` — 监控状态
- `GET /config` / `PUT /config` — 配置读写

## 开发注意事项

- Windows 环境下 stdout/stderr 会自动重配置为 UTF-8
- 当执行代码修改时, 在相应的位置添加清晰的代码注释
- 异步 HTTP 请求使用 httpx (通过 clients 模块)
- 前端构建产物放在 `frontend/dist/`，由 FastAPI 作为静态文件服务
- Docker 入口默认启动 `--serve` 模式 (含自动 watch)
- 环境变量 `PYTHONUTF8=1` 在 Docker 中设置
- Torrent 处理失败的文件会移到 `failed/` 目录

## 常用命令

```bash
# 开发运行
pip install -e .
anime-manager "葬送のフリーレン S01E12" --nfo
anime-manager --serve

# Docker 构建
docker build -t my-anime-manager .
docker run -p 8000:8000 my-anime-manager
```
