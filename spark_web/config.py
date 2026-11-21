from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUILD_VERSION = "2.10.5"
ENV_PATH = ROOT / ".env"
WORKSPACE_CONFIG_PATH = ROOT / "notion_workspace.json"
DATA_PATH = ROOT / "spark_task_data.json"
CACHE_PATH = ROOT / "spark_project_cache.json"
DB_PATH = ROOT / "spark_workbench.db"
PROJECT_TRACKING_PATH = ROOT / "project_tracking.json"


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


def _apply_windows_system_proxy() -> None:
    """按浏览器行为补全系统代理。

    urllib 的代理探测优先看环境变量，且只认 http/https/ftp 等 scheme 的 *_proxy
    变量；一旦环境里存在其它名字的 *_proxy 变量（如运行器注入的
    KIMI_WORK_NETWORK_PROXY），注册表里的系统代理就被整体跳过，请求直连而被拒。
    此时若注册表启用了 http(s) 代理，则补全 http_proxy/https_proxy 环境变量。
    """
    if os.name != "nt":
        return
    try:
        from urllib.request import getproxies_environment
        if "http" in getproxies_environment() or "https" in getproxies_environment():
            return
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return
    if not enabled or not server or server.startswith("socks"):
        return
    if not server.startswith(("http://", "https://")):
        server = f"http://{server}"
    if not os.environ.get("http_proxy"):
        os.environ["http_proxy"] = server
    if not os.environ.get("https_proxy"):
        os.environ["https_proxy"] = server


_apply_windows_system_proxy()

ENV = load_env()


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name, ENV.get(name, default)).strip()


def setting_bool(name: str, default: bool = False) -> bool:
    raw = setting(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


def load_workspace_config() -> dict:
    with WORKSPACE_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


HOST = setting("SPARK_HOST", "127.0.0.1") or "127.0.0.1"
# 绑定 0.0.0.0 时本机浏览器仍用回环地址打开，避免把 0.0.0.0 写进运行文件/自动打开的链接
DISPLAY_HOST = "127.0.0.1" if HOST in {"0.0.0.0", "::", ""} else HOST
PORT = int(setting("SPARK_WEB_PORT", "8765"))
TEAM_MODE = setting_bool("SPARK_TEAM_MODE", False)
AUTO_NOTION = setting_bool("SPARK_NOTION_AUTO_EXECUTE", True)
NOTION_TOKEN = setting("NOTION_TOKEN")
SESSION_SECRET = setting("SPARK_SESSION_SECRET") or secrets.token_hex(32)
CSRF_SECRET = setting("SPARK_CSRF_SECRET") or secrets.token_hex(32)
