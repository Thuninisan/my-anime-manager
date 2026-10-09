# Phase 8 — Candidate-Driven Torrent Mapping

Torrent 搜索现在只推荐候选作品。添加候选只加载可参与匹配的目录；用户提交最终 EpisodeMapping 后，服务端才推导每个文件的 ResourceIdentity。没有新增作品绑定确认步骤，也没有创建全局作品绑定表。

## 数据流与权威来源

```text
解析文件 → 排序后的 ResourceCandidate（按 show_key 隔离）
         → 加载 EpisodeCatalog → 原有集数自动匹配 → 文件级手动覆盖
         → 提交最终 EpisodeMapping → 服务端校验 → 文件级 ResourceIdentity
         → NFO / 下载处理计划 / EpisodeMappingSnapshot
```

| 信息 | 来源 |
|---|---|
| 可选作品与来源 | search_results[show_key].candidates |
| 已加载的目录 | episode_catalog |
| 当前文件的季、集及平台引用 | EpisodeMapping |
| 电影 TMDB 引用 | 下载项显式 tmdb_movie_id |
| 下载确认身份 | 从该文件 Mapping 和电影引用推导 |
| 历史身份与集数 | 原有 EpisodeMappingSnapshot v1 |

候选按平台和 ID 去重，来源合并为 `source` 中的多个标记。标题和年份用于排序，不用于确认作品关系。map.json 的关联只发现候选和提供 mapping_hints；分支续集也作为目录候选，不要求单独确认绑定。

## Preview API 的实际返回

[完整 JSON 样例](tests/fixtures/torrent_preview_phase8.json) 来自 FastAPI TestClient 实际调用 `POST /api/torrent/parse-and-search` 的响应。使用离线测试资源 A、B，不包含真实用户运行数据；其中 preview_id 和 expires_at 是生成样例时的临时会话值，不能用于下载。

公开顶层字段为 `preview_id`、`revision`、`expires_at`、`torrent_name`、`resource_id`、`episode_match_source`、`parsed_files`、`specials`、`subtitles`、`subtitle_files`、`skipped_files`、`search_results` 和 `episode_catalog`。

每个 search_results 条目包含 `show_key`、`media_type`、三平台 `candidates`、显示标题和 `mapping_hints`。公开契约移除了 `resource_identity`、`resource_resolution`、`provider_resolutions`、重复 provider ID、`bangumi_subject_ids`、重复 `series` 投影和全局 `resource_candidates` 列表。资源采集入口的预识别候选归入对应 show_key；无法明确归属的多作品候选不授予跨作品目录权限。

PreviewSession schema 从 2 升为 3。旧临时预览返回 `preview_schema_outdated`，需要重新预览；没有删除旧会话或修改永久历史数据、数据库字段、历史身份 schema、canonical_title。

## 添加、切换与移除

`POST /api/torrent/previews/{preview_id}/augment` 接收 `preview_revision`、`show_key`、`provider`、`provider_id`。仍接受 Bangumi 的 `subject_id` 及原有 `series_id` 输入别名。Movie ID 使用通用 provider_id，不虚构 TV Series 引用。

服务端检查 ID、作品详情和媒体类型，加载所请求候选的目录，更新目标 show_key 并增加 revision。已加载的剧集目录可复用，重复添加已缓存的候选也复用详情；不会写入 RSS 订阅或确认身份。手动 augment 只添加所请求候选，不自动重新加入用户移除的关联候选。

`POST /api/torrent/previews/{preview_id}/remove-candidate` 接收相同会话与作用域信息及 provider_id，从该 show_key 的候选集合删除作品，保留已加载缓存，并增加 revision。保留缓存不授予下载权限；引用已移除候选的 Mapping 被服务端拒绝。只有显式重新添加才恢复可用性。

前端按作品显示 TMDB、Bangumi 和 TVDB 候选，支持 ID 添加、Bangumi 名称搜索及移除。不同 TMDB/TVDB 作品的季度选项携带 `series_id:season`。Bangumi 可同时保留多个 Subject。电影可切换当前 Movie Candidate；这个选择是预览推荐，最终下载项仍显式提交 Movie ID。

## 文件映射与下载校验

保留现有 TMDB/TVDB 索引、标题匹配和人工季集调整算法。目录先按文件的 show_key 候选集合筛选，再生成 canonical EpisodeMapping。match_source=tvdb 的有效性由 TVDB 集数决定，不再要求同时匹配 TMDB。手动覆盖按稳定 file_id 保存，保留用户触碰的作品与集数；新增其他目录及切换索引不会重置有效的 Bangumi 人工选择。

