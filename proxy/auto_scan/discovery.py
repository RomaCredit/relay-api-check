"""模型发现与 API 根路径探测。"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from auto_scan.models import ModelDiscoveryResult
from proxy_transport import get_with_proxy_retry

FALLBACK_MODELS = [
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "gpt-5.5",
    "gpt-5.4",
    "MiniMax-M3",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

REPRESENTATIVE_BY_FAMILY = {
    "anthropic": [
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-opus-4-7",
    ],
    "responses": ["gpt-5.4", "gpt-5.5", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3.1-pro-preview"],
    "chat": ["gpt-5.4", "MiniMax-M3"],
}


def normalize_base_url(url: str) -> str:
    u = url.strip().rstrip("/")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("baseUrl 须为 http 或 https")
    path = (parsed.path or "").rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _models_urls(base_url: str) -> list[str]:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = (parsed.path or "").rstrip("/")
    urls = []
    if path:
        urls.append(f"{base_url}/v1/models")
    urls.append(f"{origin}/v1/models")
    if not path.endswith("/api"):
        urls.append(f"{origin}/api/v1/models")
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def fetch_models_list(
    base_url: str,
    api_key: str,
) -> tuple[list[ModelDiscoveryResult], str | None]:
    """返回模型列表与命中的 models URL（若有）。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    for url in _models_urls(base_url):
        res = None
        client = None
        data: dict[str, Any] | None = None
        try:
            res, client, prefix = await get_with_proxy_retry(
                url,
                headers=headers,
                timeout=20.0,
            )
            if res.status_code != 200:
                continue
            ct = res.headers.get("content-type") or ""
            if "json" not in ct.lower():
                continue
            raw = (prefix or b"") + await res.aread()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(parsed, dict):
                continue
            data = parsed
        except httpx.RequestError:
            continue
        finally:
            if res is not None:
                try:
                    await res.aclose()
                except Exception:
                    pass
            if client is not None:
                await client.aclose()
        if data is None:
            continue
        ids: list[str] = []
        if isinstance(data.get("data"), list):
            for item in data["data"]:
                if isinstance(item, dict) and item.get("id"):
                    ids.append(str(item["id"]))
        elif isinstance(data.get("models"), list):
            for item in data["models"]:
                if isinstance(item, str):
                    ids.append(item)
                elif isinstance(item, dict) and item.get("id"):
                    ids.append(str(item["id"]))
        if ids:
            return (
                [ModelDiscoveryResult(model_id=m, source="api", listed=True) for m in ids],
                url,
            )
    fallback = [
        ModelDiscoveryResult(model_id=m, source="fallback", listed=True) for m in FALLBACK_MODELS
    ]
    return fallback, None


def pick_representative_models(
    discovered: list[ModelDiscoveryResult],
    profile: str,
) -> dict[str, str]:
    """每个协议族选一个代表模型（用于矩阵探测）。"""
    ids = {m.model_id for m in discovered}
    result: dict[str, str] = {}
    for ep, candidates in REPRESENTATIVE_BY_FAMILY.items():
        for c in candidates:
            if c in ids:
                result[ep] = c
                break
        if ep not in result and candidates:
            result[ep] = candidates[0]
    if profile == "quick":
        # quick 只测 anthropic + responses 各一个
        return {k: v for k, v in result.items() if k in ("anthropic", "responses")}
    return result


def build_request_url(base_url: str, endpoint: str) -> str:
    suffix = {
        "chat": "/v1/chat/completions",
        "responses": "/v1/responses",
        "anthropic": "/v1/messages",
    }[endpoint]
    return f"{base_url.rstrip('/')}{suffix}"
