# Phase 7：Legacy Removal / Contract Cleanup

完成日期：2026-10-08。基线：`7978441bccf8679f1a92c0f4225e5e9a9a81f39f`。本阶段完成 runtime、写路径、API 与前端契约清理；未执行用户数据库 backfill、清空旧值、删除历史或物理 DROP。未提交或推送。

## 1. Legacy inventory

`PHASE7_LEGACY_INVENTORY.json` 保存修改前基线的全仓词法命中，逐行记录文件、行号、命中词、源文本、函数作用域、A–G 分类与处理结论。共 2416 行代码/配置/文档命中；社区映射 JSON 的 21207 行重复 provider facts 单独汇总，未改动数据。广义 `index`、`fallback`、ID 和 canonical 名字的子串也是审计信号，不能把命中数量当作待删除字段数量。基线位置不等于修改后的代码位置。

`PHASE7_LEGACY_AUDIT.md` 按真实用途说明迁移/保留决定。`results[0]`、`chain[0]`、`next(iter)` 等已检查：唯一性判断、显示/目录选择与 provider normalization 不作为未经确认的当前绑定来源。

## 2. A–G 分类

| 类别 | 真实用途与最终决定 |
|---|---|
| A 直接删除 | 无生产调用者的 ID-binding adapter，以及迁完 consumer 的旧转换/旧 NFO writer |
| B runtime compatibility | Torrent/RSS/Batch/Scan/NFO、preview/download、前端 matcher/type；迁成 canonical；普通 ID 变量与索引不机械删除 |
| C old DB read-only | 旧 history、subscription binding 与旧 replacement plan；隔离到 legacy，由 repository 进入 |
| D migration/backfill | 显式维护脚本、JSON 降级导出、历史架构文档；退出业务运行依赖 |
| E provider raw | provider client/acquisition/catalog normalization、community mapping hints、monitor candidate；不等于 confirmed identity |
| F user configuration | seasons、offset、范围、显式绑定编辑、历史编号 override、路径占位词；保留语义 |
| G fixture/regression | 旧数据/旧转换测试输入；搬到 tests helper，生产代码不 import tests |

## 3. 已停止写入的字段

- 新 subscription 与 binding 更新不写 `subscriptions.tmdb_id/tvdb_id` 镜像。
- 新 history canonical snapshot 写入不再填 `download_episodes.tmdb_ep_calc/tvdb_ep`。
- 新 Torrent replacement operation 写 `episode_mapping_snapshot`，不再填 `bangumi_sort` mirror。
- runtime mapping/batch/download/NFO 参数不生成 `src_episode`、`merged_ep`、旧 provider episode/ID 平铺副本。

更新已有行时不清空旧 mirror；tests 证明旧值不变，新行 mirror 为 NULL 仍正常。

## 4. 仍必须写入的业务/兼容字段

订阅 provider season/episode offsets、RSS offsets/筛选规则、历史 `tmdb_ep/tmdb_season` 人工编号设置保留；API 使用 `tmdb_episode_override/tmdb_season_override` 命名。`bangumi_id` 作为订阅/历史 repository key 保留。community mappings、显式 mapping override、旧 JSON importer 的原事实导入也保留。这些不是新 history 的 provider identity mirror。deprecated v1 response 的 provider ID 是即时投影，不是 DB 双写。

## 5. 已停止的正常 runtime reads

不再从 `episode_data/preview_data`、旧 search provider blocks、旧 parsed 别名、flat provider episode fields 或当前订阅 provider ID mirror 恢复身份。subscription runtime 要求 `resource_identity`；Batch/Scan/NFO 消费 `episode_mapping`。历史 NFO 只读持久化 snapshot，不从当前 binding 推断过去。provider metadata 可用 mapping 的明确坐标查 catalog，不能反向更改 mapping。

## 6. 保留的 old DB read paths

- `backend/db/download_history.py:history_snapshot`：canonical snapshot 优先；为空才调用 `backend/legacy/history.py`。
- `backend/db/identity.py`：仅 canonical binding 缺失的旧订阅行，经 `backend/legacy/subscription.py` 解读该行已存 provider IDs。
- `backend/db/torrents.py`：旧 replacement plan 经 `backend/legacy/torrent.py` 提供 partial snapshot view。
- 旧 JSON 导入与显式 maintenance backfill 继续支持既存事实。

legacy adapters 不联网、不查当前订阅、不写回；consumer 获得 canonical/partial representation。

## 7. Preview API 清理

