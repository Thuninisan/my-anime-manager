# Phase 5A — Resource recognition audit (before implementation)

## Actual call paths

- `routes_torrent.torrent_parse_and_search` → `torrent.preview.parse_and_search` → `_parallel_search` → `_search_tmdb_for_name` / `_search_bangumi_for_name` → `_organize_search_pairs` → `_fetch_episode_data` → `preview_session.build_snapshot`. Search selects raw first results, including a Bangumi original-title retry; backups are UI candidates. Movie/TV is detected before search. Snapshot selects the first TVDB mapping hint.
- Alternative release path: `preview.parse_and_search` → `torrent.search.search_by_tmdb` → `_search_tmdb_single` / `_search_tmdb_movie` → mapping lookup → `_fetch_all_episode_data`. Animation filter precedes first selection. Movie reverse mapping selects first Bangumi ID. Catalogs aggregate shows but search entries remain keyed by show.
- `routes_torrent` Batch preview → `torrent.batch_service.build_preview` → `tmdb.search_tv_show` → TMDB detail/catalog → Bangumi search → first subject → `bangumi.find_first_in_chain` → `build_bangumi_chain` / date fallback → `_find_entry_in_chain` → mapper. TMDB search uses year, exact aliases, then popularity. Batch re-searches TMDB after it already has an ID. S00 uses first chain entry. Batch chooses one majority show and applies it to all files: multi-series contamination risk.
- Scan routes → existing Batch preview/confirm. Scan is a torrent-file scanner, not a media scanner. Its identity risk is inherited from Batch.
- RSS creation/lazy enrichment → `enrich.enrich_subscription` → backtrack prequels → `_build_chain_ids` → saved Bangumi mappings or `_auto_infer_tmdb` / `_auto_infer_tvdb` → provider catalogs and existing episode-name matching → provider IDs. Single-subject and chain title fallbacks select first search results. Prequel/sequel traversal selects first matching relation. Existing mapped IDs already skip same-provider search.
- RSS download → `rss_episode_matcher.subscription_episode_mapping` → subscription provider IDs → provider catalogs → `build_episode_mapping` → common metadata resolver/NFO. This stage has no resource search; it needs an explicit canonical identity adapter.
- Resource routes/worker → `resource_monitor.recognize.recognize_resource` → title parsing → provider search → first candidate with reverse mapping → season/Bangumi episode comparison. Movie results remain candidates for manual confirmation. Recognition persistence is legacy and out of scope for schema migration.
- Preview augment → `augment_preview_session` fetches provider catalog but does not bind the selected ID to series context. Download restore can choose another series by submitted TMDB ID. Movie NFO chooses first movie context. These are identity boundary risks.

## Relationships and first items that are not resource identity

Episode `next` lookups by exact coordinates/ID, the first episode used to compute offsets, the first localized translation, display-card selection, and chronology-based season mapping are distinct from choosing a resource. Keep them within their existing responsibilities. `legacy_download_episode_mapping` rejects multiple provider IDs before its singleton extraction.

## Scope constraints / follow-up

Do not migrate resource recognition/history tables, alter episode schemas, NFO policy, or PreviewSession/RSS lifecycle. Provider clients retain raw responses. Legacy index names and display/path helpers remain boundary compatibility. External IDs currently come from mapping tables; no new provider API linkage is invented.

# Implementation report

## After refactoring

```
provider raw response → resource_adapters.provider_candidates
                     → ResourceResolver → ResourceResolution
                                       → ResourceIdentity
                                       → canonical-to-legacy context adapter
                                       → EpisodeCatalog → EpisodeMapping
                                       → existing metadata resolver / ResolvedEpisode / NFO
```

Torrent stores identities/resolutions per `series_contexts[show_key]`. Its search decisions precede episode catalog acquisition. RSS enrichment emits canonical identity/resolution, and the download adapter derives the authoritative identity from the current subscription provider fields (so edits cannot leave a stale cached identity). Batch preview retains its one-show contract, adds explicit identity, and no longer searches TMDB again after obtaining an ID. The common Batch/Scan episode adapter validates each row's own explicit provider bindings.

## Schemas

`ResourceCandidate`: provider (`bangumi|tmdb|tvdb`), positive integer provider_id, media_type (`tv|movie|special|unknown`), nullable title/original_title/year, alternative_titles, source. No score field: search ranking does not consume one. Existing RSS episode-link scores stay confined to its explicit compatibility rule.

