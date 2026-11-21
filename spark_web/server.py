from __future__ import annotations

import argparse
import datetime as dt
import errno
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import AUTO_NOTION, BUILD_VERSION, DISPLAY_HOST, HOST, NOTION_TOKEN, PORT, ROOT, TEAM_MODE, load_workspace_config, load_env
from . import chain
from .forum import (ai_json, extract_completion, extract_new_project, extract_weekly_update,
                    fetch_topic, normalized_posts, post_with_password, verify_talk_credentials)
from . import legacy, project_tracking
from .notion import NotionClient, NotionConflict, NotionError
from . import notion_preview
from .storage import ReviewConflict, Store, utcnow
from .workflows import NotionAutomation, WorkflowInputError, payload_from_json, REQUIRED_NEW_PROJECT


REQUIRED_WEEKLY = ("topic_id", "project_name", "update_key", "date", "progress")
REQUIRED_COMPLETION = ("topic_id", "project_name", "team", "start_date", "completion_date",
                       "funding_amount", "wallet", "total_funding", "expected_completion", "status")


def enrich_notion_payload(workflow: str, payload: dict) -> dict:
    """必填字段缺失时，自动从 Talk 主题提取补齐（对齐桌面版的自动提取行为）。

    用户已填写的字段始终优先；仅填充空缺字段。提取失败（如无 AI Key）时原样返回。
    """
    required = {"new-project": REQUIRED_NEW_PROJECT, "weekly": REQUIRED_WEEKLY,
                "completion": REQUIRED_COMPLETION}.get(workflow, ())
    if not required or all(payload.get(name) for name in required):
        return payload
    topic_id = str(payload.get("topic_id") or "")
    if not topic_id:
        return payload
    merged = dict(payload)
    try:
        topic = fetch_topic(topic_id)
        if workflow == "new-project":
            base = extract_new_project(topic)
        elif workflow == "weekly":
            base = extract_weekly_update(topic, 0) or {}
        else:
            base = extract_completion(topic, _base_payload(topic_id),
                                      WORKSPACE["select_values"]["completed"])
            # 本地没有 in_progress 载荷时（如直接结项/关停的帖子），先从 Notion
            # 进行中库回读基础字段，再用 AI 从帖子正文补充提取剩余必填项。
            missing_after = [name for name in required if not merged.get(name) and not base.get(name)]
            if missing_after:
                try:
                    for key_, value in automation().read_ongoing_base(topic_id).items():
                        if not base.get(key_) and value not in (None, ""):
                            base[key_] = value
                except Exception:
                    pass
                missing_after = [name for name in required if not merged.get(name) and not base.get(name)]
            if missing_after:
                supplement = _extract_from_posts(topic, missing_after)
                for key_, value in supplement.items():
                    if not base.get(key_) and value not in (None, "", []):
                        base[key_] = value
        for key, value in base.items():
            if not merged.get(key) and value not in (None, "", []):
                merged[key] = value
        if workflow == "completion" and not merged.get("expected_completion"):
            # 从未入库的项目没有预计完成日期记录，以结项日期兜底
            merged["expected_completion"] = merged.get("completion_date") or ""
        if workflow == "new-project":
            # 兜底：批准时分配金额 = 总资助金额；无交易记录时已发放记 0%
            if not merged.get("allocated_amount") and merged.get("total_funding"):
                merged["allocated_amount"] = merged["total_funding"]
            if not merged.get("distributed"):
                merged["distributed"] = "0%"
    except Exception:
        pass
    return merged


def _extract_from_posts(topic: dict, fields: list[str]) -> dict:
    """从帖子正文用 AI 补充提取指定字段，只返回确实出现的值，不得猜测。"""
    posts = normalized_posts(topic)
    prompt = f"""从以下 Spark Program 项目帖子中提取字段：{', '.join(fields)}。
只提取帖子中明确出现的信息，不得猜测或编造；找不到的字段返回空字符串。
funding_amount/total_funding 用帖子原文的数字与币种；wallet 是 CKB 地址（ckb1... 或 ckt1...）。
日期一律 YYYY-MM-DD。team 取项目团队/申请人名称。
返回一个 JSON 对象，键正好是：{', '.join(fields)}。
帖子：{json.dumps(posts, ensure_ascii=False)[:50000]}"""
    value = ai_json(prompt)
    return {k: v for k, v in value.items() if isinstance(v, (str, int, float))}


STATIC = Path(__file__).resolve().parent / "static"
WORKSPACE = load_workspace_config()
STORE = Store()
LOCAL_CSRF = secrets.token_urlsafe(32)
TRACKER_MODULE = None
REFRESH_LOCK = threading.Lock()
LAUNCH_NONCE = os.environ.get("SPARK_LAUNCH_NONCE", "")
CLIENT_LOCK = threading.Lock()
CLIENTS: dict[str, float] = {}
CLIENT_EVER_CONNECTED = False
CLIENT_EMPTY_SINCE: float | None = None
ACTIVE_REQUESTS = 0
CLIENT_STALE_AFTER = 90.0
AUTO_SHUTDOWN_EMPTY_GRACE = 8.0


def _forum_settings_payload() -> dict:
    return {"talk_username": legacy.forum_binding(), "talk_password_saved": bool(legacy.forum_password()),
            "hf_token_configured": bool(_hf_token())}



def _committee_talk_name(value: str | None) -> str:
    return (value or "").strip().removeprefix("@").casefold()


def _ensure_talk_user(forum_username: str) -> dict | None:
    """Provision a verified committee member without granting publishing privileges."""
    with STORE.transaction() as conn:
        bound = conn.execute("SELECT * FROM users WHERE lower(forum_username)=?", (forum_username,)).fetchall()
        if bound:
            return dict(bound[0]) if len(bound) == 1 and bound[0]["enabled"] else None
        existing = conn.execute("SELECT * FROM users WHERE lower(username)=?", (forum_username,)).fetchall()
        if existing:
            # Never attach a Talk identity to an unbound privileged local account by name alone.
            if len(existing) != 1 or not existing[0]["enabled"] or existing[0]["forum_username"] or existing[0]["role"] != "viewer":
                return None
            row = dict(existing[0])
            conn.execute("UPDATE users SET forum_username=? WHERE username=?", (forum_username, row["username"]))
            row["forum_username"] = forum_username
            return row
        conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?)",
                     (forum_username, STORE.hash_password(secrets.token_urlsafe(24)),
                      "viewer", forum_username, 1, utcnow()))
    STORE.audit(forum_username, "talk_auto_provision", forum_username, {"role": "viewer"})
    return {"username": forum_username, "forum_username": forum_username, "role": "viewer"}


_LOGIN_ATTEMPTS: dict[str, list[float]] = {}


def _login_throttled(ip: str) -> bool:
    """登录限流：每 IP 每分钟最多 8 次尝试（论坛密码校验会请求 Nervos Talk）。"""
    now = time.monotonic()
    window = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < 60]
    throttled = len(window) >= 8
    if not throttled:
        window.append(now)
    _LOGIN_ATTEMPTS[ip] = window
    if len(_LOGIN_ATTEMPTS) > 5000:
        _LOGIN_ATTEMPTS.clear()
    return throttled


def _resolve_login_user(uname: str, pwd: str) -> dict | None:
    """Require both committee membership and fresh verification of that exact Talk identity."""
    name = _committee_talk_name(uname)
    if name not in legacy.COMMITTEE_MEMBERS or not pwd:
        return None
    disabled = STORE.query("SELECT 1 FROM users WHERE enabled=0 AND (lower(username)=? OR lower(forum_username)=?)",
                           (name, name))
    if disabled or not verify_talk_credentials(name, pwd):
        return None
    return _ensure_talk_user(name)


def _team_token_hash(token: str) -> str:
    # Previous sessions may have been issued for non-committee or local-password logins.
    return hashlib.sha256(("talk-committee-v1:" + token).encode()).hexdigest()


def _topic_text_cached(topic_id: str) -> dict:
    from .config import CACHE_PATH
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    bucket = cache.setdefault("topic_text_cache", {})
    entry = bucket.get(str(topic_id))
    if entry and time.time() - entry.get("fetched_at", 0) < 3600:
        return entry["value"]
    value = legacy.topic_text(str(topic_id))
    bucket[str(topic_id)] = {"fetched_at": time.time(), "value": value}
    try:
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return value


def _cache_review_draft(plan: dict, content: str, analysis: str = "") -> int | None:
    """AI 草稿生成成功后自动存入审阅队列；同一 topic+模板 覆盖旧草稿。"""
    topic_id = str(plan.get("topic_id") or "")
    content = (content or "").strip()
    if not topic_id or not content:
        return None
    title = str((plan.get("post") or {}).get("title") or "")
    url = str((plan.get("post") or {}).get("full_url") or f"https://talk.nervos.org/t/{topic_id}")
    return STORE.save_review_draft(topic_id, title, url, str(plan.get("template_name") or ""),
                                   content, analysis or "")


ASR_VENV = ROOT / "asr_venv"
ASR_PYTHON = ASR_VENV / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
TRANSCRIBE_SCRIPT = ROOT / "tools" / "transcribe.py"
DIARIZE_SCRIPT = ROOT / "tools" / "diarize.py"
ASR_LOG = ROOT / "setup-asr.log"
_asr_busy = False
_asr_installing = False
_diarization_installing = False
_asr_progress_file: Path | None = None  # 转写子进程的进度文件（diarize 模式）


def _asr_progress() -> dict:
    if not (_asr_busy and _asr_progress_file):
        return {}
    try:
        raw = _asr_progress_file.read_text(encoding="utf-8", errors="replace")
        percent, _, note = raw.partition("|")
        return {"percent": int(percent), "note": note}
    except (OSError, ValueError):
        return {}


def _hf_token() -> str:
    env = load_env()
    return (os.environ.get("SPARK_HF_TOKEN") or env.get("SPARK_HF_TOKEN") or "").strip()


def _asr_env() -> dict:
    """转写子进程环境：走 hf 镜像，禁用易在代理下卡死的 Xet 协议，加下载超时。"""
    env = dict(os.environ)
    env["HF_ENDPOINT"] = "https://hf-mirror.com"
    env["HF_HUB_DISABLE_XET"] = "1"
    env["HF_HUB_DOWNLOAD_TIMEOUT"] = "30"
    env["HF_HUB_ETAG_TIMEOUT"] = "30"
    return env


def _no_window_flags() -> int:
    import subprocess
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _asr_installed() -> bool:
    site = ASR_VENV / "Lib" / "site-packages" if os.name == "nt" else ASR_VENV / "lib"
    if not ASR_PYTHON.exists() or not site.exists():
        return False
    return any(site.glob("faster_whisper*")) or any(site.glob("faster-whisper*"))


def _asr_log_tail(limit: int = 3000) -> str:
    try:
        return ASR_LOG.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _diarization_installed() -> bool:
    site = ASR_VENV / "Lib" / "site-packages" if os.name == "nt" else ASR_VENV / "lib"
    if not ASR_PYTHON.exists() or not site.exists():
        return False
    return any(site.glob("whisperx*"))