PreviewSession schema 升至 v2，context 明确包含 `resource_identity/resource_resolution`（unresolved 时 identity 可为 null，resolution 仍明确记录原因，字段不能缺失）。公开响应只包含轻量 canonical context、`episode_catalog`、候选、路径/UI 信息。删除 `episode_data`、legacy `index`（改 `episode_match_source`）、raw provider 搜索 blocks/map entries、generic `tmdb_id/tvdb_id` 与顶层 `torrent_path`。文件 server path 仍是合法业务字段。TMDB movie ID 与 TV series ID 分开。

旧 v1/unsupported preview 返回 409 `preview_schema_outdated`，要求重新 preview；损坏的 v2 context 不恢复旧 ID。

## 8. Download API 清理

请求以 `preview_id/preview_revision`、`file_id/episode_mapping`、上传字幕与明确业务选项为中心。server 从 preview snapshot 恢复 provider catalogs、路径、资源身份与 revision。删除旧 `preview_data/episode_data`、flat `bangumi_ep_id/bangumi_sort`、provider season/episode mirrors 及 client-supplied raw metadata。严格 Pydantic extra-forbid 覆盖请求、文件、mapping 与嵌套 provider refs，不能通过 alias 偷渡。

旧下载入口保留 URL，但没有 preview_id 的旧 body 明确返回 409，要求重新 preview。

## 9. Frontend aliases 清理

前端 types/matcher/normalizers/UI 使用 ResourceIdentity、ResourceResolution、ResourceCandidate、EpisodeCatalog、EpisodeMapping。删除 `episode_data`、`index`、raw provider block、`epNum/tmdbId/tvdbId/raw_sort` 等 wire compatibility alternatives，以及从旧 `season/episode` 恢复 canonical parsed 值的分支。订阅 UI 从 resource_identity 读 provider ID；history UI 用显式 override 名字。目录读取使用 canonical catalogs API。

TypeScript 编译及静态契约测试阻止旧字段重新被使用。

## 10. 删除的 production compatibility adapters

删除 `backend/services/nfo/episode_compat.py`，移除旧 `write_episode_files` 导出。删除 production `legacy_batch_episode_mapping`、`legacy_download_episode_mapping`、`bind_legacy_episode_ids`、`mapping_to_legacy_batch_episode`、`download_entry_with_mapping`、`seed_preview_metadata`、`private_nfo_context`。`generate_metadata` 改为明确的 keyword-only episode_mapping 输入，不再接受旧 flat episode signature。

## 11. 隔离/命名后的边界

旧 history adapter 从 domain persistence 移至 `backend/legacy/history.py`；旧 subscription 与 replacement plan adapter 同目录。history NDJSON stream 的前端消费迁到 v2，旧 stream 由显式 v1 adapter 保留 override 别名。external v1 subscription DTO 位于 `backend/api/external_api_v1_adapter.py`。历史 fixture conversion 位于 `tests/legacy_helpers.py`。维护导出投影仅在 `scripts/export_legacy_json.py`。

合法 provider boundary 明确命名 `provider_binding_identity`、`provider_catalog_context`、`provider_episode_metadata`、`seed_provider_catalogs`，不再以模糊 legacy/compat 名字混入业务。

## 12. Provider raw 的范围

provider clients 和 acquisition/normalization adapters 内仍可使用 `epNum/airDate/still_path/tmdbId/tvdbId` 等实际响应字段。request-local provider cache 与 canonical mapping/candidate/catalog 转换在明确边界完成；公开 canonical catalogs 和 frontend 不消费 provider raw。没有重写 provider clients 或 metadata policy。

monitor DB candidate 经 ResourceCandidate adapter；excluded candidate 被过滤，candidate evidence 继续独立于 confirmed ResourceIdentity。preview augmentation 保留候选证据。

## 13. 保留的用户配置

RSS offset、provider episode offset、TMDB/TVDB season、Bangumi sort range、用户指定 provider binding、subscription rules、历史 TMDB season/episode override、路径模板占位词均保留。路径模板仍可使用 `bangumi_sort/bangumi_ep/tmdb_episode/tvdb_episode` 词汇，值只从 canonical mapping 投影，避免修改路径语义。

## 14. Old history 的读取与再处理

已有 canonical/partial snapshot 直接读取。legacy-only 行仅用该行 Bangumi subject/sort、`tmdb_ep_calc/tvdb_ep` 构造 partial mapping；无法证明的 provider series/episode IDs 与 season 保持 null。可变人工 `tmdb_ep/tmdb_season` 不当作历史事实。

查看和 duplicate 查询继续支持旧行。旧 history 再生成 NFO 若 provider 身份不充分，返回 409 `legacy_history_unresolved`；不会拿当前订阅 binding 补齐。人工编号 override 仅作用于此次处理用的 mapping copy，不改持久化 snapshot。

