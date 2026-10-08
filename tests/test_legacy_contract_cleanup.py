"""Phase 7 guards and old/mixed SQLite contract regressions."""
import ast
import asyncio
import copy
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import event
from backend import data
from backend.db import connection, download_history, torrents
from backend.db.models import DownloadEpisode, Subscription, TorrentCardOperation
from backend.domain.preview import PreviewDownloadRequest, TorrentPreviewResponse
from backend.services.torrent import preview_session
from backend.services.torrent.preview_view import session_view
from tests import test_persistence_canonical as persistence_fixtures
from tests import test_preview_sessions as preview_fixtures

ROOT = Path(__file__).resolve().parents[1]


class StaticLegacyGuards(unittest.TestCase):
    def test_runtime_tokens_and_imports(self):
        policy = json.loads((ROOT / 'legacy_allowlist.json').read_text())
        forbidden = re.compile(r'\b(?:' + '|'.join(map(re.escape, policy['forbidden_tokens'])) + r')\b')
        failures = []
        for directory in policy['python_roots']:
            for path in (ROOT / directory).rglob('*.py'):
                relative = path.relative_to(ROOT).as_posix()
                allowed = policy['token_exceptions'].get(relative, {})
                source = path.read_text()
                scopes = [node for node in ast.walk(ast.parse(source))
                          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
                for match in forbidden.finditer(source):
                    token = match.group()
                    line = source.count('\n', 0, match.start()) + 1
                    permitted = allowed.get(token, {}).get('functions', [])
                    if not any(node.name in permitted and node.lineno <= line <= node.end_lineno for node in scopes):
                        failures.append(f'{relative}:{line}: {token}')
        for path in (ROOT / 'backend').rglob('*.py'):
            relative = path.relative_to(ROOT).as_posix()
            if relative.startswith('backend/legacy/'):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = [node.module or ''] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]
                    if any('legacy' in module.split('.') for module in modules):
                        if relative not in policy['legacy_import_boundaries']:
                            failures.append(f'{relative}:{node.lineno}: legacy import')
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('tests'):
                    failures.append(f'{relative}:{node.lineno}: fixture import')
        self.assertEqual(failures, [])

    def test_frontend_matcher_has_no_old_wire_aliases(self):
        forbidden = re.compile(r'\b(?:episode_data|preview_data|src_episode|merged_ep|bgm_sort|tmdb_ep|tvdb_ep|epNum|tmdbId|tvdbId|raw_sort|map_entries)\b')
        files = [* (ROOT / 'frontend/src/components/torrent').glob('*.tsx'),
                 ROOT / 'frontend/src/types/matchTable.ts', ROOT / 'frontend/src/lib/episodeAdapters.ts',
                 ROOT / 'frontend/src/lib/matchUtils.ts', ROOT / 'frontend/src/hooks/useMatchOverrides.ts']
        self.assertEqual([(str(p.relative_to(ROOT)), forbidden.findall(p.read_text())) for p in files
                          if forbidden.search(p.read_text())], [])

    def test_legacy_adapters_have_no_io_or_current_binding_imports(self):
        prohibited = ('httpx', 'requests', 'clients', 'services', 'db', 'socket', 'subprocess')
        for path in (ROOT / 'backend/legacy').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [node.module or ''] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                    for name in names:
                        self.assertFalse(set(name.split('.')) & set(prohibited), (path, name))

    def test_confirmed_identity_and_history_writers_do_not_assign_mirrors(self):
        mirrors={'tmdb_id','tvdb_id','tvdb_ep','tmdb_ep_calc'}
        for file in ('backend/db/identity.py','backend/domain/persistence.py','backend/data/__init__.py'):
            tree=ast.parse((ROOT/file).read_text())
            for node in ast.walk(tree):
                targets=node.targets if isinstance(node,ast.Assign) else [node.target] if isinstance(node,(ast.AnnAssign,ast.AugAssign)) else []
                for target in targets:
                    if isinstance(target,ast.Attribute):
                        self.assertNotIn(target.attr,mirrors,(file,node.lineno))

    def test_backfill_is_maintenance_only(self):
        for path in (ROOT / 'backend').rglob('*.py'):
            if path.name == 'persistence_migration.py':
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module and 'persistence_migration' in node.module:
                    self.assertNotIn('backfill', [a.name for a in node.names], str(path))


