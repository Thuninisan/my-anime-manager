# CLAUDE.md

My Anime Manager — TMDB + Bangumi + qBittorrent 联动工具，为 Jellyfin 自动生成 NFO 元数据并管理番剧下载。纯 Web 应用，通过 FastAPI 提供 REST API + React SPA 前端。

## 技术栈

- **后端**: Python 3.11+, FastAPI + uvicorn, httpx (异步 HTTP)
- **前端**: React 19 + TypeScript (Vite), Tailwind CSS v4, @base-ui/react 组件
- **部署**: Docker 多阶段构建 (node:20-alpine → python:3.12-alpine)
- **包管理**: setuptools + pyproject.toml

## 项目结构

```
my_anime_manager/
├── api.py               # FastAPI 应用 (~2230 行) — 所有路由 + 后台 worker
├── config.py            # 配置管理 (内存覆盖 > settings.json > 默认值)
├── data/
│   ├── __init__.py      # 数据层 — JSON 文件读写 (订阅/历史/设置/映射)
│   ├── bangumi_mikan_map.json  # 社区维护的 Bangumi→Mikan+TMDB 映射表
│   ├── subscriptions.json      # 用户订阅数据
│   ├── download_history.json   # 下载历史 (含 TMDB 覆盖)
│   ├── rss_settings.json       # 全局 RSS 排除模式
│   └── settings.json           # 持久化应用配置
├── clients/             # 外部 API 客户端 (薄封装)
│   ├── bangumi.py       # Bangumi v0 API
│   ├── tmdb.py          # TMDB v3 API (默认 language=ja)
│   ├── qbittorrent.py   # qBittorrent Web API
│   └── mikan.py         # Mikanani (蜜柑计划) RSS 抓取
├── services/            # 业务逻辑
│   ├── bangumi.py       # Bangumi 搜索、续集链、剧集匹配
│   ├── tmdb.py          # TMDB 搜索、详情、季→集映射
│   ├── batch_service.py # Torrent 批量处理 (preview→confirm→execute)
│   ├── torrent_preview.py  # 前端 torrent 解析管线 (parse + search)
│   ├── downloader.py    # RSS 下载 worker
│   ├── rss.py           # RSS 订阅管理 (Mikan 查找 + feed 解析)
│   ├── nfo/             # NFO 元数据子包 (XML 生成 + 图片下载 + 编排)
│   │   ├── nfo_xml.py, images.py, generator.py, metadata_builder.py
│   ├── mapper.py        # 季/集映射逻辑
│   └── episode_metadata.py  # 剧集元数据解析与匹配
├── utils/               # 工具函数
│   ├── parser.py        # 文件名解析 (anitopy)
│   ├── torrent_parser.py    # torrent 文件名批量解析
│   ├── torrent_file_reader.py  # bencode torrent 文件读取
│   ├── torrent_hash.py  # info hash 计算
│   ├── episode_name_match.py  # 模糊匹配 (NFKC→exact→substr→Dice)
│   └── http_retry.py    # HTTP 重试封装
└── vendor/anitopy/      # 内嵌的 anime 文件名解析器

frontend/
├── src/
│   ├── App.tsx          # 主布局 (侧边栏 + 路由出口)
│   ├── main.tsx         # React Router (/torrent, /rss, /settings)
│   ├── api/             # 前端 API 调用层
│   │   ├── torrentApi.ts    # torrent 解析/下载/字幕
│   │   ├── rssApi.ts        # RSS 订阅/历史/下载器/TMDB 搜索
│   │   └── client.ts        # 通用 fetch 封装
│   ├── hooks/           # 自定义 hooks
│   ├── types/           # TypeScript 类型定义 (preview.ts)
│   ├── components/
│   │   ├── ui/          # shadcn/ui 基础组件 (button/dialog/table...)
│   │   ├── rss/         # RSS 业务组件 (14 个)
│   │   ├── settings/    # 设置页面组件 (4 个)
│   │   ├── Cards/       # 信息卡片组件
│   │   ├── layout/      # AppLayout
│   │   ├── icons/       # SVG 图标组件
│   │   └── shared/      # 通用表单组件
│   └── pages/           # TorrentPage, RssPage, SettingsPage
└── package.json

scripts/
└── download_bangumi_data.py  # 下载 bangumi-data → 构建 bangumi_mikan_map.json

run.py                  # 开发启动: python run.py (带热重载)
Dockerfile              # 多阶段构建, ENTRYPOINT: uvicorn
```

