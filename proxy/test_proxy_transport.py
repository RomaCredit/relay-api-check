"""Tests for PROXY_LIST / Cloudflare block detection."""
from proxy_transport import is_blocked_response, parse_proxy_list, proxy_attempts


def test_parse_proxy_list():
    assert parse_proxy_list("") == []
    assert parse_proxy_list("socks5://hub-warp:1080") == ["socks5://hub-warp:1080"]
    assert parse_proxy_list("a,b, c") == ["a", "b", "c"]


def test_proxy_attempts_includes_direct():
    attempts = proxy_attempts()
    assert attempts[0] == ("direct", None)


def test_blocked_cloudflare_html():
    body = "<html><title>Attention Required! | Cloudflare</title>Sorry, you have been blocked"
    assert is_blocked_response(403, body, "text/html; charset=UTF-8")


def test_not_blocked_json_api_error():
    body = '{"error":{"message":"无效的令牌","type":"packy_api_error"}}'
    assert not is_blocked_response(401, body, "application/json")
    assert not is_blocked_response(403, body, "application/json")


def test_not_blocked_relay_upstream_error():
    body = '{"error":{"message":"No active API keys available for this group"}}'
    assert not is_blocked_response(403, body, "application/json")