class CanonicalPreviewContractTests(unittest.TestCase):
    setUp = preview_fixtures.PreviewSessionTests.setUp
    tearDown = preview_fixtures.PreviewSessionTests.tearDown
    request = preview_fixtures.PreviewSessionTests.request

    def test_preview_and_restored_files_have_no_legacy_fields(self):
        view = session_view(self.row)
        TypeAdapter(TorrentPreviewResponse).validate_python(view, strict=True)
        forbidden = {'episode_data','preview_data','index','tmdb_id','tvdb_id','bangumi_id',
                     'bangumi_ep_id','tmdb_season','tmdb_episode','tvdb_season','tvdb_episode',
                     'tmdb','bangumi','map_entries','context_json','episode_metadata'}
        self.assertFalse(forbidden & view.keys())
        for series in view['search_results'].values():
            self.assertFalse(forbidden & series.keys())
        restored = preview_session.restore_download_request(self.request())
        for file in restored['files']:
            self.assertFalse(forbidden & file.keys())
        self.assertNotIn('preview_data', restored)
        self.assertEqual(restored['files'][0]['episode_mapping'], self.mapping)

    def test_aliases_rejected_at_every_download_level(self):
        for key in ('preview_data','episode_data','bangumi_id','tmdb_season','index'):
            for level in ('body','file','mapping','provider'):
                body = self.request()
                target = body if level == 'body' else body['files'][0] if level == 'file' else body['files'][0]['mapping'] if level == 'mapping' else body['files'][0]['mapping']['tmdb']
                target[key] = 999
                with self.subTest(key=key, level=level), self.assertRaises(HTTPException) as raised:
                    preview_session.restore_download_request(body)
                self.assertEqual(raised.exception.status_code, 422)

    def test_v1_preview_and_old_download_require_repreview(self):
        from backend.db import preview_sessions
        from backend.api.routes_torrent import torrent_download
        preview_sessions.update(self.row.id, 1, schema_version=1)
        with self.assertRaises(HTTPException) as raised:
            preview_session.restore_download_request(self.request())
        self.assertEqual((raised.exception.status_code, raised.exception.detail), (409,'preview_schema_outdated'))
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(torrent_download({'torrent_path':'old.torrent','files':[]}))
        self.assertEqual(raised.exception.status_code, 409)

    def test_invalid_v2_context_does_not_restore_legacy_identifiers(self):
        from backend.db import preview_sessions
        invalid=copy.deepcopy(self.snapshot)
        del invalid['series_contexts']['A']['resource_identity']
        preview_sessions.update(self.row.id,1,context_json=json.dumps(invalid))
        with self.assertRaises(HTTPException) as raised:
            preview_session.load_preview_session(self.row.id)
        self.assertEqual((raised.exception.status_code,raised.exception.detail),(409,'preview_context_invalid'))
        preview_sessions.update(self.row.id,1,context_json='[]')
        with self.assertRaises(HTTPException) as raised:
            preview_session.load_preview_session(self.row.id)
        self.assertEqual(raised.exception.status_code,409)

    def test_movie_projection_keeps_movie_id_out_of_series_id(self):
        result=copy.deepcopy(self.result)
        result['search_results']['A']['media_type']='movie'
        result['provider_catalogs']={}
        row=preview_session.create_preview_session(result,str(self.source))
        view=session_view(row)
        TypeAdapter(TorrentPreviewResponse).validate_python(view,strict=True)
        entry=view['search_results']['A']
        self.assertIsNone(entry['tmdb_series_id'])
        self.assertEqual(entry['tmdb_movie_id'],1)
        self.assertIsNone(view['series'][0]['tmdb_series_id'])

    def test_python_wire_and_frontend_download_fields_agree(self):
        schema = TypeAdapter(PreviewDownloadRequest).json_schema()
        typescript = (ROOT / 'frontend/src/api/torrentApi.ts').read_text()
        for name in schema['properties']:
            self.assertRegex(typescript, rf'\b{re.escape(name)}\??:')
        for forbidden in ('preview_data','bangumi_ep_id','tmdb_episode','tvdb_episode'):
            self.assertNotIn(forbidden, schema['properties'])