## 15. Legacy-only 数量与测试

没有读取用户运行数据库，因此真实 legacy-only 数量未知，也没有伪称全量 canonical coverage。混合 DB 回归构造 2 条 legacy-only 行（成功/失败）与 1 条 canonical 行，验证 canonical 优先、旧事实可读、duplicate 状态正确；SQL listener 证明读阶段没有 INSERT/UPDATE/DELETE，并拦截网络和 current binding 查询。读取后旧 snapshot 仍为 NULL，计算镜像原值不变。另有 unresolved regeneration、partial snapshot 与旧 subscription/replacement 回归。

## 16. Migration/backfill 网络与 runtime 分离

保留 `scripts/backfill_persistence.py` 与确定性 `backend/db/persistence_migration.py:backfill`。backfill 使用已存事实，不调用 provider/search 网络；幂等与保守历史推断回归通过。runtime startup 使用 schema upgrade/旧数据导入，不调用 backfill。schema upgrade 不等于历史补全。

## 17. Static guards

`legacy_allowlist.json` 定义精确 token（不是 substring）及函数级业务例外。核心 services/API/domain 禁止 src_episode、merged_ep、bgm_sort、tmdb_ep、tvdb_ep、tmdb_ep_calc、episode_data、preview_data、episode_compat 与旧 projection 函数等。canonical `tmdb_episode_number` 不误报。

AST guard 同时检查 Import/ImportFrom：legacy 模块只允许 history/identity/torrents repository 与 explicit migration；禁止生产依赖 tests helper。另有 mirror assignment guard、legacy adapter I/O/current-binding import guard、maintenance-only backfill guard、frontend wire alias guard、实际 payload forbidden-key 与严格 schema rejection tests。

## 18. 保留的 DB columns

没有 DROP 或重建表。保留 subscription `tmdb_id/tvdb_id`，history `tmdb_ep_calc/tvdb_ep`，torrent operation `bangumi_sort`，用于旧事实读/显式维护导入。history `tmdb_ep/tmdb_season`、订阅 season/offset、Bangumi repository key、community mapping fields 与 candidate 表字段属于业务配置/证据存储，也保留。

## 19. 未来 DROP 候选与前提

可在独立 Phase 7.5 评估上述已停止新写的 mirror columns；当前不能直接宣称安全 DROP，因为 legacy-only 行、旧 operation、import/export/backfill 仍需它们。必须先备份、清点真实数据、保留无法完整 canonicalize 的事实并迁移 adapter/维护工具，证明无 runtime/外部/历史依赖，再执行 SQLite 专用 migration 与恢复测试。配置字段与 candidate evidence 不属于无条件 DROP 候选。

## 20. 文件变更

下方为本阶段完整工作树列表（M 修改、A 新增、D 删除），所有路径相对项目根目录。