def _diarize_env() -> dict:
    """说话人分离子进程环境：pyannote 门禁模型必须直连 huggingface.co
    （镜像无法携带 token 下载门禁模型），因此不用 HF_ENDPOINT 镜像。"""
    env = dict(os.environ)
    env.pop("HF_ENDPOINT", None)
    env["HF_HUB_DISABLE_XET"] = "1"
    env["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"
    env["HF_HUB_ETAG_TIMEOUT"] = "60"
    token = _hf_token()
    if token:
        env["HF_TOKEN"] = token
        env["HUGGING_FACE_HUB_TOKEN"] = token
    return env


def _install_diarization() -> None:
    global _diarization_installing
    _diarization_installing = True

    def log(message: str) -> None:
        with ASR_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"[{dt.datetime.now().isoformat(timespec='seconds')}] {message}\n")

    try:
        import subprocess
        log("开始安装说话人分离组件（torch CPU + WhisperX，约 1GB，耐心等候）")
        steps = [
            ("安装 torch/torchaudio CPU 版", [str(ASR_PYTHON), "-m", "pip", "install",
             "--index-url", "https://download.pytorch.org/whl/cpu", "torch", "torchaudio"]),
            ("安装 WhisperX（官方源）", [str(ASR_PYTHON), "-m", "pip", "install",
             "-i", "https://pypi.org/simple", "git+https://github.com/m-bain/whisperX.git"]),
            ("对齐 torchaudio 版本（与 torch 匹配）", [str(ASR_PYTHON), "-m", "pip",
             "install", "--index-url", "https://download.pytorch.org/whl/cpu",
             "torchaudio==2.8.0"]),
            ("固定 av==12.3.0（与 faster-whisper 1.2.1 兼容）", [str(ASR_PYTHON), "-m",
             "pip", "install", "-i", "https://pypi.org/simple", "av==12.3.0"]),
        ]
        for name, command in steps:
            log(f"步骤：{name}")
            result = subprocess.run(command, capture_output=True, text=True,
                                    env=_asr_env(), creationflags=_no_window_flags())
            log(result.stdout[-2500:])
            if result.returncode != 0:
                log(f"步骤失败 returncode={result.returncode}: {result.stderr[-2500:]}")
                return
        nltk_env = dict(_asr_env())
        nltk_env["NLTK_ALLOW_PROXIED_URLOPEN"] = "1"
        log("步骤：下载 NLTK punkt_tab 数据")
        result = subprocess.run([str(ASR_PYTHON), "-c",
                                 "import nltk,os;os.environ['NLTK_ALLOW_PROXIED_URLOPEN']='1';"
                                 "raise SystemExit(0 if nltk.download('punkt_tab') else 1)"],
                                capture_output=True, text=True, env=nltk_env,
                                creationflags=_no_window_flags())
        log(result.stdout[-1500:])
        if result.returncode != 0:
            log(f"步骤失败 returncode={result.returncode}: {result.stderr[-1500:]}")
            return
        log("说话人分离组件安装完成；还需在设置页配置 Hugging Face Token "
            "并在 HF 网站接受 pyannote 模型协议")
    except Exception as exc:
        log(f"安装异常: {exc}")
    finally:
        _diarization_installing = False


