# Phase 3: RSS Canonicalization

## Audited call chain

Before this change:

1. `rss.fetch_rss_snapshot -> _parse_rss`: anitopy parses guid/title into legacy `episode_number`; feed view adds tags and filtering state.
2. `downloader._process_subscription -> _fetch_passed_items`: publication/tag/exclusion filters; primary/backup offset yields `sort = RSS episode + offset`; dedup/source priority uses Bangumi sort.
3. `_download_item`: enrich missing subscription metadata, validate provider IDs and sort, handle replacement, download torrent bytes.
4. `submit_episode_torrent`: provider coordinates computed separately from subscription `season`/`ep_offset`; path formatting, paused qBittorrent add.
5. `metadata_builder.generate_metadata`: history TMDB overrides; legacy batch fields.
6. `generator.batch_nfo_generator`: legacy mapping adapter, fetch catalogs through MetadataContext, select candidates, bind missing identities, shared `resolve_nfo_episode -> resolve_episode`, assets and XML writer; returns paths for rename.
7. Resume torrent and record unchanged download-history schema.

`enrich_subscription` owns subscription ID/season discovery and offset inference. It may match first-episode names to discover subscription rules. `mapper.find_target_entry` belongs to file/chain mapping and is not called by this RSS download chain. `tmdb.build_season_episode_map` and `tvdb.fetch_tvdb_series_episodes` provide normalized legacy provider payloads. History upload uses `submit_episode_torrent`; history regeneration uses `regen_episode_nfo -> generate_metadata`. BD replacement validates recorded sort coverage and paths, then uses the existing Torrent processing lifecycle; it has no independent RSS metadata fallback to migrate.

## New chain and ownership

```text
feed parser -> RssEpisodeRef
  -> RSS normalization (raw number + subscription RSS offset)
  -> RSS matcher (logical Bangumi sort + subscription provider rules + history overrides + EpisodeCatalog)
  -> EpisodeMapping
  -> centralized mapping_to_legacy_batch_episode projection
  -> shared metadata_candidates_from_catalogs (legacy=False)
  -> EpisodeMetadataCandidates
  -> resolve_nfo_episode / resolve_episode / existing policy
  -> ResolvedEpisode -> existing XML writer and assets
  -> existing path/rename/resume/history lifecycle
```

`RssEpisodeRef` has `rss_episode_number: int | None`, `title: str`, `release_title: str | None`, and `parsed_show_name: str | None`. Missing/unparseable input is None; explicit zero remains zero. Public feed `episode_number` is retained as a view projection. Old feed snapshots adapt at `item_episode_ref`; their historical zero sentinel remains unknown.

`RssMappingContext` names the Bangumi subject, optional logical sort or RSS offset, and independent TMDB/TVDB provider rules (`series_id`, `season_number`, optional explicit `episode_number` or `episode_offset`). The matcher consumes canonical catalog fields only. Legacy subscription/history settings are interpreted by `subscription_episode_mapping`, the input adapter, before entering the pure matcher.

RSS offset applies only during input normalization. Selection/dedup requires logical sort before provider acquisition; the matcher accepts that already normalized coordinate. No offset or RSS-only number is put in EpisodeMapping, candidates, or NFO. Mapping `parsed.episode_number` is the normalized logical number, not raw feed numbering. Existing nested Bangumi domain coordinates remain `subject_id`, `episode_id`, `episode_number` (API ep), and `episode_absolute` (API sort); this phase does not rename the established Phase 1 model.

Bangumi lookup uses exact sort; ep never substitutes for missing sort. Missing ep remains None. Providers independently use subscription season and `logical sort + provider episode offset`, with explicit history TMDB season/episode overrides taking precedence. Season 0 and episode 0 are preserved. Exact season/number lookup binds provider IDs when present; missing IDs retain authoritative coordinates. Multi-season subscriptions and different provider season layouts stay independent. Match source remains None because these are RSS subscription rules, not a Torrent provider matching strategy. Numbering and metadata provenance are determined by the existing resolver policy.

One MetadataContext is owned by each `_process_subscription` invocation and shared across primary/backup items. Direct upload/regeneration creates its own context. It retains provider metadata and the latest normalized RSS EpisodeCatalog per subject for the job; it is neither persisted nor a PreviewSession. Canonical mappings are built before qBittorrent commit. NFO uses the same mapping and raw metadata caches. The full metadata objects retain images, guests, ratings and plots; the lightweight identity catalog is not substituted for metadata.

