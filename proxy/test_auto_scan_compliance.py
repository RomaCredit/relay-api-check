"""合规引擎单测。"""
from auto_scan.compliance import (
    detect_endpoint_for_model,
    run_compliance,
    summarize_checks,
)
from auto_scan.models import ComplianceCheck, ProtocolCellResult


def test_detect_endpoint():
    assert detect_endpoint_for_model("claude-sonnet-4-6") == "anthropic"
    assert detect_endpoint_for_model("gpt-5.4") == "responses"
    assert detect_endpoint_for_model("gpt-4o") == "chat"


def test_compliance_pass_chat_json():
    cell = ProtocolCellResult(
        endpoint="chat",
        stream=False,
        model="gpt-4o",
        url="https://relay.example/v1/chat/completions",
        http_status=200,
        success=True,
        content_type="application/json",
    )
    raw = '{"choices":[{"message":{"role":"assistant","content":"OK"}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
    checks = run_compliance(
        cell,
        request_headers={"Authorization": "Bearer sk-test", "Content-Type": "application/json"},
        request_body={"model": "gpt-4o", "temperature": 0.7, "max_tokens": 64, "stream": False},
        raw_body=raw,
        stream_requested=False,
    )
    assert summarize_checks(checks) in ("pass", "warn")
    assert any(c.id == "path" and c.status == "pass" for c in checks)


def test_compliance_claude_code_only():
    cell = ProtocolCellResult(
        endpoint="anthropic",
        stream=False,
        model="claude-sonnet-4-6",
        url="https://relay.example/v1/messages",
        http_status=403,
        success=False,
        content_type="application/json",
    )
    raw = '{"error":{"message":"only allows Claude Code clients"}}'
    checks = run_compliance(
        cell,
        request_headers={"x-api-key": "sk", "anthropic-version": "2023-06-01"},
        request_body={"model": "claude-sonnet-4-6", "max_tokens": 64},
        raw_body=raw,
        stream_requested=False,
    )
    assert cell.diagnosis_code == "claude_code_only"
    assert summarize_checks(checks) == "fail"
