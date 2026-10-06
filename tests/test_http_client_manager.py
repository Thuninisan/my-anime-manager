import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import httpcore
from httpcore._backends.mock import AsyncMockStream

from backend import config
from backend.utils.http_client import HTTPClientManager, _ProxyTransport, _proxy_url
from backend.utils import http_retry


class HTTPClientManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_proxy_tls_does_not_poison_following_requests(self):
        streams = []

        class ProxyStream(AsyncMockStream):
            async def start_tls(self, *args, **kwargs):
                if len(streams) <= 2:
                    raise httpcore.ConnectError("TLS failed after CONNECT")
                return self

        async def connect(*args, **kwargs):
            stream = ProxyStream([
                b"HTTP/1.1 200 Connection established\r\n\r\n",
                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok",
            ])
            streams.append(stream)
            return stream

        transport_factory = httpx.AsyncHTTPTransport

        def small_pool(**kwargs):
            return transport_factory(**kwargs, limits=httpx.Limits(max_connections=2))

        manager = HTTPClientManager()
        try:
            with patch.object(config, "PROXY_HOST", "proxy.example"), patch.object(config, "PROXY_PORT", 7890), patch("httpcore._backends.auto.AutoBackend.connect_tcp", side_effect=connect), patch("backend.utils.http_client.httpx.AsyncHTTPTransport", side_effect=small_pool):
                client = manager.get_client()
                for _ in range(2):
                    with self.assertRaises(httpx.ConnectError):
                        await client.get("https://destination.invalid/", timeout=.05)
                    self.assertTrue(streams[-1]._closed)
                response = await client.get("https://destination.invalid/", timeout=.05)
                self.assertEqual(response.text, "ok")
                self.assertTrue(all(stream._closed for stream in streams))
        finally:
            await manager.aclose()

    async def test_proxy_stream_lifetime_and_client_shutdown(self):
        transport = _ProxyTransport("http://proxy.example:7890")
        stream = AsyncMockStream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"])
        with patch("httpcore._backends.auto.AutoBackend.connect_tcp", new_callable=AsyncMock, return_value=stream):
            async with httpx.AsyncClient(transport=transport) as client:
                async with client.stream("GET", "http://destination.invalid/") as response:
                    self.assertEqual(await response.aread(), b"ok")
                self.assertTrue(stream._closed)
                self.assertFalse(transport._active)
        # An unconsumed response must also be closed when the client shuts down.
        stream = AsyncMockStream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"])
        with patch("httpcore._backends.auto.AutoBackend.connect_tcp", new_callable=AsyncMock, return_value=stream):
            async with httpx.AsyncClient(transport=transport) as client:
                response = await client.send(client.build_request("GET", "http://destination.invalid/"), stream=True)
                self.assertFalse(stream._closed)
            self.assertTrue(stream._closed)
            await response.aclose()
            self.assertFalse(transport._active)

    async def test_proxy_request_error_and_cancellation_close_transport(self):
        for error in (httpx.ConnectError("failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                transport = _ProxyTransport("http://proxy.example:7890")
                with patch("backend.utils.http_client.httpx.AsyncHTTPTransport") as factory:
                    factory.return_value.handle_async_request = AsyncMock(side_effect=error)
                    factory.return_value.aclose = AsyncMock()
                    with self.assertRaises(type(error)):
                        await transport.handle_async_request(httpx.Request("GET", "https://destination.invalid/"))
                    factory.return_value.aclose.assert_awaited_once()
                    self.assertFalse(transport._active)

    async def test_proxy_address_formats(self):
        cases = {
            "proxy.example": "http://proxy.example:7890",
            "  proxy.example  ": "http://proxy.example:7890",
            "proxy.example:8080": "http://proxy.example:8080",
            "http://proxy.example": "http://proxy.example:7890",
            "http://proxy.example:8080/": "http://proxy.example:8080",
            "https://proxy.example:80": "https://proxy.example:80",
            "http://user:password@proxy.example:8080": "http://user:password@proxy.example:8080",
            "::1": "http://[::1]:7890",
            "[::1]:8080": "http://[::1]:8080",
            "": None,
            "  ": None,
        }
        for host, expected in cases.items():
            with self.subTest(host=host), patch.object(config, "PROXY_HOST", host), patch.object(config, "PROXY_PORT", 7890):
                self.assertEqual(_proxy_url(), expected)

    async def test_invalid_proxy_fails_without_exposing_credentials(self):
        for host in ("http://", "socks5://proxy.example:1080", "proxy.example:bad", "http://user:secret@proxy.example/path"):
            with self.subTest(host=host), patch.object(config, "PROXY_HOST", host):
                with self.assertRaises(ValueError) as caught:
                    _proxy_url()
                self.assertNotIn(host, str(caught.exception))
                self.assertNotIn("secret", str(caught.exception))

    async def test_proxy_changes_select_new_client(self):
        manager = HTTPClientManager()
        with patch.object(config, "PROXY_HOST", "proxy.example"), patch.object(config, "PROXY_PORT", 8080):
            first = manager.get_client()
            with patch.object(config, "PROXY_PORT", 8081):
                self.assertIsNot(first, manager.get_client())
            with patch.object(config, "PROXY_HOST", ""):
                self.assertIsNot(first, manager.get_client())
            self.assertIs(first, manager.get_client())
        await manager.aclose()

    async def test_http_and_https_connect_to_configured_proxy(self):
        manager = HTTPClientManager()
        try:
            with patch.object(config, "PROXY_HOST", "http://proxy.example:8080"), patch.object(config, "PROXY_PORT", 1), patch.dict("os.environ", {"NO_PROXY": "*"}), patch("httpcore._backends.auto.AutoBackend.connect_tcp", new_callable=AsyncMock) as connect:
                connect.side_effect = httpcore.ConnectError("stop before opening socket")
                for url in ("http://destination.invalid/feed", "https://destination.invalid/image"):
                    with self.subTest(url=url):
                        with self.assertRaises(httpx.ConnectError):
                            await manager.get_client().get(url)
                        self.assertEqual(connect.call_args.kwargs["host"], "proxy.example")
                        self.assertEqual(connect.call_args.kwargs["port"], 8080)
                self.assertEqual(connect.await_count, 2)
        finally:
            await manager.aclose()

    async def test_real_requests_reach_proxy_for_http_and_https(self):
        requests = []

        async def handle(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                requests.append(request.split(b"\r\n", 1)[0])
                if request.startswith(b"CONNECT "):
                    # Recording CONNECT proves HTTPS is routed through the proxy.
                    writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                else:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        try:
            server = await asyncio.start_server(handle, "127.0.0.1", 0)
        except PermissionError:
            self.skipTest("This environment does not allow local listening sockets")
        port = server.sockets[0].getsockname()[1]
        manager = HTTPClientManager()
        try:
            async with server:
                with patch.object(config, "PROXY_HOST", f" http://127.0.0.1:{port}/ "), patch.object(config, "PROXY_PORT", 1), patch.dict("os.environ", {"NO_PROXY": "*"}), patch.object(http_retry, "http_client_manager", manager):
                    response = await http_retry.fetch_with_retry("http://proxy-test.invalid/feed", max_retries=1, timeout=2)
                    self.assertEqual(response.text, "ok")
                    with self.assertRaises(httpx.ProxyError):
                        await http_retry.fetch_with_retry("https://proxy-test.invalid/image", max_retries=1, timeout=2)
            self.assertEqual(requests, [b"GET http://proxy-test.invalid/feed HTTP/1.1", b"CONNECT proxy-test.invalid:443 HTTP/1.1"])
        finally:
            await manager.aclose()
            server.close()
            await server.wait_closed()

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
            factory.assert_called_once()
            transport = factory.call_args.kwargs["transport"]
            self.assertIsInstance(transport, _ProxyTransport)
            self.assertEqual(transport._proxy, "http://proxy.example:8080")
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
