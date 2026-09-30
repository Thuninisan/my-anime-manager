"""Application-owned outbound HTTP clients."""

import httpx

from .. import config


class HTTPClientManager:
    def __init__(self) -> None:
        self._clients: dict[str | None, httpx.AsyncClient] = {}

    def get_client(self) -> httpx.AsyncClient:
        proxy = f"http://{config.PROXY_HOST}:{config.PROXY_PORT}" if config.PROXY_HOST else None
        client = self._clients.get(proxy)
        if client is None:
            client = httpx.AsyncClient(proxy=proxy, follow_redirects=True)
            self._clients[proxy] = client
        return client

    async def aclose(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.aclose()


http_client_manager = HTTPClientManager()
