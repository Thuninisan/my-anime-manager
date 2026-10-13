# RSS downloader 的职责与入口

RSS 下载按订阅业务和单集提交拆分。模块之间不通过旧 `downloader.py` 调用，
旧模块仅保留公共入口的兼容别名，不持有独立任务、锁或状态。

| 模块 | 职责 | 主要调用方 |
| --- | --- | --- |
| `enrichment.py` | 补全缓存判断、缓存来源偏移补算、完整补全编排与保存 | RSS 补全流式 API、创建订阅响应 |
| `worker.py` | 启停、调度、共享锁、全量轮询状态和累计统计 | 下载器 API、设置 API、应用生命周期、首次订阅任务 |
| `polling.py` | 单订阅主备轮询、Feed 快照复用、偏移补算、范围刷新和完结判定 | worker |
| `selection.py` | 标签、排除词、日期、集数、去重、失败上限和来源优先级 | polling、RSS 单集下载 |
| `episode_download.py` | RSS 补全回退、自动替换、种子获取与临时文件清理 | polling |
| `../downloads/episode_submission.py` | 单集映射、暂停添加、元数据与重命名、恢复、历史保存；连接检查 | RSS 单集下载、人工补集/替换 API |
| `paths.py` | 优先读取实际媒体路径，缺失时使用已有路径模板 | 单集 API、NFO、BD 替换 |
| `episode_nfo.py` | 使用历史映射重生成单集 NFO 和图片，不调用 qBittorrent | 历史 API |

## 调用与并发边界

全量轮询使用 `worker.run_once()` 或由 `worker.start()` 启动。
首次订阅轮询使用 `worker.poll_subscription()`。两个入口共用 worker 的一把锁；
不要绕过 worker 直接并发调用 `polling.process_subscription()`。

polling 返回本次成功提交数量，不修改调度状态。worker 只在全量轮询中累加该数量；
首次单订阅轮询返回独立结果，不修改全量统计。`running` 表示全量轮询执行中，
`scheduler_running` 表示周期调度已开启，二者保持不同含义。

## 保留的业务边界

- 主来源先处理，备用来源随后补集。筛选和自动替换共用
  `selection.SOURCE_PRIORITY`，未知来源仍按 -1 处理。
- Feed 快照和 MetadataContext 按一次订阅处理复用；主备来源各自保存偏移。
- 自动 RSS 替换与人工单集替换保留各自原有的旧种子删除时机和失败处理。
- 提交服务仅处理订阅单集种子，不承接 Torrent 多文件/整季提交。
- 单集 NFO 的冲突检查和写入锁仍由 API 与现有 `nfo_refresh` 管理。
  单集生成保留图片处理；整订阅批量刷新仍使用原有 NFO-only 流程。
- `rss.py`、`enrich.py`、`rss_episode_matcher.py`、`nfo/` 和现有数据访问层
  继续提供 Feed、元数据、映射、输出及持久化能力，本次拆分不重组这些模块。

## 已保存订阅的补全入口

- `copy_enrichment_fields()` 只提取响应字段，不验证缓存；创建订阅接口沿用此投影。
- `get_cached_enrichment()` 检查订阅和当前社区映射，返回有效缓存或 `None`。
- 缓存命中后调用 `refresh_cached_enrichment()`，仍按当前主备 RSS 重算来源偏移。
  相同 URL 共用一份快照；仅保存非 `None` 偏移，保留零和负数。
- 缓存未命中时调用 `enrich_saved_subscription()`，通过可选回调报告进度，
  使用现有 `enrich.py` 补全并持久化。保存保留映射预期值与身份版本校验，
  冲突向调用方传播；底层返回 `None` 时仍返回 `None`。
- API 负责进度队列、`step/done/error` 事件及 NDJSON 编码。缓存分支属于请求生命周期，
  缓存命中消息在获取 Feed 前发送；完整补全由独立任务执行，断开连接后继续。
- 缓存响应仅含补全字段与顶层来源偏移；完整响应沿用保存后、写来源偏移前读取的
  订阅快照，并附加顶层来源偏移。此处不统一两个分支的响应结构。
- 首次轮询和自动下载的补全回退继续使用原入口，保留各自的快照复用、重试和跳过策略。

新增筛选条件放入 selection；新增订阅处理步骤放入 polling；
修改单集提交顺序应同时检查自动 RSS 和人工补集/替换的行为。