服务端检查当前 revision、match_source、候选作用域、目录存在性、媒体类型、Episode ID 所属 Series/Subject，以及季号、集号、Bangumi ep/sort 与目录的精确一致性。无 Episode ID 的精确坐标也必须在目录中存在。没有 Series/Subject ID 时不能携带伪造的 Episode 引用。未解析的特殊文件及字幕通过映射引用确定一个有效作用域，不能组合不同 show_key 的候选。

TV 文件身份使用原有 resource_identity 工厂，Bangumi、TMDB 和 TVDB ID 全部来自该文件的最终 Mapping；未选择的平台保留 null，没有搜索默认值回填。canonical_title 保留在持久化契约中，本次生成的身份不依赖该字段，显示与命名从最终候选/目录标题取得。不同作品不共用第一个搜索结果的根目录标题。

电影要求显式、已允许的 tmdb_movie_id，TV 引用必须为空，Bangumi Subject 来自该文件 Mapping。保持现有单次电影任务限制：多个 Movie ID、电影与 TV 混合、同一电影使用多个 Bangumi Subject 的单次请求仍被拒绝。

一个 TMDB Series 可以被多个文件映射到不同 Bangumi Subject。每个文件保存自己的身份与季集坐标。NFO 使用已验证的 Mapping，电影 NFO 使用最终 Movie Candidate；下载处理计划现在也保存电影的 EpisodeMappingSnapshot。原有 RSS、Batch、Scan、字幕处理、BD Replacement、监控恢复和历史读取流程保持其数据契约。

## 修改文件

| 范围 | 文件 |
|---|---|
| API 与预览契约 | backend/api/routes_torrent.py；backend/api/routes_resources.py；backend/domain/preview.py |
| 搜索、候选及会话 | backend/services/resource_resolver.py；backend/services/torrent/preview.py；backend/services/torrent/search.py；backend/services/torrent/preview_session.py；backend/services/torrent/preview_view.py |
| NFO、下载处理及快照 | backend/services/torrent/metadata.py；backend/services/torrent/monitor.py |
| 前端契约与 API | frontend/src/types/preview.ts；frontend/src/types/matchTable.ts；frontend/src/api/torrentApi.ts；frontend/src/lib/episodeAdapters.ts |
| 前端匹配与交互 | frontend/src/lib/matchUtils.ts；frontend/src/hooks/useMatchOverrides.ts；frontend/src/components/torrent/InfoCards.tsx；frontend/src/components/torrent/MatchTable.tsx；frontend/src/components/torrent/TorrentPreview.tsx |
| 测试 | tests/test_candidate_driven_torrent.py；tests/test_preview_sessions.py；tests/test_resource_recognition_flows.py；tests/test_persistence_canonical.py；tests/test_legacy_contract_cleanup.py；frontend/src/lib/episodeMatching.test.mjs |
| 交付资料 | PHASE8.md；tests/fixtures/torrent_preview_phase8.json |

## 验证及边界

Python 全量 unittest：301 项，300 通过，1 跳过，无失败。跳过的是 HTTP 客户端本地监听测试，因为当前沙箱不允许本地监听 socket。

新增 22 项后端候选驱动回归测试，覆盖候选移除与重新添加、缓存复用、跨作品隔离、目录与坐标校验、索引冲突、多个 Subject 的文件级身份、无默认 TMDB 回填、普通及上传字幕、电影 NFO 与处理计划快照、RSS 不被修改、特殊文件作用域以及最终标题来源。现有全量测试还覆盖 RSS、Batch/Scan、BD 替换、持久化恢复和历史 Snapshot。

`cd frontend && npm run test` 通过；新增候选移除、多 TMDB Series、人工 Bangumi 选择在 augment 后保留、电影显式引用的断言。`npm run build` 中 `tsc -b` 和 Vite 构建通过；`git diff --check` 通过。

构建仍有 Vite 原配置的 `__dirname` 提醒和 bundle 大于 500 kB 的提醒；不影响构建。验证使用离线 provider/qBittorrent 测试替身，未执行真实外部服务下载或浏览器端到端操作。

交互式 Torrent 搜索、PreviewSession、公开预览与前端匹配链路没有旧 resource_resolution/provider_resolutions 依赖。ResourceResolution 与 ResourceResolver 保留给其他业务；Phase 7 EpisodeMapping、ResourceIdentity 与永久 Snapshot 契约保持不变。
