# Episode Domain：第一阶段

匹配流程现在是 `filename/path → ParsedEpisodeRef → EpisodeCatalog → Matcher → EpisodeMapping → MatchTable`。
`MatchRow.mapping` 是坐标的唯一来源，候选集、UI 标题和最终 episode metadata 分开保存。

## 字段映射

| 原字段 / Provider 字段 | Canonical 字段 | 语义 |
| --- | --- | --- |
| parser `season` / `src_season` | `parsed.season_number` | 原始解析季号 |
| parser `episode` / `src_episode` | `parsed.episode_number` | 原始解析集号 |
| `bgm_entry_id` / `bangumi_id` | `bangumi.subject_id` | Bangumi Subject ID |
| `bgm_ep_id` | `bangumi.episode_id` | Bangumi Episode object ID |
| Bangumi API `ep` | `bangumi.episode_number` | Bangumi ep，可为小数 |
| Bangumi API `sort` / `bgm_sort` | `bangumi.episode_absolute` | Bangumi sort，独立于 ep |
| TMDB series ID / episode `tmdbId` | `tmdb.series_id` / `tmdb.episode_id` | 分别是作品、单集实体 ID |
| `tmdb_season` / `tmdb_ep` / `epNum` | `tmdb.season_number` / `tmdb.episode_number` | TMDB 坐标 |
| TVDB series ID / episode `tvdbId` | `tvdb.series_id` / `tvdb.episode_id` | 分别是作品、单集实体 ID |
| `tvdb_season` / `tvdb_ep` / `epNum` | `tvdb.season_number` / `tvdb.episode_number` | TVDB 坐标 |
| merged `name` / `overview` | `EpisodeMetadata.title` / `.plot` | 最终单集标题、简介 |
| merged `airDate` / `air_date` | `EpisodeMetadata.air_date` | 首播日期 |
| merged `runtime` | `EpisodeMetadata.runtime_minutes` | 时长（分钟） |
| merged `stillPath` / `still_path` | `EpisodeMetadata.thumbnail_url` | 完整图片 URL；相对路径需调用方提供 thumbnail_base_url |
| merged `site_rating` / `voteAverage` / `vote_average` | `EpisodeMetadata.rating` | 保留既有 merged rating 优先级 |

前后端领域类型分别位于 `frontend/src/types/episode.ts` 和 `backend/domain/episode.py`。
Python 使用标准库 `TypedDict`，不增加依赖。`EpisodeMetadata`、`ResolvedEpisode` 和
`SeriesMetadata` 已定义；metadata sources 不明确时保持 null，不推测来源，不发起请求。

## 兼容边界

- Backend preview/search 继续输出旧 `episode_data`，供既有 NFO 等消费者使用；同时新增 snake_case
  `episode_catalog`。旧 `parsed_files.season/episode/parsed` 暂时保留，新的 `parsed_episode`
  是规范化的坐标；其中旧 `parsed` 是原始 parser 调试结果，不是新的 EpisodeMapping.parsed。
- `backend/domain/episode_adapters.py` 集中转换 provider 候选集及已经 merged 的 NFO metadata。
  `ep` 和原始 `sort` 都在 Bangumi fetch 边界保留下来；历史 sort→ep fallback 只留在兼容数据里。
- Frontend 上传预览、资源预览、手动 TMDB/Bangumi 获取均在 API adapter 处 normalize。
  UI 的 `parsed_files[].parsed` 只包含坐标，`episode_data` 的类型是 canonical `EpisodeCatalog`。
- `frontend/src/lib/episodeAdapters.ts` 将 canonical mapping 转回现有下载 payload，统一负责
  `bangumi_id/bangumi_ep_id/tmdb_season/tmdb_episode/tvdb_season/tvdb_episode`。
- 原始 provider 单集 metadata 暂存在 adapter 私有 WeakMap 中，并在提交 NFO preview data 时恢复。
  这样既有简介、图片路径、guestStars 角色等字段不会丢失，UI/domain 不暴露 provider aliases。
  这是当前临时 UI 生命周期的兼容桥；若以后将 canonical catalog 持久化或 JSON clone，需先迁移
  metadata resolver，不能依赖此缓存。
- `legacyBangumiAbsolute` 仅供旧 Bangumi-first 匹配策略继续执行既有 sort fallback；生成 mapping
  时始终使用真实 ep/sort，不能以 ep 填充缺失的 episode_absolute。

`src_episode/tmdb_ep/tvdb_ep/bgm_sort` 已从 MatchRow 和当前 matcher/UI 移除。
RSS、下载历史、NFO、batch/scan、资源采集仍有旧字段；它们属于后续迁移范围。
旧 API/search_results/map_entries 的 series lookup ID 结构仍兼容，不是新的 episode domain 类型。

本阶段没有改动匹配分数、候选顺序、RSS offset、数据库 schema、命名/path template、下载或
qBittorrent 行为，也没有新增 provider 请求。Season 0 和缺失 provider 的 null 坐标有回归测试。

## 验证

- `cd frontend && npm test`：Episode 匹配、真实 React override hook、字幕关联与兼容转换。
- `cd frontend && npm run build`：TypeScript project build 和 Vite 构建。
- `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`：包含 Episode domain/boundary 回归测试。

Phase 2 已迁移 `merged_ep → EpisodeMetadata → ResolvedEpisode → NFO writer`；详见下面的数据流和兼容边界。

## Phase 2：NFO metadata canonicalization

当前下载数据流：

