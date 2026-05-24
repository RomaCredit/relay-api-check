"""Path whitelist tests for api-check-proxy."""
from main import _relay_api_path_allowed


def test_root_v1_paths():
    assert _relay_api_path_allowed("/v1/chat/completions")
    assert _relay_api_path_allowed("/api/v1/responses")


def test_group_prefix_paths():
    assert _relay_api_path_allowed("/codex-pro/v1/chat/completions")
    assert _relay_api_path_allowed("/claude-aws/v1/messages")
    assert _relay_api_path_allowed("/api/openai/v1/chat/completions")


def test_rejects_unsafe():
    assert not _relay_api_path_allowed("/../v1/chat/completions")
    assert not _relay_api_path_allowed("/admin/login")
    assert not _relay_api_path_allowed("/v1/other")
