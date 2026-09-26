from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUILD_VERSION = "2.6.1"
ENV_PATH = ROOT / ".env"
WORKSPACE_CONFIG_PATH = ROOT / "notion_workspace.json"
DATA_PATH = ROOT / "spark_task_data.json"
CACHE_PATH = ROOT / "spark_project_cache.json"
DB_PATH = ROOT / "spark_workbench.db"


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value.replace(r"\n", "\n")
    return values


ENV = load_env()


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name, ENV.get(name, default)).strip()


def setting_bool(name: str, default: bool = False) -> bool:
    raw = setting(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


def load_workspace_config() -> dict:
    with WORKSPACE_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


HOST = "127.0.0.1"
PORT = int(setting("SPARK_WEB_PORT", "8765"))
TEAM_MODE = setting_bool("SPARK_TEAM_MODE", False)
AUTO_NOTION = setting_bool("SPARK_NOTION_AUTO_EXECUTE", True)
NOTION_TOKEN = setting("NOTION_TOKEN")
SESSION_SECRET = setting("SPARK_SESSION_SECRET") or secrets.token_hex(32)
CSRF_SECRET = setting("SPARK_CSRF_SECRET") or secrets.token_hex(32)
