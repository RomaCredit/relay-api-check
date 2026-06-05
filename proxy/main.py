"""CORS-safe HTTPS proxy for Relay API Check (OpenAI / Anthropic relay testing)."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from proxy_transport import post_with_proxy_retry

from auto_scan.routes import router as auto_scan_router
from claude_cli_test import (
    ClaudeCliTestRequest,
    check_rate_limit,
    client_ip,
    normalize_base_url,
    run_claude_cli_test,
    validate_base_url_ssrf,
)
from codex_cli_test import (
    CodexCliTestRequest,
    check_rate_limit as check_codex_rate_limit,
    run_codex_cli_test,
)

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

# OpenAI 形态：/v1/...；部分中转站（如 NekoCode）为 /api/v1/...
_ALLOWED_RELAY_SUFFIXES = (
    "chat/completions",
    "responses",
    "messages",
    "models",
)


_SAFE_PATH_SEGMENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def _relay_api_path_allowed(path: str) -> bool:
    """允许 /v1/...、/api/v1/...，以及分组前缀路径如 /codex-pro/v1/chat/completions。"""
    if not path or ".." in path:
        return False
    path = (path.split("?")[0] or "/").rstrip("/") or "/"
    if not re.match(r"^(/[a-zA-Z0-9][a-zA-Z0-9._/-]*)?$", path):
        return False
    for suffix in _ALLOWED_RELAY_SUFFIXES:
        needle = f"/v1/{suffix}"
        if needle not in path:
            continue
        tail = path[path.index(needle) :]
        if tail != needle and not tail.startswith(f"{needle}/"):
            continue
        prefix = path[: path.index(needle)]
        if prefix == "":
            return True
        segments = [s for s in prefix.split("/") if s]
        if segments and all(_SAFE_PATH_SEGMENT.match(s) for s in segments):
            return True
    return False


def _allowed_path_hint() -> str:
    bases = ", ".join(f"/v1/{s} 或 /api/v1/{s}" for s in _ALLOWED_RELAY_SUFFIXES)
    return f"{bases}；或分组前缀如 /codex-pro/v1/chat/completions"

app = FastAPI(title="API Check Proxy", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "x-relay-check-ingest-token"],
)
app.include_router(auto_scan_router)


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
    if not _relay_api_path_allowed(path):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "proxy_path_not_allowed",
                "message": f"检测代理不允许路径: {path}",
                "path": path,
                "allowed": _allowed_path_hint(),
                "hint": (
                    "Base URL 填到分组入口即可，例如 https://www.right.codes/codex-pro；"
                    "本工具会拼接 /v1/chat/completions 等。"
                    "若 API 在 /api/v1/...，Base URL 用 https://域名/api（勿含 /api/v1）。"
                ),
            },
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


@app.post("/api/test-claude-cli")
async def test_claude_cli(request: Request, payload: ClaudeCliTestRequest) -> dict:
    """
    通过子进程执行 `claude -p`（非 shell），注入 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN。
    用于验证仅 Claude Code / Claude CLI 可使用的 Key；不记录、不日志 apiKey。
    """
    ip = client_ip(request)
    check_rate_limit(ip)

    base_url = normalize_base_url(payload.baseUrl)
    validate_base_url_ssrf(base_url, _resolve_host_ips)

    prompt = (payload.prompt or "").strip() or "Reply with only: OK"
    model = (payload.model or "").strip() or None

    # 切勿记录 payload.apiKey
    return await run_claude_cli_test(
        base_url=base_url,
        api_key=payload.apiKey,
        model=model,
        prompt=prompt,
        timeout_ms=payload.timeoutMs,
    )


@app.post("/api/test-codex-cli")
async def test_codex_cli(request: Request, payload: CodexCliTestRequest) -> dict:
    """
    通过子进程执行 `codex exec`（非 shell），注入 OPENAI_API_KEY 与 openai_base_url。
    用于验证仅 Codex CLI 可使用的 Key；不记录、不日志 apiKey。
    """
    ip = client_ip(request)
    check_codex_rate_limit(ip)

    base_url = normalize_base_url(payload.baseUrl)
    validate_base_url_ssrf(base_url, _resolve_host_ips)

    prompt = (payload.prompt or "").strip() or "Reply with only: OK"
    model = (payload.model or "").strip() or None

    return await run_codex_cli_test(
        base_url=base_url,
        api_key=payload.apiKey,
        model=model,
        prompt=prompt,
        timeout_ms=payload.timeoutMs,
    )


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

    try:
        upstream, client, prefix = await post_with_proxy_retry(
            target,
            headers=forward_headers,
            content=body_bytes,
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    content_type = upstream.headers.get("content-type") or ""
    media_type = upstream.headers.get("content-type")

    # 非 SSE：缓冲整包再返回，避免浏览器 fetch().json() 读 StreamingResponse 时出现 Failed to fetch
    if "text/event-stream" not in content_type:
        try:
            body = prefix or b""
            body += await upstream.aread()
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
            if prefix:
                yield prefix
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
