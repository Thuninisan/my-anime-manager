# 业务服务的结构与边界

services 按业务能力分包。API 负责请求、响应和 HTTP 状态；service 负责业务流程；
clients 负责外部服务请求；db 负责持久化；domain 保存统一模型及不依赖 I/O 的业务规则。
目录和命名调整不改变公开 API 路径、预览协议、数据库 schema 或下载业务策略。

## 模块归属

| 包 | 职责 | 主要入口 |
| --- | --- | --- |
| `discovery/` | 放送日历、在线搜索结果整理及封面缓存关联 | `calendar.get_calendar`、`search.search_bangumi` |
| `subscriptions/` | Feed、订阅补全、主备筛选、首次/定时轮询、历史路径和单集 NFO | `enrichment`、`worker`、`initial_poll`，详见包内 README |
| `resources/` | 来源验证、Feed/详情/种子采集、资源识别、从资源建立预览 | `sources.add_source`、`worker.trigger`、`recognition.recognize_resource`、`preview.preview_cached_torrent` |
| `torrent/` | 多文件预览、候选维护、提交、合集查询、BD 替换和任务恢复 | `preview`、`preview_session`、`submission.submit_download`、`queries.list_collections`、`recovery` |
| `downloads/` | 按确定的目标路径暂停添加种子、重命名及恢复下载 | `episode_submission.submit_torrent` |
| `metadata/` | 平台业务目录、短期缓存、中文元数据解析、翻译、封面、NFO/图片和批量刷新 | `providers`、`context.MetadataContext`、`resolution.resolve_nfo_episode`、`nfo`、`refresh` |
| `media/` | 共享命名规则、文件计划、硬链接和可恢复的 ASS 处理 | `paths.format_download_path`、`planning.build_processing`、`linking.install_hardlink`、`fonts` |
| `mappings/` | 社区映射同步、平台绑定、变更影响预览及确认任务 | `sync.synchronize`、`sync.run_scheduler`、`bindings.select_bangumi`、`changes.preview`、`changes.confirm` |
| `system/` | 设置生效、目录扫描/监控、源码更新检查和重启触发 | `settings.update`、`directory_tasks`、`updates` |
| `workflows/` | 后处理执行上下文及恢复检查点租约续期 | `execution.execution`、`execution.current_job` |

沿用 `torrent` 单数包名和已经拆分的订阅文件名；不为目录统一改动稳定的调用签名。
`downloader.py` 仅保留已有公共入口的兼容别名，不持有独立状态；项目内部直接使用对应业务包。
其余旧根模块和 `nfo/`、`resource_monitor/` 已迁入上表中的包。

## 共用规则与基础设施

- `domain/resource_resolution.py`：作品候选判定、关系约束与候选排序。
- `domain/rss_mapping.py`：RSS 集号、来源偏移与平台目录转换。
- `domain/batch_episode.py`、`domain/scanned_episode.py`：批处理和扫描输入的坐标约束。
- `domain/bangumi_chain.py`：已确定关联链上的季集定位。
- `domain/metadata_resolution.py`：字段来源、零值、季集坐标原子选择和 provenance 规则。
- `domain/preview_metadata.py`：从可信预览快照读取单集元数据，不访问预览会话数据库。
- `domain/resource_sources.py`：内置来源定义，供 Feed 和数据库初始化共用。

平台原始字段转换仍由现有 domain adapters 承担，领域结构以 ResourceIdentity、
EpisodeCatalog、EpisodeMapping 和 ResolvedEpisode 为准。HTTP 协议和数据库状态不进入这些规则。

## 依赖和状态的归属

1. services 不导入 `backend.api`。新业务入口返回数据或抛出业务异常，由路由映射为 HTTP 响应。
   原有下载、预览会话、单集/批量 NFO 和 BD 接口仍保留既有 HTTPException 契约；目录迁移不重写这些异常语义。
2. `metadata` 不反向依赖 `subscriptions`、`torrent` 或 `downloads`。Bangumi 剧集缓存归 provider，
   快照读取归 domain；qBittorrent 的单集重命名归 downloads；单集提交判断、后台 NFO 和历史记录归 subscriptions.episode_download。
3. `media` 不调用订阅或种子流程。Torrent monitor 负责等待下载并协调文件操作；
   recovery 独占任务注册、阶段推进和恢复，文件计划及实际硬链接归 media。
4. 定时轮询和首次轮询通过 subscriptions.worker 共用锁。拆包不复制缓存、任务表或写入锁。
5. system.directory_tasks 独占扫描/监控状态，system.updates 独占更新检查缓存。
   应用生命周期启动任务并等待停止；API 不再持有后台任务状态或 worker 实现。
6. 数据库租约、事务和 checkpoint 持久化仍由 db 负责；workflows 仅绑定执行上下文并续期。
7. 多文件 Torrent、自动目录批处理和 RSS 单集提交保留各自执行入口。
   只有实际共用的元数据、路径与文件操作下沉，不用模式参数统一不同的下载策略。

## 扩展时放在哪里

- 增加 RSS 筛选条件：`subscriptions/selection.py`；增加一次订阅处理步骤：`polling.py`。
- 增加采集来源：来源配置及必要的 `resources/feeds.py`、`details.py` 适配，不修改下载流程。
- 增加外部元数据源：客户端请求放 clients，平台业务目录放 metadata/providers，字段归一化和判定规则放 domain。
- 修改 NFO 内容：`metadata/nfo/nfo_xml.py`；修改字段优先级：domain metadata resolution；修改中文补全：metadata resolution/plots。
- 修改输出目录或命名：`media/paths.py`；修改 Torrent 文件计划：`media/planning.py`。
- 增加后处理阶段：`torrent/recovery.py` 协调；文件或字幕操作放 media；checkpoint 操作调用 db。
- 增加设置的运行时副作用：`system/settings.py`，由它通知拥有状态的服务。

不增加只转发数据库调用的查询包装，不建立空模块，也不通过大型 Service 类或通用工作流框架替代明确的业务入口。

## 季与作品的 NFO 简介

- `metadata/plots.py` 分别解析 series 和 season 简介，返回文本、来源和状态；XML 写入层只接收最终文本。
- series 优先 TMDB 中文 overview、TVDB 中文 overview，再依次翻译同一作品的 TMDB、TVDB 原文；缺少原文时按日文、英文补取。不使用任意一季的 Bangumi summary 代替作品简介。
- season 优先对应 TMDB 季的中文 overview、已映射 Bangumi subject 的内嵌中文，再依次翻译 Bangumi summary、TMDB 季原文。季号来自平台映射，支持第 0 季；不使用整部作品的 overview 代替季简介。
- `metadata/translation.py` 共用各层级的简介翻译、中文校验和重试能力，缓存键包含文本范围与原文。仅成功译文和成功解析结果进入缓存，补全失败允许后续任务重试。
- 已有季或作品 NFO 未开启覆盖时，不额外解析简介。覆盖刷新及映射重建中，新简介不可用时保留旧的有效中文；没有可保留内容则留空，并记录来源与降级状态。
