"""External metadata failures are readable resource-preview API responses."""
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.clients.errors import MissingAPIKeyError


class ResourcePreviewErrorTests(unittest.TestCase):
    def request_with_error(self, error):
        path = Path('/tmp/resource-preview.torrent')
        with patch('backend.api.routes_resources.resources.get_resource', return_value={
                'torrent_path': str(path), 'source': 'example', 'source_id': '1'}), \
             patch('backend.api.routes_resources.resources.torrent_file_path', return_value=path), \
             patch('backend.api.routes_resources.Path.is_file', return_value=True), \
             patch('backend.services.torrent.preview.parse_and_search', AsyncMock(side_effect=error)), \
             patch('backend.services.torrent.preview_session.create_preview_session') as create:
            response = TestClient(app).post('/api/resources/1/torrent-preview')
            create.assert_not_called()
            return response

    def test_provider_failures_return_502_with_readable_details(self):
        for provider, label in (('tmdb', 'TMDB'), ('bangumi', 'Bangumi'), ('tvdb', 'TVDB')):
            with self.subTest(provider=provider):
                response = self.request_with_error(RuntimeError(f'preview_provider_fetch_failed: {provider}'))
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()['detail'], f'{label} 数据获取失败，请检查网络或代理设置后重试')

    def test_search_failure_returns_502_without_internal_error_code(self):
        response = self.request_with_error(RuntimeError('resource_provider_request_failed'))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['detail'], '外部元数据服务请求失败，请检查网络或代理设置后重试')

    def test_missing_credentials_keep_configuration_error(self):
        response = self.request_with_error(MissingAPIKeyError('TMDB_API_KEY'))
        self.assertEqual(response.status_code, 503)
        self.assertIn('TMDB_API_KEY', response.json()['detail'])

    def test_unrelated_runtime_errors_are_not_mislabeled_as_network_failures(self):
        with self.assertRaisesRegex(RuntimeError, 'unexpected preview bug'):
            self.request_with_error(RuntimeError('unexpected preview bug'))
