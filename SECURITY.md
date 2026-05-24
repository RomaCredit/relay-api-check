# Security

Relay API Check includes an **HTTPS forward proxy** (`POST /api/proxy`) so browsers can test third-party relay APIs without CORS errors.

## Risks

If you expose the proxy to the public internet **without restrictions**, attackers may use it as an open proxy to reach allowed upstream API paths.

## Built-in mitigations

- Upstream URLs must use **HTTPS**
- **SSRF protection**: private/reserved IPs are blocked after DNS resolution
- Only relay API paths are allowed: `/v1/{chat/completions,responses,messages,models}`, `/api/v1/...`, or **one or more safe path segments** before `/v1/...` (e.g. Right Code `/codex-pro/v1/chat/completions`)

## Recommended deployment

1. Set `ALLOWED_ORIGINS` to **your frontend origin only** (never `*` in production).
2. Prefer deploying behind VPN, IP allowlist, or Basic Auth at the reverse proxy.
3. Do not expose the proxy on a domain you do not control.
4. Monitor logs for unusual traffic patterns.

Report security issues privately to the repository maintainers before public disclosure.
