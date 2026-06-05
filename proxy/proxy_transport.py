"""Outbound HTTP with PROXY_LIST fallback (Cloudflare / WAF blocked VPS egress)."""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_RETRY_STATUSES = frozenset({403, 429, 502, 503, 520, 521, 522, 523})

_BLOCK_MARKERS = (
    "cloudflare",
    "cf-ray",
    "you have been blocked",
    "attention required",
    "sorry, you have been blocked",
)


def parse_proxy_list(raw: str | None = None) -> list[str]:
    value = raw if raw is not None else os.getenv("PROXY_LIST", "")
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def proxy_attempts() -> list[tuple[str, str | None]]:
    attempts: list[tuple[str, str | None]] = [("direct", None)]
    for proxy in parse_proxy_list():
        attempts.append((f"proxy:{proxy.split('@')[-1]}", proxy))
    return attempts


def enrich_headers(headers: dict[str, str]) -> dict[str, str]:
    out = dict(headers)
    if not any(k.lower() == "user-agent" for k in out):
        out["User-Agent"] = DEFAULT_USER_AGENT
    return out


def is_blocked_response(
    status_code: int,
    body_text: str = "",
    content_type: str = "",
) -> bool:
    if status_code in _RETRY_STATUSES and status_code not in (403, 429):
        return True

    text = (body_text or "")[:4000].lower()
    ct = (content_type or "").lower()

    if any(mark in text for mark in _BLOCK_MARKERS):
        return True

    if status_code in (403, 429):
        stripped = (body_text or "").lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            return False
        if "text/html" in ct or stripped.lower().startswith("<!doctype") or "<html" in text:
            return True

    return False


def _client_kwargs(
    timeout: httpx.Timeout | float,
    proxy: str | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
    }
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


async def _read_preview(res: httpx.Response, limit: int = 32_768) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in res.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
    return b"".join(chunks)


async def request_with_proxy_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    timeout: httpx.Timeout | float = 30.0,
) -> tuple[httpx.Response, httpx.AsyncClient, bytes | None]:
    """
    Try direct egress first, then each PROXY_LIST entry on WAF/Cloudflare block.

    Returns (response, client, consumed_prefix). When consumed_prefix is set the
    response body stream is already partially/fully read into that buffer.
    Caller must aclose() response and client when done.
    """
    req_headers = enrich_headers(headers or {})
    attempts = proxy_attempts()
    last_error: httpx.RequestError | None = None

    for label, proxy in attempts:
        client = httpx.AsyncClient(**_client_kwargs(timeout, proxy))
        try:
            req = client.build_request(method, url, headers=req_headers, content=content)
            res = await client.send(req, stream=True)
            ct = res.headers.get("content-type") or ""

            if not is_blocked_response(res.status_code, "", ct):
                return res, client, None

            preview = await _read_preview(res)
            text = preview.decode("utf-8", errors="replace")
            if not is_blocked_response(res.status_code, text, ct):
                return res, client, preview

            await res.aclose()
            await client.aclose()
        except httpx.RequestError as exc:
            await client.aclose()
            last_error = exc
            if label == "direct" and len(attempts) > 1:
                continue
            raise

    if last_error is not None:
        raise last_error
    raise httpx.RequestError(
        f"All proxy attempts blocked for {url}",
        request=httpx.Request(method, url),
    )


async def post_with_proxy_retry(
    url: str,
    *,
    headers: dict[str, str],
    content: bytes,
    timeout: httpx.Timeout | float = 30.0,
) -> tuple[httpx.Response, httpx.AsyncClient, bytes | None]:
    return await request_with_proxy_retry(
        "POST",
        url,
        headers=headers,
        content=content,
        timeout=timeout,
    )


async def get_with_proxy_retry(
    url: str,
    *,
    headers: dict[str, str],
    timeout: httpx.Timeout | float = 20.0,
) -> tuple[httpx.Response, httpx.AsyncClient, bytes | None]:
    return await request_with_proxy_retry(
        "GET",
        url,
        headers=headers,
        timeout=timeout,
    )
