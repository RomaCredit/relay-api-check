# Relay API Check

Browser-based **relay / API gateway compatibility checker** for OpenAI-compatible and Anthropic Messages endpoints.

Test whether a third-party relay matches expected routes, auth headers, streaming (SSE), and response shapes — with raw request/response JSON and a compliance checklist.

**Repository:** [github.com/RomaCredit/relay-api-check](https://github.com/RomaCredit/relay-api-check)

Live demo: [https://check.romaapi.com](https://check.romaapi.com)

## Features

- **Three API shapes**: OpenAI Chat (`/v1/chat/completions`), OpenAI Responses (`/v1/responses`), Anthropic Messages (`/v1/messages`)
- **Claude CLI real-client test** (`/api/test-claude-cli`) for keys that only accept Claude Code / Claude CLI (not plain HTTP)
- **Auto endpoint routing** by model name (e.g. `claude*` → Anthropic, `gpt-5.4` → Responses)
- **CORS proxy** (`/api/proxy`) for browser-side testing
- **Tabs**: text reply, response detail (raw HTTP + body), request detail, compliance checks
- **One-click test scenarios** and diagnostic report export
- **Auto scan** (`/api/auto-scan`): server-orchestrated discovery, protocol matrix, Agent probes (quick / standard / deep profiles); progress via SSE; optional Hub suite ingest

## Quick start (Docker)

```bash
cd relay-api-check
cp .env.example .env
docker compose up -d --build
```

Open **http://localhost:8080**

- Frontend: static `frontend/index.html`
- Proxy: `http://proxy:8090` (exposed only inside compose network; Caddy routes `/api/proxy`)

## Claude CLI real-client test

Some relay keys return `only allows Claude Code clients` on `/v1/messages` or `/v1/chat/completions` but work when called via the official **Claude CLI** (`claude -p`).

In the UI, choose **Claude CLI · claude -p 真实客户端**. The backend runs (no shell):

```text
claude -p "Reply with only: OK"
```

with environment:

- `ANTHROPIC_BASE_URL` ← Base URL
- `ANTHROPIC_AUTH_TOKEN` ← API Key
- `ANTHROPIC_MODEL` ← Model (optional)
- `ANTHROPIC_API_KEY` cleared

**Server requirements**

- Must run on a **VPS / Docker / own server** where you can install the CLI.
- **Not suitable** for Vercel, Netlify, Cloudflare Pages, or other serverless hosts.
- Before use, on the **same machine/container** that runs `api-check-proxy`:

  ```bash
  claude --version
  ```

Install Claude Code / Claude CLI per [Anthropic documentation](https://docs.anthropic.com/en/docs/claude-code) (e.g. `npm install -g @anthropic-ai/claude-code` on the host, then ensure the proxy container can execute `claude` via `PATH` or mount the binary).

Optional env:

| Variable | Description |
|----------|-------------|
| `CLAUDE_CLI_PATH` | Path to `claude` binary (default: `claude`) |

API keys are **never** logged or stored; responses only include a masked key (`sk-abc…xyz1`).

## Configuration

| Variable | Description |
|----------|-------------|
| `ALLOWED_ORIGINS` | Comma-separated origins allowed to call `/api/proxy` (must match your UI URL) |
| `CLAUDE_CLI_PATH` | Executable for Claude CLI tests (default `claude`) |
| `HUB_SUITE_INGEST_URL` | Hub `POST /api/relay-api-check/suite-ingest` URL (optional; RomaAPI compose sets this) |
| `RELAY_API_CHECK_INGEST_SECRET` | Shared token header `x-relay-check-ingest-token` for Hub ingest (optional) |

See [SECURITY.md](./SECURITY.md) before exposing the proxy publicly.

## Deploy behind your own domain

1. Copy `deploy/Caddyfile.standalone` or adapt `deploy/nginx.conf.example`
2. Set `ALLOWED_ORIGINS=https://check.yourdomain.com`
3. Mount `frontend/` as static files; reverse-proxy `/api/proxy` to the proxy service on port 8090

### RomaAPI production (this monorepo)

`check.romaapi.com` is wired in the root `docker-compose.yml` + `caddy/Caddyfile`.

**Do not `docker build` on the production VPS** (low memory). Use GitHub Actions + GHCR:

```bash
cd /path/to/romaapi.com
./scripts/deploy-api-check-proxy-via-github.sh
```

Static `frontend/` is bind-mounted; `git pull` updates the UI without rebuilding the proxy image.

## Static-only mode (no proxy)

If your relay already sends CORS headers, you can host **only** `frontend/index.html` on any static server and disable **「服务端代理」** in the UI.

Without the proxy, cross-origin relays will fail in the browser with CORS errors.

## Project layout

Open **自动扫描** in the UI (or `?mode=auto&base=…`) to run a full relay profile without clicking through each protocol manually.

Auto-scan API:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auto-scan` | Start scan `{ baseUrl, apiKey, profile? }` → `{ scanId }` |
| `GET` | `/api/auto-scan/{id}` | Status + report when done |
| `GET` | `/api/auto-scan/{id}/events` | SSE progress |

## Project layout

```
relay-api-check/
├── frontend/index.html   # Manual + auto scan UI
├── frontend/auto-scan.js # Auto scan tab logic
├── proxy/auto_scan/      # Orchestrator, discovery, compliance
├── specs/                # Protocol capability YAML
├── proxy/                # FastAPI CORS proxy
├── deploy/               # Caddy / Nginx examples
├── docker-compose.yml    # Standalone stack
├── LICENSE
└── SECURITY.md
```

## License

MIT — see [LICENSE](./LICENSE).
