# My Anime Manager

TMDB + Bangumi 联动工具，为 Jellyfin 生成 NFO 元数据文件，支持 qBittorrent 下载管理。

## 功能

- **剧集查询** — 输入 `节目名 SXXEXX`，自动交叉查询 TMDB 和 Bangumi，输出匹配结果
- **NFO 生成** — 为 Jellyfin 生成剧集级 (`*.nfo`)、节目级 (`tvshow.nfo`)、季级 (`season.nfo`) 元数据
- **图片下载** — 从 TMDB 下载节目海报/背景/Logo，从 Bangumi 下载季封面
- **qBittorrent 集成** — 种子添加 → 文件解析 → 元数据匹配 → 重命名 → NFO 生成 → 恢复下载，全自动流程
- **目录扫描** — 监控目录，自动处理新种子，成功后删除

## 安装

```bash
# 克隆项目
git clone <repo-url>
cd My-anime-manager

# 安装依赖
pip install httpx qbittorrent-api

# 或直接安装包
pip install .
```

## 配置

通过环境变量配置：

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `TMDB_API_KEY` | ✅ | — | TMDB API 密钥，在 [tmdb.org/settings/api](https://www.themoviedb.org/settings/api) 申请 |
| `BANGUMI_UA` | — | `JellyfinTmdbHelper/1.0` | Bangumi API 的 User-Agent |
| `API_DELAY_MS` | — | `600` | Bangumi 请求间隔（毫秒），避免被限流 |
| `PROXY_HOST` | — | — | HTTP 代理地址（TMDB/Bangumi 走代理） |
| `PROXY_PORT` | — | `7890` | HTTP 代理端口 |
| `QBITTORRENT_URL` | — | `http://localhost:8080` | qBittorrent WebUI 地址 |
| `QBITTORRENT_USERNAME` | — | `admin` | qBittorrent 用户名 |
| `QBITTORRENT_PASSWORD` | — | — | qBittorrent 密码 |
| `QBITTORRENT_SAVE_PATH` | — | `/downloads` | qBittorrent 下载保存路径 |
| `TORRENT_WATCH_DIR` | — | `/data/torrent` | `--scan` 模式的默认扫描目录 |

### 配置文件示例

**PowerShell：**
```powershell
$env:TMDB_API_KEY = "你的API密钥"
$env:QBITTORRENT_URL = "http://192.168.1.100:8080"
$env:QBITTORRENT_PASSWORD = "你的密码"
$env:QBITTORRENT_SAVE_PATH = "D:/downloads"
$env:PROXY_HOST = "127.0.0.1"
$env:PROXY_PORT = "7890"
```

**Linux / macOS (`~/.bashrc` 或 `~/.zshrc`)：**
```bash
export TMDB_API_KEY="你的API密钥"
export QBITTORRENT_URL="http://192.168.1.100:8080"
export QBITTORRENT_PASSWORD="你的密码"
export QBITTORRENT_SAVE_PATH="/downloads"
export PROXY_HOST="127.0.0.1"
export PROXY_PORT="7890"
```

## 用法

### 单集查询

查询指定剧集，打印 TMDB 和 Bangumi 的匹配信息：

```bash
python -m my_anime_manager "葬送のフリーレン S01E12"
```

输出示例：
```
🎯 查询: 葬送のフリーレン S01E12
───────────────────────────────────────────────────────

📡 === TMDB 阶段 ===
🔍 TMDB 搜索: "葬送のフリーレン"
   ✅ 找到: 葬送的芙莉莲 (Sousou no Frieren) [id: 123456]
   ...

📚 === Bangumi 阶段 ===
🔍 Bangumi 搜索: "葬送のフリーレン"
   → 5 个结果
   ...

📺 查询结果
═══════════════════════════════════════════════════════
输入:        葬送のフリーレン S01E12
TMDB 节目:   葬送的芙莉莲
TMDB 剧集:   S01E12 - "真正的勇者"
BGM 条目:    葬送のフリーレン
BGM EP URL:  https://bgm.tv/ep/123456
═══════════════════════════════════════════════════════
```

### 生成 NFO 文件

在查询基础上，额外生成 NFO 元数据文件并下载图片：

```bash
python -m my_anime_manager "葬送のフリーレン S01E12" --nfo
```

生成的文件结构：
```
葬送のフリーレン/
├── tvshow.nfo          # 节目级元数据（来自 TMDB）
├── backdrop.jpg        # 背景图
├── folder.jpg          # 海报（Jellyfin 文件夹图）
├── landscape.jpg       # 横幅图
├── logo.png            # Logo
└── Season 1/
    ├── season.nfo       # 季级元数据（来自 Bangumi）
    ├── season01-poster.jpg  # 季海报
    ├── 葬送のフリーレン 01.nfo  # 第1集 NFO
    ├── 葬送のフリーレン 01-thumb.jpg  # 第1集缩略图
    ├── 葬送のフリーレン 02.nfo
    ├── ...
    └── 葬送のフリーレン 12.nfo
```

### 单个种子处理

将种子推送到 qBittorrent（暂停状态），自动匹配 TMDB/Bangumi，重命名文件结构，生成 NFO，恢复下载：

```bash
python -m my_anime_manager --torrent "/path/to/xxx.torrent"
```

处理流程：
1. 登录 qBittorrent
2. 添加种子（暂停状态）
3. 解析文件列表，过滤 OP/ED/Special
4. 从文件名推断节目名，搜索 TMDB
5. 搜索 Bangumi 并构建续集条目链
6. 预加载 Bangumi 剧集数据 + 下载季海报
7. 逐集匹配 TMDB → Bangumi，生成 NFO + 缩略图
8. 在 qBittorrent 中重命名/重组文件结构
9. 恢复下载

qBittorrent 中重命名后的文件结构：
```
/downloads/葬送のフリーレン/
├── tvshow.nfo
├── backdrop.jpg
├── folder.jpg
├── Season 1/
│   ├── season.nfo
│   ├── season01-poster.jpg
│   ├── 葬送のフリーレン 01.mkv
│   ├── 葬送のフリーレン 01.nfo
│   ├── 葬送のフリーレン 01-thumb.jpg
│   ├── ...
│   └── Extra/
│       ├── NCOP1.mkv
│       └── NCED1.mkv
└── Season 2/
    └── ...
```

### 目录扫描模式

持续监控目录，自动处理新出现的 `.torrent` 文件，处理成功后删除：

```bash
# 使用默认目录（$TORRENT_WATCH_DIR）
python -m my_anime_manager --scan

# 指定目录
python -m my_anime_manager --scan /path/to/torrents
```

## Docker 部署

```bash
# 构建镜像
docker build -t my-anime-manager .

# 或使用 docker-compose
docker-compose up -d
```

`docker-compose.yml` 中配置环境变量和卷挂载后，容器会自动以 `--scan` 模式运行。

## NFO 文件说明

生成的 NFO 文件兼容 Jellyfin、Kodi、Emby 等媒体服务器：

- **剧集 NFO** — 包含 TMDB 剧集标题/简介/播出日期/时长、导演/编剧/演员（含配音角色）、制作公司、Bangumi 剧集 ID、TMDB uniqueid
- **节目 NFO** — 包含 TMDB 节目标题/简介/类型/评分/状态/制作公司
- **季 NFO** — 包含 Bangumi 条目名称/简介/播出日期、Bangumi uniqueid

所有文本字段均经过 XML 转义处理，支持中文和日文字符。

## 技术栈

- **httpx** — 异步 HTTP 客户端，支持代理
- **bencodepy** — torrent info hash 计算
- **Python 标准库** — argparse、asyncio、pathlib、xml

## License

ISC