```text
EpisodeCatalog + 手动覆盖后的 EpisodeMapping
  → download.episode_mapping + preview episode_data（临时 metadata bridge）
  → MetadataContext（复用 preview catalog；按 mapping 的 series/subject ID 获取缺少的数据）
  → metadata_candidates_from_catalogs（按 episode ID / 已确定坐标取数据）
  → EpisodeMetadataCandidates
  → resolve_nfo_episode / resolve_episode
  → ResolvedEpisode（mapping + metadata + 原子季集坐标 + provenance）
  → nfo_xml.generate_episode_nfo（只序列化）
```

`EpisodeMetadata` 表示单一 provider 候选，包含标题、原名、plot、播出日期、时长、评分、
票数、still path / 完整 URL、provider episode ID、导演、编剧、演员名和带角色的 guest stars。
`EpisodeMetadataCandidates` 分别保存 TMDB / TVDB / Bangumi 的可选候选。
`ResolvedEpisode` 不包含 raw provider payload；下载处理状态和图片下载结果仍由 orchestrator 保存。
`MetadataResolutionPolicy` 将 numbering、字段来源及中文/翻译策略独立于 `match_source` 表达。

实际 legacy episode policy（不是对未来来源的推测）：

| 字段 | 现有输出规则 |
| --- | --- |
| title | Bangumi 中文 → Bangumi 原名的有效译文 → TMDB 标题；TVDB 标题不参与 |
| original title | Bangumi 原名 → 最终 title |
| plot | 先使用 TMDB → TVDB 中的现有中文简介；否则依次 TMDB zh-CN → TVDB zho → Bangumi desc 译文 → TMDB/TVDB 非中文原文译文 → 初始简介译文；全部失败为空 |
| still | TMDB → TVDB；独立 provenance 决定 CDN |
| rating | TVDB → TMDB；null 才表示没有评分，0 保留（修复旧 truthy 吞零） |
| air date / runtime | TMDB；不擅自添加 TVDB/Bangumi fallback |
| numbering | 优先完整 TVDB 季集对，否则完整 TMDB 季集对；0 合法；不使用 parsed 坐标猜值 |
| guest stars | TMDB → TVDB；保留 name + character/role |
| directors / writers / vote count | TMDB |

翻译 provenance 使用 `bangumi:translated` / `tmdb:translated` / `tvdb:translated`。
未知或空结果的 provenance 为 null；数值 0 不被当作缺失 metadata。
XML 继续保留旧标签行为（包括零时长/未知时长输出为空、未评分不输出 rating 标签）。
原 writer 虽接收 actors/directors/writers/studios 参数，但没有输出对应 XML 标签；本阶段保留
canonical 数据，不为此新增 XML 标签。7 个重构前 writer golden fixtures 用于验证内容兼容。

原 `merged_ep` 字段审计：

- identity：Bangumi episode ID、TVDB episode ID，归入 mapping reference；provider metadata 也保留其原 episode ID。
- coordinates：TMDB/TVDB 季集号、Bangumi ep/sort，归入 EpisodeMapping，ep 和 sort 独立。
- metadata：name、overview、airDate/air_date、runtime、stillPath/still_path、site_rating/voteAverage、guestStars，归入候选。
- processing state：路径、file stem、缩略图下载结果、覆盖/计数，不进入 metadata domain。

仍保留的边界：

- preview/search `episode_data` 和旧下载字段保持兼容；新提交同时携带 `episode_mapping`。
- `episode_metadata_adapters.py` 负责旧 batch/download 输入、provider catalog 取值、preview cache 注入。
- 旧 API 的作品查找只在 adapter 执行，歧义时明确报错；canonical mapping 完全绕过该查找。
- 缺 episode ID 的旧调用按已经确定的坐标绑定 ID；canonical mapping 不被重新编号或覆盖。
- `episode_compat.py` 保留旧 NFO/file 调用签名；RSS、旧 batch/scan 和 monitor fallback 经此边界进入唯一 canonical XML writer。
- 旧 batch/scan 的上游准备和中文辅助函数仍保留，没有迁移 RSS offset、历史结构或数据库。

删除了主生成链路的 `merged_ep`、writer 调用中的 provider fallback、重复的 Bangumi episode ID
lookup，以及 `_find_tmdb_id` / `_find_tvdb_id`。修复 season 0、sort/episode 0 的 truthy 丢失，
且不再在多作品 torrent 中无条件取第一个 TMDB ID。

Frontend 私有 WeakMap 暂时保留并标记 TODO：merged preview catalog 的原始 metadata 经桥接恢复，
backend 注入本次 MetadataContext，避免为删除 cache 再请求完整 episode catalogs。
作品详情、图片和必要的 TVDB 中文补充依然可能需要请求，这些不是 episode catalog 的重复下载。
下一阶段应在 preview API 提供 canonical metadata/catalog references 后移除 WeakMap，并逐步迁移
旧 RSS/batch/scan 输入、历史记录和资源识别；不要通过删掉原始 metadata 的方式完成迁移。

API boundary 会把 canonical mapping 投影到旧处理字段，使旧 path/BD 流程也读取相同编号。
monitor 通过同一 adapter 获取 path 参数，显式保留 TVDB season 0 和 Bangumi ep/sort；
不会把 S00 媒体链接到另一季而与预生成 NFO 分离。没有修改持久化 history/database schema。

Phase 2 验证：新增 20 个 unittest 方法（含 provider 子组合和 7 个 XML golden 子场景）；
全量后端 167 tests，1 skipped；frontend tests 和 TypeScript/Vite build 通过；diff check 通过。
