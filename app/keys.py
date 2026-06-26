import json
from typing import Optional

from . import db
from .security import key_prefix, make_key, sha256_hex


def create_client_key(name: str = "", allowed_models: list[str] | None = None, daily_limit: int = 0) -> dict:
    key = make_key("sk-cb")
    now = db.now_ts()
    key_id = db.execute(
        """
        INSERT INTO client_keys
            (name, key_hash, key_prefix, allowed_models, daily_limit, created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            name or "default",
            sha256_hex(key),
            key_prefix(key),
            json.dumps(allowed_models, ensure_ascii=False) if allowed_models else None,
            int(daily_limit or 0),
            now,
        ),
    )
    return {"id": key_id, "key": key, "key_prefix": key_prefix(key), "name": name or "default"}


def list_client_keys() -> list[dict]:
    items = db.rows("SELECT * FROM client_keys ORDER BY id DESC")
    for item in items:
        item.pop("key_hash", None)
        if item.get("allowed_models"):
            try:
                item["allowed_models"] = json.loads(item["allowed_models"])
            except Exception:
                item["allowed_models"] = None
    return items


def verify_client_key(token: str) -> Optional[dict]:
    if not token:
        return None
    item = db.row("SELECT * FROM client_keys WHERE key_hash=? AND status='active'", (sha256_hex(token),))
    if not item:
        return None
    if item.get("allowed_models"):
        try:
            item["allowed_models"] = json.loads(item["allowed_models"])
        except Exception:
            item["allowed_models"] = None
    return item


def touch_key(key_id: int, tokens: int = 0) -> None:
    db.execute(
        """
        UPDATE client_keys
        SET total_requests=total_requests+1,
            total_tokens=total_tokens+?,
            last_used_at=?
        WHERE id=?
        """,
        (int(tokens or 0), db.now_ts(), key_id),
    )


def delete_client_key(key_id: int) -> None:
    db.execute("DELETE FROM client_keys WHERE id=?", (key_id,))
