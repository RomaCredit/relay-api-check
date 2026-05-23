# Relay API Check

Browser-based **relay / API gateway compatibility checker** for OpenAI-compatible and Anthropic Messages endpoints.

Test whether a third-party relay matches expected routes, auth headers, streaming (SSE), and response shapes — with raw request/response JSON and a compliance checklist.

**Repository:** [github.com/RomaCredit/relay-api-check](https://github.com/RomaCredit/relay-api-check)

Live demo: [https://check.romaapi.com](https://check.romaapi.com)

## Features

- **Three API shapes**: OpenAI Chat (`/v1/chat/completions`), OpenAI Responses (`/v1/responses`), Anthropic Messages (`/v1/messages`)
- **Auto endpoint routing** by model name (e.g. `claude*` → Anthropic, `gpt-5.4` → Responses)
- **CORS proxy** (`/api/proxy`) for browser-side testing
- **Tabs**: text reply, response detail (raw HTTP + body), request detail, compliance checks
- **One-click test scenarios** and diagnostic report export

## Quick start (Docker)

```bash
cd relay-api-check
cp .env.example .env
docker compose up -d --build
```

Open **http://localhost:8080**

- Frontend: static `frontend/index.html`
- Proxy: `http://proxy:8090` (exposed only inside compose network; Caddy routes `/api/proxy`)

## Configuration

| Variable | Description |
|----------|-------------|
| `ALLOWED_ORIGINS` | Comma-separated origins allowed to call `/api/proxy` (must match your UI URL) |

See [SECURITY.md](./SECURITY.md) before exposing the proxy publicly.

## Deploy behind your own domain

1. Copy `deploy/Caddyfile.standalone` or adapt `deploy/nginx.conf.example`
2. Set `ALLOWED_ORIGINS=https://check.yourdomain.com`
3. Mount `frontend/` as static files; reverse-proxy `/api/proxy` to the proxy service on port 8090

### RomaAPI production (this monorepo)

`check.romaapi.com` is wired in the root `docker-compose.yml` + `caddy/Caddyfile`:

```bash
cd /path/to/romaapi.com
docker compose up -d --build api-check-proxy caddy
```

## Static-only mode (no proxy)

If your relay already sends CORS headers, you can host **only** `frontend/index.html` on any static server and disable **「服务端代理」** in the UI.

Without the proxy, cross-origin relays will fail in the browser with CORS errors.

## Project layout

```
relay-api-check/
├── frontend/index.html   # Single-page app (no build step)
├── proxy/                # FastAPI CORS proxy
├── deploy/               # Caddy / Nginx examples
├── docker-compose.yml    # Standalone stack
├── LICENSE
└── SECURITY.md
```

## License

MIT — see [LICENSE](./LICENSE).