`ResourceIdentity`: media_type (`tv|movie`), nullable canonical_title, nullable bangumi_subject_id/tmdb_series_id/tmdb_movie_id/tvdb_series_id. No episode coordinates. At least one positive provider ID is required; 0 and bool are invalid. TV/movie IDs cannot coexist. Legacy adapters alone translate old 0 sentinels to None. TVDB movies remain unsupported by canonical identity; the resource monitor's legacy movie records remain review candidates.

`ResourceResolution`: status (`resolved|ambiguous|unresolved`), nullable identity, canonical candidates, reason. Search compatibility projections raise `ambiguous_resource`; API failure remains an exception or the existing resource-monitor `failed` envelope, rather than a resolver's no-candidates outcome. Legacy enrichment returns None on fatal errors to retain its existing lifecycle; that is an outer compatibility facade, not the canonical resolver result.

## Actual precedence and compatibility

1. Valid explicit known identity.
2. Saved/confirmed canonical mapping.
3. Existing cross-provider mapping table linkage (adapted at the caller boundary before search).
4. Unique explicit Bangumi 主线 relationship with a mapped TV identity for a special subject.
5. Compatible media type (movie results cannot become TV identities).
6. Matching year, when supplied. As before, this is a preference when no candidate matches the year.
7. Exact NFKC/case/punctuation-normalized title, original title or alias.
8. Exactly one remaining candidate: `legacy_single_candidate_fallback`. Several remaining candidates are ambiguous; neither order nor popularity breaks the tie.

The resolver does not invent provider external-ID APIs. Current cross-provider linkage comes from the existing map. Provider adapters and orchestration retain animation filtering and alias extraction. Existing RSS episode-name inference is retained as `legacy_episode_name_link`; equal scores across different resources are ambiguous. Backtracked Bangumi root titles remain an explicit compatibility search source. Batch's chronology-based episode chain fallback remains episode compatibility, not a replacement TMDB identity. Legacy preview index labels, lightweight `tmdb` search objects, older v1 snapshot projections, subscription fields and outer service return contracts remain boundary compatibility.

## Removed implicit decisions / re-resolution

- Torrent TMDB/Bangumi first raw search result, movie Bangumi first result, original-title retry first result.
- Alternative release path's first animation result and movie's first reverse-mapped Bangumi ID.
- TMDB popularity winner after unresolved title/year matching.
- RSS TMDB/TVDB first search result in both single-subject and root-title fallbacks.
- First prequel/sequel relation in Bangumi, RSS chain traversal and Torrent sequel expansion. Multiple branches now require confirmation.
- Batch chain's default first subject and S00 main-subject default.
- First reverse-mapped index resource in resource-monitor recognition.
- PreviewSnapshot's first TVDB hint.
- Batch's second TMDB title search after obtaining the series ID.
- Frontend augment targeting the first search key in a multi-series preview.
- Movie NFO selecting the first of several movie contexts.

Singleton extraction after a cardinality check and exact episode coordinate lookups remain. First translation/first episode offset computations and display-only title/card choices are not resource decisions.

## Specials, relations and media types

A Bangumi relation is not identity equality. Unique prequel/sequel links establish traversal; branched links are ambiguous. Explicit 主线 plus an existing TV provider mapping may bind a special Bangumi subject to the main TMDB/TVDB TV series. The special's Bangumi subject ID is preserved, and season 0 stays in episode rules. Batch S00 binds Bangumi only via a saved `tmdb_season == 0` mapping; otherwise TMDB-only matching continues. Related Torrent sequel/OVA catalogs record their subject IDs in that show's context rather than becoming global implicit identities. Movie mapping uses the existing `tmdb_season == -1` discriminator where the legacy map shares numeric TMDB namespaces.

Existing file-count movie detection is retained as input compatibility (general preview <=2 files, alternative release ==1 file). This heuristic is not expanded into a new media classifier. Canonical TV/movie validation applies after the branch is chosen.

## Isolation and integration

- Torrent: each show has its own identity/resolution; snapshots validate canonical IDs against compatibility projections. Download recovery rejects references belonging to another show. Augment updates only the selected show's canonical binding and resolution/revision. Movie augmentation has a separate movie detail path. Known cross-provider mapping avoids Bangumi title search. Old v1 snapshots remain readable without a lifecycle/schema-version migration.
- Frontend: multi-show matching receives only that show's provider catalogs and explicit related subject IDs. Duplicate S/E coordinates are checked within a show. Manual augmentation requires an explicit target when several shows exist. Provider raw metadata stays backend-owned.
- RSS: enrichment emits identity/resolution; episode mapping uses a canonical subscription adapter and fetches catalogs by confirmed IDs, without title resource search. Existing manual subscription fields remain authoritative.
- Batch/Scan: Batch preview adds a canonical identity and refuses multi-show inputs before provider calls because its existing response/confirm structure supports one tvshow. The common episode adapter separately validates each row's provider binding, so canonical Batch/Scan rows for A/B remain independent. Scan remains a `.torrent` scanner.
- Mixed movie/TV or multiple-movie download requests are explicitly rejected because existing movie processing exposes one movie_meta/output context. This prevents first-movie contamination without rewriting processing/history/path contracts.

