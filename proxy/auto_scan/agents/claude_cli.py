from __future__ import annotations

from auto_scan.models import AgentProbeResult, CheckStatus

from claude_cli_test import claude_cli_available, run_claude_cli_test


async def probe_claude_cli(
    *,
    base_url: str,
    api_key: str,
    model: str | None,
) -> AgentProbeResult:
    if not claude_cli_available():
        return AgentProbeResult(
            agent_id="claude_cli",
            label="Claude CLI",
            status="skipped",
            evidence="服务端未安装 claude 命令",
        )
    try:
        result = await run_claude_cli_test(
            base_url=base_url,
            api_key=api_key,
            model=model,
            prompt="Reply with only: OK",
            timeout_ms=30_000,
        )
    except Exception as exc:  # noqa: BLE001
        return AgentProbeResult(
            agent_id="claude_cli",
            label="Claude CLI",
            status="fail",
            evidence=str(exc)[:200],
        )
    ok = bool(result.get("success"))
    diagnosis = result.get("diagnosis") or {}
    note = diagnosis.get("note") or result.get("stdout") or ""
    status: CheckStatus = "pass" if ok else "fail"
    if diagnosis.get("likelyClaudeCodeOnlyKey"):
        status = "pass" if ok else "warn"
    return AgentProbeResult(
        agent_id="claude_cli",
        label="Claude CLI",
        status=status,
        evidence=str(note)[:300],
        detail={
            "keyValid": diagnosis.get("keyValid"),
            "realClaudeClientAccepted": diagnosis.get("realClaudeClientAccepted"),
            "likelyClaudeCodeOnlyKey": diagnosis.get("likelyClaudeCodeOnlyKey"),
            "exitCode": result.get("exitCode"),
        },
    )
