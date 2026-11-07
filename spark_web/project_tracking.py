from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import threading
import zipfile
from functools import wraps
from html import unescape
from typing import Any, Callable

from .config import PROJECT_TRACKING_PATH
from .forum import clean_html, fetch_topic
from .notion import property_value


LEGACY_DATABASE_IDS = (
    "2a7639bc0d1b8007b612f1caef0a4a78",
    "1da24205dae080799ba1c11378bb1bf7",
)
COMMITTEE_USERS = {"xingtianchunyan", "zz_tovarishch", "yixiu.ckbfans.bit", "hanssen"}
BUCKET_STATUS = {"pre_review": "Pre-review", "submitted": "Submitted", "pending": "Pending",
                 "in_progress": "In-Progress", "rejection": "Rejection",
                 "completion": "Completion", "closure": "Closure"}
RELATED_STATUS = "申请人有话说"
_TRACKING_LOCK = threading.RLock()


def _serial(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _TRACKING_LOCK:
            return fn(*args, **kwargs)
    return wrapped


def load() -> dict:
    try:
        value = json.loads(PROJECT_TRACKING_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.setdefault("projects", [])
            return value
    except (OSError, ValueError):
        pass
    return {"updated_at": None, "projects": [], "import_errors": []}


def save(value: dict) -> None:
    temp = PROJECT_TRACKING_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(PROJECT_TRACKING_PATH)


def _related_whitelist_status(post: dict, target: str) -> str:
    if target not in BUCKET_STATUS:
        return ""
    author = str(post.get("original_poster_username") or post.get("username") or post.get("author") or "").strip().lower()
    if not author or author in COMMITTEE_USERS:
        return ""
    tags = {str(tag).strip().lower().replace("_", "-") for tag in post.get("tags") or []}
    lifecycle = {name.replace("_", "-") for name in BUCKET_STATUS}
    if tags & lifecycle or "in-progress" in tags:
        return ""
    return BUCKET_STATUS[target]


def _board_posts(board: dict) -> tuple[dict[str, dict], dict[str, str]]:
    from .legacy import _all_cached_posts, load_data
    posts = {str(post.get("id")): dict(post) for post in _all_cached_posts(board)
             if post.get("id") is not None}
    status: dict[str, str] = {}
    classified = board.get("classified") or {}
    for bucket in ("pre_review", "submitted", "pending", "in_progress",
                   "rejection", "completion", "closure"):
        items = ((classified.get("spark_program") or {}).get("pre_review") or []) \
            if bucket == "pre_review" else (classified.get(bucket) or [])
        for post in items:
            status[str(post.get("id"))] = BUCKET_STATUS[bucket]
    whitelist = board.get("board_whitelist")
    if whitelist is None:
        whitelist = load_data().get("board_whitelist", {})
    for topic_id, target in (whitelist or {}).items():
        post = posts.get(str(topic_id))
        if post and _related_whitelist_status(post, str(target)):
            status[str(topic_id)] = RELATED_STATUS
    return posts, status


def _date(value: Any) -> str:
    return str(value or "")[:10]


def _duration_days(text: str) -> int | None:
    patterns = ((r"(?:duration|timeline|period)\s*[:：-]?\s*(\d+)\s*weeks?", 7),
                (r"(?:duration|timeline|period)\s*[:：-]?\s*(\d+)\s*months?", 30),
                (r"(?:周期|工期|时长)\s*[:：-]?\s*(\d+)\s*周", 7),
                (r"(?:周期|工期|时长)\s*[:：-]?\s*(\d+)\s*个?月", 30))
    for pattern, multiplier in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            return int(match.group(1)) * multiplier
    return None


def _event_status(text: str) -> str | None:
    rules = (
        ("Closure", r"\b(?:closed?|closure|terminated?)\b|关停|终止"),
        ("Completion", r"\b(?:completion|completed|final acceptance)\b|结项|完成验收"),
        ("Rejection", r"\b(?:rejected?|rejection|declined?)\b|拒绝|不予通过"),
        ("In-Progress", r"\b(?:approved?|accepted?)\b|批准|通过立项|正式立项"),
        ("Pending", r"\bpending\b|待定"),
    )
    for status, pattern in rules:
        if re.search(pattern, text or "", re.I | re.S):
            if status == "Rejection" and re.search(r"\b(?:not|no)\s+(?:a\s+)?(?:rejection|rejected)\b|并非拒绝|不是拒绝", text, re.I):
                continue
            return status
    return None


def _topic_events(topic: dict) -> list[dict]:
    posts = topic.get("post_stream", {}).get("posts", [])
    events: list[dict] = []
    first = next((post for post in posts if int(post.get("post_number") or 0) == 1), None)
    if first:
        events.append({"date": _date(first.get("created_at")), "status": "Submitted",
                       "post_number": 1,
                       "url": topic.get("url", ""), "summary": "提案发布"})
    for post in sorted(posts, key=lambda item: int(item.get("post_number") or 0)):
        number = int(post.get("post_number") or 0)
        if number <= 1:
            continue
        if str(post.get("username") or "").lower() not in COMMITTEE_USERS:
            continue
        text = clean_html(post.get("cooked", ""))
        status = _event_status(text)
        pending = bool(re.search(r"\bpending\b|待定", text, re.I))
        if not status and not pending:
            continue
        event = {"date": _date(post.get("created_at")), "post_number": number,
                 "url": f"{topic.get('url', '')}/{number}",
                 "summary": re.sub(r"\s+", " ", text).strip()[:240]}
        if pending:
            events.append({**event, "status": "Pending"})
        if status and status != "Pending":
            events.append({**event, "status": status})
    return events


def _links_in_topic(topic: dict, *, completed: bool = False) -> tuple[str, str]:
    repository, demo = "", ""
    first = next((post for post in topic.get("post_stream", {}).get("posts", [])
                  if int(post.get("post_number") or 0) == 1), {})
    applicant = str(first.get("username") or "").lower()
    for post in topic.get("post_stream", {}).get("posts", []):
        owned = int(post.get("post_number") or 0) == 1 or (applicant and str(post.get("username") or "").lower() == applicant)
        if owned or (completed and re.search(r"\b(?:completion|completed|final report|demo)\b|结项|演示", clean_html(post.get("cooked") or ""), re.I)):
            cooked = post.get("cooked") or ""
            for match in re.finditer(r'<a\b[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>(.*?)</a>', cooked, re.I | re.S):
                href, label = match.groups()
                url = unescape(href)
                if not repository and re.search(r"https?://(?:www\.)?(?:github\.com|gitlab\.com|codeberg\.org|bitbucket\.org)/", url, re.I):
                    repository = url
                context = clean_html(cooked[max(0, match.start() - 100):match.start()])[-80:]
                explicit = re.search(r"\b(?:demo|live preview|try it|play online|prototype)\b|演示|体验|试用|试玩|在线预览", clean_html(label), re.I)
                contextual = completed and re.search(r"(?:demo|live preview|try it|play online|prototype|演示|体验|试用|试玩|在线预览)\s*(?:链接|地址|[:：\-]|\s){0,8}$", context, re.I)
                likely_url = completed and re.search(r"(?:/demo(?:/|\?|$)|/play(?:/|\?|$)|/app(?:/|\?|$)|(?:youtube\.com/watch|youtu\.be/|bilibili\.com/video/|loom\.com/share/))", url, re.I)
                if not demo and (explicit or contextual or likely_url) and not re.search(r"(?:github\.com|gitlab\.com|codeberg\.org|bitbucket\.org|talk\.nervos\.org)/", url, re.I):
                    demo = url
    return repository, demo


WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _weekday(text: Any) -> str:
    found = re.search(r"(?:每\s*)?(?:周|星期|礼拜)\s*([一二三四五六日天])|\b(?:every\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", str(text or ""), re.I)
    if not found:
        return ""
    if found.group(1):
        return "周日" if found.group(1) == "天" else "周" + found.group(1)
    return WEEKDAYS[("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday").index(found.group(2).lower())]


def _update_schedule(value: Any, *date_values: Any) -> list[dict]:
    text = "、".join(str(item) for item in value) if isinstance(value, list) else str(value or "")
    pieces = [piece.strip() for piece in re.split(r"[;；\n]+", text) if piece.strip()]
    return [{"text": piece, "weekday": _weekday(piece)} for piece in pieces]


def _schedule_for_row(row: dict) -> list[dict]:
    schedule = _update_schedule(row.get("update_channels"))
    if not schedule and row.get("manual_update_weekdays"):
        schedule = [{"text": "每周更新", "weekday": ""}]
    for index, item in enumerate(schedule):
        manual = (row.get("manual_update_weekdays") or {}).get(str(index))
        if manual is not None:
            item["weekday"] = manual
    return schedule


@_serial
def set_update_weekday(topic_id: str, index: int, weekday: str) -> dict:
    current = load()
    row = next((item for item in current["projects"] if str(item.get("topic_id")) == str(topic_id)), None)
    if row is None:
        raise ValueError("未找到该项目")
    if weekday not in (*WEEKDAYS, ""):
        raise ValueError("请选择周一至周日")
    if not row.get("update_channels") and index == 0:
        row["update_channels"] = "每周更新"
    schedule = _schedule_for_row(row)
    if index < 0 or index >= len(schedule):
        raise ValueError("未找到该条约定更新")
    row.setdefault("manual_update_weekdays", {})[str(index)] = weekday
    row["update_schedule"] = _schedule_for_row(row)
    current["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save(current)
    return current


def _proposal_budget(cooked: str) -> str:
    text = clean_html(re.sub(r"</(?:p|li|tr|h[1-6])>|<br\s*/?>", "\n", cooked or "", flags=re.I))
    amount = r"(?:[$€¥]\s*\d[\d,.]*(?:\s*[kKmM])?|\d[\d,.]*(?:\s*[kKmM])?\s*(?:USD|USDT|USDC|CKB|RMB|CNY|美元|人民币|元))"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    label = r"(?:total\s*(?:requested|budget|funding|grant)|(?:requested|proposed)\s*(?:budget|funding|amount)|(?:budget|grant|funding)\s*(?:requested|request|total|needed)|总(?:预算|金额|资助)|申请(?:预算|金额|资助)|预算(?:总额|合计)|资金需求)"
    for index, line in enumerate(lines):
        if re.search(label, line, re.I) and not re.search(r"\b(?:milestone|phase|installment)\b|阶段|分期", line, re.I):
            found = re.search(amount, line, re.I) or (re.search(amount, lines[index + 1], re.I) if index + 1 < len(lines) else None)
            if found:
                return found.group(0).strip()
    simple = [re.search(amount, line, re.I) for line in lines
              if re.search(r"^(?:budget|grant|funding|预算)\s*[:：]", line, re.I)]
    if len(simple) == 1 and simple[0]:
        return simple[0].group(0).strip()
    return ""


def _review_status(current: str) -> str:
    if current in {"In-Progress", "Completion", "Closure"}:
        return "通过"
    if current == "Rejection":
        return "拒绝"
    if current == "Pending":
        return "Pending"
    return "未正式评审"


def _execution_status(current: str) -> str:
    return {"In-Progress": "进行中", "Completion": "结项阶段", "Closure": "强制关闭",
            "Rejection": "立项阶段", "Pending": "立项阶段", "Submitted": "立项阶段",
            "Pre-review": "立项阶段"}.get(current, "未知")


def _import_current(review: Any, execution: Any) -> str:
    review, execution = str(review or ""), str(execution or "")
    if execution in {"彻底完成", "结项阶段"}:
        return "Completion"
    if execution == "强制关闭":
        return "Closure"
    if execution == "进行中":
        return "In-Progress"
    if review == "Pending":
        return "Pending"
    if review == "拒绝":
        return "Rejection"
    if review == "通过":
        return "In-Progress"
    return "Unknown"


@_serial
def refresh_from_forum(board: dict, fetcher: Callable[[str], dict] = fetch_topic,
                       scope: str = "active", topic_ids: set[str] | None = None) -> dict:
    from .legacy import load_data
    whitelist = board.get("board_whitelist")
    if whitelist is None:
        whitelist = load_data().get("board_whitelist", {})
    existing = {str(row.get("topic_id")): row for row in load().get("projects", [])
                if row.get("topic_id")}
    posts, statuses = _board_posts(board)
    active = {"Pre-review", "Submitted", "Pending", "In-Progress"}
    ids = [topic_id for topic_id in posts if
           (topic_id in topic_ids if topic_ids is not None else
            scope == "all" or statuses.get(topic_id) in active | {RELATED_STATUS}
            or (existing.get(topic_id) or {}).get("current_status") in active)]
    if topic_ids is not None and not ids:
        raise ValueError("目标帖子不在本地看板缓存；请先刷新帖子时间线")
    errors = []
    for topic_id in ids:
        post, current = posts[topic_id], statuses.get(topic_id, "Unknown")
        row = dict(existing.get(topic_id) or {})
        try:
            topic = fetcher(topic_id)
            topic["url"] = post.get("url") or f"https://talk.nervos.org/t/{topic_id}"
            topic_posts = topic.get("post_stream", {}).get("posts", [])
            first_post = next((item for item in topic_posts if int(item.get("post_number") or 0) == 1), {})
            first_text = clean_html(first_post.get("cooked", ""))
            related = current == RELATED_STATUS
            events = _topic_events(topic)
            if related:
                events = [{"date": _date(first_post.get("created_at") or post.get("created_at")),
                           "status": RELATED_STATUS, "post_number": 1, "url": topic["url"],
                           "summary": "申请人发布关联内容（非独立提案）"}]
            if topic.get("_fetch_incomplete"):
                errors.append({"topic_id": topic_id, "error": "帖子楼层抓取不完整，保留历史阶段；" +
                               str(topic.get("_fetch_error") or "请稍后重试")})
                seen = {(event.get("post_number"), event.get("status")) for event in events}
                events += [event for event in row.get("stages", [])
                           if not related and (event.get("post_number"), event.get("status")) not in seen]
                events.sort(key=lambda event: (event.get("date") or "", event.get("post_number") or 0))
            repository, demo = _links_in_topic(topic, completed=current == "Completion")
            proposal_budget = _proposal_budget(first_post.get("cooked", ""))
            if current != "Unknown" and not related and not any(event["status"] == current for event in events):
                events.append({"date": _date(post.get("last_posted_at") or post.get("created_at")),
                               "status": current, "post_number": None, "url": post.get("url", ""),
                               "summary": "当前 Talk 分类标签"})
            approved = next((event for event in events if event["status"] == "In-Progress"), None)
            finished = next((event for event in events if event["status"] in {"Completion", "Closure"}), None)
            start = row.get("start_date") or (approved or {}).get("date") or ""
            planned = row.get("planned_completion") or ""
            duration = _duration_days(first_text)
            if start and duration and not planned:
                planned = (dt.date.fromisoformat(start) + dt.timedelta(days=duration)).isoformat()
            row.update({"topic_id": topic_id, "name": post.get("title") or topic.get("title", ""),
                        "proposal_url": post.get("url", ""),
                        "applicant": row.get("applicant") or first_post.get("username") or "",
                        "review_status": _review_status(current),
                        "execution_status": _execution_status(current), "current_status": current,
                        "related_status": BUCKET_STATUS.get(str((whitelist or {}).get(topic_id)), "") if related else "",
                        "tags": post.get("tags") or [], "proposal_date": _date(first_post.get("created_at") or post.get("created_at")),
                        "proposal_created_at": first_post.get("created_at") or post.get("created_at") or "",
                        "start_date": start, "planned_completion": planned,
                        "repository_url": row.get("repository_url") or repository,
                        "demo_url": row.get("demo_url") or demo,
                        "proposal_budget": proposal_budget or row.get("proposal_budget", ""),
                        "actual_completion": row.get("actual_completion") or (finished or {}).get("date") or "",
                        "last_activity": _date((topic_posts[-1] if topic_posts else {}).get("created_at")),
                        "pending_rounds": [event for event in events if event["status"] == "Pending"],
                        "stages": events})
            row["update_schedule"] = _schedule_for_row(row)
            existing[topic_id] = row
        except Exception as exc:
            errors.append({"topic_id": topic_id, "error": str(exc)})
    projects = sorted(existing.values(), key=lambda row: (row.get("proposal_created_at") or row.get("proposal_date") or "", row.get("topic_id") or ""), reverse=True)
    result = {"updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "projects": projects, "refresh_errors": errors,
              "import_errors": load().get("import_errors", [])}
    save(result)
    return result


def _clean_name(value: Any) -> str:
    text = unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _page_link(page: dict, name: str) -> str:
    prop = (page.get("properties") or {}).get(name) or {}
    body = prop.get(prop.get("type")) or []
    for item in body if isinstance(body, list) else []:
        href = item.get("href") or ((item.get("text") or {}).get("link") or {}).get("url")
        if href:
            return href
    return ""


def _first(values: dict, *names: str) -> Any:
    for name in names:
        if values.get(name) not in (None, "", []):
            return values[name]
    return ""


def _match_post(name: str, url: str, board: dict) -> dict | None:
    from .legacy import _all_cached_posts, _project_match_score, topic_id_from_url
    if url:
        try:
            wanted = topic_id_from_url(url)
            return next((post for post in _all_cached_posts(board) if str(post.get("id")) == wanted), None)
        except ValueError:
            pass
    scored = sorted(((_project_match_score(name, post), post) for post in _all_cached_posts(board)),
                    key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def _tracking_match(page: dict, source_key: str, props: dict, projects: dict[str, dict]) -> str | None:
    """Financial rows require stable ID/URL or a unique exact title, never a fuzzy hit."""
    from .forum import _simplify_project_name
    identity = str(property_value(page, props.get("topic_id", "")) or "").strip()
    if identity:
        return identity if identity in projects else None
    url = str(property_value(page, props.get("proposal_url", "")) or "")
    match = re.search(r"/t/(?:[^/]+/)?(\d{3,})(?:/|$|\?)", url)
    if match:
        return match.group(1) if match.group(1) in projects else None
    title = _simplify_project_name(str(property_value(page, props["title"]) or ""))
    normalized = "".join(character for character in title.casefold() if character.isalnum())
    if not normalized:
        return None
    matches = [topic_id for topic_id, row in projects.items()
               if "".join(character for character in _simplify_project_name(row.get("name", "")).casefold()
                          if character.isalnum()) == normalized]
    return matches[0] if len(matches) == 1 else None


@_serial
def sync_funding(notion: Any, config: dict, board: dict | None = None) -> dict:
    """Read only the three configured Notion tables; never run reconciliation."""
    current = load()
    projects = {str(row.get("topic_id")): row for row in current.get("projects", []) if row.get("topic_id")}
    board_statuses = _board_posts(board)[1] if board is not None else {}
    errors: list[dict] = []
    conflicts: list[dict] = []
    unmatched: dict[str, int] = {}
    sources = ("fund_allocations", "ongoing_projects", "completed_projects")
    candidates: dict[str, dict[str, list[Any]]] = {key: {} for key in (*sources, "ongoing_total")}
    for source_key in sources:
        try:
            props = config["properties"][source_key]
            for page in notion.query(config["data_sources"][source_key]):
                if source_key == "fund_allocations":
                    names = [props.get("amount")]
                elif source_key == "completed_projects":
                    names = [props.get("funding_amount")]
                else:
                    names = [props.get("distributed")]
                amount = next((property_value(page, name) for name in names
                               if name and property_value(page, name) not in (None, "")), "")
                total = (property_value(page, props["total_funding"])
                         if source_key == "ongoing_projects" and props.get("total_funding") else "")
                topic_id = _tracking_match(page, source_key, props, projects)
                if not topic_id:
                    if amount not in (None, "") or total not in (None, ""):
                        unmatched[source_key] = unmatched.get(source_key, 0) + 1
                    continue
                if amount not in (None, ""):
                    candidates[source_key].setdefault(topic_id, []).append(amount)
                if total not in (None, ""):
                    candidates["ongoing_total"].setdefault(topic_id, []).append(total)
        except Exception as exc:
            errors.append({"source": source_key, "error": str(exc)})
    updated = 0
    failed_sources = {item["source"] for item in errors}
    for topic_id, row in projects.items():
        proposed = candidates["fund_allocations"].get(topic_id, [])
        fallback = candidates["ongoing_total"].get(topic_id, [])
        status = board_statuses.get(topic_id, row.get("current_status"))
        source = ("ongoing_projects" if status == "In-Progress" else
                  "completed_projects" if status in {"Completion", "Closure"} else "")
        actual = candidates[source].get(topic_id, []) if source else []
        changes = {}
        if status in {"Pre-review", "Submitted", "Pending", "Rejection"}:
            pass  # 立项前预算仅显示提案主帖，不从 Notion 回填。
        elif len({str(amount) for amount in proposed}) > 1:
            conflicts.append({"topic_id": topic_id, "source": "fund_allocations", "error": "拟资助金额冲突，保留原值"})
        elif not proposed and len({str(amount) for amount in fallback}) > 1:
            conflicts.append({"topic_id": topic_id, "source": "ongoing_projects", "error": "拟资助金额冲突，保留原值"})
        elif proposed or fallback:
            wanted = proposed[0] if proposed else fallback[0]
            provenance = "fund_allocations.amount" if proposed else "ongoing_projects.total_funding"
            if row.get("proposed_amount") != wanted or row.get("proposed_amount_source") != provenance:
                changes.update({"proposed_amount": wanted, "proposed_amount_source": provenance})
        if source and len({str(amount) for amount in actual}) > 1:
            conflicts.append({"topic_id": topic_id, "source": source, "error": "实际资助金额冲突，保留原值"})
        elif source and source not in failed_sources:
            expected = actual[0] if actual else ""
            provenance = f"{source}.{'distributed' if source == 'ongoing_projects' else 'funding_amount'}"
            if row.get("actual_amount") != expected or row.get("actual_amount_source") != provenance:
                changes.update({"actual_amount": expected, "actual_amount_source": provenance})
        if status not in {"In-Progress", "Completion", "Closure"} and row.get("actual_amount_source"):
            changes.update({"actual_amount": "", "actual_amount_source": ""})
        if changes:
            row.update(changes)
            updated += 1
    current.update({"projects": list(projects.values()), "funding_synced_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "funding_errors": errors, "funding_conflicts": conflicts, "funding_unmatched": unmatched})
    save(current)
    return {**current, "funding_updated": updated}


@_serial
def import_notion_once(notion: Any, board: dict) -> dict:
    current = load()
    projects = {str(row.get("topic_id")): row for row in current.get("projects", []) if row.get("topic_id")}
    board_statuses = _board_posts(board)[1]
    errors, imported = [], 0
    for database_id in LEGACY_DATABASE_IDS:
        try:
            source = notion.database_data_source_id(database_id)
            if not source:
                raise RuntimeError("没有可查询的数据源")
            schema = notion.retrieve_data_source(source).get("properties") or {}
            for page in notion.query(source):
                values = {name: property_value(page, name) for name in schema}
                name = _clean_name(_first(values, "名称", "项目名称", "项目", "Name"))
                link = _first(values, "提案链接", "帖子链接", "Proposal URL", "Nervos Talk Proposal URL")
                link = str(link or _page_link(page, "名称"))
                post = _match_post(name, link, board)
                if not post:
                    continue
                topic_id = str(post["id"])
                row = dict(projects.get(topic_id) or {})
                review = _first(values, "评审状态 ", "评审状态", "Review Status") or row.get("review_status", "")
                execution = _first(values, "状态", "项目状态", "Status") or row.get("execution_status", "")
                board_status = board_statuses.get(topic_id)
                row.update({"topic_id": topic_id, "name": post.get("title") or name,
                            "proposal_url": post.get("url") or link,
                            "proposal_date": _date(post.get("created_at")),
                            "proposal_created_at": post.get("created_at") or "",
                            "applicant": _first(values, "申请人信息", "申请人", "团队", "Applicant") or row.get("applicant", ""),
                            "review_status": review, "execution_status": execution,
                            "current_status": board_status or _import_current(review, execution),
                            "start_date": _first(values, "开始日期", "Start Date") or row.get("start_date", ""),
                            "planned_completion": _first(values, "计划结项日期", "预计完成日期", "Planned Completion") or row.get("planned_completion", ""),
                            "actual_completion": _first(values, "实际结项日期", "完成日期", "Actual Completion") or row.get("actual_completion", ""),
                            "update_channels": _first(values, "约定更新", "更新渠道", "Update Channel") or row.get("update_channels", []),
                            "update_date": _first(values, "约定更新日期", "更新日期", "Update Date") or row.get("update_date", ""),
                            "repository_url": _first(values, "仓库链接", "代码仓库", "Repository URL") or row.get("repository_url", ""),
                            "demo_url": _first(values, "Demo链接", "演示链接", "Demo URL") or row.get("demo_url", "")})
                row["update_schedule"] = _schedule_for_row(row)
                projects[topic_id] = row
                imported += 1
        except Exception as exc:
            errors.append({"database_id": database_id, "error": str(exc)})
    current.update({"projects": list(projects.values()), "import_errors": errors,
                    "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    save(current)
    return {**current, "imported": imported}


@_serial
def import_text(text: str, board: dict) -> dict:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("请粘贴包含表头和至少一行数据的表格")
    if "|" in lines[0]:
        rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
        if len(rows) > 1 and all(re.fullmatch(r"[:\- ]+", cell or "-") for cell in rows[1]):
            rows.pop(1)
    else:
        dialect = csv.Sniffer().sniff("\n".join(lines), delimiters="\t,")
        rows = list(csv.reader(io.StringIO("\n".join(lines)), dialect))
    return _import_rows(rows, board)


def _import_rows(rows: list[list[str]], board: dict, *, persist: bool = True,
                 current: dict | None = None) -> dict:
    headers = [header.lstrip("\ufeff").strip() for header in rows[0]]
    current = current if current is not None else load()
    projects = {str(row.get("topic_id")): row for row in current.get("projects", []) if row.get("topic_id")}
    board_statuses = _board_posts(board)[1]
    imported = 0
    for cells in rows[1:]:
        values = dict(zip(headers, cells))
        name = _clean_name(_first(values, "名称", "项目名称", "项目", "Name"))
        link = str(_first(values, "提案链接", "帖子链接", "Proposal URL", "Nervos Talk Proposal URL"))
        post = _match_post(name, link, board)
        if not post:
            continue
        topic_id = str(post["id"])
        row = dict(projects.get(topic_id) or {})
        review = _first(values, "评审状态", "评审状态 ", "Review Status") or row.get("review_status", "")
        execution = _first(values, "状态", "项目状态", "Status") or row.get("execution_status", "")
        board_status = board_statuses.get(topic_id)
        row.update({"topic_id": topic_id, "name": post.get("title") or name,
                    "proposal_url": post.get("url") or link,
                    "proposal_date": _date(post.get("created_at")),
                    "proposal_created_at": post.get("created_at") or "",
                    "applicant": _first(values, "申请人信息", "申请人", "团队", "Applicant") or row.get("applicant", ""),
                    "review_status": review, "execution_status": execution,
                    "current_status": board_status or _import_current(review, execution),
                    "start_date": _first(values, "开始日期", "Start Date") or row.get("start_date", ""),
                    "planned_completion": _first(values, "计划结项日期", "预计完成日期", "Planned Completion") or row.get("planned_completion", ""),
                    "actual_completion": _first(values, "实际结项日期", "完成日期", "Actual Completion") or row.get("actual_completion", ""),
                    "update_channels": _first(values, "约定更新", "更新渠道", "Update Channel") or row.get("update_channels", []),
                    "update_date": _first(values, "约定更新日期", "更新日期", "Update Date") or row.get("update_date", ""),
                    "repository_url": _first(values, "仓库链接", "代码仓库", "Repository URL") or row.get("repository_url", ""),
                    "demo_url": _first(values, "Demo链接", "演示链接", "Demo URL") or row.get("demo_url", "")})
        row["update_schedule"] = _schedule_for_row(row)
        projects[topic_id] = row
        imported += 1
    current.update({"projects": list(projects.values()), "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    if persist:
        save(current)
    return {**current, "imported": imported}


@_serial
def import_notion_zip(raw: bytes, board: dict) -> dict:
    """Import CSV tables within a Notion export, without extracting its subpage tree."""
    if len(raw) > 50_000_000:
        raise ValueError("ZIP 文件过大（最多 50 MB）")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (TypeError, zipfile.BadZipFile) as exc:
        raise ValueError("请选择有效的 Notion ZIP 导出文件") from exc
    current = load()
    imported = 0
    scanned = 0
    skipped = []
    total_size = 0
    with archive:
        entries = archive.infolist()
        if len(entries) > 1000:
            raise ValueError("ZIP 内文件过多（最多 1000 个）")
        for entry in entries:
            if entry.is_dir() or not entry.filename.lower().endswith(".csv"):
                continue
            scanned += 1
            total_size += entry.file_size
            if entry.file_size > 5_000_000 or total_size > 20_000_000:
                raise ValueError("ZIP 解压后的 CSV 数据过大")
            try:
                with archive.open(entry) as member:
                    data = member.read(5_000_001)
                if len(data) > 5_000_000:
                    raise ValueError("CSV 文件过大")
                content = data.decode("utf-8-sig")
                rows = list(csv.reader(io.StringIO(content, newline="")))
                if not rows:
                    continue
                headers = {header.strip() for header in rows[0]}
                if not headers.intersection({"名称", "项目名称", "项目", "Name"}) or not headers.intersection(
                        {"评审状态", "评审状态 ", "状态", "项目状态", "Review Status", "Status", "开始日期", "Start Date"}):
                    continue  # Ignore unrelated Notion subpage CSVs.
                result = _import_rows(rows, board, persist=False, current=current)
                imported += result["imported"]
            except (UnicodeError, csv.Error, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
                skipped.append({"file": entry.filename, "error": str(exc)})
    if not scanned:
        raise ValueError("ZIP 中没有 CSV 表格；请在 Notion 导出时包含数据库")
    if not imported and not skipped:
        raise ValueError("ZIP 中未找到可匹配的项目行；请先刷新帖子时间线，并确认导出包含项目表 CSV")
    save(current)
    return {**current, "imported": imported, "csv_scanned": scanned, "skipped": skipped}