def _install_asr() -> None:
    global _asr_installing
    _asr_installing = True

    def log(message: str) -> None:
        with ASR_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"[{dt.datetime.now().isoformat(timespec='seconds')}] {message}\n")

    try:
        log("开始安装本地转写环境")
        import subprocess
        import sys
        if not ASR_VENV.exists():
            log("创建虚拟环境 asr_venv")
            result = subprocess.run([sys.executable, "-m", "venv", str(ASR_VENV)],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                log(f"venv 创建失败: {result.stderr[-2000:]}")
                return
        env = _asr_env()
        log("pip 安装 faster-whisper（官方源）")
        result = subprocess.run([str(ASR_PYTHON), "-m", "pip", "install", "--upgrade",
                                 "-i", "https://pypi.org/simple",
                                 "faster-whisper", "av==12.3.0"],
                                capture_output=True, text=True, env=env,
                                creationflags=_no_window_flags())
        log(result.stdout[-3000:])
        if result.returncode != 0:
            log(f"安装失败 returncode={result.returncode}: {result.stderr[-3000:]}")
        else:
            log("安装完成，首次转写时会自动下载识别模型")
    except Exception as exc:
        log(f"安装异常: {exc}")
    finally:
        _asr_installing = False
LAST_REFRESH_AT: float | None = None
SCHEDULER_INTERVAL = 60.0
SCHEDULER_MIN_GAP = 2 * 3600.0
SCHEDULER_TZ = dt.timezone(dt.timedelta(hours=8))


def browser_exe() -> str | None:
    """找一个可用的 Chromium 内核浏览器可执行文件（用于无痕打开）。"""
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def open_link_privately(url: str) -> dict:
    """优先用浏览器无痕窗口打开链接（对齐桌面版【删除标签】任务的双击行为）。"""
    exe = browser_exe()
    if exe:
        flag = "--incognito" if "chrome.exe" in exe.lower() else "--inprivate"
        import subprocess
        subprocess.Popen([exe, flag, url], shell=False)
        return {"ok": True, "mode": "private"}
    webbrowser.open(url)
    return {"ok": True, "mode": "default"}


def scheduled_refresh_loop() -> None:
    """定时刷新守护：北京时间 8:00-20:00、距上次≥2小时、且有页面在线时静默刷新。"""
    global LAST_REFRESH_AT
    while True:
        time.sleep(SCHEDULER_INTERVAL)
        try:
            now = time.monotonic()
            with CLIENT_LOCK:
                has_clients = bool(CLIENTS)
            if not has_clients:
                continue
            beijing_hour = dt.datetime.now(SCHEDULER_TZ).hour
            if not (8 <= beijing_hour < 20):
                continue
            if LAST_REFRESH_AT is not None and now - LAST_REFRESH_AT < SCHEDULER_MIN_GAP:
                continue
            if not REFRESH_LOCK.acquire(blocking=False):
                continue
            REFRESH_LOCK.release()
            result = refresh_board(actor="scheduler")
            if result.get("status") in {"ok", "partial"}:
                LAST_REFRESH_AT = time.monotonic()
        except Exception:
            traceback.print_exc()


def client_event(action: str, client_id: str) -> int:
    global CLIENT_EVER_CONNECTED, CLIENT_EMPTY_SINCE
    if not client_id or len(client_id) > 128 or not all(ch.isalnum() or ch in "-_" for ch in client_id):
        raise ValueError("无效的页面客户端标识")
    now = time.monotonic()
    with CLIENT_LOCK:
        if action in {"open", "heartbeat"}:
            CLIENTS[client_id] = now
            CLIENT_EVER_CONNECTED = True
            CLIENT_EMPTY_SINCE = None
        elif action == "close":
            CLIENTS.pop(client_id, None)
            if CLIENT_EVER_CONNECTED and not CLIENTS and CLIENT_EMPTY_SINCE is None:
                CLIENT_EMPTY_SINCE = now
        else:
            raise ValueError("无效的页面客户端动作")
        return len(CLIENTS)


def auto_shutdown_monitor(server: ThreadingHTTPServer) -> None:
    """Stop only after the final page leaves and in-flight work is complete."""
    global CLIENT_EMPTY_SINCE
    started = time.monotonic()
    while True:
        time.sleep(1.0)
        now = time.monotonic()
        with CLIENT_LOCK:
            stale = [client_id for client_id, seen in CLIENTS.items() if now - seen > CLIENT_STALE_AFTER]
            for client_id in stale:
                CLIENTS.pop(client_id, None)
            if CLIENTS:
                CLIENT_EMPTY_SINCE = None
            elif CLIENT_EVER_CONNECTED and CLIENT_EMPTY_SINCE is None:
                CLIENT_EMPTY_SINCE = now
            empty_for = now - CLIENT_EMPTY_SINCE if CLIENT_EMPTY_SINCE is not None else 0.0
            should_stop = CLIENT_EVER_CONNECTED and not CLIENTS and empty_for >= AUTO_SHUTDOWN_EMPTY_GRACE
            no_page_timeout = not CLIENT_EVER_CONNECTED and now - started >= 120.0
            active = ACTIVE_REQUESTS
        if (should_stop or no_page_timeout) and active == 0:
            print("No active browser pages; stopping local service.", flush=True)
            server.shutdown()
            return


def tracker_module():
    global TRACKER_MODULE
    if TRACKER_MODULE is None:
        path = ROOT / "spark_task_tracker.py"
        spec = importlib.util.spec_from_file_location("spark_legacy", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        TRACKER_MODULE = module
    return TRACKER_MODULE


def automation() -> NotionAutomation:
    return NotionAutomation(NotionClient(NOTION_TOKEN, WORKSPACE["api_version"]), STORE, WORKSPACE)


def chain_rpc() -> chain.ChainRPC:
    return chain.ChainRPC()


def _chain_notion() -> Any:
    """链上检索的 Notion 依赖。未配置 Token 时返回 None：检索照常进行，
    只是不与 Notion 表对账（全部报 missing / 多签无项目清单）。"""
    if not NOTION_TOKEN:
        return None
    return automation().notion


def _chain_limit(query: dict, default: int = 500, maximum: int = 2000) -> int:
    raw = (query.get("limit") or [""])[0]
    try:
        return max(1, min(int(raw), maximum)) if raw else default
    except ValueError:
        return default


def quick_links() -> dict[str, str]:
    values = {
        "dashboard": "https://app.notion.com/p/CKB-Spark-Program-Dashboard-2a0639bc0d1b833ca3ca010fd134795b",
        "project_list": "https://app.notion.com/p/e95639bc0d1b83f5952d811d03c925b3",
        "fund_pool": "https://app.notion.com/p/146639bc0d1b83c18f1181a8fa1c9261",
        "contacts": "https://www.notion.so/Spark-Program-Mini-Grants-1da24205dae080978340d534c2f4a94d",
    }
    try:
        values.update(tracker_module().QUICK_LINKS)
    except Exception:
        pass
    return values


# ===== Notion 本地反向代理 =====
# evergreen-stream-df9.notion.site 对公开页面也下发 X-Frame-Options: SAMEORIGIN
# 与受限 CSP，平台级禁止外部嵌入。本地工作台 (127.0.0.1) 与 iframe 是同级源，
# 由本服务反向代理文档并剥除帧限制头、把相对资源引用改写成 notion.site 绝对地址，
# 使脚本/样式直连 notion.site 加载（子资源不受 X-Frame-Options 限制）。
NOTION_UPSTREAM_BASE = "https://evergreen-stream-df9.notion.site"
NOTION_PROXY_TIMEOUT = 30
NOTION_PROXY_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_notion_urlopen = urllib.request.urlopen  # 测试可注入假上游

# 代理响应必须剥除的响应头（值均为小写键）
_PROXY_STRIP_HEADERS = {
    "x-frame-options",                      # 否则 iframe 被浏览器拒绝
    "content-security-policy",              # 限制 iframe 与脚本源，本地嵌入必须去
    "content-security-policy-report-only",
    "set-cookie",                           # 第三方 iframe 场景 cookie 会被拒，留着只报错
    # 逐跳头 / 由本服务重建的头
    "transfer-encoding", "connection", "keep-alive", "content-length",
    "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade",
}
# HTML 属性引用形态（不含斜杠，斜杠属于资源前缀）→ 这些前缀开头的相对路径补绝对域名
_PROXY_ATTR_NEEDLES = ('href="', 'src="', 'action="', 'content="', 'url(', '"')
# Notion 现行静态资源前缀：/_assets 为新版前端资源目录，/_next 为旧版，不可删
_PROXY_ASSET_PREFIXES = ("/_assets", "/_next", "/_notion", "/images")
# 前缀级反向代理白名单（写死，不开放任意路径）：SPA 壳内相对引用与同源 XHR
# （loadCachedPageChunkV2 等 POST JSON）经本服务同路径透传到 notion.site。
# 实测工作台自身路由（/、/static/、/api/*）均不在这些前缀下，无前缀方案无冲突。
NOTION_PROXY_PREFIXES = ("/_assets/", "/_next/", "/_notion/", "/images/", "/api/v3/",
                         "/statsig/", "/print.", "/f/", "/katex/")
# Notion 前端会把地址栏规范化为 /<标题>-<32位十六进制页面ID> 并整页跳转（实测行为），
# 因此页面路径必须在根路径下提供代理，否则规范化请求落到工作台 404 直接整页死亡。
# 匹配规则：单段或多段路径，最后一段以 32 位小写十六进制 ID 结尾（标题部分可含字母数字、
# 连字符、下划线、括号、波浪号），且不含点号（排除 /static/ 下的文件）。
_NOTION_PAGE_PATH = re.compile(r"^/[A-Za-z0-9_\-~%/]*[A-Za-z0-9_\-]*-?[0-9a-f]{32}$")


def is_notion_page_path(path: str) -> bool:
    """是否为 Notion 公开页路径（末段以 32 位十六进制页面 ID 结尾、无文件扩展名）。"""
    if "." in path.rsplit("/", 1)[-1]:
        return False
    return bool(_NOTION_PAGE_PATH.match(path))


def rewrite_notion_html(body: bytes) -> bytes:
    """把 Notion HTML 中的相对资源引用改写成 notion.site 绝对地址。

    只替换紧跟 /_assets、/_next、/_notion、/images 的引用，已含域名的绝对地址不受影响。
    """
    text = body.decode("utf-8", errors="replace")
    for needle in _PROXY_ATTR_NEEDLES:
        for prefix in _PROXY_ASSET_PREFIXES:
            text = text.replace(needle + prefix, needle + NOTION_UPSTREAM_BASE + prefix)
    return text.encode("utf-8")


def fetch_notion(target: str, method: str = "GET", body: bytes | None = None,
                 content_type: str | None = None):
    """请求上游固定地址；target 含 path 与 query string。调用方处理 HTTPError。

    POST 时原样转发请求体（loadCachedPageChunkV2 需要）并透传 Content-Type。
    """
    upstream = f"{NOTION_UPSTREAM_BASE}/{target}"
    headers = {
        "User-Agent": NOTION_PROXY_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "identity",  # 不请求压缩，省去解压，浏览器拿到的都是明文
    }
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(upstream, method=method, data=body, headers=headers)
    return _notion_urlopen(request, timeout=NOTION_PROXY_TIMEOUT)


def _base_payload(topic_id: str) -> dict:
    rows = STORE.query("SELECT payload_json FROM sync_jobs WHERE topic_id=? AND workflow='in_progress' ORDER BY updated_at DESC LIMIT 1",
                       (topic_id,))
    if rows and rows[0].get("payload_json"):
        return json.loads(rows[0]["payload_json"])
    projects = STORE.query("SELECT * FROM projects WHERE topic_id=?", (topic_id,))
    row = projects[0] if projects else {}
    return {"topic_id": topic_id, "project_name": row.get("title", "")}


def _board_posts() -> dict[str, dict]:
    """从看板缓存取 topic_id → 帖子信息（标题、作者），用于作业卡片富化。"""
    posts: dict[str, dict] = {}
    try:
        board = legacy.load_cache() or {}
        for group in (board.get("all_posts") or {}).values():
            for post in group or []:
                posts[str(post.get("id"))] = post
        for group in (board.get("classified") or {}).get("spark_program", {}).values():
            for post in group or []:
                posts.setdefault(str(post.get("id")), post)
    except Exception:
        pass
    return posts


def enrich_jobs_state(state: dict) -> dict:
    """给同步作业补充 project_name / applicant，标记已被兄弟任务覆盖的僵尸作业。

    - 历史播种用过 workflow='in-progress'，refresh 用 'in_progress'，两者指向同一
      项目；只要同一 topic 下归一化 workflow 相同且存在 verified/core_verified 的
      兄弟任务，本作业即视为 stale（已被覆盖，无需再执行）。
    - status='ignored' 的作业不再返回（用户手动忽略）。
    """
    projects = {str(row["topic_id"]): row for row in state.get("projects") or []}
    posts = _board_posts()

    def normalize(workflow: str) -> str:
        return "in_progress" if workflow == "in-progress" else workflow

    jobs = [job for job in (state.get("jobs") or []) if job.get("status") != "ignored"]
    # 待执行类作业（queued/ready/needs_input/partial）若存在同 topic 同 workflow 的
    # 兄弟作业已 verified/core_verified/conflict，即视为僵尸。conflict 兄弟说明
    # Notion 中已有同名记录（仅属性不一致）；conflict 作业本身始终展示（需人工核对）。
    covering = ("verified", "core_verified", "conflict")
    for job in jobs:
        topic_id = str(job.get("topic_id") or "")
        payload = job.get("payload") or {}
        project = projects.get(topic_id) or {}
        post = posts.get(topic_id) or {}
        name = (payload.get("project_name") or project.get("title")
                or post.get("title") or "")
        if name:
            job["project_name"] = name
        applicant = (post.get("original_poster_username") or post.get("author") or "")
        if applicant:
            job["applicant"] = applicant
        if job.get("status") in ("queued", "ready", "needs_input", "partial"):
            pair = (topic_id, normalize(job.get("workflow") or ""))
            if any((str(other.get("topic_id") or ""), normalize(other.get("workflow") or "")) == pair
                   and other.get("status") in covering
                   and other.get("idempotency_key") != job.get("idempotency_key")
                   for other in jobs):
                job["stale"] = True
    state["jobs"] = jobs
    return state


def refresh_board(actor: str = "local-admin") -> dict:
    global LAST_REFRESH_AT
    if not REFRESH_LOCK.acquire(blocking=False):
        return {"status": "busy", "message": "已有刷新任务正在运行"}
    result = {"new_projects": [], "weekly_updates": [], "transitions": [], "errors": [],
              "notion_configured": bool(NOTION_TOKEN), "auto_notion": bool(AUTO_NOTION)}
    try:
        board = tracker_module().fetch_nervos_talk_projects()
        board = legacy.apply_whitelist(board, legacy.load_data().get("board_whitelist", {}))
        legacy.save_cache(board)
        if board.get("errors"):
            result["errors"].extend(board["errors"])
        classified = board.get("classified") or {}
        scanned = sum(len(classified.get(k) or []) for k in
                      ("submitted", "pending", "in_progress", "rejection", "completion", "closure"))
        spark_group = classified.get("spark_program") or {}
        if isinstance(spark_group, dict):
            scanned += sum(len(v) for v in spark_group.values() if isinstance(v, list))
        result["scanned"] = scanned

        # AI 两级分类（对齐桌面版）：proposal / non_proposal，并自动建【预审】【删除标签】任务
        try:
            provider, api_key, model = legacy.ai_credentials()
            classify_ai = getattr(tracker_module(), "classify_spark_posts_with_ai", None)
            if api_key and classify_ai:
                classify_ai(classified, api_key, provider, model)
                board = legacy.apply_whitelist(board, legacy.load_data().get("board_whitelist", {}))
                legacy.save_cache(board)
                classified = board.get("classified") or {}
                result["ai_classified"] = True
                spark_groups = classified.get("spark_program") or {}
                result["ai_classify_counts"] = {
                    "pre_review": len(spark_groups.get("pre_review", [])),
                    "non_proposal": len(spark_groups.get("non_proposal", [])),
                }
                try:
                    added = legacy.auto_create_review_tasks(STORE)
                    if added:
                        result["auto_tasks"] = added
                except Exception as exc:
                    result["errors"].append(f"auto_tasks: {exc}")
        except Exception as exc:
            result["errors"].append(f"ai_classify: {exc}")
        LAST_REFRESH_AT = time.monotonic()
        seeded = STORE.get_meta("board_seeded_v1")
        states = {"in_progress": "in_progress", "completion": "completion", "closure": "closure"}
        whitelist = board.get("board_whitelist") or {}
        def proposal_post(post: dict) -> bool:
            return not project_tracking._related_whitelist_status(
                post, str(whitelist.get(str(post.get("id"))) or ""))
        if not seeded:
            preexisting = {row["topic_id"] for row in STORE.query("SELECT topic_id FROM projects")}
            for bucket, lifecycle in states.items():
                for post in classified.get(bucket, []):
                    if not proposal_post(post):
                        continue
                    topic_id = str(post["id"])
                    if topic_id not in preexisting:
                        STORE.upsert_project(topic_id, post.get("title", ""), post.get("url", ""),
                                             lifecycle, int(post.get("posts_count") or 0))
            STORE.set_meta("board_seeded_v1", utcnow())
            STORE.audit(actor, "board_seeded", "nervos_talk", {"projects": len(STORE.query('SELECT * FROM projects'))})
            result["seeded"] = True

        # Rebuild local↔Notion identity before any automatic write.  This makes
        # fresh installs and restored databases adopt complete manual records
        # instead of creating title-based duplicates.
        if NOTION_TOKEN:
            try:
                runner = automation()
                if hasattr(runner, "reconcile_notion"):
                    overview = runner.reconcile_notion(actor)
                    result["notion_reconciled"] = {
                        "matched": len(overview.get("matched_projects") or []),
                        "verified": len(overview.get("verified_jobs") or []),
                        "conflicts": overview.get("conflicts") or [],
                    }
            except Exception as exc:
                result["errors"].append(f"Notion 对账失败，已停止本轮自动写入: {exc}")
                STORE.audit(actor, "notion_reconcile_failed", "", {"error": str(exc)})
                # A failed identity preflight makes all following creates unsafe.
                return {**result, "status": "partial"}

        in_progress_posts = {str(p["id"]): p for p in classified.get("in_progress", []) if proposal_post(p)}
        for topic_id, post in in_progress_posts.items():
            existing = STORE.query("SELECT * FROM projects WHERE topic_id=?", (topic_id,))
            is_new = STORE.upsert_project(topic_id, post.get("title", ""), post.get("url", ""),
                                          "in_progress", int(post.get("posts_count") or 0) if not existing else 0)
            if is_new:
                STORE.create_workflow_tasks(topic_id, post.get("title", ""), quick_links())
                key = STORE.queue_job(topic_id, "in_progress")
                try:
                    topic = fetch_topic(topic_id)
                    payload = enrich_notion_payload("new-project", extract_new_project(topic))
                    STORE.set_job_payload(key, payload)
                    if AUTO_NOTION and NOTION_TOKEN:
                        automation().execute_new_project(payload, actor)
                    else:
                        STORE.set_job_status(key, "ready", "已提取数据；等待配置 NOTION_TOKEN 或手动执行")
                    STORE.advance_forum_cursor(topic_id, max((p.get("post_number", 0) for p in topic.get("post_stream", {}).get("posts", [])), default=0))
                    result["new_projects"].append(topic_id)
                except Exception as exc:
                    STORE.set_job_status(key, "needs_input", str(exc))
                    result["errors"].append(f"{topic_id}: {exc}")
            else:
                project = existing[0]
                # 刷新幂等：该 topic 的入库作业已核验/被忽略时，绝不再为其排队
                # in_progress 作业（否则会把用户主动忽略的问题作业重新跑一遍，重复建页）
                settled = STORE.query(
                    """SELECT 1 FROM sync_jobs WHERE topic_id=?
                       AND workflow IN ('in_progress','in-progress')
                       AND status IN ('verified','core_verified','ignored') LIMIT 1""", (topic_id,))
                pending = [] if settled else STORE.query("""SELECT j.*,s.status AS step_status FROM sync_jobs j
                    JOIN sync_steps s USING(idempotency_key)
                    WHERE j.topic_id=? AND j.workflow='in_progress'
                      AND s.step IN ('fund_pool','project_list') AND s.status IN ('pending','failed')""", (topic_id,))
                if pending:
                    try:
                        job = pending[0]
                        topic = fetch_topic(topic_id)
                        payload = json.loads(job["payload_json"]) if job.get("payload_json") else extract_new_project(topic)
                        payload = enrich_notion_payload("new-project", payload)
                        STORE.set_job_payload(job["idempotency_key"], payload)
                        if AUTO_NOTION and NOTION_TOKEN:
                            automation().execute_new_project(payload, actor)
                        else:
                            STORE.set_job_status(job["idempotency_key"], "ready", "已恢复未完成入库；等待配置 NOTION_TOKEN 或手动执行")
                        STORE.advance_forum_cursor(topic_id, max((p.get("post_number", 0) for p in topic.get("post_stream", {}).get("posts", [])), default=0))
                        result["new_projects"].append(topic_id)
                    except NotionConflict as exc:
                        result["errors"].append(f"resume {topic_id}: {exc}")
                    except Exception as exc:
                        result["errors"].append(f"resume {topic_id}: {exc}")
                    continue
                cursor = int(project.get("last_forum_post_number") or 0)
                if int(post.get("posts_count") or 0) > cursor:
                    try:
                        topic = fetch_topic(topic_id)
                        update = extract_weekly_update(topic, cursor)
                        if update:
                            update.update({"topic_id": topic_id, "project_name": project["title"]})
                            key = STORE.queue_job(topic_id, "weekly", update)
                            if AUTO_NOTION and NOTION_TOKEN:
                                automation().execute_weekly_update(update, actor)
                            else:
                                STORE.set_job_status(key, "ready", "已提取周报；等待配置 NOTION_TOKEN 或手动执行")
                            result["weekly_updates"].append(topic_id)
                    except Exception as exc:
                        result["errors"].append(f"weekly {topic_id}: {exc}")

        for bucket in ("completion", "closure"):
            target_status = WORKSPACE["select_values"]["completed" if bucket == "completion" else "shutdown"]
            for post in classified.get(bucket, []):
                if not proposal_post(post):
                    continue
                topic_id = str(post["id"])
                title = post.get("title", "")
                # 结项公告/报告/公示类帖子不是项目主帖，不建结项页
                if re.search(r"completion report|结项报告|announcement|公示|spotlight|专访|termination", title, re.I):
                    continue
                existing = STORE.query("SELECT * FROM projects WHERE topic_id=?", (topic_id,))
                if not existing:
                    STORE.upsert_project(topic_id, title, post.get("url", ""), bucket,
                                         int(post.get("posts_count") or 0))
                    continue
                # 刷新幂等：该 topic 的此类作业已核验（迁移跑过）或被用户忽略时，
                # 不再排队 completion/closure 作业，防止重复建结项页
                settled = STORE.query(
                    """SELECT 1 FROM sync_jobs WHERE topic_id=? AND workflow=?
                       AND status IN ('verified','core_verified','ignored') LIMIT 1""",
                    (topic_id, bucket))
                if settled:
                    continue
                key = STORE.queue_job(topic_id, bucket)
                try:
                    topic = fetch_topic(topic_id)
                    base = _base_payload(topic_id)
                    if NOTION_TOKEN:
                        try:
                            recovered = automation().read_ongoing_base(topic_id)
                            base = {**recovered, **{k: v for k, v in base.items() if v}}
                        except Exception as exc:
                            result["errors"].append(f"{bucket} {topic_id} Notion 基础字段回读失败: {exc}")
                    payload = extract_completion(topic, base, target_status)
                    missing = [name for name in REQUIRED_COMPLETION if not payload.get(name)]
                    if missing:
                        try:
                            for name, value in _extract_from_posts(topic, missing).items():
                                if not payload.get(name) and value not in (None, "", []):
                                    payload[name] = value
                        except Exception as exc:
                            result["errors"].append(f"{bucket} {topic_id} 补充提取失败: {exc}")
                    STORE.set_job_payload(key, payload)
                    if AUTO_NOTION and NOTION_TOKEN:
                        automation().execute_completion(payload, actor)
                    else:
                        STORE.set_job_status(key, "ready", "已提取结项数据；等待配置 NOTION_TOKEN 或手动执行")
                    result["transitions"].append(topic_id)
                except Exception as exc:
                    STORE.set_job_status(key, "needs_input", str(exc))
                    result["errors"].append(f"{bucket} {topic_id}: {exc}")
        STORE.audit(actor, "board_refreshed", "nervos_talk", result)
        result["board_counts"] = {
            "pre_review": len((classified.get("spark_program") or {}).get("pre_review", [])),
            **{name: len(classified.get(name, [])) for name in ("submitted", "pending", "in_progress", "rejection", "completion", "closure")},
        }
        result["status"] = "ok" if not result["errors"] else "partial"
        return result
    finally:
        REFRESH_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "SparkWorkbench/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def handle_one_request(self) -> None:
        global ACTIVE_REQUESTS
        with CLIENT_LOCK:
            ACTIVE_REQUESTS += 1
        try:
            super().handle_one_request()
        finally:
            with CLIENT_LOCK:
                ACTIVE_REQUESTS -= 1

    def _cookies(self) -> cookies.SimpleCookie:
        jar = cookies.SimpleCookie()
        jar.load(self.headers.get("Cookie", ""))
        return jar

    def _session(self) -> dict | None:
        if not TEAM_MODE:
            return {"username": "local-admin", "role": "administrator", "csrf_token": LOCAL_CSRF,
                    "forum_username": legacy.forum_binding()}
        token = self._cookies().get("spark_session")
        if not token:
            return None
        token_hash = _team_token_hash(token.value)
        rows = STORE.query("""SELECT s.username,s.csrf_token,s.expires_at,u.role,u.forum_username,u.enabled
            FROM sessions s JOIN users u USING(username) WHERE s.token_hash=?""", (token_hash,))
        if rows and rows[0]["enabled"] and rows[0]["expires_at"] > utcnow() and (
                _committee_talk_name(rows[0]["forum_username"]) in legacy.COMMITTEE_MEMBERS):
            return rows[0]
        return None

    def _actor(self) -> str:
        session = self._session()
        return session["username"] if session else "anonymous"

    def _password_transport(self) -> bool:
        """Accept only direct local access or the explicitly configured private TLS proxy."""
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        local_peer = self.client_address[0] in ("127.0.0.1", "::1")
        configured = (os.environ.get("SPARK_TEAM_HTTPS_ORIGIN") or
                      load_env().get("SPARK_TEAM_HTTPS_ORIGIN", "")).rstrip("/")
        if (local_peer and configured.startswith("https://") and origin == configured
                and self.headers.get("X-Forwarded-Proto") == "https"):
            return True
        if (local_peer and re.fullmatch(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?", host, re.I)
                and not self.headers.get("X-Forwarded-Proto") and origin in ("", f"http://{host}")):
            return False
        raise PermissionError("请从本机地址或已配置的私有 HTTPS 入口访问，勿通过局域网 HTTP 输入密码")

    def _login_response(self, username: str, secure: bool = False) -> None:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        token_hash = _team_token_hash(token)
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)).isoformat()
        with STORE.transaction() as conn:
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                         (token_hash, username, csrf, expires, utcnow()))
        body = json.dumps({"ok": True, "csrf_token": csrf}).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8",
                   {"Set-Cookie": f"spark_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"
                                  + ("; Secure" if secure else "")})

    def _send(self, status: int, body: bytes, content_type: str,
              extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    # ===== Notion 反向代理（见模块级说明） =====
    def _send_proxy(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
        """直接写响应，绕开 _send 默认附加的 X-Frame-Options/CSP（否则 iframe 被拦）。"""
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def _proxy_notion(self, parsed, method: str = "GET", body: bytes | None = None,
                      content_type: str | None = None, target: str | None = None) -> None:
        if target is None:
            target = parsed.path.removeprefix("/notion-view/")
        if not target or target.startswith("/notion-view"):
            self.json(404, {"error": "not found"})
            return
        if parsed.query:
            target = f"{target}?{parsed.query}"
        try:
            try:
                resp = fetch_notion(target, method=method, body=body, content_type=content_type)
            except urllib.error.HTTPError as exc:
                resp = exc  # 4xx/5xx 原样转发状态码与正文，前端能看到错误而不是空白
            status = int(getattr(resp, "status", None) or getattr(resp, "code", 500) or 500)
            raw_headers = resp.headers.items() if resp.headers else []
            out_headers: list[tuple[str, str]] = []
            for key, value in raw_headers:
                lower = key.lower()
                if lower in _PROXY_STRIP_HEADERS:
                    continue
                if lower == "location":
                    value = self._rewrite_location(value)
                out_headers.append((key, value))
            content_type = (resp.headers.get("Content-Type") or "").lower() if resp.headers else ""
            body = resp.read() or b""
            if "text/html" in content_type:
                body = rewrite_notion_html(body)
            self._send_proxy(status, out_headers, body)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # notion.site 网络不可达等：返回带标记的 502，前端据此显示回退说明
            message = f"无法连接 notion.site（{exc}）"
            body = (f'<!doctype html><html><body data-spark-proxy-error="1">'
                    f'<p style="font-family:sans-serif">{message}</p>'
                    f'<p>请点击面板中的「↗ 在 Notion 中打开」直接查看公开页面。</p>'
                    f'</body></html>').encode("utf-8")
            self._send_proxy(502, [("Content-Type", "text/html; charset=utf-8"),
                                   ("Cache-Control", "no-store")], body)
        except Exception:
            traceback.print_exc()
            self.json(502, {"error": "Notion 代理加载失败"})

    def _proxy_notion_prefix(self, parsed) -> None:
        """白名单前缀透传：SPA 壳内相对引用（对不同 UA 引号形态多变，HTML 重写兜不全）
        与同源 XHR 打到本服务后，按同路径代理到 notion.site，GET/POST 均支持。"""
        method = self.command if self.command in {"GET", "POST"} else "GET"
        body = None
        content_type = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 20_000_000:
                self.json(413, {"error": "请求体过大"})
                return
            body = self.rfile.read(length) if length else None
            content_type = self.headers.get("Content-Type")
        target = parsed.path.lstrip("/")
        if parsed.query:
            target = f"{target}?{parsed.query}"
        try:
            try:
                resp = fetch_notion(target, method=method, body=body, content_type=content_type)
            except urllib.error.HTTPError as exc:
                resp = exc  # 4xx/5xx 原样转发状态码与正文，前端能看到错误而不是空白
            status = int(getattr(resp, "status", None) or getattr(resp, "code", 500) or 500)
            raw_headers = resp.headers.items() if resp.headers else []
            out_headers: list[tuple[str, str]] = []
            for key, value in raw_headers:
                lower = key.lower()
                if lower in _PROXY_STRIP_HEADERS:
                    continue
                out_headers.append((key, value))
            content_type_resp = (resp.headers.get("Content-Type") or "").lower() if resp.headers else ""
            resp_body = resp.read() or b""
            if "text/html" in content_type_resp:
                resp_body = rewrite_notion_html(resp_body)
            self._send_proxy(status, out_headers, resp_body)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            message = f"无法连接 notion.site（{exc}）"
            err_body = (f'<!doctype html><html><body data-spark-proxy-error="1">'
                        f'<p style="font-family:sans-serif">{message}</p>'
                        f'</body></html>').encode("utf-8")
            self._send_proxy(502, [("Content-Type", "text/html; charset=utf-8"),
                                   ("Cache-Control", "no-store")], err_body)
        except Exception:
            traceback.print_exc()
            self.json(502, {"error": "Notion 代理加载失败"})

    @staticmethod
    def _rewrite_location(value: str) -> str:
        """上游 3xx：同域 Location 重写回 /notion-view/ 继续代理；外域原样透传。"""
        try:
            parts = urllib.parse.urlsplit(value)
        except ValueError:
            return value
        if parts.scheme in {"http", "https"} or parts.netloc:
            if parts.netloc != urllib.parse.urlsplit(NOTION_UPSTREAM_BASE).netloc:
                return value
            rewritten = parts.path.lstrip("/")
            if parts.query:
                rewritten = f"{rewritten}?{parts.query}"
            return f"/notion-view/{rewritten}"
        if value.startswith("/") and not value.startswith("//"):
            return f"/notion-view{value}"
        return value

    def do_HEAD(self) -> None:
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def body_json(self, max_bytes: int = 2_000_000) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > max_bytes:
            raise ValueError("请求体过大")
        return payload_from_json(self.rfile.read(length) if length else b"{}")

    def body_bytes(self, max_bytes: int) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > max_bytes:
            raise ValueError("ZIP 文件为空或请求体过大")
        return self.rfile.read(length)

    def require_auth(self) -> dict:
        session = self._session()
        if not session:
            raise PermissionError("请先登录")
        return session

    def require_csrf(self, session: dict) -> None:
        # Local mode has no authenticated browser session. It is protected by
        # loopback binding, same-origin JSON requests, and strict Host/Origin
        # validation instead of a process-random token that becomes stale
        # whenever the server is restarted while a browser tab remains open.
        if not TEAM_MODE:
            host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise PermissionError("本地服务拒绝非本机 Host")
            origin = self.headers.get("Origin", "")
            if origin:
                origin_host = (urllib.parse.urlparse(origin).hostname or "").lower()
                if origin_host not in {"127.0.0.1", "localhost", "::1"}:
                    raise PermissionError("本地服务拒绝跨站请求")
            return
        supplied = self.headers.get("X-CSRF-Token", "")
        if not supplied or not secrets.compare_digest(supplied, session["csrf_token"]):
            raise PermissionError("CSRF 校验失败")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self.json(200, {"ok": True, "version": BUILD_VERSION, "launch_nonce": LAUNCH_NONCE,
                            "team_mode": TEAM_MODE, "notion_configured": bool(NOTION_TOKEN),
                            "auto_notion": AUTO_NOTION})
            return
        if path == "/api/state":
            session = self._session()
            if not session:
                self.json(401, {"error": "请先登录"})
                return
            state = enrich_jobs_state(STORE.list_state())
            state["session"] = {"username": session["username"], "role": session["role"],
                                "csrf_token": session["csrf_token"]}
            state["config"] = {"version": BUILD_VERSION, "team_mode": TEAM_MODE, "notion_configured": bool(NOTION_TOKEN),
                               "auto_notion": AUTO_NOTION}
            self.json(200, state)
            return
        if path == "/api/workbench":
            session = self._session()
            if not session:
                self.json(401, {"error": "请先登录"})
                return
            value = legacy.public_state(quick_links())
            value.update(enrich_jobs_state(STORE.list_state()))
            value["timeline"] = legacy.timeline(value.get("board") or {})
            value["project_tracking"] = project_tracking.load()
            value["session"] = {"username": session["username"], "role": session["role"],
                                "csrf_token": session["csrf_token"],
                                "forum_username": session.get("forum_username")}
            value["config"] = {"version": BUILD_VERSION, "team_mode": TEAM_MODE, "notion_configured": bool(NOTION_TOKEN),
                               "auto_notion": AUTO_NOTION}
            self.json(200, value)
            return
        if path == "/api/notion/overview":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            cached = STORE.get_meta("notion_overview")
            self.json(200, json.loads(cached) if cached else {"updated_at": None, "sources": {}})
            return
        if path == "/api/notion/preview":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            preview_path = ROOT / "notion_preview.json"
            if preview_path.exists():
                try:
                    self.json(200, json.loads(preview_path.read_text(encoding="utf-8")))
                except (ValueError, OSError):
                    self.json(404, {"error": "预览配置文件无效"})
            else:
                self.json(404, {"error": "未配置预览页面"})
            return
        if path == "/api/notion/preview-table":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            key = (query.get("key") or [""])[0]
            refresh = (query.get("refresh") or [""])[0].lower() in {"1", "true", "yes"}
            preview_cfg = ROOT / "notion_preview.json"
            if not preview_cfg.exists():
                self.json(404, {"error": "未配置预览页面"})
                return
            try:
                config = json.loads(preview_cfg.read_text(encoding="utf-8"))
                result = notion_preview.table_json(key, config, refresh=refresh)
            except notion_preview.PreviewError as exc:
                self.json(502, {"error": str(exc)})
                return
            except (ValueError, OSError) as exc:
                self.json(500, {"error": f"预览配置读取失败: {exc}"})
                return
            self.json(200, result)
            return
        if path == "/notion-view" or path.startswith("/notion-view/"):
            # 本地反向代理公开 Notion 页面（GET-only、上游固定、不含业务数据），
            # iframe 内联加载不走会话校验，剥帧限制头后可直接嵌入。
            self._proxy_notion(parsed)
            return
        if any(path.startswith(prefix) for prefix in NOTION_PROXY_PREFIXES):
            # Notion SPA 壳内相对引用/同源 XHR 的前缀级透传（白名单见模块定义），
            # 同样不走会话校验：iframe 页面是公开内容，本服务只起中继作用。
            self._proxy_notion_prefix(parsed)
            return
        if is_notion_page_path(path):
            # Notion 前端地址栏规范化后的整页跳转（见 _NOTION_PAGE_PATH 说明），
            # 按同路径代理 SPA HTML 壳，浏览器地址栏与 Notion 路由完全一致。
            self._proxy_notion(parsed, target=path.lstrip("/"))
            return
        if path == "/api/chain/scan-main":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            try:
                query = urllib.parse.parse_qs(parsed.query)
                if (query.get("cached") or [""])[0].lower() in {"1", "true"}:
                    # 只读缓存：不触发任何链上请求，直接返回上次扫描结果
                    self.json(200, chain.cached_main())
                    return
                limit = _chain_limit(query)
                resume = (query.get("resume") or ["1"])[0].lower() not in {"0", "false", "no"}
                result = chain.scan_main(_chain_notion(), WORKSPACE, chain_rpc(),
                                         limit=limit, resume=resume)
                if not NOTION_TOKEN:
                    result["notice"] = "未配置 Notion Token：以下为链上全量结果，全部按未入库处理"
                result.pop("known_multisig", None)
                result["updated_at"] = chain.cached_main().get("updated_at")
                self.json(200, result)
            except chain.ChainError as exc:
                self.json(502, {"error": f"链上检索失败: {exc}"})
            return
        if path == "/api/chain/scan-multisig":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            try:
                query = urllib.parse.parse_qs(parsed.query)
                if (query.get("cached") or [""])[0].lower() in {"1", "true"}:
                    self.json(200, chain.cached_multisig())
                    return
                resume = (query.get("resume") or ["1"])[0].lower() not in {"0", "false", "no"}
                if not NOTION_TOKEN and not chain.local_project_wallets():
                    self.json(200, {"multisig": [], "updated_at": None,
                                    "notice": "未配置 Notion Token，且本地 project_wallets.json 没有已确认的多签清单：多签检索暂不可用；主钱包检索不受影响"})
                    return
                result = chain.scan_multisig(_chain_notion(), WORKSPACE, chain_rpc(),
                                             resume=resume)
                payload = {"multisig": result, "updated_at": chain.cached_multisig().get("updated_at")}
                if not NOTION_TOKEN:
                    payload["notice"] = "未配置 Notion Token：已按本地 project_wallets.json 清单检索；项目名与链上数据请以 Notion 项目列表为准"
                self.json(200, payload)
                return
            except chain.ChainError as exc:
                self.json(502, {"error": f"链上检索失败: {exc}"})
            return
        if path == "/api/knowledge":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            self.json(200, legacy.read_knowledge((query.get("path") or [""])[0]))
            return
        if path == "/api/topic":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            self.json(200, legacy.topic_text((query.get("url") or [""])[0]))
            return
        if path == "/api/audit":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            self.json(200, {"events": STORE.audit_events()})
            return
        if path == "/api/settings/forum":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            self.json(200, _forum_settings_payload())
            return
        if path == "/api/drafts":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            status = (query.get("status") or [None])[0]
            self.json(200, {"drafts": STORE.review_drafts(status or None)})
            return
        if path == "/api/drafts/item":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            try:
                draft = STORE.review_document(int((query.get("id") or ["0"])[0]))
            except ValueError:
                self.json(400, {"error": "无效的草稿编号"})
                return
            except KeyError:
                self.json(404, {"error": "草稿不存在"})
                return
            if query.get("proposal") == ["0"]:
                self.json(200, draft)
                return
            proposal, proposal_error = {}, ""
            try:
                proposal = _topic_text_cached(draft["topic_id"])
            except Exception as exc:
                proposal_error = str(exc)
            self.json(200, {**draft, "proposal": proposal, "proposal_error": proposal_error})
            return
        if path == "/api/transcribe/status":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            self.json(200, {"installed": _asr_installed(), "installing": _asr_installing,
                            "busy": _asr_busy, "log": _asr_log_tail(),
                            "diarization_installed": _diarization_installed(),
                            "diarization_installing": _diarization_installing,
                            "hf_token_configured": bool(_hf_token()),
                            "progress": _asr_progress()})
            return
        if path == "/api/meeting-items":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            self.json(200, legacy.meeting_items_get())
            return
        if path == "/api/knowledge/index":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            self.json(200, {"tags": legacy.knowledge_index()})
            return
        if path == "/api/knowledge/chunks":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            chunk_id = (query.get("id") or [""])[0]
            if chunk_id:
                self.json(200, legacy.knowledge_chunk_content(chunk_id))
                return
            tag = (query.get("tag") or [""])[0] or None
            self.json(200, {"chunks": legacy.knowledge_chunks(tag)})
            return
        if path == "/api/templates":
            if not self._session():
                self.json(401, {"error": "请先登录"})
                return
            self.json(200, {"templates": legacy.merged_templates()})
            return
        if path == "/" or path.startswith("/static/"):
            name = "index.html" if path == "/" else path.removeprefix("/static/")
            target = (STATIC / name).resolve()
            if STATIC.resolve() not in target.parents and target != STATIC.resolve():
                self.json(404, {"error": "not found"})
                return
            if not target.is_file():
                self.json(404, {"error": "not found"})
                return
            headers = {}
            if not TEAM_MODE:
                headers["Set-Cookie"] = f"spark_csrf={LOCAL_CSRF}; SameSite=Strict; Path=/"
            self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream", headers)
            return
        self.json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if any(path.startswith(prefix) for prefix in NOTION_PROXY_PREFIXES):
            # loadCachedPageChunkV2 等 Notion 前端 XHR：请求体原样转发，不做会话校验
            self._proxy_notion_prefix(parsed)
            return
        try:
            if path in {"/api/client/open", "/api/client/heartbeat", "/api/client/close"}:
                data = self.body_json()
                count = client_event(path.rsplit("/", 1)[-1], str(data.get("client_id", "")))
                self.json(200, {"ok": True, "active_pages": count})
                return
            if path == "/api/login":
                if not TEAM_MODE:
                    self.json(400, {"error": "本地模式无需登录"})
                    return
                secure = self._password_transport()
                if _login_throttled(self.client_address[0]):
                    self.json(429, {"error": "尝试过于频繁，请一分钟后再试"})
                    return
                data = self.body_json()
                uname = str(data.get("username", "")).strip()
                pwd = str(data.get("password", ""))
                # 团队模式与 Nervos Talk 共用一套账号：成员直接用 Talk 用户名+密码登录，
                # 首次登录自动开 viewer 账号；联络员预建的同名账号保留原角色并自动绑定。
                user = _resolve_login_user(uname, pwd)
                if not user:
                    raise PermissionError("登录未通过：仅委员会的四个 Talk 账号可以登录。请核对 Talk 用户名和密码；若论坛要求额外验证，当前直登方式不支持。")
                self._login_response(user["username"], secure=secure)
                return
            session = self.require_auth()
            self.require_csrf(session)
            if path == "/api/refresh":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能触发同步")
                self.json(200, refresh_board(session["username"]))
                return
            if path == "/api/timeline/refresh":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能刷新帖子缓存")
                board = tracker_module().fetch_nervos_talk_projects()
                board = legacy.apply_whitelist(board, legacy.load_data().get("board_whitelist", {}))
                legacy.save_cache(board)
                timeline = legacy.timeline(board)
                STORE.audit(session["username"], "timeline_refreshed", "nervos_talk",
                            {"posts": len(timeline), "errors": len(board.get("errors") or [])})
                self.json(200, {"board": board, "timeline": timeline,
                                "errors": board.get("errors") or []})
                return
            if path == "/api/tasks":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能添加任务")
                value = STORE.create_task(self.body_json())
                STORE.audit(session["username"], "task_created", str(value["id"]), value)
                self.json(200, value)
                return
            if path.startswith("/api/tasks/") and path.endswith("/update"):
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改任务")
                task_id = int(path.split("/")[3])
                value = STORE.update_task(task_id, self.body_json())
                STORE.audit(session["username"], "task_updated", str(task_id), value)
                self.json(200, value)
                return
            if path == "/api/legacy/section":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改工作台数据")
                data = self.body_json()
                self.json(200, legacy.replace_section(str(data.get("section") or ""), data.get("value")))
                return
            if path == "/api/templates/save":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能编辑模板")
                data = self.body_json()
                result = legacy.save_template(str(data.get("name") or ""), str(data.get("original") or ""),
                                              str(data.get("desc") or ""), str(data.get("template") or ""))
                STORE.audit(session["username"], "template_saved", result["name"],
                            {"original": str(data.get("original") or "")})
                self.json(200, result)
                return
            if path == "/api/project-tracking/refresh":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能重建项目跟踪数据")
                data = self.body_json()
                value = project_tracking.refresh_from_forum(
                    legacy.load_cache() or {}, scope=str(data.get("scope") or "active"))
                STORE.audit(session["username"], "project_tracking_refreshed", "",
                            {"projects": len(value.get("projects") or []),
                             "errors": len(value.get("refresh_errors") or [])})
                self.json(200, value)
                return
            if path == "/api/project-tracking/refresh-one":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能更新项目跟踪缓存")
                topic_id = str(self.body_json().get("topic_id") or "")
                if not topic_id.isdigit():
                    raise ValueError("Topic ID 必须是数字")
                value = project_tracking.refresh_from_forum(
                    legacy.load_cache() or {}, topic_ids={topic_id})
                STORE.audit(session["username"], "project_tracking_topic_refreshed", topic_id,
                            {"errors": len(value.get("refresh_errors") or [])})
                self.json(200, value)
                return
            if path == "/api/project-tracking/sync-funding":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能更新本地项目跟踪缓存")
                if not NOTION_TOKEN:
                    raise ValueError("未配置 NOTION_TOKEN，无法读取资金表")
                value = project_tracking.sync_funding(NotionClient(NOTION_TOKEN, WORKSPACE["api_version"]),
                                                      WORKSPACE, legacy.load_cache() or {})
                STORE.audit(session["username"], "project_tracking_funding_read", "notion",
                            {"updated": value.get("funding_updated", 0), "errors": len(value.get("funding_errors") or [])})
                self.json(200, value)
                return
            if path == "/api/project-tracking/update-weekday":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能编辑约定更新星期")
                data = self.body_json()
                value = project_tracking.set_update_weekday(
                    str(data.get("topic_id") or ""), int(data.get("index", -1)), str(data.get("weekday") or ""))
                STORE.audit(session["username"], "project_tracking_weekday_updated", str(data.get("topic_id") or ""),
                            {"index": data.get("index"), "weekday": data.get("weekday")})
                self.json(200, value)
                return
            if path == "/api/project-tracking/import-notion":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能导入旧表")
                value = project_tracking.import_notion_once(automation().notion, legacy.load_cache() or {})
                STORE.audit(session["username"], "project_tracking_imported", "notion",
                            {"imported": value.get("imported", 0),
                             "errors": value.get("import_errors") or []})
                self.json(200, value)
                return
            if path == "/api/project-tracking/import-text":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能导入旧表")
                value = project_tracking.import_text(str(self.body_json().get("text") or ""),
                                                     legacy.load_cache() or {})
                STORE.audit(session["username"], "project_tracking_imported", "text",
                            {"imported": value.get("imported", 0)})
                self.json(200, value)
                return
            if path == "/api/project-tracking/import-zip":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能导入旧表")
                value = project_tracking.import_notion_zip(
                    self.body_bytes(max_bytes=50_000_000),
                    legacy.load_cache() or {})
                STORE.audit(session["username"], "project_tracking_imported", "zip",
                            {"imported": value.get("imported", 0),
                             "csv_scanned": value.get("csv_scanned", 0)})
                self.json(200, value)
                return
            if path == "/api/tone":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改措辞知识")
                self.json(200, legacy.save_tone(str(self.body_json().get("value") or "")))
                return
            if path == "/api/knowledge":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改知识库")
                data = self.body_json()
                self.json(200, legacy.write_knowledge(str(data.get("path") or ""), str(data.get("content") or "")))
                return
            if path == "/api/meetings":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能新建会议")
                self.json(200, legacy.new_meeting(self.body_json()))
                return
            if path == "/api/board/manual":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能添加项目")
                data = self.body_json()
                value = legacy.add_manual_project(str(data.get("url") or ""), str(data.get("bucket") or "in_progress"))
                STORE.audit(session["username"], "board_project_added", str(value["id"]), {"bucket": data.get("bucket")})
                self.json(200, value)
                return
            if path.startswith("/api/meetings/") and path.endswith("/update"):
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改会议")
                meeting_id = path.split("/")[3]
                self.json(200, legacy.update_meeting(meeting_id, self.body_json()))
                return
            if path == "/api/ai/draft":
                data = self.body_json()
                plan = legacy.ai_draft_plan(data)
                env = load_env()
                provider = plan["provider"]
                api_key = env.get("SPARK_KIMI_API_KEY") if provider == "kimi" else env.get("SPARK_AI_API_KEY")
                tracker = tracker_module()
                long_running = plan["is_pre_review"] or "超时" in plan["template_name"] or "[zz]" in plan["template_name"]
                timeout = 300 if long_running else 120
                if plan["is_pre_review"]:
                    # 两阶段预审：阶段一分析（落盘备查）→ 阶段二按模板格式化
                    analysis, error = tracker.call_ai_api(provider, api_key, plan["stages"][0]["prompt"],
                                                          plan["model"], timeout=timeout)
                    if error:
                        raise RuntimeError(error)
                    analysis = analysis or ""
                    try:
                        (ROOT / "pre_review_analysis_last.md").write_text(analysis, encoding="utf-8")
                    except Exception:
                        pass
                    stage2 = legacy.pre_review_format_prompt(
                        plan["template_name"], plan["template_text"], analysis, plan["post"],
                        tone_knowledge=legacy.load_data().get("reply_tone_knowledge", ""),
                        supplement_content=str(data.get("supplement") or "").strip())
                    content, error = tracker.call_ai_api(provider, api_key, stage2, plan["model"], timeout=timeout)
                    if error or not content:
                        # 保底：阶段二失败时返回分析报告，便于人工使用
                        STORE.audit(session["username"], "ai_draft_generated",
                                    str(data.get("topic_url") or ""),
                                    {"template": data.get("template"), "provider": provider,
                                     "model": plan["model"], "fallback": "analysis"})
                        self.json(200, {"content": analysis, "prompt": plan["stages"][0]["prompt"],
                                        "analysis": analysis, "analysis_only": True})
                        return
                    STORE.audit(session["username"], "ai_draft_generated", str(data.get("topic_url") or ""),
                                {"template": data.get("template"), "provider": provider, "model": plan["model"]})
                    _cache_review_draft(plan, content, analysis)
                    self.json(200, {"content": content, "prompt": stage2, "analysis": analysis})
                    return
                content, error = tracker.call_ai_api(provider, api_key, plan["stages"][0]["prompt"],
                                                     plan["model"], timeout=timeout)
                if error:
                    raise RuntimeError(error)
                STORE.audit(session["username"], "ai_draft_generated", str(data.get("topic_url") or ""),
                            {"template": data.get("template"), "provider": provider, "model": plan["model"]})
                _cache_review_draft(plan, content)
                self.json(200, {"content": content, "prompt": plan["stages"][0]["prompt"]})
                return
            if path == "/api/ai/translate":
                data = self.body_json()
                text = str(data.get("text") or "").strip()
                if not text:
                    raise ValueError("没有可翻译内容")
                settings = legacy.ai_settings()
                env = load_env()
                api_key = env.get("SPARK_KIMI_API_KEY") if settings["provider"] == "kimi" else env.get("SPARK_AI_API_KEY")
                prompt = "将下列内容在中文和英文之间互译，保持 Markdown、链接、数字和专有名词；只输出译文：\n\n" + text
                content, error = tracker_module().call_ai_api(settings["provider"], api_key, prompt,
                                                               settings["model"], timeout=120)
                if error:
                    raise RuntimeError(error)
                self.json(200, {"content": content})
                return
            if path == "/api/ai/transcript-polish":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能使用 AI 整理")
                data = self.body_json()
                text = str(data.get("text") or "").strip()
                if not text:
                    raise ValueError("没有可整理的转写内容")
                glossary = str(data.get("glossary") or "").strip()
                settings = legacy.ai_settings()
                env = load_env()
                api_key = env.get("SPARK_KIMI_API_KEY") if settings["provider"] == "kimi" else env.get("SPARK_AI_API_KEY")
                prompt = (
                    "你是会议纪要整理助手。下面是会议语音转写的原始文本，其中可能有同音错别字与断句错误，"
                    "专有名词优先采用术语表中的写法。请整理成便于阅读的纪要底稿：\n"
                    "1. 按发言人分段：能根据对话上下文推断不同发言人时用「发言人A：」「发言人B：」这类标记区分，"
                    "发言回合轮换处必须换段；无法可靠区分时按语义段落组织。不要编造发言人姓名。\n"
                    "2. 修正明显的语音识别错误，保持原意，不得添加原文中没有的事实、结论或承诺。\n"
                    "3. 精简口语填充词（嗯、那个、就是说之类），保持口语自然、语句连贯。\n"
                    "4. 每个段落开头保留原文的时间戳标记（如【03:12】），便于回查录音。\n"
                    "5. 直接输出整理后的文本，不要输出任何解释或前后缀。\n\n"
                    f"术语表：\n{glossary or '（无）'}\n\n原始转写：\n{text[:50000]}")
                content, error = tracker_module().call_ai_api(settings["provider"], api_key, prompt,
                                                                settings["model"], timeout=300)
                if error:
                    raise RuntimeError(error)
                STORE.audit(session["username"], "transcript_polished", "",
                            {"provider": settings["provider"], "model": settings["model"],
                             "chars": len(text)})
                self.json(200, {"content": content})
                return
            if path == "/api/ai/summarize":
                data = self.body_json()
                transcript = str(data.get("transcript") or "").strip()
                resolutions = data.get("resolutions") or []
                if not transcript and not resolutions:
                    raise ValueError("请先填写会议转写或决议")
                settings = legacy.ai_settings()
                env = load_env()
                api_key = env.get("SPARK_KIMI_API_KEY") if settings["provider"] == "kimi" else env.get("SPARK_AI_API_KEY")
                prompt = ("请将以下委员会会议整理为简洁、可执行的 Markdown 纪要，包含讨论摘要、决定、负责人和后续行动；"
                          "不得编造缺失事实。\n\n决议：\n" + json.dumps(resolutions, ensure_ascii=False, indent=2) +
                          "\n\n会议转写：\n" + transcript)
                content, error = tracker_module().call_ai_api(settings["provider"], api_key, prompt,
                                                               settings["model"], timeout=120)
                if error:
                    raise RuntimeError(error)
                self.json(200, {"content": content})
                return
            if path == "/api/settings/ai":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改设置")
                data = self.body_json()
                provider = str(data.get("provider") or "siliconflow")
                model = str(data.get("model") or "")
                legacy.save_ai_preferences(provider, model)
                key = str(data.get("api_key") or "").strip()
                if key:
                    from .setup import write_env
                    env = load_env()
                    env["SPARK_KIMI_API_KEY" if provider == "kimi" else "SPARK_AI_API_KEY"] = key
                    write_env(env)
                self.json(200, legacy.ai_settings())
                return
            if path == "/api/settings/local":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改运行开关")
                global AUTO_NOTION
                data = self.body_json()
                from .setup import write_env
                env = load_env()
                changed = []
                restart_required = False
                if "auto_notion" in data:
                    env["SPARK_NOTION_AUTO_EXECUTE"] = "1" if data.get("auto_notion") else "0"
                    AUTO_NOTION = bool(data.get("auto_notion"))
                    changed.append("auto_notion")
                if "team_mode" in data:
                    new_mode = bool(data.get("team_mode"))
                    if new_mode != TEAM_MODE:
                        restart_required = True
                    env["SPARK_TEAM_MODE"] = "1" if new_mode else "0"
                    changed.append("team_mode")
                write_env(env)
                STORE.audit(session["username"], "local_settings_updated", "", {"changed": changed})
                self.json(200, {"ok": True, "changed": changed, "restart_required": restart_required,
                                "auto_notion": AUTO_NOTION})
                return
            if path == "/api/settings/forum":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改设置")
                data = self.body_json()
                username = str(data.get("talk_username") or "").strip()
                if str(data.get("talk_password") or "").strip():
                    raise ValueError("工作台不保存论坛密码；登录或发布时可单次输入")
                if data.get("talk_api_key"):
                    raise ValueError("Nervos Talk 发布不使用个人 API Key")
                current = legacy.forum_binding()
                if username and username != current:
                    if TEAM_MODE:
                        raise PermissionError("团队模式由管理员为工作台账号绑定论坛身份")
                    legacy.set_forum_binding(username)
                hf_token = str(data.get("hf_token") or "").strip()
                if hf_token:
                    from .setup import write_env
                    env = load_env()
                    env["SPARK_HF_TOKEN"] = hf_token
                    write_env(env)
                STORE.audit(session["username"], "forum_settings_updated", "",
                            {"talk_username": legacy.forum_binding()})
                self.json(200, {"ok": True, **_forum_settings_payload()})
                return
            if path == "/api/drafts":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能保存草稿")
                data = self.body_json()
                topic_url = str(data.get("topic_url") or "").strip()
                topic_id = legacy.topic_id_from_url(topic_url) if topic_url else str(data.get("topic_id") or "").strip()
                content = str(data.get("content") or "").strip()
                if not topic_id or not content:
                    raise ValueError("缺少帖子链接或草稿内容")
                draft_id = STORE.save_review_draft(
                    topic_id, str(data.get("title") or ""),
                    topic_url or f"https://talk.nervos.org/t/{topic_id}",
                    str(data.get("template") or ""), content, str(data.get("analysis") or ""))
                STORE.audit(session["username"], "review_draft_saved", topic_id, {"draft_id": draft_id})
                self.json(200, {"ok": True, "id": draft_id})
                return
            if path in {"/api/drafts/edit", "/api/drafts/comment"}:
                # A signed-in committee reviewer can edit and annotate drafts, not publish.
                data = self.body_json()
                draft_id, revision = data.get("id"), data.get("revision")
                if type(draft_id) is not int or not isinstance(revision, str):
                    raise ValueError("缺少有效的草稿编号或版本")
                if path == "/api/drafts/edit":
                    content = data.get("content")
                    if not isinstance(content, str):
                        raise ValueError("草稿正文必须为文本")
                    result = STORE.edit_review_draft(draft_id, revision, content)
                    action = "review_draft_edited"
                else:
                    start, end, body = data.get("start"), data.get("end"), data.get("body")
                    if type(start) is not int or type(end) is not int or not isinstance(body, str):
                        raise ValueError("请选中正文并填写批注")
                    result = STORE.comment_on_review_draft(draft_id, revision, start, end, body, session["username"])
                    action = "review_draft_commented"
                STORE.audit(session["username"], action, str(draft_id), {})
                self.json(200, result)
                return
            if path == "/api/drafts/reviewed":
                # 委员会成员（viewer）也可标记已阅
                data = self.body_json()
                draft_id = int(data.get("id"))
                if not STORE.review_draft(draft_id):
                    raise ValueError("草稿不存在")
                STORE.mark_review_draft(draft_id, session["username"], revision=data.get("revision"))
                STORE.audit(session["username"], "review_draft_reviewed", str(draft_id), {})
                self.json(200, {"ok": True})
                return
            if path == "/api/transcribe/install":
                global _asr_installing, _diarization_installing
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能安装组件")
                data = self.body_json()
                if data.get("component") == "diarization":
                    if _diarization_installed():
                        self.json(200, {"ok": True, "installed": True})
                        return
                    if _diarization_installing:
                        self.json(200, {"ok": True, "installing": True})
                        return
                    threading.Thread(target=_install_diarization, daemon=True).start()
                    self.json(200, {"ok": True, "started": True})
                    return
                if _asr_installed():
                    self.json(200, {"ok": True, "installed": True})
                    return
                if _asr_installing:
                    self.json(200, {"ok": True, "installing": True})
                    return
                threading.Thread(target=_install_asr, daemon=True).start()
                self.json(200, {"ok": True, "started": True})
                return
            if path == "/api/transcribe":
                global _asr_busy, _asr_progress_file
                if not _asr_installed():
                    raise RuntimeError("本地转写环境未安装，请在会议纪要页点击「安装本地转写」")
                if _asr_busy:
                    raise RuntimeError("已有转写任务进行中，请稍候")
                query = urllib.parse.parse_qs(parsed.query)
                filename = os.path.basename((query.get("filename") or ["recording.webm"])[0])
                model = (query.get("model") or ["small"])[0]
                if model not in {"tiny", "base", "small", "medium", "large-v3"}:
                    raise ValueError("无效模型")
                raw = self.body_bytes(300_000_000)
                uploads = ROOT / "runtime_uploads"
                uploads.mkdir(exist_ok=True)
                suffix = os.path.splitext(filename)[1] or ".webm"
                tmp = uploads / f"upload_{secrets.token_hex(8)}{suffix}"
                tmp.write_bytes(raw)
                glossary_path = None
                glossary = (query.get("glossary") or [""])[0].strip()
                if glossary:
                    glossary_path = uploads / f"upload_{secrets.token_hex(8)}.txt"
                    glossary_path.write_text(glossary, encoding="utf-8")
                diarize = (query.get("diarize") or [""])[0] in {"1", "true", "yes"}
                script = TRANSCRIBE_SCRIPT
                env = _asr_env()
                extra_args: list[str] = []
                if diarize:
                    if not _diarization_installed():
                        raise RuntimeError("说话人分离组件未安装，请在会议纪要页点击「安装说话人分离」")
                    token = _hf_token()
                    if not token:
                        raise RuntimeError("未配置 Hugging Face Token（设置页「语音转写」区块填写）")
                    script = DIARIZE_SCRIPT
                    env = _diarize_env()
                    _asr_progress_file = uploads / f"asr_progress_{secrets.token_hex(6)}.txt"
                    extra_args = ["--token", token, "--progress-file", str(_asr_progress_file)]
                STORE.audit(session["username"], "transcribe_started", "",
                            {"model": model, "bytes": len(raw), "diarize": diarize})
                _asr_busy = True
                try:
                    import subprocess
                    command = [str(ASR_PYTHON), str(script), "--audio", str(tmp),
                               "--model", model, "--language", "zh"]
                    if glossary_path:
                        command += ["--glossary-file", str(glossary_path)]
                    command += extra_args
                    result = subprocess.run(command, capture_output=True, text=True,
                                            timeout=7200, env=env, encoding="utf-8",
                                            errors="replace", creationflags=_no_window_flags())
                    if result.returncode != 0:
                        err_tail = (result.stderr or result.stdout)[-800:]
                        if "401" in err_tail or "gated" in err_tail.lower():
                            raise RuntimeError("Hugging Face 模型授权失败：请在 huggingface.co 登录后接受 "
                                               "pyannote/segmentation-3.0、pyannote/speaker-diarization-3.1 "
                                               "与 pyannote/speaker-diarization-community-1 的模型协议，"
                                               "并确认 Token 有效")
                        raise RuntimeError(f"转写失败: {err_tail}")
                    value = None
                    for line in reversed((result.stdout or "").splitlines()):
                        line = line.strip()
                        if line.startswith("{"):
                            try:
                                value = json.loads(line)
                            except ValueError:
                                value = None
                            if value is not None:
                                break
                    if value is None:
                        raise RuntimeError(f"转写输出解析失败，原始输出末尾: {(result.stdout or '')[-300:]}")
                    STORE.audit(session["username"], "transcribed", "",
                                {"model": model, "seconds": value.get("duration"),
                                 "diarized": bool(value.get("diarized"))})
                    self.json(200, value)
                finally:
                    _asr_busy = False
                    for item in (tmp, glossary_path, _asr_progress_file):
                        if item:
                            try:
                                item.unlink()
                            except OSError:
                                pass
                    _asr_progress_file = None
                return
            if path == "/api/notion/test":
                self.json(200, {"user": automation().notion.me()})
                return
            if path == "/api/notion/reconcile":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能触发对账")
                self.json(200, automation().reconcile_notion(session["username"]))
                return
            if path == "/api/notion/new-project":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能执行 Notion 写入")
                payload = enrich_notion_payload("new-project", self.body_json())
                self.json(200, automation().execute_new_project(payload, session["username"]))
                return
            if path == "/api/notion/weekly":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能执行 Notion 写入")
                payload = enrich_notion_payload("weekly", self.body_json())
                self.json(200, automation().execute_weekly_update(payload, session["username"]))
                return
            if path == "/api/notion/completion":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能执行 Notion 写入")
                payload = enrich_notion_payload("completion", self.body_json())
                self.json(200, automation().execute_completion(payload, session["username"]))
                return
            if path == "/api/notion/preview-edit":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能执行 Notion 写入")
                if not NOTION_TOKEN:
                    self.json(400, {"error": "未配置 NOTION_TOKEN，无法写回 Notion"})
                    return
                data = self.body_json()
                key = str(data.get("key") or "")
                row_id = str(data.get("row_id") or "")
                column_name = str(data.get("column") or "")
                value = data.get("value")
                if not key or not row_id or not column_name or not isinstance(value, str):
                    self.json(400, {"error": "缺少必填字段: key, row_id, column, value"})
                    return
                preview_cfg = ROOT / "notion_preview.json"
                if not preview_cfg.exists():
                    self.json(404, {"error": "未配置预览页面"})
                    return
                try:
                    config = json.loads(preview_cfg.read_text(encoding="utf-8"))
                    # 列定义以 Notion 实际 schema 为准（防止前端篡改类型）
                    parents = []
                    for parent in config.get("parents") or []:
                        wanted = [{"key": t["key"], "hint": t.get("hint") or ""}
                                  for t in (config.get("tabs") or [])
                                  if t.get("key") in (parent.get("tables") or [])]
                        parents.append({"page_id": parent.get("page_id"), "tables": wanted})
                    tables = notion_preview.discover_tables(parents)
                    info = tables.get(key)
                    if not info:
                        self.json(404, {"error": f"未配置的表 key：{key}"})
                        return
                    schema = notion_preview._sync_schema(info["space_id"], info["collection_id"])
                    column = next(({"name": (v.get("name") or k), "type": v.get("type") or ""}
                                   for k, v in schema.items()
                                   if (v.get("name") or k) == column_name), None)
                    if not column:
                        self.json(404, {"error": f"列不存在：{column_name}"})
                        return
                    notion_preview.edit_cell(automation().notion, row_id, column, value)
                    notion_preview._table_cache.pop(key, None)  # 仅作废该表缓存
                except notion_preview.PreviewError as exc:
                    self.json(400, {"error": str(exc)})
                    return
                except NotionError as exc:
                    self.json(502, {"error": f"Notion 写入失败: {exc}"})
                    return
                STORE.audit(session["username"], "notion_preview_edit", row_id,
                            {"table": key, "column": column_name, "value": value[:200]})
                self.json(200, {"ok": True})
                return
            if path == "/api/chain/apply-main":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能执行 Notion 写入")
                data = self.body_json()
                only = data.get("only")
                records = chain.main_records_for_apply(chain_rpc())
                result = chain.apply_main(automation().notion, WORKSPACE, records,
                                          only=[str(x) for x in only] if only else None)
                STORE.audit(session["username"], "chain_apply_main", "",
                            {"created": result["created"], "updated": result["updated"],
                             "preserved": result["preserved"],
                             "errors": result["errors"]})
                self.json(200, result)
                return
            if path == "/api/chain/apply-multisig":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能执行 Notion 写入")
                rpc = chain_rpc()
                results = chain.scan_multisig(automation().notion, WORKSPACE, rpc)
                result = chain.apply_multisig(automation().notion, WORKSPACE, rpc,
                                              results=results)
                STORE.audit(session["username"], "chain_apply_multisig", "",
                            {"written": len(result["written"]), "errors": result["errors"]})
                self.json(200, result)
                return
            if path == "/api/jobs/dismiss":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改同步作业")
                data = self.body_json()
                key = str(data.get("key") or "")
                rows = STORE.query("SELECT idempotency_key,status FROM sync_jobs WHERE idempotency_key=?", (key,))
                if not rows:
                    self.json(404, {"error": "作业不存在"})
                    return
                STORE.set_job_status(key, "ignored", "用户手动忽略")
                STORE.audit(session["username"], "job_dismissed", key, {"previous_status": rows[0]["status"]})
                self.json(200, {"ok": True, "key": key})
                return
            if path.startswith("/api/tasks/") and path.endswith("/toggle"):
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改任务")
                task_id = int(path.split("/")[3])
                tasks = STORE.query("SELECT * FROM tasks WHERE id=?", (task_id,))
                if not tasks:
                    self.json(404, {"error": "task not found"})
                    return
                task = tasks[0]
                if task["verification_gated"]:
                    raise PermissionError("该任务只能在 Notion 回读验证成功后自动完成")
                with STORE.transaction() as conn:
                    conn.execute("UPDATE tasks SET done=?,completed_at=? WHERE id=?",
                                 (0 if task["done"] else 1, None if task["done"] else utcnow(), task_id))
                STORE.audit(session["username"], "task_toggled", str(task_id), {"done": not bool(task["done"])})
                self.json(200, {"ok": True})
                return
            if path == "/api/forum/post":
                if session["role"] not in {"operator", "approver", "administrator"}:
                    raise PermissionError("当前角色无发帖权限")
                self._password_transport()
                data = self.body_json()
                if data.get("confirmation") != "正式发布":
                    raise PermissionError("必须输入“正式发布”进行确认")
                forum_username = session.get("forum_username")
                if not forum_username:
                    raise PermissionError("当前工作台账号未绑定自己的 Nervos Talk 用户名")
                topic_id, raw, password = str(data.get("topic_id") or ""), data.get("raw"), data.get("password")
                if not re.fullmatch(r"[1-9][0-9]{0,17}", topic_id) or not isinstance(raw, str) or not raw.strip():
                    raise ValueError("请提供有效的 Nervos Talk 帖子和非空草稿")
                if not isinstance(password, str) or not password:
                    raise ValueError("请单次输入绑定账号的 Talk 密码")
                response = post_with_password(topic_id, raw, forum_username, password)
                STORE.audit(session["username"], "forum_posted", topic_id,
                            {"forum_username": forum_username, "post_id": response.get("id")})
                self.json(200, {"post_id": response.get("id"), "post_number": response.get("post_number")})
                return
            if path == "/api/tasks/batch":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能批量操作任务")
                data = self.body_json()
                ids = [int(x) for x in (data.get("ids") or [])]
                action = str(data.get("action") or "")
                if action == "complete":
                    completed, skipped = [], []
                    for task_id in ids:
                        rows = STORE.query("SELECT * FROM tasks WHERE id=?", (task_id,))
                        if not rows or rows[0]["verification_gated"]:
                            skipped.append(task_id)
                            continue
                        with STORE.transaction() as conn:
                            conn.execute("UPDATE tasks SET done=1, completed_at=? WHERE id=?",
                                         (utcnow(), task_id))
                        completed.append(task_id)
                    STORE.audit(session["username"], "tasks_batch_completed", "", {"ids": completed})
                    self.json(200, {"completed": completed, "skipped": skipped})
                    return
                if action == "delete":
                    deleted, skipped = [], []
                    for task_id in ids:
                        try:
                            STORE.delete_task(task_id)
                            deleted.append(task_id)
                        except Exception:
                            skipped.append(task_id)
                    STORE.audit(session["username"], "tasks_batch_deleted", "", {"ids": deleted})
                    self.json(200, {"deleted": deleted, "skipped": skipped})
                    return
                raise ValueError("不支持的批量操作")
            if path == "/api/tasks/open-link":
                data = self.body_json()
                task_id = int(data.get("id"))
                rows = STORE.query("SELECT * FROM tasks WHERE id=?", (task_id,))
                if not rows:
                    self.json(404, {"error": "task not found"})
                    return
                task = rows[0]
                url = (task.get("link") or "").strip()
                if not url:
                    match = re.search(r"https?://\S+", task.get("note") or "")
                    url = match.group(0) if match else ""
                if not url:
                    raise ValueError("该任务没有可打开的链接")
                if task.get("type") == "删除标签":
                    self.json(200, open_link_privately(url))
                else:
                    webbrowser.open(url)
                    self.json(200, {"ok": True, "mode": "default"})
                return
            if path == "/api/board/whitelist":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能修改白名单")
                data = self.body_json()
                ids = [str(x) for x in (data.get("ids") or [])]
                target = str(data.get("target") or "")
                value = legacy.add_whitelist(ids, target)
                if target == "in_progress":
                    board = legacy.load_cache() or {}
                    all_posts: dict[str, dict] = {}
                    for posts in (board.get("all_posts") or {}).values():
                        for post in posts:
                            all_posts[str(post.get("id"))] = post
                    for posts in ((board.get("classified") or {}).get("spark_program") or {}).values():
                        if isinstance(posts, list):
                            for post in posts:
                                all_posts[str(post.get("id"))] = post
                    for topic_id in ids:
                        post = all_posts.get(topic_id)
                        if post:
                            STORE.create_workflow_tasks(topic_id, post.get("title", ""), quick_links())
                STORE.audit(session["username"], "board_whitelist_updated", target, {"ids": ids})
                self.json(200, value)
                return
            if path == "/api/meeting-items/refresh":
                self.json(200, legacy.meeting_items_refresh())
                return
            if path == "/api/meeting-items/summarize":
                self.json(200, legacy.meeting_items_summarize())
                return
            if path == "/api/meetings/parse":
                data = self.body_json()
                text = str(data.get("text") or "")
                mode = str(data.get("mode") or "regex")
                if not text.strip():
                    raise ValueError("请输入内容")
                if mode == "ai":
                    provider, api_key, _model = legacy.ai_credentials()
                    if not api_key:
                        raise RuntimeError("未配置 AI API Key，请先在设置中配置")
                    prompt = (
                        "请从以下会议记录中提取结构化信息，返回严格JSON格式：\n"
                        "{\"date\":\"日期\",\"title\":\"会议标题\",\"resolutions\":["
                        "{\"project\":\"项目名称\",\"decision\":\"通过/拒绝/待定/关闭/完成\",\"budget\":\"预算金额\",\"note\":\"备注\"}"
                        "]}\n\n"
                        f"会议记录：\n{text[:8000]}\n\n"
                        "请只返回JSON，不要解释。"
                    )
                    content, error = tracker_module().call_ai_api(provider, api_key, prompt,
                                                                  "deepseek-ai/DeepSeek-V4-Flash", timeout=120)
                    if error:
                        raise RuntimeError(error)
                    match = re.search(r"\{.*\}", content or "", re.DOTALL)
                    if not match:
                        raise RuntimeError("AI 返回中未找到 JSON")
                    parsed = json.loads(match.group())
                    for res in parsed.get("resolutions", []):
                        res["decision"] = legacy.standardize_decision(str(res.get("decision") or ""))
                    self.json(200, {"parsed": parsed})
                    return
                self.json(200, {"parsed": legacy.parse_meeting_text(text)})
                return
            if path.startswith("/api/meetings/") and path.endswith("/link-talk"):
                meeting_id = path.split("/")[3]
                data = self.body_json()
                self.json(200, legacy.link_talk(meeting_id, int(data.get("index")), str(data.get("url") or "")))
                return
            if path.startswith("/api/meetings/") and path.endswith("/refresh-posts"):
                meeting_id = path.split("/")[3]
                self.json(200, legacy.refresh_resolution_posts(meeting_id))
                return
            if path == "/api/characters/reset":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能重置人物角色")
                self.json(200, legacy.reset_characters())
                return
            if path == "/api/tone/generate":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能生成措辞知识")
                self.json(200, {"content": legacy.generate_tone_knowledge()})
                return
            if path == "/api/knowledge/rebuild":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能重建知识库索引")
                import subprocess
                import sys
                script = ROOT / "build_knowledge_index.py"
                if not script.is_file():
                    raise FileNotFoundError("未找到 build_knowledge_index.py")
                proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                                      capture_output=True, text=True, timeout=300)
                self.json(200, {"returncode": proc.returncode,
                                "stdout": (proc.stdout or "")[-2000:], "stderr": (proc.stderr or "")[-2000:]})
                return
            self.json(404, {"error": "not found"})
        except chain.ChainError as exc:
            self.json(502, {"error": f"链上数据访问失败: {exc}"})
        except PermissionError as exc:
            self.json(403, {"error": str(exc)})
        except ReviewConflict as exc:
            self.json(409, {"error": str(exc)})
        except (ValueError, WorkflowInputError) as exc:
            self.json(400, {"error": str(exc)})
        except (KeyError, FileNotFoundError) as exc:
            self.json(404, {"error": str(exc)})
        except NotionConflict as exc:
            self.json(409, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self.json(500, {"error": str(exc)})

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            session = self.require_auth()
            self.require_csrf(session)
            if session["role"] == "viewer":
                raise PermissionError("只读用户不能删除数据")
            if path.startswith("/api/tasks/"):
                task_id = int(path.split("/")[3])
                STORE.delete_task(task_id)
                STORE.audit(session["username"], "task_deleted", str(task_id), {})
                self.json(200, {"ok": True})
                return
            if path.startswith("/api/drafts/"):
                draft_id = int(path.split("/")[3])
                STORE.delete_review_draft(draft_id)
                STORE.audit(session["username"], "review_draft_deleted", str(draft_id), {})
                self.json(200, {"ok": True})
                return
            if path.startswith("/api/meetings/"):
                meeting_id = path.split("/")[3]
                self.json(200, legacy.delete_meeting(meeting_id))
                return
            self.json(404, {"error": "not found"})
        except PermissionError as exc:
            self.json(403, {"error": str(exc)})
        except (ValueError, KeyError) as exc:
            self.json(400, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self.json(500, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Spark Program browser workbench")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--runtime-file", type=Path)
    parser.add_argument("--auto-shutdown", action="store_true")
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer((HOST, args.port), Handler)
    except OSError as exc:
        if exc.errno not in {errno.EADDRINUSE, 10048}:
            raise
        print(f"Port {args.port} is already in use; selecting a free local port.", flush=True)
        server = ThreadingHTTPServer((HOST, 0), Handler)
    actual_port = int(server.server_address[1])
    url = f"http://{DISPLAY_HOST}:{actual_port}/"
    runtime_path = args.runtime_file.resolve() if args.runtime_file else None
    if runtime_path:
        temp = runtime_path.with_suffix(runtime_path.suffix + ".tmp")
        temp.write_text(
            f"version={BUILD_VERSION}\nnonce={LAUNCH_NONCE}\nurl={url}\n"
            f"team_mode={1 if TEAM_MODE else 0}\n",
            encoding="utf-8",
        )
        os.replace(temp, runtime_path)
    print(f"Spark Program Workbench: {url}")
    if args.auto_shutdown:
        threading.Thread(target=auto_shutdown_monitor, args=(server,), daemon=True).start()
    threading.Thread(target=scheduled_refresh_loop, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if runtime_path:
            try:
                runtime_path.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
