# Phase 4A audit

Batch: `build_preview` reads torrent contents → `parse_qbit_file_list` observations → show search / TMDB season map / Bangumi chain and episode cache → `find_target_entry` and `match_episode` → flat episode blocks → `execute_confirm` rename → `generate_metadata_collection` → bespoke plot lookup → compatibility NFO writer. Another active batch entry is `batch_nfo_generator`, already using the Phase 2 resolver, with a legacy ID-binding adapter.

Scan: `/scan` and watch enumerate top-level `.torrent` files → `process_torrent` → the above batch preview/confirmation. No media filesystem scanner, existing NFO identity lookup, directory season hints, history lookup, or resource recognition call exists. Observation is torrent path plus parsed member filenames. Series recognition belongs to existing preview search; episode matching follows its explicit TMDB coordinates and Bangumi chain. No new hint capability should be invented.

Identity fields: parser `season/episode/showName` are observations; `tmdb_season`, `tmdb_episode`, provider IDs and Bangumi ep/sort are provider coordinates; `season_number/episode_number` in old batch blocks are output numbering; `name/overview/air_date/runtime/rating` are metadata; old paths and rename plans are compatibility output. History fields are outside both lifecycles. Titles participate only in series recognition before episode mapping.

Problems: scan/watch call a one-argument function with three arguments and expect a boolean from a None-returning wrapper. Batch indexes catalog lists by episode minus one (fails for sparse catalogs and episode zero). Bangumi sort zero is replaced by ep. Episode plot fallback uses output episode number and show-level TMDB ID rather than matched coordinates. Legacy NFO compatibility reconstructs identity from output numbering and loses TMDB episode ID. Batch catalogs already live in preview locals but are not passed to plot lookup.

Ownership: one torrent preview/job owns the TMDB season map and Bangumi episode cache. Scan owns enumeration and each torrent owns its own batch context. No PreviewSession, history schema, provider client, or resource-recognition change is needed.

# Implemented boundaries

Batch preview now carries EpisodeMapping before confirmation/NFO, with TMDB series/episode ID and coordinates, Bangumi subject/episode ID plus independent ep/sort. Legacy flat batch rows pass the existing centralized domain adapter; explicit episode_mapping wins. Missing episode IDs stay missing; metadata lookup cannot bind identity. Provider series IDs are never inferred from metadata or title at normalization. The existing shared batch generator also no longer binds IDs back from metadata candidates.

Scan observation schema: torrent_path, file_path (torrent member), file_name, parsed_season_number, parsed_episode_number. The existing series-search/chain matching remains the recognition boundary. Scan resolver accepts its explicit matched input, copies mapping, and records parsed observations separately. It cannot infer identity from observation alone and raises unresolved_episode without a provider context. NFO/history/directory identity hints and ambiguity tests for those nonexistent capabilities are deliberately absent.

After: batch input → normalization → EpisodeMapping → provider candidates → shared resolve_nfo_episode → ResolvedEpisode → canonical XML writer. Scan enumeration → torrent preview recognition/matching → ScannedEpisodeRef + matched input → EpisodeMapping → same batch confirmation and metadata pipeline.

The older collection keeps its existing TMDB field selection, Chinese plot handling, original-title fallback and explicit batch output numbering. Output numbering overrides the resolved numbering for compatibility with rename plans; provider coordinates are never overwritten. The ordinary shared batch generator retains the established public metadata policy. No second fallback resolver was introduced.

Catalog requests: batch matching fetches the TMDB season map and Bangumi lists once per subject; preview carries those payloads into a new per-confirm MetadataContext. Episode Chinese fallback consumes the same context. Extra language catalogs, series details, translations and images remain allowed requests. Scan has no separate catalog fetch: each torrent has one batch context; no global scan cache or preview session lifecycle is introduced. Reuse is verified by a no-request mock assertion, not a live-provider benchmark.

Compatibility: mapping_to_legacy_batch_episode now projects all provider episode IDs and Bangumi ep as well as existing coordinates. Old rename plans, paths, subtitle operations, output naming and batch numbering remain. The original collection's no-TMDB metadata skip is retained to avoid changing existing NFO output semantics; Bangumi-only/TVDB-only normalization is supported, but that legacy collection does not invent a new numbering/fallback policy. Shared canonical NFO regression covers the current resolver's supported coordinates.

History and resource recognition: no reads or writes are added; neither is part of the actual scan/batch entry chain. No schema migration or permanent canonical table is introduced. Existing resource recognition remains untouched.

Additional fixes: exact epNum lookup replaces catalog list indexing; zero Bangumi sort survives matching/dropdown projection; episode plot requests use provider coordinates; scan/watch call the valid signature; process_torrent returns success/failure so failed jobs retain their torrent.

Phase 5 notes/TODO: existing series recognition still has historical first-result/first-chain-entry choices (including special handling); removing these safely belongs to resource-recognition policy work. No media-library scanner exists. Adding NFO/history hints would require a separate input lifecycle and explicit conflict policy. Legacy output numbering remains a collection compatibility override; full path/history canonicalization is deferred. The old show-detail code expects a dict while the client may return a response; its existing caught fallback is outside episode identity scope.