class CanonicalPersistenceContractTests(unittest.TestCase):
    setUp = persistence_fixtures.PersistenceTests.setUp
    tearDown = persistence_fixtures.PersistenceTests.tearDown

    def test_new_subscription_binding_and_history_leave_mirrors_null(self):
        data.add_subscription('Show','feed',10,0,'')
        data.update_subscription(10, {'tmdb':{'id':20,'season':0,'ep_offset':5}, 'tvdb':{'id':30,'season':0}})
        sub = data.list_subscriptions()[0]
        data.mark_downloaded(10,0,'feed','guid','primary',episode_mapping=persistence_fixtures.mapping(),
                             resource_identity=sub['resource_identity'])
        with connection.new_session() as session:
            row = session.get(Subscription,10)
            self.assertEqual((row.tmdb_id,row.tvdb_id),(None,None))
            self.assertEqual((row.tmdb_season,row.tmdb_ep_offset),(0,5))
            history = session.get(DownloadEpisode,(10,0))
            self.assertEqual((history.tvdb_ep,history.tmdb_ep_calc),(None,None))
            self.assertIsNotNone(history.episode_mapping_snapshot)
        data.update_subscription(10, {'active':0})
        self.assertEqual(data.list_subscriptions()[0]['identity_revision'],sub['identity_revision'])

    def test_old_and_mixed_reads_are_readonly_without_current_identity(self):
        download_history.ensure_imported(data._HIST_FILE)
        with connection.new_session() as session, session.begin():
            session.add(DownloadEpisode(bangumi_id=10,episode_number=1,tmdb_ep_calc=7,tvdb_ep=0,status='downloaded'))
            session.add(DownloadEpisode(bangumi_id=10,episode_number=2,status='failed'))
        data.mark_downloaded(10,3,'feed','guid','primary',episode_mapping=persistence_fixtures.mapping(number=3))
        writes=[]
        def inspect_sql(conn,cursor,statement,parameters,context,executemany):
            if statement.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')):
                writes.append(statement)
        engine=connection.get_engine()
        event.listen(engine,'before_cursor_execute',inspect_sql)
        try:
            with patch.object(data,'list_subscriptions',side_effect=AssertionError('current binding forbidden')), \
                 patch('httpx.AsyncClient.request',side_effect=AssertionError('network forbidden')):
                result=data.get_all_episodes(10)
                self.assertIsNone(result['1']['episode_mapping_snapshot']['episode_mapping']['tmdb']['series_id'])
                self.assertEqual(result['1']['episode_mapping_snapshot']['episode_mapping']['tmdb']['episode_number'],7)
                self.assertEqual(result['3']['episode_mapping_snapshot']['source'],'canonical')
                self.assertTrue(data.is_downloaded(10,1))
                self.assertTrue(data.is_downloaded(10,3))
                self.assertFalse(data.is_downloaded(10,2))
        finally:
            event.remove(engine,'before_cursor_execute',inspect_sql)
        self.assertEqual(writes,[])
        with connection.new_session() as session:
            row=session.get(DownloadEpisode,(10,1))
            self.assertIsNone(row.episode_mapping_snapshot)
            self.assertEqual((row.tmdb_ep_calc,row.tvdb_ep),(7,0))

    def test_history_write_keeps_existing_legacy_calculated_values(self):
        download_history.ensure_imported(data._HIST_FILE)
        with connection.new_session() as session,session.begin():
            session.add(DownloadEpisode(bangumi_id=10,episode_number=0,status='downloaded',tvdb_ep=88,tmdb_ep_calc=77))
        data.mark_downloaded(10,0,'feed','guid','primary',episode_mapping=persistence_fixtures.mapping())
        with connection.new_session() as session:
            row=session.get(DownloadEpisode,(10,0))
            self.assertEqual((row.tvdb_ep,row.tmdb_ep_calc),(88,77))
        view=data.get_all_episodes(10)['0']
        self.assertNotIn('tvdb_ep',view)
        self.assertNotIn('tmdb_ep_calc',view)
        self.assertEqual(view['episode_mapping_snapshot']['episode_mapping']['tmdb']['episode_number'],0)

    def test_existing_mirrors_preserved_on_binding_edit(self):
        data.add_subscription('Show','feed',10,0,'')
        with connection.new_session() as session,session.begin():
            row=session.get(Subscription,10)
            row.tmdb_id=777
            row.tvdb_id=888
        data.update_subscription(10, {'tmdb':{'id':20}})
        with connection.new_session() as session:
            row=session.get(Subscription,10)
            self.assertEqual((row.tmdb_id,row.tvdb_id),(777,888))
        self.assertEqual(data.list_subscriptions()[0]['resource_identity']['tmdb_series_id'],20)

    def test_unresolved_history_regeneration_never_uses_current_binding(self):
        from backend.services import downloader
        data.add_subscription('Show','feed',10,0,'')
        data.update_subscription(10,{'tmdb':{'id':999}})
        download_history.ensure_imported(data._HIST_FILE)
        with connection.new_session() as session,session.begin():
            session.add(DownloadEpisode(bangumi_id=10,episode_number=1,status='downloaded'))
        with patch.object(downloader,'generate_metadata',AsyncMock()) as generate, \
             patch('httpx.AsyncClient.request',side_effect=AssertionError('network forbidden')):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(downloader.regen_episode_nfo(10,1))
            self.assertEqual(raised.exception.status_code,409)
            generate.assert_not_awaited()

    def test_torrent_completion_and_replacement_use_snapshot_without_sort_mirror(self):
        from backend.services.torrent.monitor import build_processing
        mapping=persistence_fixtures.mapping(number=0)
        context={'hardlink_root':'/library','torrent_name':'Show','replace_bangumi_id':10,'files':[
            {'torrent_path':'a.mkv','replacement_target':'Show/a.mkv','episode_mapping':mapping,
             'resource_identity':persistence_fixtures.resource_identity(bangumi_subject_id=10,tmdb_series_id=20,tvdb_series_id=30)}]}
        processing=build_processing(context)
        self.assertNotIn('bangumi_sort',processing['files'][0])
        torrents.save_torrent({'info_hash':'phase7','processing':processing})
        with connection.new_session() as session:
            self.assertIsNone(session.query(TorrentCardOperation).one().bangumi_sort)
        torrents.finish_torrent('phase7','completed')
        completed=torrents.get_torrent('phase7')['completion_snapshot']['files'][0]
        self.assertEqual(completed['episode_mapping_snapshot']['episode_mapping'],mapping)


