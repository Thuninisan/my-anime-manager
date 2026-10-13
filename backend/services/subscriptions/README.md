# RSS downloader 的职责与入口

RSS 下载按订阅业务和单集提交拆分。模块之间不通过旧 `downloader.py` 调用，
旧模块仅保留公共入口的兼容别名，不持有独立任务、锁或状态。

| 模块 | 职责 | 主要调用方 |
| --- | --- | --- |
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

新增筛选条件放入 selection；新增订阅处理步骤放入 polling；
修改单集提交顺序应同时检查自动 RSS 和人工补集/替换的行为。
