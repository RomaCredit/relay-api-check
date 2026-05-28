from __future__ import annotations

from auto_scan.agents.claude_cli import probe_claude_cli
from auto_scan.agents.codex import probe_codex_http, probe_placeholder
from auto_scan.models import AgentProbeResult

AGENT_PROFILES: dict[str, list[str]] = {
    "quick": ["claude_cli"],
    "standard": ["claude_cli", "codex"],
    "deep": ["claude_cli", "codex", "opencode", "kiro"],
}


async def run_agent_probes(
    *,
    profile: str,
    base_url: str,
    api_key: str,
    model: str | None,
    protocols_report: dict,
    claude_code_only: bool,
) -> dict[str, AgentProbeResult]:
    ids = AGENT_PROFILES.get(profile, AGENT_PROFILES["standard"])
    out: dict[str, AgentProbeResult] = {}

    if "claude_cli" in ids:
        out["claude_cli"] = await probe_claude_cli(
            base_url=base_url, api_key=api_key, model=model
        )
    if "codex" in ids:
        out["codex"] = await probe_codex_http(
            protocols_report=protocols_report,
            claude_code_only=claude_code_only,
        )
    if "opencode" in ids:
        out["opencode"] = await probe_placeholder("opencode", "OpenCode")
    if "kiro" in ids:
        out["kiro"] = await probe_placeholder("kiro", "Kiro")

    return out