## Provider requests

| Stage | Requests and reuse |
| --- | --- |
| Subscription enrichment, if required | Existing Bangumi subject/relations and provider ID discovery. TMDB season maps use MetadataContext. TVDB auto-inference and offset inference now consume the same cached normalized series catalog as matcher/NFO. |
| RSS matching | Bangumi episodes, TMDB zh-CN season map, TVDB jpn series catalog, once per successful cache key in the context; fetched earlier enrichment data is reused. TMDB season-map acquisition may fetch series detail as before. |
| Metadata/NFO | Catalog getters hit the same context caches. Bangumi subject and TMDB zh-CN series detail may still be acquired. Existing single-episode TVDB translation, plot translation and image downloads remain. |
| Completion refresh | Existing post-success `_refresh_sortrange` explicitly invalidates the global Bangumi episode cache and requests a fresh list to protect airing subscription completion behavior. This intentional freshness request remains; it is not used to rebuild NFO identity. |

Removed duplicate TVDB catalog acquisition between subscription auto-inference/offset inference and NFO. RSS identity binding after metadata acquisition is eliminated. TMDB/Bangumi/NFO successful fetch reuse already existed in MetadataContext and is preserved/extended, not claimed as newly removed requests. Provider failures can still trigger existing retry behavior; successful-cache guarantees do not imply negative caching.

## Compatibility and remaining boundaries

- `mapping_to_legacy_batch_episode` is the existing centralized canonical-to-legacy projection; it carries the mapping so the generator bypasses legacy ID binding.
- `generate_metadata` retains its old callable signature for regeneration/direct callers. These enter the RSS matcher first; explicit TVDB coordinates are passed into the matcher, not recalculated after it.
- `format_download_path`, qBittorrent rename, history `tmdb_ep`/`tvdb_ep`, stored subscription provider rules, and `resolve_episode_paths` remain compatibility boundaries.
- Regeneration and history uploads preserve the existing historical TMDB override behavior. No database schema change.
- No RSS-specific episode metadata fallback remains on the migrated path. Existing series/season title/plot orchestration remains shared and unchanged in policy; those objects are not EpisodeMetadata.
- RSS NFO never re-matches titles/show names or discovers provider IDs. Name matching remains only in subscription enrichment before episode mappings are built.
- BD replacement service is unchanged. Existing coverage validation, source priority and Torrent canonical handling remain in place; full batch/scan migration is deferred.
- Bangumi-only matching/resolution is tested, but the existing download guard requiring TMDB or TVDB remains. This is deliberate behavioral compatibility.

## Additional fixes

1. `_auto_infer_tvdb` was called with `metadata_ctx` but did not accept it, breaking missing-ID inference. It now accepts/reuses the context.
2. Shared generator's season plot fallback assigned a string to `resolved`, clobbering ResolvedEpisode. The plot has its own variable.
3. RSS download rejected sort 0; offset inference confused Bangumi sort 0 with ep; season 0 skipped provider offset inference. Presence checks now preserve zero. Legacy global `_get_bangumi_ep_id` remains outside the new matcher and is not used by canonical RSS NFO.

## Validation and next phase

New RSS tests cover parser unknown/zero, signed offset, ep versus sort, missing ep/sort, provider subsets/IDs, equal/different coordinates, multi-season and season 0, manual history override, cache reuse, zero rating, and six Phase 2 XML goldens: regular, special, missing_tmdb, missing_tvdb, bangumi_only and manual_override. RSS generation additionally rejects any attempt to invoke legacy ID binding. Submission tests mock provider acquisition explicitly and continue to test paused-add failures, cleanup, history and source behavior without network calls.

Final validation: backend unittest discovery, frontend npm test, frontend production build and git diff --check. See the completion message for exact totals. Build retains existing Vite config-loader and bundle-size warnings.

Before batch/scan canonicalization: define their authoritative input-to-mapping rules, context ownership, missing coordinates and manual decisions, and remove their remaining legacy ID binding. No blocker requiring a database migration or PreviewSession exists for RSS. Path/history canonicalization remains separate work. Live provider/qBittorrent end-to-end behavior is not exercised by unit tests.
