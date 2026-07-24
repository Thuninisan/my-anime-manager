"""DeepSeek API client — OpenAI-compatible chat completions.

Authentication: API key passed via ``Authorization: Bearer`` header.
No login flow needed — the key is used directly on every request.
"""

import json
import logging

import httpx

from .. import config

logger = logging.getLogger(__name__)

_BASE = "https://api.deepseek.com"


def _proxy() -> str | None:
    if config.PROXY_HOST:
        return f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"
    return None


def _auth_headers() -> dict[str, str]:
    """Build the Authorization header from the configured API key."""
    apikey = config.DEEPSEEK_API_KEY
    if not apikey:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    return {"Authorization": f"Bearer {apikey}"}


# ═══════════════════════════════════════════════════════════════════════
# Chat completions
# ═══════════════════════════════════════════════════════════════════════


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str = "deepseek-chat",
    temperature: float = 0.7,
    max_tokens: int | None = None,
    stream: bool = False,
    **kwargs,
) -> httpx.Response:
    """Send a chat completion request to DeepSeek.

    Args:
        messages: List of message dicts with ``"role"`` and ``"content"`` keys.
        model: Model name (default ``"deepseek-chat"``).
        temperature: Sampling temperature (0–2, default 0.7).
        max_tokens: Max tokens to generate (optional).
        stream: Whether to stream the response.
        **kwargs: Additional parameters forwarded to the API.

    Returns:
        ``httpx.Response`` — call ``.json()`` to get the completion result.
    """
    headers = {
        **_auth_headers(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    body.update(kwargs)

    async with httpx.AsyncClient(
        timeout=120.0,
        proxy=_proxy(),
    ) as client:
        resp = await client.post(
            f"{_BASE}/v1/chat/completions",
            content=json.dumps(body),
            headers=headers,
        )
        resp.raise_for_status()
        return resp


# ═══════════════════════════════════════════════════════════════════════
# Convenience helpers
# ═══════════════════════════════════════════════════════════════════════


async def chat(
    prompt: str,
    *,
    system: str | None = None,
    model: str = "deepseek-chat",
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> str:
    """Send a single-turn chat and return the text response.

    Args:
        prompt: The user message.
        system: Optional system prompt.
        model: Model name.
        temperature: Sampling temperature.
        max_tokens: Max tokens to generate.

    Returns:
        The model's text response.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = await chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    data = resp.json()
    return data["choices"][0]["message"]["content"]