class RssWireContractTests(unittest.TestCase):
    setUp = persistence_fixtures.PersistenceTests.setUp
    tearDown = persistence_fixtures.PersistenceTests.tearDown

    def test_v2_subscription_and_history_contracts_with_deprecated_v1_view(self):
        import httpx
        from fastapi import FastAPI
        from backend.api import routes_rss, routes_history
        data.add_subscription('Show','feed',10,0,'')
        data.update_subscription(10,{'tmdb':{'id':20,'season':0}})
        data.mark_downloaded(10,0,'feed','guid','manual',episode_mapping=persistence_fixtures.mapping())
        original=data.get_all_episodes(10)['0']['episode_mapping_snapshot']
        app=FastAPI()
        app.include_router(routes_rss.router)
        app.include_router(routes_history.router)
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                modern=await client.get('/api/rss/v2/subscriptions')
                old=await client.get('/api/rss/subscriptions')
                self.assertEqual((modern.status_code,old.status_code),(200,200))
                self.assertNotIn('id',modern.json()[0]['tmdb'])
                self.assertEqual(modern.json()[0]['resource_identity']['tmdb_series_id'],20)
                self.assertEqual(old.json()[0]['tmdb']['id'],20)
                history=(await client.get('/api/rss/subscriptions/10/history')).json()
                self.assertEqual(history['episodes'][0]['episode_mapping_snapshot'],original)
                self.assertNotIn('tmdb_ep',history['episodes'][0])
                from backend.api.external_api_v1_adapter import history_stream_v1
                stream=await routes_rss.subscription_history_stream(10)
                initial=json.loads(await anext(stream.body_iterator))
                self.assertNotIn('tmdb_ep',initial['episodes'][0])
                await stream.body_iterator.aclose()
                async def frame():
                    yield json.dumps(initial).encode()
                projected=json.loads(await anext(history_stream_v1(frame())))
                self.assertIn('tmdb_ep',projected['episodes'][0])
                response=await client.patch('/api/rss/v2/download-history/10/0',json={'tmdb_episode_override':8,'tmdb_season_override':0})
                self.assertEqual(response.status_code,200)
                self.assertEqual(data.get_all_episodes(10)['0']['episode_mapping_snapshot'],original)
                response=await client.patch('/api/rss/v2/download-history/10/0',json={'tmdb_ep':8})
                self.assertEqual(response.status_code,422)
        with patch.object(routes_rss.rss_poster,'poster_url',return_value=''):
            asyncio.run(run())

    def test_frontend_subscription_and_history_have_no_identity_aliases(self):
        source=(ROOT/'frontend/src/types/preview.ts').read_text()
        self.assertNotRegex(source,r'\b(?:epNum|tmdbId|tvdbId|tmdb_ep)\b')
        self.assertIn('tmdb_episode_override',source)
        self.assertIn('episode_mapping_snapshot',source)


