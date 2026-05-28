"""FastAPI 路由：/api/auto-scan"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auto_scan.orchestrator import run_auto_scan
from auto_scan.store import STORE

# 延迟导入避免循环
def _validate_url(url: str) -> str:
    from main import validate_target_url

    return validate_target_url(url)


def _client_ip(request: Request) -> str:
    from claude_cli_test import client_ip

    return client_ip(request)


router = APIRouter(prefix="/api/auto-scan", tags=["auto-scan"])


class AutoScanCreate(BaseModel):
    baseUrl: str = Field(..., min_length=4, max_length=500)
    apiKey: str = Field(..., min_length=1, max_length=500)
    profile: Literal["quick", "standard", "deep"] = "standard"


@router.post("")
async def create_scan(request: Request, payload: AutoScanCreate) -> dict:
    ip = _client_ip(request)
    try:
        rec = STORE.create(
            client_ip=ip,
            base_url=payload.baseUrl.strip(),
            api_key=payload.apiKey,
            profile=payload.profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    asyncio.create_task(run_auto_scan(rec, validate_url=_validate_url))
    return {"scanId": rec.scan_id}


@router.get("/{scan_id}")
async def get_scan(scan_id: str) -> dict:
    rec = STORE.get(scan_id)
    if not rec:
        raise HTTPException(status_code=404, detail="扫描不存在或已过期")
    return {
        "scanId": rec.scan_id,
        "state": rec.state,
        "progress": rec.progress,
        "error": rec.error,
        "report": rec.report if rec.state in ("done", "error") else None,
        "meta": rec.report.get("meta"),
    }


@router.get("/{scan_id}/events")
async def scan_events(scan_id: str) -> StreamingResponse:
    rec = STORE.get(scan_id)
    if not rec:
        raise HTTPException(status_code=404, detail="扫描不存在或已过期")

    async def event_stream():
        import json

        async for item in rec.subscribe():
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
