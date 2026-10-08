"""FontInAss single-file wire protocol (OK=200, WARN=201, MISSING_FONT=300)."""

import base64
import asyncio
import json
import logging
from time import perf_counter
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from ..utils.http_client import http_client_manager
from ..logging.logging_config import safe_url

logger = logging.getLogger(__name__)


@dataclass
class SubsetResult:
    code: int
    messages: list[str]
    data: bytes


class SubsetError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, retry_after: float = 0):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


async def subset(server: str, timeout: int, data: bytes) -> SubsetResult:
    parsed = urlsplit(server)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
        logger.warning("fontinass.request.invalid_url")
        raise SubsetError("FontInAss 服务地址必须是有效的 HTTP/HTTPS 地址")
    started = perf_counter()
    logger.debug("fontinass.request.start origin=%s input_bytes=%d timeout_s=%d strict=true font_name_mode=alias",
                 safe_url(server), len(data), timeout)
    try:
        async with asyncio.timeout(timeout):
            response = await http_client_manager.get_client().post(
                server.rstrip("/") + "/api/subset",
                headers={"X-Fonts-Check": "1", "X-Font-Name-Mode": "alias"},
                files={"file": ("subtitle.ass", data, "application/octet-stream")},
                timeout=httpx.Timeout(timeout, connect=10), follow_redirects=False,
            )
    except httpx.InvalidURL as error:
        logger.warning("fontinass.request.invalid_url")
        raise SubsetError("FontInAss 服务地址无效") from error
    except (httpx.RequestError, TimeoutError) as error:
        logger.debug("fontinass.request.network_failure origin=%s elapsed_ms=%.1f error_type=%s",
                     safe_url(server), (perf_counter() - started) * 1000, type(error).__name__)
        # Do not expose configured URLs or proxy credentials in task messages.
        raise SubsetError(f"FontInAss 网络请求失败 ({type(error).__name__})", retryable=True) from error
    logger.debug("fontinass.request.response origin=%s elapsed_ms=%.1f http_status=%d output_bytes=%d content_type=%r code_header=%r",
                 safe_url(server), (perf_counter() - started) * 1000, response.status_code,
                 len(response.content), response.headers.get("Content-Type", ""), response.headers.get("X-Code", "")[:32])
    if response.status_code == 429 or response.status_code >= 500:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        value = response.headers.get("Retry-After", "0")
        try:
            delay = max(0, float(value))
        except ValueError:
            try:
                delay = max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                delay = 0
        logger.debug("fontinass.request.retryable http_status=%d retry_after_s=%.1f", response.status_code, delay)
        raise SubsetError(f"FontInAss HTTP {response.status_code}", retryable=True, retry_after=delay)
    if not response.is_success:
        raise SubsetError(f"FontInAss HTTP {response.status_code}")
    try:
        code = int(response.headers["X-Code"])
        messages = json.loads(base64.b64decode(response.headers["X-Message"], validate=True).decode("utf-8"))
        if not isinstance(messages, list) or not all(isinstance(message, str) for message in messages):
            raise ValueError("Invalid messages")
    except (KeyError, ValueError, UnicodeError) as error:
        logger.warning("fontinass.request.invalid_protocol http_status=%d has_code=%s has_message=%s error_type=%s",
                       response.status_code, "X-Code" in response.headers, "X-Message" in response.headers, type(error).__name__)
        raise SubsetError("FontInAss 响应协议无效，缺少或损坏 X-Code/X-Message") from error
    logger.debug("fontinass.request.decoded code=%d message_count=%d output_bytes=%d", code, len(messages), len(response.content))
    return SubsetResult(code, messages, response.content)
