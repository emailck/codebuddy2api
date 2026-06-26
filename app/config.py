import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


ADMIN_TOKEN = get_env("CB2PAI_ADMIN_TOKEN", "change-this-admin-token")
MASTER_KEY = get_env("CB2PAI_MASTER_KEY", "change-this-master-key-at-least-32-chars")
HOST = get_env("CB2PAI_HOST", "0.0.0.0")
PORT = int(get_env("CB2PAI_PORT", "8787"))
DB_PATH = Path(get_env("CB2PAI_DB_PATH", "data/codebuddy2api.db"))
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

DEFAULT_ENDPOINT = get_env("CB2PAI_DEFAULT_ENDPOINT", "https://www.codebuddy.ai").rstrip("/")
DEFAULT_MODELS = [
    "Auto",
    "Hy3 preview",
    "GLM-5v-Turbo",
    "GLM-5.2",
    "GLM-5.1",
    "Kimi-K2.7-Code",
    "Kimi-K2.6",
    "MiniMax-M3",
    "Deepseek-V4-Pro",
    "Deepseek-V4-Flash",
]

MODEL_PROBE_CANDIDATES = [
    "Auto",
    "Hy3 preview",
    "GLM-5v-Turbo",
    "GLM-5.2",
    "GLM-5.1",
    "Kimi-K2.7-Code",
    "Kimi-K2.6",
    "MiniMax-M3",
    "Deepseek-V4-Pro",
    "Deepseek-V4-Flash",
]
