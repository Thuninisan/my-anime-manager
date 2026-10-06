"""Application-owned outbound HTTP clients."""

from urllib.parse import urlsplit, urlunsplit

import httpx

from .. import config


class _ProxyResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, transport: httpx.AsyncHTTPTransport, owner: "_ProxyTransport"):
        self._stream = stream
        self._transport = transport
        self._owner = owner

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        try:
            await self._stream.aclose()
        finally:
            await self._owner._close_transport(self._transport)


class _ProxyTransport(httpx.AsyncBaseTransport):
    """Keep proxy pools request-scoped, including streaming responses.

    A failed TLS handshake after CONNECT can leave an active connection in
    httpcore's proxy pool. Sharing that pool indefinitely eventually makes
    every upstream request fail with PoolTimeout. A fresh transport per
    request prevents failed tunnels from starving unrelated requests.
    """

    def __init__(self, proxy: str):
        self._proxy = proxy
        self._active: set[httpx.AsyncHTTPTransport] = set()

    async def _close_transport(self, transport: httpx.AsyncHTTPTransport) -> None:
        if transport in self._active:
            self._active.remove(transport)
            await transport.aclose()

    async def aclose(self) -> None:
        for transport in list(self._active):
            await self._close_transport(transport)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = httpx.AsyncHTTPTransport(proxy=self._proxy)
        self._active.add(transport)
        try:
            response = await transport.handle_async_request(request)
        except BaseException:
            await self._close_transport(transport)
            raise
        response.stream = _ProxyResponseStream(response.stream, transport, self)
        return response


def _proxy_url() -> str | None:
    """Accept a host or HTTP proxy URL without duplicating scheme/port."""
    host = config.PROXY_HOST.strip()
    if not host:
        return None
    if "://" not in host:
        # Bare IPv6 addresses need brackets before a port can be appended.
        if host.count(":") > 1 and not host.startswith("["):
            host = f"[{host}]"
        host = f"http://{host}"
    try:
        parsed = urlsplit(host)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        netloc = parsed.netloc
        if parsed.port is None:
            netloc = f"{netloc}:{config.PROXY_PORT}"
        return urlunsplit((parsed.scheme, netloc, "", "", ""))
    except ValueError:
        # Do not include the input: a proxy URL may contain credentials.
        raise ValueError("代理地址无效，请填写 IP、域名或 HTTP/HTTPS 代理地址") from None


class HTTPClientManager:
    def __init__(self) -> None:
        self._clients: dict[str | None, httpx.AsyncClient] = {}

    def get_client(self) -> httpx.AsyncClient:
        proxy = _proxy_url()
        client = self._clients.get(proxy)
        if client is None:
            if proxy:
                client = httpx.AsyncClient(transport=_ProxyTransport(proxy), follow_redirects=True)
            else:
                client = httpx.AsyncClient(proxy=None, follow_redirects=True)
            self._clients[proxy] = client
        return client

    async def aclose(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.aclose()


http_client_manager = HTTPClientManager()