class DowngradeExportTests(unittest.TestCase):
    def test_maintenance_projections_are_copies_of_current_and_historical_facts(self):
        from scripts.export_legacy_json import legacy_subscription_export, legacy_history_export
        identity=persistence_fixtures.resource_identity(bangumi_subject_id=10,tmdb_series_id=20)
        record={'resource_identity':identity,'tmdb':{'season':0}}
        old=legacy_subscription_export(record)
        self.assertEqual(old['tmdb']['id'],20)
        self.assertNotIn('id',record['tmdb'])
        snapshot=persistence_fixtures.episode_mapping_snapshot(persistence_fixtures.mapping(tmdb=999,number=7))
        history={'episodes':{'10':{'7':{'episode_mapping_snapshot':snapshot}}}}
        exported=legacy_history_export(history)
        self.assertEqual(exported['episodes']['10']['7']['tmdb_ep_calc'],7)
        self.assertNotIn('tmdb_ep_calc',history['episodes']['10']['7'])


class CatalogWireContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_payloads_stop_before_public_catalog_view(self):
        from backend.api.routes_torrent import tmdb_catalog_view, bangumi_catalog_view
        from unittest.mock import Mock
        detail=Mock()
        detail.json.return_value={'name':'Show'}
        raw={'0':{'name':'Specials','episodes':[{'epNum':0,'tmdbId':11,'name':'Episode','overview':'private','stillPath':'private.jpg'}]}}
        with patch('backend.services.tmdb.build_season_episode_map',AsyncMock(return_value=raw)), \
             patch('backend.clients.tmdb.get_tv_detail',AsyncMock(return_value=detail)):
            result=await tmdb_catalog_view(10)
        episode=result['seasons']['0']['episodes'][0]
        self.assertEqual((episode['episode_id'],episode['season_number'],episode['episode_number']),(11,0,0))
        for key in ('overview','stillPath','epNum','tmdbId'):
            self.assertNotIn(key,episode)
        with patch('backend.api.routes_torrent.torrent_bangumi_episodes',AsyncMock(return_value={
            'name':'Show','episodes':[{'id':21,'ep':2.5,'sort':3,'raw_sort':0}]})):
            result=await bangumi_catalog_view(20)
        episode=result['episodes'][0]
        self.assertEqual((episode['episode_number'],episode['episode_absolute']),(2.5,0))
        for key in ('id','ep','sort','raw_sort'):
            self.assertNotIn(key,episode)
