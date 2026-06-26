# codebuddy2api

CodeBuddy CN reverse proxy with an OpenAI-compatible API surface.

`codebuddy2api` is a small standalone gateway for private deployment. You can add
one or more CodeBuddy accounts in the web console, create local API keys, and use
OpenAI-compatible clients such as Codex through `/v1`.

## Features

- Web admin console at `/`.
- Add CodeBuddy upstream accounts by API key or bearer token.
- Encrypt upstream secrets in SQLite with `CB2PAI_MASTER_KEY`.
- Create local client keys (`sk-cb-...`), stored as SHA-256 hashes.
- Basic account scheduling, weights, priorities, and failover.
- Request logs with model, account, token usage, status, and latency.
- OpenAI-compatible endpoints:
  - `GET /v1/models`
  - `POST /v1/chat/completions`
  - `POST /v1/responses`
  - `POST /responses`
  - `POST /responses/compact`
  - `POST /backend-api/codex/responses`
  - `POST /backend-api/codex/responses/compact`
- Streaming SSE and non-stream aggregation.
- Basic Codex Responses event lifecycle:
  - `response.created`
  - `response.output_item.added`
  - `response.content_part.added`
  - `response.output_text.delta`
  - `response.output_text.done`
  - `response.content_part.done`
  - `response.output_item.done`
  - `response.completed`
- Basic function call and function result mapping for Codex-style calls.

## Limits

This is a fast standalone build, not a full sub2api fork.

- Native Responses WebSocket mode is not implemented.
- Full computer-use/image tool bridging is not implemented.
- Quota management is basic.
- Keep it private unless you add HTTPS, strong admin credentials, and network
  restrictions.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export CB2PAI_ADMIN_TOKEN="change-this-admin-token"
export CB2PAI_MASTER_KEY="change-this-master-key-at-least-32-chars"
export CB2PAI_DEFAULT_ENDPOINT="https://copilot.tencent.com"
python server.py
```

Open:

```text
http://127.0.0.1:8787
```

Enter the admin token in the console, add a CodeBuddy account, then create a
client API key.

## Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:CB2PAI_ADMIN_TOKEN="change-this-admin-token"
$env:CB2PAI_MASTER_KEY="change-this-master-key-at-least-32-chars"
$env:CB2PAI_DEFAULT_ENDPOINT="https://copilot.tencent.com"
python server.py
```

## Docker

Edit secrets in `docker-compose.yml`, then:

```bash
docker compose up -d --build
```

The database is stored in `./data`.

## Environment Variables

| Name | Default | Description |
| --- | --- | --- |
| `CB2PAI_ADMIN_TOKEN` | `change-this-admin-token` | Admin console/API bearer token. |
| `CB2PAI_MASTER_KEY` | `change-this-master-key-at-least-32-chars` | Encryption key for upstream secrets. Keep it stable. |
| `CB2PAI_HOST` | `0.0.0.0` | Listen host. |
| `CB2PAI_PORT` | `8787` | Listen port. |
| `CB2PAI_DB_PATH` | `data/codebuddy2api.db` | SQLite database path. |
| `CB2PAI_DEFAULT_ENDPOINT` | `https://www.codebuddy.ai` | Default upstream endpoint. |

If you change `CB2PAI_MASTER_KEY`, existing encrypted upstream secrets cannot be
decrypted.

## Add CodeBuddy CN Account

Use the web console, or call the admin API:

```bash
curl -X POST http://127.0.0.1:8787/admin/accounts \
  -H "Authorization: Bearer change-this-admin-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "codebuddy-cn",
    "auth_type": "api_key",
    "endpoint": "https://copilot.tencent.com",
    "domain": "copilot.tencent.com",
    "api_key": "YOUR_CODEBUDDY_API_KEY",
    "user_id": "anonymous",
    "status": "active",
    "weight": 1,
    "priority": 0
  }'
```

For bearer-token mode, set `auth_type` to `bearer` and pass `access_token`
instead of `api_key`.

## Create Client API Key

```bash
curl -X POST http://127.0.0.1:8787/admin/api-keys \
  -H "Authorization: Bearer change-this-admin-token" \
  -H "Content-Type: application/json" \
  -d '{"name":"codex"}'
```

The full key is only returned once.

## OpenAI-Compatible Usage

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-cb-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5.1",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": false
  }'
```

Streaming:

```bash
curl -N http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-cb-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5.1",
    "messages": [{"role": "user", "content": "hello"}],
    "stream": true
  }'
```

## Codex Usage

Use the OpenAI-compatible base URL when possible:

```text
OPENAI_BASE_URL=http://127.0.0.1:8787/v1
OPENAI_API_KEY=sk-cb-...
```

The gateway maps Codex HTTP `/v1/responses` requests onto CodeBuddy chat
completions and emits Responses-style SSE events. It also exposes direct aliases
for clients that call:

```text
/backend-api/codex/responses
/backend-api/codex/responses/compact
```

## API Summary

Admin endpoints require:

```text
Authorization: Bearer <CB2PAI_ADMIN_TOKEN>
```

Client endpoints require a generated local key when at least one key exists:

```text
Authorization: Bearer sk-cb-...
```

Admin:

- `GET /admin/accounts`
- `POST /admin/accounts`
- `PUT /admin/accounts/{id}`
- `DELETE /admin/accounts/{id}`
- `POST /admin/accounts/{id}/test`
- `GET /admin/api-keys`
- `POST /admin/api-keys`
- `DELETE /admin/api-keys/{id}`
- `GET /admin/logs`

Client:

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /responses`
- `POST /responses/compact`
- `POST /backend-api/codex/responses`
- `POST /backend-api/codex/responses/compact`

## Model Aliases

Some common OpenAI-style model names are mapped to CodeBuddy model names in
`app/proxy.py`, for example:

- `gpt-4o` -> `glm-5.2`
- `gpt-4o-mini` -> `glm-5.1`
- `deepseek-chat` -> `deepseek-v4-pro`
- `moonshot-v1-128k` -> `kimi-k2.7`

You can call CodeBuddy model names directly as well.

## Security Notes

- Do not commit `data/*.db`, `.env`, or local virtualenv files.
- Use a strong `CB2PAI_ADMIN_TOKEN`.
- Use a long random `CB2PAI_MASTER_KEY`.
- Put the service behind HTTPS before remote use.
- Restrict access by firewall, reverse proxy auth, or private network.

## sub2api

See [SUB2API_INTEGRATION.md](SUB2API_INTEGRATION.md) for notes about integrating
CodeBuddy CN as an upstream type in sub2api.
