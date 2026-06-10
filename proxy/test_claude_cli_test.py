"""Tests for Claude CLI test helpers."""
from claude_cli_test import (
    build_diagnosis,
    mask_api_key,
    normalize_base_url,
)
import pytest
from fastapi import HTTPException


def test_mask_api_key():
    assert mask_api_key("sk-abcdefghijklmnop") == "sk-abc...mnop"
    assert mask_api_key("short") == "***"


def test_normalize_base_url():
    assert normalize_base_url("https://api.example.com/") == "https://api.example.com"


def test_normalize_rejects_bad_scheme():
    with pytest.raises(HTTPException):
        normalize_base_url("ftp://example.com")


def test_diagnosis_success():
    d = build_diagnosis(exit_code=0, stdout="OK", stderr="", timed_out=False)
    assert d["realClaudeClientAccepted"] is True
    assert d["keyValid"] is True


def test_diagnosis_claude_code_only_message():
    d = build_diagnosis(
        exit_code=1,
        stdout="",
        stderr="only allows Claude Code clients",
        timed_out=False,
    )
    assert d["realClaudeClientAccepted"] is False
    assert "Claude CLI" in d["note"]


def test_diagnosis_cloudflare_blocked():
    d = build_diagnosis(
        exit_code=1,
        stdout="Failed to authenticate. API Error: Attention Required! | Cloudflare\n",
        stderr="",
        timed_out=False,
    )
    assert d["code"] == "cloudflare_blocked"
    assert "Cloudflare" in d["note"]
    assert "api.romaapi.com" in d["note"]
