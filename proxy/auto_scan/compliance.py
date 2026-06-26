"""协议合规检查（对齐 frontend runComplianceCheck 核心项）。"""
from __future__ import annotations

import json
import re
from typing import Any

from auto_scan.models import CheckStatus, ComplianceCheck, ProtocolCellResult

PATH_SUFFIX = {
    "chat": "/v1/chat/completions",
    "responses": "/v1/responses",
    "anthropic": "/v1/messages",
}

CLAUDE_CODE_ONLY_RE = re.compile(
    r"only allows claude code clients|claude code client",
    re.I,
)


def detect_endpoint_for_model(model: str) -> str:
    m = model.lower().strip()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gpt-5."):
        return "responses"
    if m.startswith("gemini"):
        return "responses"
    return "chat"


def summarize_checks(checks: list[ComplianceCheck]) -> CheckStatus:
    if any(c.status == "fail" for c in checks):
        path = next((c for c in checks if c.id == "path"), None)
        http = next((c for c in checks if c.id == "http"), None)
        if path and path.status == "pass" and http and http.status == "pass":
            return "warn"
        return "fail"
    if any(c.status == "warn" for c in checks):
        return "warn"
    if any(c.status == "pass" for c in checks):
        return "pass"
    return "unknown"


def diagnose_error_body(
    *,
    http_status: int | None,
    raw_body: str,
    endpoint: str,
) -> tuple[str | None, str | None]:
    text = raw_body[:2000].lower()
    if CLAUDE_CODE_ONLY_RE.search(text):
        return "claude_code_only", "Key 可能仅支持 Claude Code / CLI 客户端"
    if http_status == 404 and "route not found" in text:
        return "route_not_found", "中转站未实现该路由"
    if http_status in (401, 403) or "unauthorized" in text or "invalid api key" in text:
        return "auth_failed", "认证失败或 Key 无效"
    if "<html" in text or "<!doctype" in text:
        return "html_not_api", "返回 HTML 而非 API JSON"
    if endpoint == "chat" and "gpt-5." in text:
        return "wrong_endpoint", "GPT-5.x 可能应走 Responses 端点"
    return None, raw_body[:240] if raw_body else None


def parse_json_body(raw: str) -> Any | None:
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def extract_text_from_response(endpoint: str, data: Any, stream: bool) -> str:
    if not isinstance(data, dict):
        return ""
    if endpoint == "anthropic":
        content = data.get("content")
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            return "".join(parts)
        return str(data.get("text") or "")
    if endpoint == "responses":
        out = data.get("output")
        if isinstance(out, list):
            for item in out:
                if isinstance(item, dict) and item.get("type") == "message":
                    content = item.get("content")
                    if isinstance(content, list):
                        return "".join(
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") in ("output_text", "text")
                        )
        return str(data.get("output_text") or "")
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0]
        if isinstance(ch0, dict):
            msg = ch0.get("message") or ch0.get("delta") or {}
            if isinstance(msg, dict):
                return str(msg.get("content") or "")
    return ""


def parse_sse_events(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


def run_compliance(
    cell: ProtocolCellResult,
    *,
    request_headers: dict[str, str],
    request_body: dict[str, Any],
    raw_body: str,
    stream_requested: bool,
) -> list[ComplianceCheck]:
    checks: list[ComplianceCheck] = []
    ep = cell.endpoint

    def add(cid: str, label: str, status: CheckStatus, detail: str = "") -> None:
        checks.append(ComplianceCheck(id=cid, label=label, status=status, detail=detail))

    suffix = PATH_SUFFIX.get(ep, "")
    if suffix and suffix in (cell.url.split("?")[0] or ""):
        add("path", "请求路径", "pass", suffix)
    elif suffix:
        add("path", "请求路径", "fail", f"期望含 {suffix}")

    if ep == "anthropic":
        if request_headers.get("x-api-key") and request_headers.get("anthropic-version"):
            add("auth", "鉴权 (Anthropic)", "pass", "x-api-key + anthropic-version")
        else:
            add("auth", "鉴权 (Anthropic)", "fail", "缺少 Anthropic 头")
    else:
        auth = request_headers.get("Authorization") or request_headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            add("auth", "鉴权 (Bearer)", "pass", "Authorization: Bearer")
        else:
            add("auth", "鉴权 (Bearer)", "fail", "缺少 Bearer")

    if cell.http_status == 200:
        add("http", "HTTP 状态", "pass", "200")
    elif cell.http_status is not None:
        add("http", "HTTP 状态", "fail" if cell.http_status >= 400 else "warn", str(cell.http_status))

    ct = (cell.content_type or "").lower()
    is_sse = "text/event-stream" in ct

    if not cell.success:
        code, msg = diagnose_error_body(http_status=cell.http_status, raw_body=raw_body, endpoint=ep)
        cell.diagnosis_code = code
        add("body", "响应体", "fail", msg or cell.error_message or "请求失败")
        if code == "claude_code_only":
            add("agent-hint", "Agent 提示", "warn", "建议运行 Claude CLI 探测")
        return checks

    if stream_requested and is_sse:
        add("stream-ct", "流式 Content-Type", "pass", cell.content_type or "")
        events = parse_sse_events(raw_body)
        if ep == "anthropic":
            types = {e.get("type") for e in events}
            for t in ("message_start", "content_block_delta", "message_stop"):
                if t in types:
                    add(f"sse-{t}", f"SSE · {t}", "pass", "已收到")
                else:
                    add(f"sse-{t}", f"SSE · {t}", "warn", "未收到")
        text = ""
        for ev in events:
            text += extract_text_from_response(ep, ev, True)
        if text.strip():
            add("content", "回复内容", "pass", text.strip()[:80])
        else:
            add("content", "回复内容", "warn", "流式无文本增量")
    else:
        data = parse_json_body(raw_body)
        if data is None and raw_body.strip():
            add("body", "JSON 解析", "fail", "非 JSON 响应")
            return checks
        if isinstance(data, dict) and data.get("error"):
            add("api-result", "业务错误", "fail", str(data.get("error"))[:120])
        text = extract_text_from_response(ep, data, False)
        if text.strip():
            add("content", "回复内容", "pass", text.strip()[:80])
        else:
            add("content", "回复内容", "warn", "无可见文本")
        usage = data.get("usage") if isinstance(data, dict) else None
        if usage:
            add("usage", "Token 用量", "pass", json.dumps(usage)[:100])
        else:
            add("usage", "Token 用量", "warn", "未见 usage")

    if request_body.get("temperature") is not None:
        add("temperature", "temperature", "pass", str(request_body["temperature"]))
    return checks
