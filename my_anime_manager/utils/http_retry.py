"""Shared HTTP retry wrapper with exponential backoff.

Used by TMDB client, image downloader, and any other module that makes
outbound HTTP requests and wants automatic retry on transient failures.
"""

import asyncio

import httpx

from .. import config

# Common User-Agent for all outbound requests.
# A realistic browser UA helps get through CDN / reverse-proxy filters.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Exception types that indicate a transient failure worth retrying.
_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
)


def _proxy() -> str | None:
    if config.PROXY_HOST:
        return f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"
    return None


async def fetch_with_retry(
    url: str,
    *,
    max_retries: int = 3,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    method: str = "GET",
    label: str = "",
) -> httpx.Response:
    """Fetch a URL with exponential-backoff retry on transient errors.

    Retries on: timeout, connection errors, 5xx server errors.
    Does NOT retry on: 4xx client errors, unknown exceptions.

    Args:
        url: Full URL to fetch.
        max_retries: Maximum attempts (default 3).
        timeout: Request timeout in seconds.
        headers: Extra headers merged on top of the default User-Agent.
        params: Query-string parameters.
        method: HTTP method (default GET).
        label: Short description for log messages (e.g. ``"TMDB S2"``).

    Returns:
        ``httpx.Response`` on success.

    Raises:
        The last exception after all retries are exhausted.
    """
    _headers = {"User-Agent": USER_AGENT}
    if headers:
        _headers.update(headers)

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(
                proxy=_proxy(),
                timeout=timeout,
                follow_redirects=True,
                headers=_headers,
            ) as client:
                if method == "GET":
                    resp = await client.get(url, params=params)
                else:
                    resp = await client.request(method, url, params=params)
                resp.raise_for_status()
                return resp

        except _RETRYABLE_EXCEPTIONS as e:
            last_error = e
            prefix = f"{label}: " if label else ""
            if attempt < max_retries:
                delay = 2 ** attempt  # 2s, 4s, 8s
                print(
                    f"   ⚠️ {prefix}HTTP 请求失败 "
                    f"(尝试 {attempt}/{max_retries})，"
                    f"{delay}s 后重试: {type(e).__name__}: {e}"
                )
                await asyncio.sleep(delay)
            else:
                raise  # retries exhausted

        except httpx.HTTPStatusError as e:
            # Retry only on server errors (5xx), not client errors (4xx)
            if e.response.status_code >= 500:
                last_error = e
                prefix = f"{label}: " if label else ""
                if attempt < max_retries:
                    delay = 2 ** attempt
                    print(
                        f"   ⚠️ {prefix}服务器错误 {e.response.status_code} "
                        f"(尝试 {attempt}/{max_retries})，"
                        f"{delay}s 后重试"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
            else:
                raise  # 4xx — don't retry

        except Exception:
            raise  # Unknown errors — don't retry

    # Should never reach here, but satisfy the type checker
    assert last_error is not None
    raise last_error
