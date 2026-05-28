"""Codex / CLI-only 中转的 HTTP 特征探测（基于 Responses + 错误文案）。"""
from __future__ import annotations

import re

from auto_scan.models import AgentProbeResult, CheckStatus

CODEX_HINT_RE = re.compile(r"codex|openai.?codex|gpt.?codex", re.I)


async def probe_codex_http(
    *,
    protocols_report: dict,
    claude_code_only: bool,
) -> AgentProbeResult:
    """根据 HTTP 矩阵推断是否像 Codex 专属或通用 OpenAI。"""
    responses = protocols_report.get("responses") or {}
    sync = responses.get("sync") or {}
    err = (sync.get("errorMessage") or "") + (sync.get("responseSnippet") or "")
    if CODEX_HINT_RE.search(err):
        return AgentProbeResult(
            agent_id="codex",
            label="Codex (HTTP)",
            status="warn",
            evidence="响应含 Codex 相关提示",
        )
    if sync.get("status") == "pass":
        return AgentProbeResult(
            agent_id="codex",
            label="Codex (HTTP)",
            status="pass",
            evidence="Responses 端点可用（OpenAI 兼容）",
        )
    if claude_code_only:
        return AgentProbeResult(
            agent_id="codex",
            label="Codex (HTTP)",
            status="skipped",
            evidence="Key 表现为 Claude Code 专用，未测 Codex HTTP",
        )
    return AgentProbeResult(
        agent_id="codex",
        label="Codex (HTTP)",
        status="unknown",
        evidence="Responses 未通过或未探测",
    )


async def probe_placeholder(agent_id: str, label: str) -> AgentProbeResult:
    return AgentProbeResult(
        agent_id=agent_id,
        label=label,
        status="skipped",
        evidence="尚未实现，预留扩展",
    )
