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

from .config import AUTO_NOTION, BUILD_VERSION, HOST, NOTION_TOKEN, PORT, ROOT, TEAM_MODE, load_workspace_config, load_env
from . import chain
from .forum import (ai_json, extract_completion, extract_new_project, extract_weekly_update,
                    fetch_topic, normalized_posts, post_as_user)
from . import legacy
from .notion import NotionClient, NotionConflict
from .storage import Store, utcnow
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
NOTION_PROXY_TIMEOUT = 20
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


def rewrite_notion_html(body: bytes) -> bytes:
    """把 Notion HTML 中的相对资源引用改写成 notion.site 绝对地址。

    只替换紧跟 /_assets、/_next、/_notion、/images 的引用，已含域名的绝对地址不受影响。
    """
    text = body.decode("utf-8", errors="replace")
    for needle in _PROXY_ATTR_NEEDLES:
        for prefix in _PROXY_ASSET_PREFIXES:
            text = text.replace(needle + prefix, needle + NOTION_UPSTREAM_BASE + prefix)
    return text.encode("utf-8")


def fetch_notion(target: str):
    """GET 上游固定地址；target 含 path 与 query string。调用方处理 HTTPError。"""
    upstream = f"{NOTION_UPSTREAM_BASE}/{target}"
    request = urllib.request.Request(upstream, method="GET", headers={
        "User-Agent": NOTION_PROXY_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "identity",  # 不请求压缩，省去解压，浏览器拿到的都是明文
    })
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
    result = {"new_projects": [], "weekly_updates": [], "transitions": [], "errors": []}
    try:
        board = tracker_module().fetch_nervos_talk_projects()
        board = legacy.apply_whitelist(board, legacy.load_data().get("board_whitelist", {}))
        legacy.save_cache(board)
        if board.get("errors"):
            result["errors"].extend(board["errors"])
        classified = board.get("classified") or {}

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
        if not seeded:
            preexisting = {row["topic_id"] for row in STORE.query("SELECT topic_id FROM projects")}
            for bucket, lifecycle in states.items():
                for post in classified.get(bucket, []):
                    topic_id = str(post["id"])
                    if topic_id not in preexisting:
                        STORE.upsert_project(topic_id, post.get("title", ""), post.get("url", ""),
                                             lifecycle, int(post.get("posts_count") or 0))
            STORE.set_meta("board_seeded_v1", utcnow())
            STORE.audit(actor, "board_seeded", "nervos_talk", {"projects": len(STORE.query('SELECT * FROM projects'))})
            result["seeded"] = True

        in_progress_posts = {str(p["id"]): p for p in classified.get("in_progress", [])}
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
                    "forum_username": None}
        token = self._cookies().get("spark_session")
        if not token:
            return None
        token_hash = hashlib.sha256(token.value.encode()).hexdigest()
        rows = STORE.query("""SELECT s.username,s.csrf_token,s.expires_at,u.role,u.forum_username,u.enabled
            FROM sessions s JOIN users u USING(username) WHERE s.token_hash=?""", (token_hash,))
        return rows[0] if rows and rows[0]["enabled"] and rows[0]["expires_at"] > utcnow() else None

    def _actor(self) -> str:
        session = self._session()
        return session["username"] if session else "anonymous"

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

    def _proxy_notion(self, parsed) -> None:
        target = parsed.path.removeprefix("/notion-view/")
        if not target or target.startswith("/notion-view"):
            self.json(404, {"error": "not found"})
            return
        if parsed.query:
            target = f"{target}?{parsed.query}"
        try:
            try:
                resp = fetch_notion(target)
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

    def body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 2_000_000:
            raise ValueError("请求体过大")
        return payload_from_json(self.rfile.read(length) if length else b"{}")

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
            value["session"] = {"username": session["username"], "role": session["role"],
                                "csrf_token": session["csrf_token"]}
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
        if path == "/notion-view" or path.startswith("/notion-view/"):
            # 本地反向代理公开 Notion 页面（GET-only、上游固定、不含业务数据），
            # iframe 内联加载不走会话校验，剥帧限制头后可直接嵌入。
            self._proxy_notion(parsed)
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
                result = chain.scan_main(automation().notion, WORKSPACE, chain_rpc(),
                                         limit=limit, resume=resume)
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
                result = chain.scan_multisig(automation().notion, WORKSPACE, chain_rpc(),
                                             resume=resume)
                self.json(200, {"multisig": result, "updated_at": chain.cached_multisig().get("updated_at")})
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
        path = urllib.parse.urlparse(self.path).path
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
                data = self.body_json()
                rows = STORE.query("SELECT * FROM users WHERE username=? AND enabled=1", (data.get("username", ""),))
                if not rows or not STORE.verify_password(data.get("password", ""), rows[0]["password_hash"]):
                    raise PermissionError("用户名或密码错误")
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)).isoformat()
                with STORE.transaction() as conn:
                    conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                                 (token_hash, rows[0]["username"], csrf, expires, utcnow()))
                body = json.dumps({"ok": True, "csrf_token": csrf}).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8",
                           {"Set-Cookie": f"spark_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"})
                return
            session = self.require_auth()
            self.require_csrf(session)
            if path == "/api/refresh":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能触发同步")
                self.json(200, refresh_board(session["username"]))
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
                    self.json(200, {"content": content, "prompt": stage2, "analysis": analysis})
                    return
                content, error = tracker.call_ai_api(provider, api_key, plan["stages"][0]["prompt"],
                                                     plan["model"], timeout=timeout)
                if error:
                    raise RuntimeError(error)
                STORE.audit(session["username"], "ai_draft_generated", str(data.get("topic_url") or ""),
                            {"template": data.get("template"), "provider": provider, "model": plan["model"]})
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
            if path == "/api/chain/apply-main":
                if session["role"] == "viewer":
                    raise PermissionError("只读用户不能执行 Notion 写入")
                data = self.body_json()
                only = data.get("only")
                records = chain.fetch_main_wallet_records(chain_rpc())
                result = chain.apply_main(automation().notion, WORKSPACE, records,
                                          only=[str(x) for x in only] if only else None)
                STORE.audit(session["username"], "chain_apply_main", "",
                            {"created": result["created"], "updated": result["updated"],
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
                data = self.body_json()
                if data.get("confirmation") != "正式发布":
                    raise PermissionError("必须输入“正式发布”进行确认")
                forum_username = session.get("forum_username")
                if not forum_username:
                    raise PermissionError("当前工作台账号未绑定自己的 Nervos Talk 用户名")
                response = post_as_user(str(data.get("topic_id", "")), data.get("raw", ""), forum_username)
                STORE.audit(session["username"], "forum_posted", str(data.get("topic_id")),
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
    url = f"http://{HOST}:{actual_port}/"
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