## 启动方式

```bash
# 开发 (热重载)
python run.py

# 直接启动
uvicorn my_anime_manager.api:app --host 0.0.0.0 --port 8000

# Docker
docker build -t my-anime-manager .
docker run -p 8000:8000 -v ./data:/app/data my-anime-manager
```

## 版本管理

应用版本统一存放在 4 个位置，通过 `scripts/bump_version.py` 一键同步：

```bash
python scripts/bump_version.py X.Y.Z
```

更新的文件：
- `my_anime_manager/__init__.py` — `__version__` + `__version_info__`（后端 API 引用）
- `pyproject.toml` — `version`（pip/setuptools）
- `frontend/package.json` — `"version"`（npm）
- `Dockerfile` — `LABEL org.opencontainers.image.version`

## 配置系统

`config.py` 使用三层优先级: **内存覆盖 > settings.json > 默认值**。

环境变量只在首次启动 (settings.json 不存在时) 用来自动初始化文件，之后不再读取。

通过 `__getattr__` 实现模块级属性访问 (`from .config import TMDB_API_KEY`)。

敏感键 (`TMDB_API_KEY`, `QBITTORRENT_PASSWORD`) 在 `get_all()` 中自动脱敏，且前端传空字符串时不会被覆盖。

可通过 API (`GET /config`, `PUT /config`) 读写，修改自动持久化到 `data/settings.json`。

## API 端点总览

| 方法 | 路径 | 说明 |
|------|------|------|
| **Torrent** | | |
| POST | `/api/torrent/parse-and-search` | 上传 .torrent → 解析 + TMDB/Bangumi 搜索 |
| POST | `/api/torrent/download` | 添加到 qBittorrent (可选预生成 NFO), 后台监控 |
| POST | `/api/torrent/subtitle/upload` | 上传字幕 |
| DELETE | `/api/torrent/subtitle/delete` | 删除字幕 |
| GET | `/api/torrent/bangumi/{id}/episodes` | 获取 Bangumi 剧集列表 |
| **扫描** | | |
| POST | `/scan` | 后台扫描目录 |
| GET | `/scan/status` | 扫描进度 |
| GET | `/watch/status` | 监控状态 |
| **配置** | | |
| GET | `/config` | 读配置 (敏感字段脱敏) |
| PUT | `/config` | 写配置 (持久化到 settings.json) |
| **RSS 订阅** | | |
| GET | `/api/rss/search` | 按名称搜索 Bangumi→Mikan 映射 |
| GET | `/api/rss/bangumi/{id}` | 查找 Mikan 字幕组 |
| GET | `/api/rss/bangumi/{id}/meta` | 获取 Bangumi 元数据 |
| POST | `/api/rss/bangumi/{id}/assign-mikan` | 手动关联 Mikan ID |
| POST | `/api/rss/manual-subscribe` | 手动输入 RSS URL 订阅 |
| GET | `/api/rss/subscriptions` | 列出订阅 |
| POST | `/api/rss/subscriptions` | 创建/更新订阅 |
| POST | `/api/rss/subscriptions/{id}/enrich-stream` | NDJSON 流: 丰富订阅元数据 |
| PATCH | `/api/rss/subscriptions/{id}/tmdb` | 手动设置 TMDB ID/Season |
| PATCH | `/api/rss/subscriptions/{id}/activate` | 重新激活已完成订阅 |
| PATCH | `/api/rss/subscriptions/{id}` | 更新订阅字段 |
| DELETE | `/api/rss/subscriptions/{id}` | 删除订阅 |
| DELETE | `/api/rss/subscriptions/{id}/rss` | 清除主/备 RSS |
| **下载器** | | |
| GET/POST | `/api/rss/downloader/*` | 状态/启动/停止/单次运行/配置 |
| **下载历史** | | |
| GET | `/api/rss/subscriptions/{id}/history` | 获取历史 |
| GET | `/api/rss/subscriptions/{id}/history-stream` | NDJSON 流: 历史 + 实时 qBittorrent |
| POST/DELETE/PATCH | `/api/rss/download-history/{id}/{sort}` | 增删改历史记录 |
| POST | `/api/rss/download-history/{id}/{sort}/upload` | 上传 .torrent 补全 |
| POST | `/api/rss/download-history/{id}/{sort}/replace` | 替换种子 |
| **TMDB** | | |
| GET | `/api/rss/tmdb-search` | 搜索 TMDB 节目 |
| GET | `/api/rss/tmdb/{id}/seasons` | 获取 TMDB 季剧集数据 |
| **RSS 设置** | | |
| GET/PUT | `/api/rss/settings` | 全局 RSS 排除模式 |

