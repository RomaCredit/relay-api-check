"""Claude CLI real-client test — spawn `claude -p` with injected env (no shell)."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, field_validator

# 每分钟每 IP 最多 5 次 Claude CLI 测试
_RATE_LIMIT_WINDOW_SEC = 60
_RATE_LIMIT_MAX = 5
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)

CLAUDE_CLI_BIN = os.getenv("CLAUDE_CLI_PATH", "claude").strip() or "claude"

DEFAULT_PROMPT = "Reply with only: OK"
MAX_PROMPT_LEN = 1000
MAX_BASE_URL_LEN = 300
MAX_MODEL_LEN = 100
MAX_TIMEOUT_MS = 60_000
DEFAULT_TIMEOUT_MS = 30_000


class ClaudeCliTestRequest(BaseModel):
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


def mask_api_key(key: str) -> str:
    k = (key or "").strip()
    if len(k) <= 10:
        return "***"
    return f"{k[:6]}...{k[-4:]}"


def client_ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for")
    if xf:
        return xf.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Claude CLI 测试过于频繁，请稍后再试（每 IP 每分钟最多 5 次）",
        )
    bucket.append(now)


def normalize_base_url(url: str) -> str:
    u = url.strip().rstrip("/")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="baseUrl 须为 http 或 https")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="无效的 baseUrl")
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=400, detail="禁止访问本地地址")
    return u


def validate_base_url_ssrf(base_url: str, resolve_host_ips) -> str:
    """复用 main 的 DNS 解析与内网拦截（由调用方注入 resolve_host_ips）。"""
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    for ip in resolve_host_ips(host):
        # 与 main._is_blocked_ip 一致
        import ipaddress

        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效主机") from None
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        ):
            raise HTTPException(status_code=400, detail="禁止访问内网或保留地址")
    return base_url


def claude_cli_available() -> bool:
    return shutil.which(CLAUDE_CLI_BIN) is not None


def build_diagnosis(
    *,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    timed_out: bool,
) -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}".lower()
    note_parts: list[str] = []

    key_valid: bool | None = None
    real_accepted = False
    likely_code_only: bool | None = None

    if timed_out:
        note_parts.append("执行超时，已终止子进程。")
        return {
            "keyValid": None,
            "realClaudeClientAccepted": False,
            "likelyClaudeCodeOnlyKey": None,
            "note": " ".join(note_parts) or "Claude CLI 调用超时。",
        }

    if exit_code == 0 and stdout.strip():
        real_accepted = "ok" in stdout.lower() or len(stdout.strip()) > 0
        key_valid = True
        likely_code_only = True
        note_parts.append("该 Key 可以通过 Claude CLI 真实客户端调用。")
        return {
            "keyValid": key_valid,
            "realClaudeClientAccepted": real_accepted,
            "likelyClaudeCodeOnlyKey": likely_code_only,
            "note": " ".join(note_parts),
        }

    if re.search(r"only allows claude code clients|claude code client", combined):
        note_parts.append(
            "普通 HTTP 请求会失败；当前 Claude CLI 仍被拒绝，可能是环境变量未生效、"
            "客户端版本不匹配或中转站校验更严格。"
        )
        likely_code_only = True
        key_valid = None
    elif re.search(r"cloudflare|attention required|you have been blocked", combined):
        note_parts.append(
            "检测站 VPS 直连 Base URL 时被 Cloudflare/WAF 拦截（非 Key 无效）。"
            "Claude CLI 子进程不走服务端 WARP 代理；请改用 Anthropic Messages + 服务端代理，"
            "或换无 Cloudflare 的 Base URL（RomaAPI 请用 https://api.romaapi.com）。"
        )
        return {
            "keyValid": None,
            "realClaudeClientAccepted": False,
            "likelyClaudeCodeOnlyKey": likely_code_only,
            "code": "cloudflare_blocked",
            "note": " ".join(note_parts),
        }
    elif re.search(r"rate limit|limit exceeded|quota", combined):
        note_parts.append("Key 或上游账号可能触发限额。")
    elif re.search(r"unauthorized|invalid api key|authentication|401", combined):
        note_parts.append("Key 无效或认证方式不匹配。")
        key_valid = False
    elif re.search(
        r"model.*not found|unsupported model|not found.*model|issue with the selected model",
        combined,
    ):
        note_parts.append(
            "模型名可能不受支持，或 Base URL 不正确（RomaAPI 须为 https://api.romaapi.com，"
            "不要用 https://romaapi.com）。"
        )
    else:
        note_parts.append(
            "Claude CLI 调用失败，请检查 Base URL、Key、Model 或服务端 claude 命令是否可用。"
        )

    return {
        "keyValid": key_valid,
        "realClaudeClientAccepted": False,
        "likelyClaudeCodeOnlyKey": likely_code_only,
        "note": " ".join(note_parts),
    }


async def run_claude_cli_test(
    *,
    base_url: str,
    api_key: str,
    model: str | None,
    prompt: str,
    timeout_ms: int,
) -> dict[str, Any]:
    if not claude_cli_available():
        return {
            "ok": False,
            "type": "claude_cli",
            "status": "server_missing_claude_cli",
            "message": "Server has not installed Claude CLI",
            "stdout": "",
            "stderr": "",
            "exitCode": None,
            "durationMs": 0,
            "timedOut": False,
            "maskedKey": mask_api_key(api_key),
            "baseUrl": base_url,
            "model": model or "",
            "diagnosis": {
                "keyValid": None,
                "realClaudeClientAccepted": False,
                "likelyClaudeCodeOnlyKey": None,
                "note": (
                    "服务端未检测到 claude 命令。请先在服务器安装 Claude Code / Claude CLI，"
                    "并确认运行 Web 服务的进程 PATH 可以执行 claude --version。"
                ),
            },
        }

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = api_key
    env["ANTHROPIC_API_KEY"] = ""
    if model:
        env["ANTHROPIC_MODEL"] = model
    else:
        env.pop("ANTHROPIC_MODEL", None)

    start = time.monotonic()
    timed_out = False
    proc: asyncio.subprocess.Process | None = None

    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_CLI_BIN,
            "-p",
            prompt,
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
        return {
            "ok": False,
            "type": "claude_cli",
            "status": "server_missing_claude_cli",
            "message": "Server has not installed Claude CLI",
            "stdout": "",
            "stderr": "",
            "exitCode": None,
            "durationMs": int((time.monotonic() - start) * 1000),
            "timedOut": False,
            "maskedKey": mask_api_key(api_key),
            "baseUrl": base_url,
            "model": model or "",
            "diagnosis": {
                "keyValid": None,
                "realClaudeClientAccepted": False,
                "likelyClaudeCodeOnlyKey": None,
                "note": f"无法执行 {CLAUDE_CLI_BIN}，请检查 PATH 或设置 CLAUDE_CLI_PATH。",
            },
        }

    duration_ms = int((time.monotonic() - start) * 1000)
    stdout = (stdout_b or b"").decode("utf-8", errors="replace")[:8000]
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")[:8000]

    diagnosis = build_diagnosis(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )

    ok = (
        not timed_out
        and exit_code == 0
        and bool(stdout.strip())
        and diagnosis.get("realClaudeClientAccepted")
    )

    status = "success" if ok else ("timeout" if timed_out else "failed")
    message = (
        "Claude CLI real client test succeeded"
        if ok
        else ("Claude CLI real client test timed out" if timed_out else "Claude CLI real client test failed")
    )

    return {
        "ok": ok,
        "type": "claude_cli",
        "status": status,
        "message": message,
        "stdout": stdout,
        "stderr": stderr,
        "exitCode": exit_code,
        "durationMs": duration_ms,
        "timedOut": timed_out,
        "maskedKey": mask_api_key(api_key),
        "baseUrl": base_url,
        "model": model or "",
        "diagnosis": diagnosis,
    }
