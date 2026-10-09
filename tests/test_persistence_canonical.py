"""Real SQLite upgrades, immutable history, conservative backfill and consumers."""
import copy
import json
import tempfile
from backend.db.download_history import history_snapshot
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine

from backend import data
from backend.db import connection, legacy_data, torrents
from backend.db.models import Subscription, DownloadEpisode
from backend.db.identity import read_resource_identity, update_resource_identity
from backend.db.persistence_migration import upgrade, backfill
from backend.domain.resource import resource_identity
from backend.domain.episode import create_episode_mapping
from backend.domain.persistence import (episode_mapping_snapshot, load_episode_mapping_snapshot,
                                        write_history_snapshot)


def mapping(subject=10, tmdb=20, tvdb=30, number=0):
    return create_episode_mapping(
        {"season_number": 0, "episode_number": number},
        {"subject_id": subject, "episode_id": 100 if subject else None,
         "episode_number": number, "episode_absolute": number},
        {"series_id": tmdb, "episode_id": None, "season_number": 0, "episode_number": number},
        {"series_id": tvdb, "episode_id": 300 if tvdb else None, "season_number": 0, "episode_number": number})


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(connection, "DB_PATH", self.root / "db.sqlite3"),
                        patch.object(connection, "LEGACY_RESOURCE_DB", self.root / "missing.db"),
                        patch.object(data, "_SUBS_FILE", self.root / "subs.json"),
                        patch.object(data, "_HIST_FILE", self.root / "history.json"),
                        patch.object(torrents, "LEGACY_FILE", self.root / "torrents.json")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        if connection._engine is not None:
            connection._engine.dispose()
            connection._engine = None
            connection._engine_path = None
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_real_legacy_upgrade_twice_preserves_data_and_nullable_columns(self):
        engine = create_engine(f"sqlite:///{self.root / 'legacy.db'}")
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE rss_subscriptions (bangumi_id INTEGER PRIMARY KEY, name TEXT)")
            conn.exec_driver_sql("INSERT INTO rss_subscriptions VALUES (10, 'kept')")
            conn.exec_driver_sql("CREATE TABLE download_episodes (bangumi_id INTEGER, episode_number INTEGER)")
            conn.exec_driver_sql("INSERT INTO download_episodes VALUES (10, 0)")
            conn.exec_driver_sql("CREATE TABLE torrent_cards (id INTEGER PRIMARY KEY)")
            upgrade(conn)
            upgrade(conn)
            self.assertEqual(conn.exec_driver_sql("SELECT name, resource_identity_json, identity_revision FROM rss_subscriptions").one(), ('kept', None, None))
            self.assertEqual(conn.exec_driver_sql("SELECT episode_number, episode_mapping_snapshot FROM download_episodes").one(), (0, None))
            for table in ('rss_subscriptions', 'download_episodes', 'torrent_cards'):
                self.assertTrue(all(row[3] == 0 for row in conn.exec_driver_sql(f"PRAGMA table_info({table})") if row[1] in ('identity_revision', 'episode_mapping_snapshot', 'completion_snapshot_json')))
        engine.dispose()

    def test_fresh_schema_and_identity_revision_provenance(self):
        with connection.new_session() as session, session.begin():
            row = Subscription(bangumi_id=10, position=0)
            session.add(row)
            first = resource_identity(bangumi_subject_id=10, tmdb_series_id=20, tvdb_series_id=30)
            self.assertTrue(update_resource_identity(row, first, source='external_id'))
            self.assertEqual(row.identity_revision, 1)
            timestamp = row.identity_updated_at
            self.assertFalse(update_resource_identity(row, dict(first, canonical_title='New title'), source='explicit_user_mapping'))
            self.assertEqual(row.identity_source, 'external_id')
            self.assertEqual(timestamp, row.identity_updated_at)
            self.assertTrue(update_resource_identity(row, dict(first, tmdb_series_id=21), source='explicit_user_mapping'))
            self.assertEqual(row.identity_revision, 2)
            self.assertIsNone(row.tmdb_id)
            self.assertEqual(row.identity_schema_version, 1)
        with connection.new_session() as session:
            self.assertEqual(read_resource_identity(session.get(Subscription, 10))['tmdb_series_id'], 21)

    def test_tv_movie_single_provider_and_conflict(self):
        for identity in (resource_identity(bangumi_subject_id=10),
                         resource_identity(bangumi_subject_id=10, tvdb_series_id=30),
                         resource_identity('movie', bangumi_subject_id=10, tmdb_movie_id=20)):
            row = Subscription(bangumi_id=10)
            update_resource_identity(row, identity, source='explicit_user_mapping')
            self.assertEqual(read_resource_identity(row), identity)
        row = Subscription(bangumi_id=10)
        invalid = dict(resource_identity(bangumi_subject_id=10), tmdb_movie_id=1)
        with self.assertRaises(ValueError):
            update_resource_identity(row, invalid, source='explicit_user_mapping')

    def test_mapping_roundtrips_zero_partial_ids_and_multi_series(self):
        for subject, tid, vid in ((10,20,30), (None,20,None), (None,None,30), (10,None,None), (11,21,31)):
            original = mapping(subject,tid,vid)
            snapshot = episode_mapping_snapshot(original)
            self.assertEqual(load_episode_mapping_snapshot(json.dumps(snapshot))['episode_mapping'], original)
            snapshot['episode_mapping']['tmdb']['series_id'] = 999
            self.assertEqual(original['tmdb']['series_id'], tid)

    def test_canonical_priority_legacy_fallback_unknown_version(self):
        row = DownloadEpisode(bangumi_id=10, episode_number=0, tmdb_ep_calc=2, tvdb_ep=0)
        fallback = history_snapshot(row)['episode_mapping']
        self.assertEqual(fallback['bangumi']['episode_absolute'], 0)
        self.assertEqual(fallback['tvdb']['episode_number'], 0)
        self.assertIsNone(fallback['tmdb']['series_id'])
        row.tmdb_ep = 99  # user override is not a past identity
        original = mapping()
        write_history_snapshot(row, original)
        row.tvdb_ep = 98
        self.assertEqual(history_snapshot(row)['episode_mapping'], original)
        row.episode_mapping_schema_version = 99
        with self.assertRaises(ValueError):
            history_snapshot(row)
        invalid = DownloadEpisode(bangumi_id=-1, episode_number=-1, tmdb_ep_calc=-2)
        self.assertIsNone(history_snapshot(invalid)['episode_mapping']['bangumi']['subject_id'])

    def test_history_immutable_subscription_edit_and_legacy_dual_write(self):
        data.add_subscription('Show', 'feed', 10, 0, '')
        data.update_subscription(10, {'tmdb': {'id': 20, 'season': 0}, 'tvdb': {'id': 30, 'season': 0}, 'bgm': {'season': 0}})
        sub = data.list_subscriptions()[0]
        data.mark_downloaded(10, 0, 'feed', 'guid', 'primary', episode_mapping=mapping(),
                             resource_identity=sub['resource_identity'], identity_revision=sub['identity_revision'])
        before = data.get_all_episodes(10)['0']
        data.update_subscription(10, {'tmdb': {'id': 99, 'season': 0}})
        after = data.get_all_episodes(10)['0']
        self.assertEqual(before, after)
        self.assertEqual(after['episode_mapping_snapshot']['resource_identity']['tmdb_series_id'], 20)
        self.assertEqual(after['episode_mapping_snapshot']['episode_mapping']['tmdb']['episode_number'], 0)
        self.assertEqual(after['episode_mapping_snapshot']['episode_mapping']['tvdb']['episode_number'], 0)
        latest = data.list_subscriptions()[0]
        self.assertEqual(latest['resource_identity']['tmdb_series_id'], 99)
        self.assertEqual(latest['bgm']['season'], 0)
        revision = latest['identity_revision']
        data.update_subscription(10, {'name': 'Renamed', 'active': 0})
        self.assertEqual(data.list_subscriptions()[0]['identity_revision'], revision)
        data.update_subscription(10, {'tmdb': {'id': 0}})
        self.assertIsNone(data.list_subscriptions()[0]['resource_identity']['tmdb_series_id'])

    def test_backfill_partial_ambiguous_canonical_and_idempotent(self):
        with connection.new_session() as session, session.begin():
            session.add(Subscription(bangumi_id=10, position=0, tmdb_id=20))
            session.add(DownloadEpisode(bangumi_id=10, episode_number=0, tmdb_ep_calc=2, status='downloaded'))
            session.add(DownloadEpisode(bangumi_id=11, episode_number=1, status='failed'))
            row = DownloadEpisode(bangumi_id=12, episode_number=1, status='downloaded')
            write_history_snapshot(row, mapping(12, 40, 50))
            original = row.episode_mapping_snapshot
            session.add(row)
            session.flush()
            first = backfill(session)
            self.assertEqual(first, {'identities': 1, 'history_partial': 1, 'skipped_canonical': 1, 'unresolved': 1})
            self.assertEqual(row.episode_mapping_snapshot, original)
            second = backfill(session)
            self.assertEqual(second, {'identities': 0, 'history_partial': 0, 'skipped_canonical': 3, 'unresolved': 1})
            self.assertIsNone(history_snapshot(session.get(DownloadEpisode, (10,0)))['episode_mapping']['tmdb']['series_id'])

    def test_torrent_completion_archives_mapping_and_paths_immutably(self):
        snapshot = episode_mapping_snapshot(mapping())
        operation = {'action':'hardlink', 'torrent_path':'input.mkv', 'target_path':'/past/episode.mkv', 'episode_mapping_snapshot':snapshot}
        torrents.save_torrent({'info_hash':'abc', 'processing':{'files':[operation]}})
        torrents.finish_torrent('abc', 'completed')
        completed = torrents.get_torrent('abc')
        self.assertNotIn('processing', completed)
        self.assertEqual(completed['completion_snapshot']['files'][0], operation)
        torrents.finish_torrent('abc', 'completed')
        self.assertEqual(torrents.get_torrent('abc')['completion_snapshot'], completed['completion_snapshot'])

    def test_startup_upgrades_existing_database_with_real_rows(self):
        from backend.db.models import Base
        old_engine = create_engine(f"sqlite:///{connection.DB_PATH}")
        Base.metadata.create_all(old_engine)
        with old_engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO rss_subscriptions (bangumi_id, position, name, download_path, active, created_at, tmdb_id) VALUES (10, 0, 'legacy', '', 1, '', 20)")
            for table, fields in (('rss_subscriptions', ('resource_identity_json','identity_source','identity_updated_at','identity_revision','identity_schema_version')),
                                  ('download_episodes', ('episode_mapping_snapshot','episode_mapping_schema_version')),
                                  ('torrent_cards', ('completion_snapshot_json','completion_schema_version'))):
                for field in fields:
                    conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {field}")
        old_engine.dispose()
        with connection.new_session() as session:
            row = session.get(Subscription, 10)
            self.assertEqual(row.name, 'legacy')
            self.assertIsNone(row.resource_identity_json)
            self.assertEqual(read_resource_identity(row)['tmdb_series_id'], 20)

    def test_new_rss_uses_latest_identity_with_same_run_metadata_context(self):
        import asyncio
        from backend.services.rss_episode_matcher import subscription_episode_mapping
        from backend.services.nfo.metadata_context import MetadataContext
        from backend.domain.rss_episode import rss_episode_ref
        data.add_subscription('Show', 'feed', 10, 0, '')
        ctx = MetadataContext()
        ctx.get_bgm_episodes = AsyncMock(return_value=[])
        ctx.get_tmdb_season_map = AsyncMock(return_value={})
        ctx.get_tvdb_series = AsyncMock(return_value={})
        async def run():
            data.update_subscription(10, {'tmdb': {'id': 20, 'season': 0}})
            first = await subscription_episode_mapping(rss_episode_ref({}, ''), data.list_subscriptions()[0], 10, ctx, sort=0)
            data.update_subscription(10, {'tmdb': {'id': 99, 'season': 0}})
            second = await subscription_episode_mapping(rss_episode_ref({}, ''), data.list_subscriptions()[0], 10, ctx, sort=0)
            self.assertEqual(first['tmdb']['series_id'], 20)
            self.assertEqual(second['tmdb']['series_id'], 99)
            self.assertEqual(second['tmdb']['season_number'], 0)
        asyncio.run(run())
        self.assertEqual([call.args[0] for call in ctx.get_tmdb_season_map.call_args_list], [20,99])

    def test_preview_stays_self_contained_after_subscription_binding_update(self):
        from backend.services.torrent import preview_session
        data.add_subscription('Show', 'feed', 10, 0, '')
        data.update_subscription(10, {'tmdb': {'id': 20, 'season': 0}})
        sub = data.list_subscriptions()[0]
        source = self.root / 'source.torrent'
        source.write_bytes(b'input')
        def result(binding):
            tid = binding['resource_identity']['tmdb_series_id']
            return {'torrent_name':'Show', 'index':'tmdb',
                'parsed_files':[{'torrent_path':'a.mkv','file_name':'a.mkv','show_name':'Show','season':0,'episode':0}],
                'search_results':{'Show':{'tmdb':{'id':tid,'name':'Show'},'bangumi':{'id':10,'name':'Show'},
                    'resource_resolution':{'status':'resolved','identity':binding['resource_identity'],'candidates':[],'reason':'existing_mapping'},
                    'identity_revision':binding['identity_revision']}},
                'provider_catalogs':{'tmdb':{str(tid):{'0':{'name':'Specials','episodes':[{'tmdbId':1000,'epNum':0,'name':'Zero'}]}}},
                                'bangumi':{'10':{'name':'Show','episodes':[{'id':2000,'ep':0,'sort':0,'raw_sort':0,'name':'Zero'}]}}}}
        with patch.object(preview_session, 'PREVIEW_DIR', self.root / 'previews'):
            old_row = preview_session.create_preview_session(result(sub), str(source))
            _, old_snapshot = preview_session.load_preview_session(old_row.id)
            data.update_subscription(10, {'tmdb': {'id':99, 'season':0}})
            new_row = preview_session.create_preview_session(result(data.list_subscriptions()[0]), str(source))
            _, new_snapshot = preview_session.load_preview_session(new_row.id)
            self.assertEqual(old_snapshot['series_contexts']['Show']['candidates']['tmdb'][0]['provider_id'],20)
            self.assertEqual(new_snapshot['series_contexts']['Show']['candidates']['tmdb'][0]['provider_id'],99)
            self.assertEqual(preview_session.load_preview_session(old_row.id)[1], old_snapshot)
            episode = create_episode_mapping({'season_number':0,'episode_number':0}, bangumi={
                'subject_id':10,'episode_id':2000,'episode_number':0,'episode_absolute':0}, tmdb={
                'series_id':20,'episode_id':1000,'season_number':0,'episode_number':0}, match_source='tmdb')
            restored = preview_session.restore_download_request({'preview_id':old_row.id,'preview_revision':old_row.revision,
                'files':[{'file_id':old_snapshot['parsed_files'][0]['file_id'],'mapping':episode}]})
            self.assertEqual(restored['files'][0]['episode_mapping']['tmdb']['series_id'],20)

    def test_history_paths_and_regeneration_do_not_follow_current_binding(self):
        import asyncio
        from backend.services import downloader
        data.add_subscription('Show', 'feed', 10, 0, '')
        data.update_subscription(10, {'tmdb':{'id':20,'season':0},'tvdb':{'id':30,'season':0}})
        sub = data.list_subscriptions()[0]
        data.mark_downloaded(10, 0, 'feed', 'guid', 'primary', episode_mapping=mapping(),
            resource_identity=sub['resource_identity'], processing_result={
                'season_dir':'/old/Show/Season 0','show_dir':'/old/Show','target_path':'/old/Show/Season 0/zero.mkv'})
        data.update_subscription(10, {'tmdb':{'id':99,'season':2},'tvdb':{'id':88,'season':3}})
        _, season_dir, show_dir = downloader.resolve_episode_paths(10,0)
        self.assertEqual((season_dir,show_dir),('/old/Show/Season 0','/old/Show'))
        with patch.object(downloader, 'generate_metadata', AsyncMock(return_value=True)) as generate:
            asyncio.run(downloader.regen_episode_nfo(10,0))
            self.assertEqual(generate.call_args.kwargs['episode_mapping'],mapping())
            self.assertEqual(generate.call_args.kwargs['episode_mapping']['tmdb']['season_number'],0)

    def test_snapshot_mismatch_rolls_back_without_changing_history(self):
        data.mark_downloaded(10,0,'feed','old','manual')
        before = data.get_all_episodes(10)['0']
        with self.assertRaises(ValueError):
            data.mark_downloaded(10,0,'feed','new','primary',episode_mapping=mapping(),
                                 resource_identity=resource_identity(bangumi_subject_id=10,tmdb_series_id=99))
        self.assertEqual(data.get_all_episodes(10)['0'],before)

    def test_preview_catalog_discovers_candidates_without_using_old_identity(self):
        import asyncio
        from backend.services.torrent.preview import _fetch_provider_catalogs
        entry = {'tmdb':None,'bangumi':None,'identity_revision':2,
                 'resource_identity':resource_identity(tvdb_series_id=99),
                 'map_entries':[{'tvdb_id':30}]}
        with patch('backend.services.tvdb.fetch_tvdb_series_episodes', AsyncMock(return_value={'seasons':{}})) as fetch:
            result = asyncio.run(_fetch_provider_catalogs({'Show':entry}, []))
        fetch.assert_awaited_once_with(30)
        self.assertIn('30',result['tvdb'])
        self.assertNotIn('99',result['tvdb'])

    def test_monitor_candidates_remain_candidates(self):
        from backend.domain.resource_adapters import monitor_resource_candidates
        candidates = monitor_resource_candidates([
            {'bangumi_id':10,'index_id':20,'media_type':'TV'},
            {'bangumi_id':10,'index_id':20,'media_type':'TV'}], 'tmdb', title='Show')
        self.assertEqual(len(candidates),2)
        self.assertEqual({c['provider'] for c in candidates},{'bangumi','tmdb'})
        self.assertTrue(all(c['source']=='resource_monitor_candidate' for c in candidates))
        self.assertTrue(all('resource_identity' not in c for c in candidates))

    def test_bd_completion_archives_replaced_history_and_final_path(self):
        old = {'0':{'episode_mapping_snapshot':episode_mapping_snapshot(mapping())}}
        torrents.save_torrent({'info_hash':'bd','processing':{'replace_bangumi_id':10,
            'replaced_history':old,'files':[{'action':'hardlink','target_path':'/final/zero.mkv.mam-bd-pending',
                                           'episode_mapping_snapshot':episode_mapping_snapshot(mapping())}]}})
        torrents.finish_torrent('bd','completed')
        archived = torrents.get_torrent('bd')['completion_snapshot']
        self.assertEqual(archived['replaced_history'],old)
        self.assertEqual(archived['files'][0]['target_path'],'/final/zero.mkv')

    def test_explicit_regeneration_override_does_not_rewrite_snapshot(self):
        import asyncio
        from backend.services import downloader
        data.add_subscription('Show','feed',10,0,'')
        data.update_subscription(10, {'tmdb':{'id':20,'season':0},'tvdb':{'id':30,'season':0}})
        data.mark_downloaded(10,0,'feed','guid','primary',episode_mapping=mapping())
        original = data.get_all_episodes(10)['0']['episode_mapping_snapshot']
        data.set_episode_overrides(10,0,tmdb_ep=2,tmdb_season=0)
        with patch.object(downloader,'generate_metadata',AsyncMock(return_value=True)) as generate:
            asyncio.run(downloader.regen_episode_nfo(10,0))
            self.assertEqual(generate.call_args.kwargs['episode_mapping']['tmdb']['episode_number'],2)
        self.assertEqual(data.get_all_episodes(10)['0']['episode_mapping_snapshot'],original)

    def test_binding_edit_preserves_season_offsets_and_feed_configuration(self):
        data.add_subscription('Show','feed',10,0,'')
        data.update_subscription(10, {'tmdb':{'id':20,'season':0,'ep_offset':12},
                                     'tvdb':{'id':30,'season':0,'ep_offset':3}})
        data.set_subscription_rss_offset(10,'primary',0)
        data.update_subscription(10, {'tmdb':{'id':99}})
        sub = data.list_subscriptions()[0]
        self.assertEqual(sub['tmdb'],{'season':0,'ep_offset':12})
        self.assertEqual(sub['tvdb'],{'season':0,'ep_offset':3})
        self.assertEqual(sub['primary']['offset'],0)

    def test_snapshot_rejects_invalid_ids_and_coordinates(self):
        for key, value in (('series_id',0), ('episode_number',float('nan')), ('season_number',-1)):
            invalid = mapping()
            invalid['tmdb'][key] = value
            with self.assertRaises(ValueError):
                episode_mapping_snapshot(invalid)
