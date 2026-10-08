# Phase 7 pre-edit audit

The exhaustive line inventory is PHASE7_LEGACY_INVENTORY.json. Reconstructed from the initially clean baseline commit; line numbers describe that baseline. Each line has a review category, enclosing function and disposition; broad hits are not all deprecated fields. Categories: A deletion, B runtime consumer migration, C old database boundary, D maintenance/migration, E provider adapter, F business configuration, G regression fixture. Broad words (index/fallback/IDs) are review signals, not forbidden tokens.

| Usage | Category | Decision |
|---|---|---|
| download_entry_with_mapping, legacy_download_episode_mapping | B → A | Migrate processing consumers to EpisodeMapping; retire legacy download input |
| mapping_to_legacy_batch_episode, legacy_batch_episode_mapping | B → A | Canonical batch/NFO entries; historical fixture conversions only in tests |
| bind_legacy_episode_ids | A | No runtime caller; remove |
| legacy_history_to_episode_mapping | C/D | Move to backend/legacy/history.py; repository and backfill only |
| history calculated mirrors tmdb_ep_calc/tvdb_ep | B/C | Stop new writes, keep old columns and conservative reads |
| subscription tmdb_id/tvdb_id columns | B/C | Stop writes, canonical identity owns current binding |
| subscription provider id inputs | F/B | Explicit binding edit configuration; adapt once in repository |
| tmdb_ep/tmdb_season history overrides | F | Retain user settings; never use as evidence of historical identity |
| RSS/provider seasons, offsets, bgm_sortrange | F | Retain numbering/filter configuration |
| community mapping IDs/seasons | E/F | Provider hints and user overrides; not confirmed identity |
| resource_bangumi_candidates | E | Candidate storage adapter, never a binding |
| epNum, airDate, stillPath etc | E | Provider normalization boundaries, migrate frontend API adaptation to server |
| episode_data frontend alias, old parser alternatives | B → A | Use episode_catalog only |
| private preview_data bridge | B → A | Pass immutable canonical snapshot |
| public search provider blocks/map_entries | B → A | Canonical series context and typed mapping hints |
| NFO episode_compat | B → A | Monitor uses canonical resolver and XML writer |
| legacy imports / JSON exports | C/D | Preserve historical import/export boundaries |
| results[0]/chain[0]/next(iter) | E/F/G | Verify uniqueness or display-only use; no first-result binding decisions |
| template placeholder sort/provider numbers | F | Preserve path configuration semantics |

No physical DROP, user-data clearing, provider lookup for backfill, or current binding substitution for old history is authorized by this phase.

## Final boundaries

See PHASE7_REPORT.md for all 24 requested delivery topics and verification. Deprecated v1 subscription DTOs and history stream override projection are isolated in backend/api/external_api_v1_adapter.py; repository-owned frontend uses v2 subscriptions and history stream. Provider catalogs have canonical public endpoints. History override names are explicit business fields. Unsupported v1 preview is rejected rather than adapted. No physical schema deletion or user-history rewrite occurred.