- `M backend/api/models.py`
- `M backend/api/routes_history.py`
- `M backend/api/routes_resources.py`
- `M backend/api/routes_rss.py`
- `M backend/api/routes_torrent.py`
- `M backend/data/__init__.py`
- `M backend/db/download_history.py`
- `M backend/db/identity.py`
- `M backend/db/legacy_data.py`
- `M backend/db/persistence_migration.py`
- `M backend/db/torrents.py`
- `M backend/domain/README.md`
- `M backend/domain/episode.py`
- `M backend/domain/episode_adapters.py`
- `M backend/domain/episode_metadata_adapters.py`
- `M backend/domain/persistence.py`
- `M backend/domain/preview.py`
- `M backend/domain/resource_adapters.py`
- `M backend/services/batch_episode_mapper.py`
- `M backend/services/bd_replacement.py`
- `M backend/services/downloader.py`
- `M backend/services/enrich.py`
- `M backend/services/episode_metadata_resolver.py`
- `M backend/services/nfo/__init__.py`
- `D backend/services/nfo/episode_compat.py`
- `M backend/services/nfo/generator.py`
- `M backend/services/nfo/metadata_builder.py`
- `M backend/services/nfo/plot_fallback.py`
- `M backend/services/rss_episode_matcher.py`
- `M backend/services/torrent/batch_service.py`
- `M backend/services/torrent/metadata.py`
- `M backend/services/torrent/monitor.py`
- `M backend/services/torrent/preview.py`
- `M backend/services/torrent/preview_session.py`
- `M backend/services/torrent/preview_view.py`
- `M backend/services/torrent/search.py`
- `M backend/services/tvdb.py`
- `M frontend/src/api/rssApi.ts`
- `M frontend/src/api/torrentApi.ts`
- `M frontend/src/components/rss/DownloadHistoryDialog.tsx`
- `M frontend/src/components/rss/EpisodeTable.tsx`
- `M frontend/src/components/rss/SubscriptionCard.tsx`
- `M frontend/src/components/rss/TmdbSearchDialog.tsx`
- `M frontend/src/components/torrent/InfoCards.tsx`
- `M frontend/src/components/torrent/MatchTable.tsx`
- `M frontend/src/components/torrent/TorrentPreview.tsx`
- `M frontend/src/hooks/useMatchOverrides.ts`
- `M frontend/src/lib/episodeAdapters.ts`
- `M frontend/src/lib/episodeMatching.test.mjs`
- `M frontend/src/lib/matchUtils.ts`
- `M frontend/src/types/episode.ts`
- `M frontend/src/types/matchTable.ts`
- `M frontend/src/types/preview.ts`
- `M scripts/export_legacy_json.py`
- `M tests/test_batch_scan_canonical.py`
- `M tests/test_episode_domain.py`
- `M tests/test_episode_metadata.py`
- `M tests/test_episode_submission.py`
- `M tests/test_metadata_context.py`
- `M tests/test_nfo_translation.py`
- `M tests/test_persistence_canonical.py`
- `M tests/test_preview_sessions.py`
- `M tests/test_resource_identity.py`
- `M tests/test_resource_recognition_flows.py`
- `M tests/test_rss_episode_canonical.py`
- `M tests/test_rss_nfo_generation.py`
- `M tests/test_torrent_subtitles.py`
- `A PHASE7_LEGACY_AUDIT.md`
- `A PHASE7_LEGACY_INVENTORY.json`
- `A PHASE7_REPORT.md`
- `A backend/api/external_api_v1_adapter.py`
- `A backend/legacy/__init__.py`
- `A backend/legacy/history.py`
- `A backend/legacy/subscription.py`
- `A backend/legacy/torrent.py`
- `A legacy_allowlist.json`
- `A tests/legacy_helpers.py`
- `A tests/test_legacy_contract_cleanup.py`

## 21. 验证结果

- `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`：254 tests，253 passed，1 skipped。skip 为原有 HTTP client manager 本地监听 socket 测试，受当前 sandbox 环境限制。
- Phase 7 新增 21 项 contract/guard/old-mixed DB 回归；原 Phase 1–6 测试迁 canonical fixture/调用签名，保留资源歧义、电影/TV、special season 0、编号/元数据/NFO、snapshot immutable/revision/backfill idempotence 等断言。
- `cd frontend && npm test`：episode/multi-series matching 与字幕测试通过。
- `cd frontend && npm run build`：TypeScript 与 Vite production build 通过。
- `git diff --check`：通过。

Vite 原有非阻断警告仍有 config native-loader 的 __dirname 和约 752 kB JS chunk 超过 500 kB。未做生产 provider 网络、真实 qBittorrent/Jellyfin 或用户数据库破坏性验证。

## 22. 顺带修复的 bug

旧 RSS TMDB search/seasons endpoints 相对 import `.clients` 修正为 `..clients`；movie preview 不再把 movie ID 放到 series ID；损坏 v2 context 明确拒绝；preview augment 不丢候选证据；monitor NFO 转 canonical writer 时保留 Bangumi original_title，避免输出漂移。取消旧 raw schema 的 season inference 与 UI provider fallback，防止错误身份/编号来源回流。

## 23. Phase 7.5 是否需要

核心 runtime 目标已达到，不需要先 DROP 才使用本次结果。物理 schema 清理是可选独立维护阶段，需要针对真实旧数据与回滚策略评估；本阶段保持旧历史长期可读。

## 24. 最终 canonical data flow

```text
Provider raw / DB candidate evidence
  → provider/candidate adapter
  → ResourceCandidate → ResourceResolution → ResourceIdentity
  → canonical current binding persistence (revision/provenance)
  → EpisodeCatalog → EpisodeMapping
  → immutable EpisodeMappingSnapshot (history / preview / completion)
  → EpisodeMetadataCandidates → EpisodeMetadataResolver → ResolvedEpisode
  → NFO / path / processing

Legacy DB row
  → repository boundary → read-only legacy adapter
  → canonical / partial / unresolved view
  → history viewer / duplicate query

Explicit maintenance only
  → stored-fact backfill / import-export
```

当前 binding 是现在的身份；snapshot 是过去的事实；候选是证据。三者不互相替代。新 preview/RSS 用最新 binding，旧 preview/history 保持自身 snapshot；生命周期与 NFO metadata policy 保持既有语义。