## Requests, files, validation and extra bugs

Batch removes one repeated TMDB title search and repeated detail request. Known TMDB→Bangumi mapping skips the Bangumi title search. Alias requests only run when local candidate resolution remains ambiguous. No network benchmark was run; these are verified control-flow reductions, not measured latency claims.

New files: `backend/domain/resource.py`, `backend/domain/resource_adapters.py`, `backend/services/resource_resolver.py`, this report, `tests/test_resource_identity.py`, `tests/test_resource_recognition_flows.py`.

Changed domain/services: preview domain; Bangumi/TMDB/enrich; RSS and Batch episode adapters; resource-monitor recognition; Torrent preview/search/batch/preview_session/preview_view/metadata. Changed frontend: torrentApi, InfoCards, matchUtils, SearchEntry types and episodeMatching regression tests. Existing preview-session and animation tests were extended/adapted.

Validation: backend full unittest suite 214 tests, 1 environment-dependent socket test skipped; Phase 1/2/2.5/3/4 regressions included. Existing NFO golden fixtures unchanged. Frontend tests passed including all three multi-series matcher modes and subtitle tests. Frontend production build passed. `git diff --check` passed. Build retains existing Vite config-loader and chunk-size warnings.

Additional bugs fixed: catalog aggregation could leak provider identities across shows; duplicate S/E checking wrongly rejected separate shows; augment fetched catalogs without binding identity; movie NFO discarded the singular Bangumi subject field; download recovery could select another context's TMDB ID; Batch could overwrite its known TMDB ID with a later search result. Preview file show keys now use the same year-stripped key as recognition.

## Before Phase 6 History/Persistence

No history/resource-table schema was changed. Permanent persistence must define how confirmed identity/provenance attaches to existing legacy rows and how edits invalidate cached bindings. Existing legacy resource-monitor candidate persistence does not store the new runtime resolution envelope. Old PreviewSession v1 contexts may lack canonical fields and use the explicit compatibility projection until expiration. These are Phase 6 design inputs, not silently performed migrations.

Separate retained capability boundaries: Batch's single-tvshow response and movie processing's single-movie context do not support mixed-resource execution. They now report explicit errors rather than cross-bind identities. Extending those contracts requires a separate scoped change. The legacy file-count movie heuristic and chronological chain fallback also remain follow-up TODOs; no fuzzy framework, provider-client rewrite, NFO-policy change or path-template redesign was introduced.

## Torrent preview file inventory (schema 4)

The public preview returns one `parsed_files` array for all torrent files. Each file has
`file_id`, `file_name`, `torrent_path`, `show_name`, `parsed_episode`, `type`, `category`,
`processing_status`, and `skip_reason`. `type` is video/subtitle/font/audio/other;
video `category` is regular/special. `processing_status` is automatic/manual/associate/ignored.
Filtered files remain in the inventory with a reason, and manual specials are not duplicated
as skipped files. The public response no longer exposes specials/subtitles/subtitle_files/skipped_files.
Only automatic videos enter episode matching; manual videos remain selectable, and associate
subtitles use the existing subtitle workflow. Downloads reject ignored files and unsupported types.
Schema 3 sessions require a fresh preview. Provider candidates and episode catalogs are unchanged.

## Torrent preview discovery and catalog loading

All torrent naming variants share the same discovery flow. Deduplicated show names
with at most two parsed files use TMDB movie search; larger groups use TV search.
TMDB searches run concurrently. The selected TV candidate discovers mapping links;
Bangumi and TVDB fall back independently when their IDs are missing. TVDB searches
use the parsed show name and retain the first series. Bangumi searches use the TMDB
Chinese title (or original title/parsed name when absent), retaining five distinct
results for TV and two non-TV results for movies, in provider order. Movies never
consult mapping or request TVDB. Mapping links are not subject to search limits.

All discovered directories are deduplicated by provider ID and loaded concurrently
across providers, with bounded concurrency per provider. Initial loading does not
expand Bangumi relation chains. Bangumi request starts, including retries and pages,
share the configured interval while responses can overlap. TVDB authentication is
serialized to avoid duplicate logins. Historical resource candidates remain available
but do not cause extra initial directory requests; manual selection loads them.
Provider request failures retain the existing explicit error behavior.
