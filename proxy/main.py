"""CORS-safe HTTPS proxy for Relay API Check (OpenAI / Anthropic relay testing)."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080",
    ).split(",")
    if o.strip()
]

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

# 缓冲整包后 body 已由 httpx 解压，须去掉上游压缩相关头，否则浏览器二次解压会 Failed to fetch
STRIP_WHEN_BUFFERED = {
    "content-encoding",
    "content-length",
    "transfer-encoding",
}

ALLOWED_PATH_PREFIXES = (
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/models",
)

app = FastAPI(title="API Check Proxy", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class ProxyRequest(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None


def _resolve_host_ips(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail=f"无法解析主机: {host}") from exc
    return list({item[4][0] for item in infos})


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
    )


def validate_target_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="仅允许 https 上游地址")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="无效 URL")
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=400, detail="禁止访问本地地址")
    path = parsed.path or "/"
    if not any(path.startswith(p) for p in ALLOWED_PATH_PREFIXES):
        raise HTTPException(
            status_code=400,
            detail=f"路径须以 {', '.join(ALLOWED_PATH_PREFIXES)} 之一开头",
        )
    for ip in _resolve_host_ips(host):
        if _is_blocked_ip(ip):
            raise HTTPException(status_code=400, detail="禁止访问内网或保留地址")
    return url


def filter_response_headers(headers: httpx.Headers, *, buffered: bool = False) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in HOP_BY_HOP:
            continue
        if buffered and lk in STRIP_WHEN_BUFFERED:
            continue
        out[k] = v
    return out


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/proxy")
async def proxy(request: Request, payload: ProxyRequest) -> Response:
    target = validate_target_url(payload.url.strip())
    forward_headers = {
        k: v
        for k, v in payload.headers.items()
        if k.lower() not in {"host", "content-length", "connection"}
    }
    body_bytes = (
        json.dumps(payload.body, ensure_ascii=False).encode("utf-8")
        if payload.body is not None
        else b""
    )

    timeout = httpx.Timeout(connect=30.0, read=900.0, write=60.0, pool=30.0)
    client = httpx.AsyncClient(timeout=timeout)

    try:
        req = client.build_request(
            "POST",
            target,
            headers=forward_headers,
            content=body_bytes,
        )
        upstream = await client.send(req, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    content_type = upstream.headers.get("content-type") or ""
    media_type = upstream.headers.get("content-type")

    # 非 SSE：缓冲整包再返回，避免浏览器 fetch().json() 读 StreamingResponse 时出现 Failed to fetch
    if "text/event-stream" not in content_type:
        try:
            body = await upstream.aread()
        finally:
            await upstream.aclose()
            await client.aclose()
        return Response(
            content=body,
            status_code=upstream.status_code,
            headers=filter_response_headers(upstream.headers, buffered=True),
            media_type=media_type,
        )

    resp_headers = filter_response_headers(upstream.headers)

    async def stream_body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_body(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=media_type,
    )
