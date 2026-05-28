"""单协议探测执行。"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from auto_scan.compliance import run_compliance, summarize_checks
from auto_scan.discovery import build_request_url
from auto_scan.models import ProtocolCellResult

PROBE_USER = "用一句话回复：兼容性检测通过"


def build_request(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    stream: bool,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    if endpoint == "chat":
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": PROBE_USER}],
            "temperature": 0.7,
            "max_tokens": 64,
            "stream": stream,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    elif endpoint == "responses":
        body = {
            "model": model,
            "input": PROBE_USER,
            "temperature": 0.7,
            "max_output_tokens": 64,
            "stream": stream,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:
        body = {
            "model": model,
            "max_tokens": 64,
            "messages": [{"role": "user", "content": PROBE_USER}],
            "temperature": 0.7,
        }
        if stream:
            body["stream"] = True
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    return endpoint, headers, body


async def run_protocol_probe(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    endpoint: str,
    model: str,
    stream: bool,
    validate_url,
) -> ProtocolCellResult:
    _, headers, body = build_request(
        endpoint=endpoint, model=model, api_key=api_key, stream=stream
    )
    url = build_request_url(base_url, endpoint)
    validate_url(url)

    cell = ProtocolCellResult(
        endpoint=endpoint,
        stream=stream,
        model=model,
        url=url,
    )
    started = time.perf_counter()
    raw = ""
    try:
        res = await client.post(
            url,
            headers=headers,
            content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=httpx.Timeout(connect=25.0, read=120.0, write=30.0),
        )
        cell.http_status = res.status_code
        cell.content_type = res.headers.get("content-type")
        raw = res.text
        cell.elapsed_ms = int((time.perf_counter() - started) * 1000)
        cell.success = res.status_code == 200
        if not cell.success:
            cell.error_message = raw[:500]
    except httpx.RequestError as exc:
        cell.elapsed_ms = int((time.perf_counter() - started) * 1000)
        cell.success = False
        cell.error_message = str(exc)
        cell.http_status = None

    cell.response_snippet = (raw or "")[:400]
    cell.checks = run_compliance(
        cell,
        request_headers=headers,
        request_body=body,
        raw_body=raw,
        stream_requested=stream,
    )
    cell.status = summarize_checks(cell.checks)
    return cell
