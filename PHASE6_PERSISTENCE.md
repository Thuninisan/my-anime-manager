# Phase 6 persistence audit and design

Audit performed before schema edits. Column inventory is defined in `backend/db/models.py`;
this ledger groups every column by its actual use, rather than treating generic IDs as canonical.

| Table | Fields / class | Writer → reader | Lifecycle / permanence |
|---|---|---|---|
| resources | source/source_id/title/URLs/descriptions (metadata/input); hash/size/torrent_path/status/error/timestamps (processing); index_type (configuration) | collector/details → monitor, resources API, preview | Permanent collected torrent, **not a work binding** |
| resource_sources | name/rss_url/download_tag/download_attribute/index_type (configuration) | source settings → collector | Permanent configuration |
| resource_torrent_files | resource_id/position/name (processing) | collector → resource API/preview | Permanent file inventory |
| resource_recognitions | resource_id/status/error (processing) | recognize → resource API | Regenerable candidate computation |
| resource_bangumi_candidates | bangumi_id/index_id/index_season/media_type (candidate identity), decision/reason/match_count (candidate provenance) | recognize → grouped resource API | Permanent candidates, never authoritative bindings |
| rss_subscriptions | bangumi_id/tmdb_id/tvdb_id (legacy work identity); bgm_season/sort range, provider season/offset (configuration); name/series_name/bgm names/rating/date (metadata); download_path/active/timestamps (configuration/processing) | subscription create/edit/augment → RSS, upload, history, paths | Permanent confirmed subscription binding |
| rss_subscription_feeds | bangumi_id/kind/rss_url/subgroup ID/name/offset (configuration) | subscription writes → polling | Permanent configuration |
| rss_subscription_feed_rules | bangumi_id/kind/rule_type/position/value (configuration) | subscription writes → filtering | Permanent configuration |
| bangumi_mappings_v2 | bangumi_id/tmdb_id/tvdb_id/anidb_id/mikan_id (provider hints), seasons (numbering hints), names (metadata) | import/community refresh/manual override → resolver/augment | Permanent hints, not a second subscription authority |
| bangumi_mapping_overrides | bangumi_id/field/text_value/int_value (user hint overrides) | mapping edit → dataset refresh | Permanent user configuration |
| download_episodes | bangumi_id/episode_number (Bangumi subject/sort); tmdb_ep/tmdb_season (user overrides); tvdb_ep/tmdb_ep_calc (legacy computed episode); RSS/guid/source/date/hash/at/fail_count/status (processing) | mark_downloaded/manual/failure/override → duplicate checks/history/NFO | Permanent latest episode record; downloaded means submitted, **not qBittorrent completion** |
| torrent_cards | hash/name/status/timestamps (processing), show/rating/poster (display metadata), encoding/codec (input), processing_mode/replace_bangumi_id/processing_present (processing) | download route → monitor/cards | Permanent card; processing plan removed at finish |
| torrent_card_bangumi | card_id/position/bangumi_id (legacy identity/display association) | card save → card API | Permanent |
| torrent_card_operations | card_id/position/source/torrent/target path/action (processing), bangumi_sort (legacy episode) | processing plan → monitor/replacement | Temporary until finish; paths must be archived before cleanup |
| torrent_preview_sessions | id/schema/revision/hash/name/context_json/timestamps/expiry (self-contained identity/catalog/metadata snapshot) | preview service → confirmation/download | Disposable; immutable across external binding changes, schema mismatch requires re-preview |
| structured_nodes | owner/root/parent/key/position/type/value (extension storage) | repositories → repositories | Owner lifecycle: subscription extras, mapping extras, recognition title snapshot, history document metadata, torrent processing/file extras and subtitle checkpoints |
| legacy_imports | name/imported_at (migration bookkeeping) | repository imports → imports | Permanent idempotence marker |

## Decisions

Confirmed RSS work bindings are owned by `rss_subscriptions`: canonical identity JSON,
source, UTC updated time, schema version and revision are nullable additive fields.
Provider columns remain compatibility mirrors; season/offset are user configuration.
Collected resources and community mapping hints do not become confirmed identities.
Torrent selections remain self-contained preview snapshots; completed cards retain per-file
mapping/resource identity and path snapshots independently of RSS bindings.

ResourceIdentity v1 preserves explicit TV/movie IDs. Identity revision starts at 1 and
changes only with media type/provider IDs, never display title. One update service validates,
compares, timestamps and projects compatibility provider IDs. Future RSS jobs read fresh
subscription identity; request-local catalogs are rebuilt by provider ID, not reused from a
previous binding. Existing previews remain self-contained, including their catalogs;
new previews use current subscriptions as known bindings before catalog acquisition.

Episode history snapshot v1 stores ResourceIdentity (when known), EpisodeMapping and
provenance, without ResolvedEpisode metadata. Reads prefer snapshot, then a centralized
non-network legacy adapter. Legacy history's primary episode key is Bangumi sort, not ep.
Current subscriptions must never be consulted to manufacture historical provider IDs.
Canonical-to-legacy writes preserve user overrides separately from calculated numbers.
Binding updates do not modify history, paths, output NFO or previews.

Upgrade uses nullable SQLite ADD COLUMN with column inspection inside the startup transaction.
Backfill is explicit and idempotent, skips canonical rows, and only uses each row's own facts.
Legacy history cannot recover series IDs or TVDB season: these remain null. Snapshot
versions are permanent contracts; unsupported versions are errors, never silently guessed.
Phase 7 must retain legacy readers until unresolved data and external API clients are handled.

