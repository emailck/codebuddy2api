from typing import Optional
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from . import accounts, db, keys, proxy
from .config import ADMIN_TOKEN, MODEL_PROBE_CANDIDATES
from .security import constant_equals

app = FastAPI(title="codebuddy2api", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    db.init_db()


def bearer_value(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else authorization


def require_admin(authorization: Optional[str] = Header(None)) -> None:
    if not constant_equals(bearer_value(authorization), ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def require_client_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
) -> Optional[dict]:
    configured = keys.list_client_keys()
    if not configured:
        return None
    token = x_api_key or bearer_value(authorization)
    item = keys.verify_client_key(token)
    if not item:
        raise HTTPException(status_code=401, detail={"error": {"message": "Invalid API key", "type": "invalid_request_error"}})
    return item


@app.get("/health")
def health():
    all_accounts = accounts.list_accounts()
    active_accounts = [a for a in all_accounts if a.get("status") == "active"]
    return {"status": "ok", "accounts": len(all_accounts), "active_accounts": len(active_accounts)}


@app.get("/v1/models")
def list_models(_client_key: Optional[dict] = Depends(require_client_key)):
    models = db.get_setting("models", [])
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": 0, "owned_by": "codebuddy"} for m in models],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, client_key: Optional[dict] = Depends(require_client_key)):
    payload = await request.json()
    if not payload.get("messages"):
        raise HTTPException(status_code=400, detail={"error": {"message": "messages is required", "type": "invalid_request_error"}})
    enforce_allowed_model(payload, client_key)
    result = await proxy.proxy_chat(payload, client_key)
    return format_proxy_result(result)


@app.post("/v1/responses")
async def responses(request: Request, client_key: Optional[dict] = Depends(require_client_key)):
    payload = await request.json()
    enforce_allowed_model(payload, client_key)
    result = await proxy.proxy_responses(payload, client_key)
    return format_proxy_result(result)


@app.post("/responses")
async def bare_responses(request: Request, client_key: Optional[dict] = Depends(require_client_key)):
    payload = await request.json()
    enforce_allowed_model(payload, client_key)
    result = await proxy.proxy_responses(payload, client_key)
    return format_proxy_result(result)


@app.post("/responses/compact")
async def bare_responses_compact(request: Request, client_key: Optional[dict] = Depends(require_client_key)):
    payload = await request.json()
    enforce_allowed_model(payload, client_key)
    result = await proxy.proxy_responses(payload, client_key)
    return format_proxy_result(result)


@app.post("/backend-api/codex/responses")
async def codex_responses(request: Request, client_key: Optional[dict] = Depends(require_client_key)):
    payload = await request.json()
    enforce_allowed_model(payload, client_key)
    result = await proxy.proxy_responses(payload, client_key)
    return format_proxy_result(result)


@app.post("/backend-api/codex/responses/compact")
async def codex_responses_compact(request: Request, client_key: Optional[dict] = Depends(require_client_key)):
    payload = await request.json()
    enforce_allowed_model(payload, client_key)
    result = await proxy.proxy_responses(payload, client_key)
    return format_proxy_result(result)


def enforce_allowed_model(payload: dict, client_key: Optional[dict]) -> None:
    if not client_key or not client_key.get("allowed_models"):
        return
    model = str(payload.get("model") or "auto-chat")
    resolved = proxy.resolve_model(model)
    if model not in client_key["allowed_models"] and resolved not in client_key["allowed_models"]:
        raise HTTPException(status_code=403, detail={"error": {"message": f"Model {model} not allowed", "type": "invalid_request_error"}})


def format_proxy_result(result):
    kind = result[0]
    if kind == "json":
        return JSONResponse(content=result[1])
    if kind == "stream":
        return StreamingResponse(
            result[1],
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    if kind == "error":
        status, detail = result[1]
        return JSONResponse(status_code=status, content=detail)
    return JSONResponse(status_code=500, content={"error": {"message": "unknown proxy result"}})


@app.get("/admin/accounts")
def admin_accounts(_=Depends(require_admin)):
    return {"accounts": accounts.list_accounts()}


@app.post("/admin/accounts")
async def admin_add_account(request: Request, _=Depends(require_admin)):
    data = await request.json()
    account_id = accounts.add_account(data)
    return {"id": account_id, "account": accounts.get_account(account_id)}


@app.put("/admin/accounts/{account_id}")
async def admin_update_account(account_id: int, request: Request, _=Depends(require_admin)):
    data = await request.json()
    if not accounts.update_account(account_id, data):
        raise HTTPException(status_code=404, detail="account not found")
    return {"account": accounts.get_account(account_id)}


@app.delete("/admin/accounts/{account_id}")
def admin_delete_account(account_id: int, _=Depends(require_admin)):
    accounts.delete_account(account_id)
    return {"ok": True}


@app.post("/admin/accounts/{account_id}/test")
async def admin_test_account(account_id: int, _=Depends(require_admin)):
    account = accounts.get_account(account_id, include_secret=True)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    result = await proxy.collect_upstream(
        f"{accounts.normalize_endpoint(account.get('endpoint'))}/v2/chat/completions",
        accounts.build_headers(account),
        proxy.build_chat_body({"model": "glm-5.1", "messages": [{"role": "user", "content": "ping"}]}),
        account,
        None,
        "account-test",
        __import__("time").time(),
    )
    return {"result": result[0], "data": result[1]}


@app.get("/admin/api-keys")
def admin_keys(_=Depends(require_admin)):
    return {"api_keys": keys.list_client_keys()}


@app.post("/admin/api-keys")
async def admin_create_key(request: Request, _=Depends(require_admin)):
    data = await request.json()
    return keys.create_client_key(
        data.get("name") or "default",
        data.get("allowed_models"),
        int(data.get("daily_limit") or 0),
    )


@app.delete("/admin/api-keys/{key_id}")
def admin_delete_key(key_id: int, _=Depends(require_admin)):
    keys.delete_client_key(key_id)
    return {"ok": True}


@app.get("/admin/logs")
def admin_logs(limit: int = 100, _=Depends(require_admin)):
    limit = min(max(limit, 1), 500)
    logs = db.rows("SELECT * FROM request_logs ORDER BY id DESC LIMIT ?", (limit,))
    return {"logs": logs}


@app.get("/admin/models")
def admin_models(_=Depends(require_admin)):
    return {
        "models": db.get_setting("models", []),
        "probe_candidates": db.get_setting("model_probe_candidates", MODEL_PROBE_CANDIDATES),
    }


@app.put("/admin/models")
async def admin_update_models(request: Request, _=Depends(require_admin)):
    data = await request.json()
    models = normalize_model_list(data.get("models") or [])
    if not models:
        raise HTTPException(status_code=400, detail="models cannot be empty")
    db.set_setting("models", models)
    if "probe_candidates" in data:
        candidates = normalize_model_list(data.get("probe_candidates") or [])
        db.set_setting("model_probe_candidates", candidates)
    return {"models": db.get_setting("models", []), "probe_candidates": db.get_setting("model_probe_candidates", MODEL_PROBE_CANDIDATES)}


@app.post("/admin/models/probe")
async def admin_probe_models(request: Request, _=Depends(require_admin)):
    data = await request.json()
    account_id = int(data.get("account_id") or 0)
    account = accounts.get_account(account_id, include_secret=True) if account_id else accounts.pick_account()
    if account and not account.get("api_key") and not account.get("access_token"):
        account = accounts.get_account(account["id"], include_secret=True)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    candidates = normalize_model_list(data.get("models") or db.get_setting("model_probe_candidates", MODEL_PROBE_CANDIDATES))
    if not candidates:
        raise HTTPException(status_code=400, detail="models cannot be empty")
    results = []
    available = []
    for model in candidates[:50]:
        resolved_model = proxy.resolve_model(model)
        result = await proxy.collect_upstream(
            f"{accounts.normalize_endpoint(account.get('endpoint'))}/v2/chat/completions",
            accounts.build_headers(account),
            proxy.build_chat_body({"model": model, "messages": [{"role": "user", "content": "ping"}]}),
            account,
            None,
            f"model-probe:{model}",
            __import__("time").time(),
        )
        item = {"model": model, "resolved_model": resolved_model, "ok": result[0] == "json"}
        if result[0] == "json":
            available.append(model)
            item["response_model"] = result[1].get("model")
            item["total_tokens"] = (result[1].get("usage") or {}).get("total_tokens")
        else:
            status, detail = result[1]
            item["status"] = status
            item["error"] = ((detail.get("error") or {}).get("message") if isinstance(detail, dict) else str(detail))[:500]
        results.append(item)
    if data.get("save"):
        db.set_setting("models", available)
    return {"account_id": account.get("id"), "available": available, "results": results, "saved": bool(data.get("save"))}


def normalize_model_list(value) -> list[str]:
    if isinstance(value, str):
        value = value.replace("\r", "\n").replace(",", "\n").split("\n")
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        model = str(item).strip()
        if model and model not in seen:
            out.append(model)
            seen.add(model)
    return out


@app.get("/", response_class=HTMLResponse)
def index():
    return Path(__file__).with_name("admin.html").read_text(encoding="utf-8")
