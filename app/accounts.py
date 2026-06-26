import json
import time
from typing import Optional

import httpx

from . import db
from .security import decrypt_text, encrypt_text


def normalize_endpoint(endpoint: str | None) -> str:
    endpoint = (endpoint or db.get_setting("default_endpoint") or "https://www.codebuddy.ai").strip()
    return endpoint.rstrip("/")


def endpoint_domain(endpoint: str) -> str:
    return endpoint.replace("https://", "").replace("http://", "").split("/", 1)[0]


def add_account(data: dict) -> int:
    now = db.now_ts()
    auth_type = (data.get("auth_type") or "api_key").strip().lower()
    endpoint = normalize_endpoint(data.get("endpoint"))
    name = data.get("name") or f"codebuddy-{auth_type}"
    return db.execute(
        """
        INSERT INTO upstream_accounts
            (name, auth_type, endpoint, api_key_enc, access_token_enc, refresh_token_enc,
             user_id, domain, expires_at, status, weight, priority, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            name,
            auth_type,
            endpoint,
            encrypt_text(data.get("api_key")),
            encrypt_text(data.get("access_token")),
            encrypt_text(data.get("refresh_token")),
            data.get("user_id") or ("anonymous" if auth_type == "api_key" else ""),
            data.get("domain") or endpoint_domain(endpoint),
            int(data.get("expires_at") or 0),
            data.get("status") or "active",
            max(1, int(data.get("weight") or 1)),
            int(data.get("priority") or 0),
            now,
            now,
        ),
    )


def list_accounts(include_secret: bool = False) -> list[dict]:
    accounts = db.rows("SELECT * FROM upstream_accounts ORDER BY id")
    out = []
    for account in accounts:
        item = sanitize_account(account)
        if include_secret:
            item["api_key"] = decrypt_text(account.get("api_key_enc"))
            item["access_token"] = decrypt_text(account.get("access_token_enc"))
            item["refresh_token"] = decrypt_text(account.get("refresh_token_enc"))
        out.append(item)
    return out


def get_account(account_id: int, include_secret: bool = False) -> Optional[dict]:
    account = db.row("SELECT * FROM upstream_accounts WHERE id=?", (account_id,))
    if not account:
        return None
    if include_secret:
        account["api_key"] = decrypt_text(account.get("api_key_enc"))
        account["access_token"] = decrypt_text(account.get("access_token_enc"))
        account["refresh_token"] = decrypt_text(account.get("refresh_token_enc"))
        return account
    return sanitize_account(account)


def sanitize_account(account: dict) -> dict:
    api_key = decrypt_text(account.get("api_key_enc"))
    access_token = decrypt_text(account.get("access_token_enc"))
    return {
        "id": account["id"],
        "name": account.get("name"),
        "auth_type": account.get("auth_type"),
        "endpoint": account.get("endpoint"),
        "user_id": account.get("user_id"),
        "domain": account.get("domain"),
        "expires_at": account.get("expires_at"),
        "status": account.get("status"),
        "weight": account.get("weight"),
        "priority": account.get("priority"),
        "total_requests": account.get("total_requests"),
        "total_tokens": account.get("total_tokens"),
        "last_error": account.get("last_error"),
        "has_api_key": bool(api_key),
        "has_access_token": bool(access_token),
        "created_at": account.get("created_at"),
        "updated_at": account.get("updated_at"),
    }


def update_account(account_id: int, data: dict) -> bool:
    current = get_account(account_id, include_secret=True)
    if not current:
        return False
    fields = []
    values = []
    mapping = {
        "name": "name",
        "auth_type": "auth_type",
        "endpoint": "endpoint",
        "user_id": "user_id",
        "domain": "domain",
        "expires_at": "expires_at",
        "status": "status",
        "weight": "weight",
        "priority": "priority",
    }
    for key, col in mapping.items():
        if key in data:
            value = data[key]
            if key == "endpoint":
                value = normalize_endpoint(value)
            if key == "weight":
                value = max(1, int(value or 1))
            if key == "priority":
                value = int(value or 0)
            fields.append(f"{col}=?")
            values.append(value)
    if "api_key" in data:
        fields.append("api_key_enc=?")
        values.append(encrypt_text(data.get("api_key")))
    if "access_token" in data:
        fields.append("access_token_enc=?")
        values.append(encrypt_text(data.get("access_token")))
    if "refresh_token" in data:
        fields.append("refresh_token_enc=?")
        values.append(encrypt_text(data.get("refresh_token")))
    if not fields:
        return True
    fields.append("updated_at=?")
    values.append(db.now_ts())
    values.append(account_id)
    db.execute(f"UPDATE upstream_accounts SET {','.join(fields)} WHERE id=?", tuple(values))
    return True


def delete_account(account_id: int) -> bool:
    db.execute("DELETE FROM upstream_accounts WHERE id=?", (account_id,))
    return True


def active_accounts(exclude_ids: set[int] | None = None) -> list[dict]:
    exclude_ids = exclude_ids or set()
    accounts = db.rows("SELECT * FROM upstream_accounts WHERE status='active'")
    candidates = []
    for account in accounts:
        if account["id"] in exclude_ids:
            continue
        account["api_key"] = decrypt_text(account.get("api_key_enc"))
        account["access_token"] = decrypt_text(account.get("access_token_enc"))
        account["refresh_token"] = decrypt_text(account.get("refresh_token_enc"))
        candidates.append(account)
    candidates.sort(
        key=lambda a: (
            -int(a.get("priority") or 0),
            (a.get("total_requests") or 0) / max(1, int(a.get("weight") or 1)),
            a["id"],
        )
    )
    return candidates


def pick_account(exclude_ids: set[int] | None = None) -> Optional[dict]:
    accounts = active_accounts(exclude_ids)
    return accounts[0] if accounts else None


def mark_success(account_id: int, tokens: int = 0) -> None:
    db.execute(
        """
        UPDATE upstream_accounts
        SET total_requests=total_requests+1,
            total_tokens=total_tokens+?,
            last_error='',
            updated_at=?
        WHERE id=?
        """,
        (int(tokens or 0), db.now_ts(), account_id),
    )


def mark_error(account_id: int, error: str, status: str | None = None) -> None:
    fields = "last_error=?, updated_at=?"
    values: list = [str(error)[:500], db.now_ts()]
    if status:
        fields += ", status=?"
        values.append(status)
    values.append(account_id)
    db.execute(f"UPDATE upstream_accounts SET {fields} WHERE id=?", tuple(values))


def build_headers(account: dict, conversation: dict | None = None) -> dict:
    conversation = conversation or {}
    endpoint = normalize_endpoint(account.get("endpoint"))
    domain = account.get("domain") or endpoint_domain(endpoint)
    auth_type = (account.get("auth_type") or "api_key").lower()
    user_id = account.get("user_id") or ("anonymous" if auth_type == "api_key" else "")
    token = account.get("api_key") if auth_type == "api_key" else account.get("access_token")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token or ''}",
        "X-User-Id": user_id,
        "X-Domain": domain,
        "X-Agent-Intent": "craft",
        "X-IDE-Type": "CLI",
        "X-IDE-Name": "CLI",
        "X-IDE-Version": "1.0.7",
        "X-Product": "SaaS",
        "User-Agent": "CLI/1.0.7 CodeBuddy/1.0.7",
    }
    if auth_type == "api_key" and account.get("api_key"):
        headers["X-API-Key"] = account["api_key"]
    if account.get("enterprise_id"):
        headers["X-Enterprise-Id"] = account["enterprise_id"]
        headers["X-Tenant-Id"] = account["enterprise_id"]
    for name, header in [
        ("conversation_id", "X-Conversation-ID"),
        ("conversation_request_id", "X-Conversation-Request-ID"),
        ("conversation_message_id", "X-Conversation-Message-ID"),
        ("request_id", "X-Request-ID"),
    ]:
        if conversation.get(name):
            headers[header] = conversation[name]
    return headers


def maybe_refresh_token(account: dict) -> bool:
    if (account.get("auth_type") or "").lower() == "api_key":
        return True
    refresh_token = account.get("refresh_token")
    access_token = account.get("access_token")
    expires_at = int(account.get("expires_at") or 0)
    if not refresh_token or not access_token:
        return bool(access_token)
    now_ms = int(time.time() * 1000)
    if expires_at and now_ms < expires_at - 60_000:
        return True

    endpoint = normalize_endpoint(account.get("endpoint"))
    headers = build_headers(account)
    headers["X-Refresh-Token"] = refresh_token
    headers["X-Auth-Refresh-Source"] = "plugin"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(f"{endpoint}/v2/plugin/auth/token/refresh", headers=headers, json={})
            data = resp.json()
    except Exception as exc:
        mark_error(account["id"], f"refresh failed: {exc}", "expired")
        return False
    if data.get("code") not in (0, 200) or not data.get("data"):
        mark_error(account["id"], f"refresh failed: {json.dumps(data, ensure_ascii=False)[:300]}", "expired")
        return False
    payload = data["data"]
    update_account(
        account["id"],
        {
            "access_token": payload.get("accessToken") or payload.get("access_token") or access_token,
            "refresh_token": payload.get("refreshToken") or payload.get("refresh_token") or refresh_token,
            "expires_at": payload.get("expiresAt") or payload.get("expires_at") or expires_at,
            "domain": payload.get("domain") or account.get("domain"),
            "status": "active",
        },
    )
    return True
