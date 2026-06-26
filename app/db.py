import json
import sqlite3
import threading
import time
from typing import Any, Optional

from .config import DB_PATH, DEFAULT_ENDPOINT, DEFAULT_MODELS

_LOCK = threading.Lock()


def now_ts() -> int:
    return int(time.time())


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    with _LOCK:
        conn = connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS upstream_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                auth_type TEXT NOT NULL DEFAULT 'api_key',
                endpoint TEXT NOT NULL,
                api_key_enc TEXT,
                access_token_enc TEXT,
                refresh_token_enc TEXT,
                user_id TEXT,
                domain TEXT,
                expires_at INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                weight INTEGER DEFAULT 1,
                priority INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                last_error TEXT,
                created_at INTEGER,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS client_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                key_hash TEXT UNIQUE NOT NULL,
                key_prefix TEXT,
                status TEXT DEFAULT 'active',
                allowed_models TEXT,
                daily_limit INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                created_at INTEGER,
                last_used_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_key_id INTEGER,
                client_key_name TEXT,
                account_id INTEGER,
                account_name TEXT,
                model TEXT,
                stream INTEGER,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                status_code INTEGER,
                finish_reason TEXT,
                error TEXT,
                duration_ms INTEGER,
                created_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        if get_setting("models") is None:
            set_setting("models", DEFAULT_MODELS, conn=conn)
        if get_setting("default_endpoint") is None:
            set_setting("default_endpoint", DEFAULT_ENDPOINT, conn=conn)
        conn.commit()
        conn.close()


def rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect()
    data = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return data


def row(sql: str, params: tuple = ()) -> Optional[dict]:
    conn = connect()
    r = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(r) if r else None


def execute(sql: str, params: tuple = ()) -> int:
    with _LOCK:
        conn = connect()
        cur = conn.execute(sql, params)
        conn.commit()
        last_id = cur.lastrowid
        conn.close()
        return int(last_id or 0)


def get_setting(key: str, default: Any = None) -> Any:
    r = row("SELECT value FROM settings WHERE key=?", (key,))
    if not r:
        return default
    try:
        return json.loads(r["value"])
    except Exception:
        return r["value"]


def set_setting(key: str, value: Any, conn: sqlite3.Connection | None = None) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    if conn is not None:
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, payload))
        return
    execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, payload))


def add_log(data: dict) -> None:
    execute(
        """
        INSERT INTO request_logs
            (client_key_id, client_key_name, account_id, account_name, model, stream,
             prompt_tokens, completion_tokens, total_tokens, status_code, finish_reason,
             error, duration_ms, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            data.get("client_key_id"),
            data.get("client_key_name"),
            data.get("account_id"),
            data.get("account_name"),
            data.get("model", ""),
            1 if data.get("stream") else 0,
            int(data.get("prompt_tokens") or 0),
            int(data.get("completion_tokens") or 0),
            int(data.get("total_tokens") or 0),
            int(data.get("status_code") or 0),
            data.get("finish_reason", ""),
            data.get("error", ""),
            int(data.get("duration_ms") or 0),
            now_ts(),
        ),
    )
