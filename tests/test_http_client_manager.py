import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend import config
from backend.utils.http_client import HTTPClientManager
from backend.utils import http_retry


class HTTPClientManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_client_and_closes_it(self):
        manager = HTTPClientManager()
        with patch("backend.utils.http_client.httpx.AsyncClient") as factory:
            factory.return_value.aclose = AsyncMock()
            first = manager.get_client()
            self.assertIs(first, manager.get_client())
            factory.assert_called_once()
            await manager.aclose()
            first.aclose.assert_awaited_once()

    async def test_proxy_configuration(self):
        manager = HTTPClientManager()
        with patch.object(config, "PROXY_HOST", "proxy.example"), patch.object(config, "PROXY_PORT", 8080), patch("backend.utils.http_client.httpx.AsyncClient") as factory:
            factory.return_value.aclose = AsyncMock()
            manager.get_client()
            factory.assert_called_once_with(proxy="http://proxy.example:8080", follow_redirects=True)
            await manager.aclose()

    async def test_retry_uses_same_client(self):
        manager = HTTPClientManager()
        request = httpx.Request("GET", "https://example.org")
        response = httpx.Response(200, request=request)
        with patch("backend.utils.http_client.httpx.AsyncClient") as factory, patch.object(http_retry, "http_client_manager", manager), patch.object(http_retry.asyncio, "sleep", new_callable=AsyncMock):
            factory.return_value.aclose = AsyncMock()
            client = manager.get_client()
            client.get = AsyncMock(side_effect=[httpx.ConnectError("transient"), response, response])
            self.assertIs(await http_retry.fetch_with_retry("https://example.org"), response)
            self.assertIs(await http_retry.fetch_with_retry("https://example.org"), response)
            factory.assert_called_once()
            self.assertEqual(client.get.await_count, 3)
            await manager.aclose()
