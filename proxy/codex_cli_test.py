"""Codex CLI real-client test — spawn `codex exec` with injected env (no shell)."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from claude_cli_test import (
    client_ip,
    mask_api_key,
    normalize_base_url,
    validate_base_url_ssrf,
)

# 每分钟每 IP 最多 5 次 Codex CLI 测试
_RATE_LIMIT_WINDOW_SEC = 60
_RATE_LIMIT_MAX = 5
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)

CODEX_CLI_BIN = os.getenv("CODEX_CLI_PATH", "codex").strip() or "codex"

DEFAULT_PROMPT = "Reply with only: OK"
MAX_PROMPT_LEN = 1000
MAX_BASE_URL_LEN = 300
MAX_MODEL_LEN = 100
MAX_TIMEOUT_MS = 60_000
DEFAULT_TIMEOUT_MS = 30_000


class CodexCliTestRequest(BaseModel):
    baseUrl: str = Field(..., min_length=1, max_length=MAX_BASE_URL_LEN)
    apiKey: str = Field(..., min_length=1, max_length=500)
    model: str | None = Field(default=None, max_length=MAX_MODEL_LEN)
    prompt: str | None = Field(default=None, max_length=MAX_PROMPT_LEN)
    timeoutMs: int = Field(default=DEFAULT_TIMEOUT_MS, ge=1000, le=MAX_TIMEOUT_MS)

    @field_validator("baseUrl", "apiKey", "model", "prompt", mode="before")
    @classmethod
    def strip_strings(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


def check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Codex CLI 测试过于频繁，请稍后再试（每 IP 每分钟最多 5 次）",
        )
    bucket.append(now)


def codex_openai_base_url(base_url: str) -> str:
    """Codex CLI openai_base_url 通常指向 …/v1（与 OpenAI SDK 一致）。"""
    u = base_url.rstrip("/")
    if u.endswith("/v1"):
        return u
    return f"{u}/v1"


def codex_cli_available() -> bool:
    return shutil.which(CODEX_CLI_BIN) is not None


def build_diagnosis(
    *,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    agent_output: str,
    timed_out: bool,
) -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}\n{agent_output}".lower()
    note_parts: list[str] = []

    key_valid: bool | None = None
    real_accepted = False
    likely_codex_only: bool | None = None

    if timed_out:
        note_parts.append("执行超时，已终止子进程。")
        return {
            "keyValid": None,
            "realCodexClientAccepted": False,
            "likelyCodexOnlyKey": None,
            "note": " ".join(note_parts) or "Codex CLI 调用超时。",
        }

    output_text = (agent_output or stdout).strip()
    if exit_code == 0 and output_text:
        real_accepted = "ok" in output_text.lower() or len(output_text) > 0
        key_valid = True
        likely_codex_only = True
        note_parts.append("该 Key 可以通过 Codex CLI 真实客户端调用。")
        return {
            "keyValid": key_valid,
            "realCodexClientAccepted": real_accepted,
            "likelyCodexOnlyKey": likely_codex_only,
            "note": " ".join(note_parts),
        }

    if re.search(r"only allows codex|codex cli client|codex.?only|originator", combined):
        note_parts.append(
            "普通 HTTP 请求会失败；当前 Codex CLI 仍被拒绝，可能是环境变量未生效、"
            "客户端版本不匹配或中转站校验更严格（如 Originator 请求头）。"
        )
        likely_codex_only = True
        key_valid = None
    elif re.search(r"rate limit|limit exceeded|quota", combined):
        note_parts.append("Key 或上游账号可能触发限额。")
    elif re.search(r"unauthorized|invalid api key|authentication|401", combined):
        note_parts.append("Key 无效或认证方式不匹配。")
        key_valid = False
    elif re.search(r"model.*not found|unsupported model|not found.*model", combined):
        note_parts.append("模型名可能不受支持，请检查 Model。")
    else:
        note_parts.append(
            "Codex CLI 调用失败，请检查 Base URL、Key、Model 或服务端 codex 命令是否可用。"
        )

    return {
        "keyValid": key_valid,
        "realCodexClientAccepted": False,
        "likelyCodexOnlyKey": likely_codex_only,
        "note": " ".join(note_parts),
    }


async def run_codex_cli_test(
    *,
    base_url: str,
    api_key: str,
    model: str | None,
    prompt: str,
    timeout_ms: int,
) -> dict[str, Any]:
    if not codex_cli_available():
        return {
            "ok": False,
            "type": "codex_cli",
            "status": "server_missing_codex_cli",
            "message": "Server has not installed Codex CLI",
            "stdout": "",
            "stderr": "",
            "agentOutput": "",
            "exitCode": None,
            "durationMs": 0,
            "timedOut": False,
            "maskedKey": mask_api_key(api_key),
            "baseUrl": base_url,
            "openaiBaseUrl": codex_openai_base_url(base_url),
            "model": model or "",
            "diagnosis": {
                "keyValid": None,
                "realCodexClientAccepted": False,
                "likelyCodexOnlyKey": None,
                "note": (
                    "服务端未检测到 codex 命令。请先在服务器安装 Codex CLI，"
                    "并确认运行 Web 服务的进程 PATH 可以执行 codex --version。"
                ),
            },
        }

    openai_base = codex_openai_base_url(base_url)
    codex_home = tempfile.mkdtemp(prefix="codex-cli-test-")
    output_path = os.path.join(codex_home, "last_message.txt")

    env = os.environ.copy()
    env["OPENAI_API_KEY"] = api_key
    env["CODEX_API_KEY"] = api_key
    env["CODEX_HOME"] = codex_home
    for key in (
        "CODEX_ACCESS_TOKEN",
        "OPENAI_API_KEY_FILE",
    ):
        env.pop(key, None)

    args: list[str] = [
        CODEX_CLI_BIN,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "-c",
        f'openai_base_url="{openai_base}"',
        "--sandbox",
        "read-only",
        "--dangerously-bypass-approvals-and-sandbox",
        "-o",
        output_path,
    ]
    if model:
        args.extend(["-m", model])
    args.append(prompt)

    start = time.monotonic()
    timed_out = False
    proc: asyncio.subprocess.Process | None = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_ms / 1000.0,
        )
        exit_code = proc.returncode
    except asyncio.TimeoutError:
        timed_out = True
        if proc and proc.returncode is None:
            proc.kill()
            try:
                await proc.wait()
            except ProcessLookupError:
                pass
        stdout_b, stderr_b = b"", b""
        exit_code = None
    except FileNotFoundError:
        shutil.rmtree(codex_home, ignore_errors=True)
        return {
            "ok": False,
            "type": "codex_cli",
            "status": "server_missing_codex_cli",
            "message": "Server has not installed Codex CLI",
            "stdout": "",
            "stderr": "",
            "agentOutput": "",
            "exitCode": None,
            "durationMs": int((time.monotonic() - start) * 1000),
            "timedOut": False,
            "maskedKey": mask_api_key(api_key),
            "baseUrl": base_url,
            "openaiBaseUrl": openai_base,
            "model": model or "",
            "diagnosis": {
                "keyValid": None,
                "realCodexClientAccepted": False,
                "likelyCodexOnlyKey": None,
                "note": f"无法执行 {CODEX_CLI_BIN}，请检查 PATH 或设置 CODEX_CLI_PATH。",
            },
        }

    duration_ms = int((time.monotonic() - start) * 1000)
    stdout = (stdout_b or b"").decode("utf-8", errors="replace")[:8000]
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")[:8000]

    agent_output = ""
    try:
        if os.path.isfile(output_path):
            agent_output = open(output_path, encoding="utf-8", errors="replace").read()[:8000]
    except OSError:
        agent_output = ""
    finally:
        shutil.rmtree(codex_home, ignore_errors=True)

    diagnosis = build_diagnosis(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        agent_output=agent_output,
        timed_out=timed_out,
    )

    ok = (
        not timed_out
        and exit_code == 0
        and bool((agent_output or stdout).strip())
        and diagnosis.get("realCodexClientAccepted")
    )

    status = "success" if ok else ("timeout" if timed_out else "failed")
    message = (
        "Codex CLI real client test succeeded"
        if ok
        else ("Codex CLI real client test timed out" if timed_out else "Codex CLI real client test failed")
    )

    return {
        "ok": ok,
        "type": "codex_cli",
        "status": status,
        "message": message,
        "stdout": stdout,
        "stderr": stderr,
        "agentOutput": agent_output,
        "exitCode": exit_code,
        "durationMs": duration_ms,
        "timedOut": timed_out,
        "maskedKey": mask_api_key(api_key),
        "baseUrl": base_url,
        "openaiBaseUrl": openai_base,
        "model": model or "",
        "diagnosis": diagnosis,
    }
