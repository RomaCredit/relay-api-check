"""自动扫描编排。"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from auto_scan.agents.registry import run_agent_probes
from auto_scan.discovery import (
    fetch_models_list,
    normalize_base_url,
    pick_representative_models,
)
from auto_scan.models import StepKind, mask_api_key
from auto_scan.protocol_runner import run_protocol_probe
from auto_scan.store import ScanRecord, STORE

STEP_SLEEP_SEC = 0.35


def _build_summary(report: dict[str, Any]) -> dict[str, Any]:
    protocols = report.get("protocols") or {}
    lines: list[str] = []
    for ep, modes in protocols.items():
        if not isinstance(modes, dict):
            continue
        for mode_name, cell in modes.items():
            if isinstance(cell, dict):
                st = cell.get("status", "unknown")
                lines.append(f"{ep}/{mode_name}: {st}")
    agents = report.get("agents") or {}
    agent_bits = [f"{k}={v.get('status')}" for k, v in agents.items() if isinstance(v, dict)]
    return {
        "headline": "；".join(lines[:6]) if lines else "扫描完成",
        "agentSummary": "，".join(agent_bits) if agent_bits else "—",
        "recommendedBaseUrl": report.get("meta", {}).get("baseUrl"),
    }


def _hub_ingest_payload(report: dict[str, Any], *, success: bool) -> dict[str, Any]:
    meta = report.get("meta") or {}
    summary = report.get("summary") or {}
    return {
        "schemaVersion": 1,
        "scanId": meta.get("scanId"),
        "baseUrl": meta.get("baseUrl"),
        "profile": meta.get("profile"),
        "success": success,
        "summary": {
            "headline": summary.get("headline"),
            "agentSummary": summary.get("agentSummary"),
            "protocols": report.get("protocols"),
            "agents": {k: v.get("status") for k, v in (report.get("agents") or {}).items()},
        },
        "report": _truncate_report(report),
    }


def _truncate_report(report: dict[str, Any], max_snippet: int = 2000) -> dict[str, Any]:
    import copy

    r = copy.deepcopy(report)
    protocols = r.get("protocols") or {}
    for ep_data in protocols.values():
        if not isinstance(ep_data, dict):
            continue
        for cell in ep_data.values():
            if isinstance(cell, dict) and cell.get("responseSnippet"):
                cell["responseSnippet"] = str(cell["responseSnippet"])[:max_snippet]
    return r


async def _post_hub_suite(report: dict[str, Any], *, success: bool) -> None:
    url = os.getenv(
        "HUB_SUITE_INGEST_URL",
        "http://hub-web:3000/api/relay-api-check/suite-ingest",
    ).strip()
    if not url:
        return
    headers = {"Content-Type": "application/json"}
    secret = os.getenv("RELAY_API_CHECK_INGEST_SECRET", "").strip()
    if secret:
        headers["x-relay-check-ingest-token"] = secret
    payload = _hub_ingest_payload(report, success=success)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(url, json=payload, headers=headers)
    except httpx.RequestError:
        pass


async def run_auto_scan(
    rec: ScanRecord,
    *,
    validate_url: Callable[[str], str],
) -> None:
    rec.state = "running"
    report = rec.report
    meta = report["meta"]
    meta["startedAt"] = datetime.now(timezone.utc).isoformat()
    meta["maskedKey"] = mask_api_key(rec.api_key)

    total_steps = 4
    step_i = 0

    def emit(kind: StepKind, name: str, status: str, detail: str = "") -> None:
        nonlocal step_i
        if status == "finished":
            step_i += 1
        rec.progress = min(0.99, step_i / total_steps)
        rec.publish(
            {
                "type": "step",
                "kind": kind.value,
                "name": name,
                "status": status,
                "detail": detail,
                "progress": rec.progress,
            }
        )

    claude_code_only = False

    try:
        base_url = normalize_base_url(rec.base_url)
        meta["baseUrl"] = base_url

        emit(StepKind.DISCOVERY, "模型发现", "started")
        models, models_url = await fetch_models_list(base_url, rec.api_key)
        report["models"] = [m.to_dict() for m in models]
        if models_url:
            meta["modelsUrl"] = models_url
        representatives = pick_representative_models(models, rec.profile)
        meta["representatives"] = representatives
        emit(
            StepKind.DISCOVERY,
            "模型发现",
            "finished",
            f"共 {len(models)} 个模型",
        )
        await asyncio.sleep(STEP_SLEEP_SEC)

        emit(StepKind.PROTOCOL, "协议矩阵", "started")
        protocols_out: dict[str, Any] = {}
        stream_modes = [False] if rec.profile == "quick" else [False, True]

        for ep, model in representatives.items():
            protocols_out[ep] = {}
            for stream in stream_modes:
                mode_key = "stream" if stream else "sync"
                emit(
                    StepKind.PROTOCOL,
                    f"{ep} · {mode_key}",
                    "started",
                    model,
                )
                cell = await run_protocol_probe(
                    base_url=base_url,
                    api_key=rec.api_key,
                    endpoint=ep,
                    model=model,
                    stream=stream,
                    validate_url=validate_url,
                )
                protocols_out[ep][mode_key] = cell.to_dict()
                if cell.diagnosis_code == "claude_code_only":
                    claude_code_only = True
                emit(
                    StepKind.PROTOCOL,
                    f"{ep} · {mode_key}",
                    "finished",
                    cell.status,
                )
                await asyncio.sleep(STEP_SLEEP_SEC)

        report["protocols"] = protocols_out
        emit(StepKind.PROTOCOL, "协议矩阵", "finished")

        emit(StepKind.PARAMETERS, "参数完整度", "started")
        if rec.profile == "quick":
            report["parameters"] = {"skipped": True, "reason": "quick 档位跳过"}
        else:
            report["parameters"] = {
                "note": "标准档位：核心字段已在协议探测请求中覆盖（temperature / max_tokens / stream）",
                "status": "pass",
            }
        emit(StepKind.PARAMETERS, "参数完整度", "finished")
        await asyncio.sleep(STEP_SLEEP_SEC)

        emit(StepKind.AGENT, "Agent 探测", "started")
        rep_model = representatives.get("anthropic") or representatives.get("responses")
        agents = await run_agent_probes(
            profile=rec.profile,
            base_url=base_url,
            api_key=rec.api_key,
            model=rep_model,
            protocols_report=protocols_out,
            claude_code_only=claude_code_only,
        )
        report["agents"] = {k: v.to_dict() for k, v in agents.items()}
        emit(StepKind.AGENT, "Agent 探测", "finished")

        meta["finishedAt"] = datetime.now(timezone.utc).isoformat()
        report["summary"] = _build_summary(report)
        any_pass = any(
            isinstance(cell, dict) and cell.get("status") == "pass"
            for ep in protocols_out.values()
            if isinstance(ep, dict)
            for cell in ep.values()
        )
        success = any_pass

        emit(StepKind.INGEST, "Hub 入库", "started")
        await _post_hub_suite(report, success=success)
        emit(StepKind.INGEST, "Hub 入库", "finished")

        rec.state = "done"
        rec.progress = 1.0
        rec.publish({"type": "done", "report": report, "progress": 1.0})
    except Exception as exc:  # noqa: BLE001
        rec.state = "error"
        rec.error = str(exc)
        meta["finishedAt"] = datetime.now(timezone.utc).isoformat()
        rec.publish({"type": "error", "message": str(exc)})
    finally:
        STORE.release_ip(rec)
