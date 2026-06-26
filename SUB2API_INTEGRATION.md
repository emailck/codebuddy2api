# sub2api integration notes

`codebuddy2api` is currently a standalone FastAPI service. It can be integrated
with sub2api, but it is not a drop-in plugin because sub2api owns routing,
account scheduling, quota accounting, and the admin frontend in its Go/Vue code.

## Recommended path

1. Add a new account type in sub2api, for example `codebuddy_cn`.
2. Store CodeBuddy API key / bearer token in sub2api's encrypted account secret
   fields.
3. Reuse sub2api's OpenAI-compatible ingress:
   - `/v1/chat/completions`
   - `/v1/responses`
   - `/backend-api/codex/responses`
4. Add an upstream adapter that sends requests to:
   - `https://copilot.tencent.com/v2/chat/completions`
5. Reuse sub2api's existing Chat Completions to Responses bridge for Codex
   event lifecycle.
6. Add frontend account forms for CodeBuddy endpoint, domain, auth type, user id,
   priority, weight, and account test.

## Headers required by the current standalone adapter

```text
Authorization: Bearer <codebuddy-api-key-or-access-token>
X-API-Key: <codebuddy-api-key>        # API key mode
X-User-Id: anonymous
X-Domain: copilot.tencent.com
X-Agent-Intent: craft
X-IDE-Type: CLI
X-IDE-Name: CLI
X-IDE-Version: 1.0.7
X-Product: SaaS
User-Agent: CLI/1.0.7 CodeBuddy/1.0.7
```

## Minimal standalone compatibility already implemented here

- OpenAI Chat Completions endpoint.
- OpenAI Responses endpoint.
- Codex direct Responses aliases.
- Streaming Responses lifecycle with terminal `response.completed`.
- Basic function call and function result mapping.

## Not implemented in the quick standalone build

- Native Responses WebSocket mode.
- Full computer-use/image bridge.
- sub2api quota, channel monitor, and frontend integration.