## Implemented consumer and compatibility policy

- Subscription create/edit/manual provider edit/enrichment writes converge on
  `update_resource_identity`. The subscription primary key is its subject; changing
  subject means creating a different subscription, not mutating its primary key.
- API subscription output exposes resource_identity, identity_source and revision.
- RSS acquisition and submission use the subscription identity, including explicit
  provider clears. Community data can no longer silently replace that binding.
- New torrent previews apply current subscribed bindings **before** acquiring
  catalogs. Canonical TVDB IDs drive catalog fetching; old mapping hints are not
  copied back over the confirmed binding. Existing previews do not consult current
  subscriptions at confirmation. Explicit preview augmentation edits only that
  preview, clears its subscription revision reference, and records user provenance.
- History repository reads the canonical mapping first and projects calculated
  legacy numbers from it. User TMDB numbering overrides remain separate configuration.
  Duplicate detection retains the existing subject/sort and source/date semantics.
- RSS successful submission stores mapping and actual rename result from the NFO
  orchestrator. Historical path lookup uses saved output directories. Explicit NFO
  regeneration uses the historical mapping; requested numbering overrides affect
  that output without mutating the stored mapping snapshot. Old unresolved rows
  retain the existing regeneration compatibility behavior.
- Torrent plans carry per-file canonical episode snapshots. Successful completion
  archives them and source/target/action before cleaning the temporary operations.
  Movies archive explicit movie ResourceIdentity, without manufacturing episode refs.
  BD plans also retain replaced RSS history; successful completion preserves that
  history and records final paths without the staging suffix.
- Resource monitor rows remain legacy candidate storage; repository output additionally
  adapts them into ResourceCandidate values. No candidate is promoted into a binding.
- No new full episode metadata is persisted. No columns or compatibility imports
  are removed by Phase 6; the earlier startup migration's existing behavior is retained.

## Migration and backfill operation

Schema upgrade happens at engine initialization, inside the existing SQLite startup
transaction. Existing JSON importers remain intact. The explicit backfill helper can
be run with `python -m scripts.backfill_persistence` after normal legacy data import.
It operates on the already imported SQLite rows, performs no provider requests, and
never overwrites a populated canonical field. Its UTC identity timestamp is the time
of backfill, not an invented original binding timestamp.

Synthetic database backfill statistics:

| Run | Identities written | Partial history written | Canonical skipped | Unresolved |
|---|---:|---:|---:|---:|
| First | 1 | 1 | 1 | 1 |
| Second | 0 | 0 | 3 | 1 |

No production/runtime dataset was backfilled during development. Old history rows
lack provider series IDs and TVDB season. Those facts cannot be reconstructed from
today's subscription; partial snapshots intentionally leave them null and identify
their source as legacy_unresolved. Failed rows are not presented as past downloads.

## Validation and Phase 7 prerequisites

Regression command: `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`.
Frontend commands: `npm test`, `npm run build`. Also run `git diff --check`.
New tests cover actual SQLite upgrade and repeatability, fresh schema, row preservation,
TV/movie validation, provenance/revision/no-op, all provider combinations, zero coordinates,
missing episode IDs, multi-series separation, canonical-first read, conservative backfill,
transaction rollback, RSS current identity, immutable preview confirmation, canonical TVDB
catalog acquisition, historical regeneration/path lookup, explicit overrides, candidate
adaptation, Torrent completion and BD history retention.

Additional bugs corrected: Bangumi season 0 was read as season 1; provider-only binding
patches discarded season/episode offsets; RSS community-ID fallbacks could override or
revive a cleared subscription binding; completed Torrent operation paths were discarded.

Phase 7 prerequisites: retain legacy calculated fields and provider mirrors until API
clients migrate; retain community mapping/override hint formats; keep the adapter for
historical rows whose missing provider facts cannot be safely recovered; document that
RSS downloaded status means successful submission while Torrent completed status means
finished media processing. Unsubscribed torrent selections have no mutable shared work
binding: their preview and completed per-file snapshots are the persistence owners for
that selection, not collected resource candidate rows. Supporting a shared editable
library-work entity would be a separate product/schema change.

Final verification: 233 backend tests discovered, 232 passed, 1 existing HTTP socket
integration test skipped because this sandbox cannot listen on a local socket.
The 19 new persistence tests all passed. Phase 1–5, preview, RSS, Batch/Scan,
resource identity and NFO regression fixtures remain in the full suite. Frontend
matching/subtitle tests passed and production build passed. Vite reported existing
native-config compatibility and chunk-size warnings. `git diff --check` passed.

Files added:
- PHASE6_PERSISTENCE.md
- backend/db/identity.py
- backend/db/persistence_migration.py
- backend/domain/persistence.py
- scripts/backfill_persistence.py
- tests/test_persistence_canonical.py

Files modified:
- backend/api/models.py
- backend/api/routes_rss.py
- backend/api/routes_torrent.py
- backend/data/__init__.py
- backend/db/connection.py
- backend/db/download_history.py
- backend/db/legacy_data.py
- backend/db/models.py
- backend/db/resource_recognitions.py
- backend/db/torrents.py
- backend/domain/preview.py
- backend/domain/resource_adapters.py
- backend/services/downloader.py
- backend/services/nfo/metadata_builder.py
- backend/services/torrent/monitor.py
- backend/services/torrent/preview.py
- backend/services/torrent/preview_session.py