## 数据流

### Torrent 处理流程

1. 前端上传 .torrent → `POST /api/torrent/parse-and-search`
2. 后端 bencode 提取文件列表 → anitopy 解析 → 并行搜索 TMDB + Bangumi
3. 前端 MatchTable 展示匹配结果, 用户可手动调整映射
4. 用户点击确认 → `POST /api/torrent/download`
5. 后端: 添加种子 (暂停) → **预生成 NFO + 下载图片** → 恢复下载 → 后台监控完成
6. 后台监控: 下载完成后创建硬链接 + 复制字幕

### RSS 订阅流程

1. 前端搜索 Bangumi → 查找 Mikan 字幕组 → 用户选择 + 设置过滤标签 → 订阅
2. 后端 enrichment: 回溯前传链 → 获取 Bangumi 数据 → 查 TMDB ID (含 Tier-1 自动推断保底)
3. 下载器定时轮询 RSS feed → 匹配过滤标签 → 下载 .torrent → 添加到 qBittorrent → **在恢复下载前生成 NFO + 图片** → 标记已下载

### NFO 生成

- **剧集匹配**: `utils/episode_name_match.py` — 三轮递进 (exact→substring→Dice 0.55) 匹配 Bangumi→TMDB 剧集名
- **中文内容**: 解析阶段用 `ja` (保证匹配准确性), 确认下载后用 `zh-CN` 重新获取 plot 和 cast
- **声优**: 调 `/season/{n}/credits` (torrent) 或 `/episode/{n}/credits` (RSS) 获取完整配音表
- **TMDB 自动推断**: 订阅 enrichment 阶段, 若映射表缺 tmdb_id, 通过链内兄弟条目的 TMDB ID + 首集名模糊匹配自动推断

## 开发注意事项

- **前端禁止使用 emoji** — 所有图标必须使用 SVG 组件 (`frontend/src/components/icons/`), 禁止 emoji 字符
- 异步 HTTP 请求使用 httpx (通过 clients 模块)
- 前端构建产物放在 `frontend/dist/`, 由 FastAPI 作为静态文件服务
- 用户可写数据 (`subscriptions.json`, `download_history.json`, `settings.json`, `rss_settings.json`) 存放在 `_USER_DATA_DIR`, 通过 `MAM_DATA_DIR` 环境变量控制 (Docker 默认为 `/app/data`)
- 环境变量 `PYTHONUTF8=1` 在 Docker 中设置
- 配置变更通过 `PUT /config` 自动持久化到 `data/settings.json`, 不再依赖环境变量
- 敏感配置 (密码/API Key) 在前端显示为 `***`, 保存时传空字符串不会覆盖已有值
- 所有相对导入使用单点 (`.module`) 而非双点 (`..module`) — `api.py` 在 `my_anime_manager` 包内
- `print()` 已从主要流程中移除, 使用标准 `logging` 模块
