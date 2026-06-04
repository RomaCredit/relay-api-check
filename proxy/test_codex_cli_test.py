"""Tests for Codex CLI test helpers."""
import pytest
from fastapi import HTTPException

from codex_cli_test import (
    build_diagnosis,
    codex_openai_base_url,
    normalize_base_url,
)


def test_codex_openai_base_url():
    assert codex_openai_base_url("https://relay.example.com/codex-pro") == (
        "https://relay.example.com/codex-pro/v1"
    )
    assert codex_openai_base_url("https://relay.example.com/v1") == (
        "https://relay.example.com/v1"
    )


def test_normalize_rejects_localhost():
    with pytest.raises(HTTPException):
        normalize_base_url("http://127.0.0.1")


def test_diagnosis_success():
    d = build_diagnosis(
        exit_code=0,
        stdout="",
        stderr="",
        agent_output="OK",
        timed_out=False,
    )
    assert d["realCodexClientAccepted"] is True
    assert d["keyValid"] is True


def test_diagnosis_codex_only_message():
    d = build_diagnosis(
        exit_code=1,
        stdout="",
        stderr="only allows Codex CLI clients",
        agent_output="",
        timed_out=False,
    )
    assert d["realCodexClientAccepted"] is False
    assert "Codex CLI" in d["note"]
