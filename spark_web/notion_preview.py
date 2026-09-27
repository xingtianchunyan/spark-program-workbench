# -*- coding: utf-8 -*-
"""Notion 公开页四表实时渲染的数据层。

Notion 前端 SPA 在「外链域」上会对公开页渲染 404（实测其路由层策略），
iframe 嵌入不可行；但匿名数据接口（loadCachedPageChunkV2 / queryCollection /
syncRecordValuesSpaceInitial）通过本地反向代理可以拿到完整 recordMap。
本模块把这三个接口包装成结构化的表格数据，供 /api/notion/preview-table 使用。

数据流：
1. loadCachedPageChunkV2 拉父页面块树 → 每张表 collection_view 块的位置与
   其前面的标题块文本（hint 匹配）；
2. syncRecordValuesSpaceInitial 按视图 ID 精确换取视图记录：
   parent_id（块）↔ collection_pointer（库）↔ 列顺序/宽度/可见性；
3. queryCollection(src=initial_load) 拉行数据；
4. 按视图列顺序把行属性格式化为 HTML 片段（转义+链接+日期）。
"""
from __future__ import annotations

import html
import json
import time
import urllib.error
import urllib.request

UPSTREAM = "https://evergreen-stream-df9.notion.site"
TIMEOUT = 30
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_urlopen = urllib.request.urlopen  # 测试可注入假上游

_DISCOVER_TTL = 600   # 表结构（哪个块是哪张表）变化频率低
_TABLE_TTL = 60       # 行数据缓存
_discover_cache: dict = {"at": 0.0, "tables": {}}
_table_cache: dict = {}

# 列宽钳制：太窄挤压内容，太宽撑破面板
_WIDTH_MIN, WIDTH_MAX = 96, 480


class PreviewError(Exception):
    """预览数据拉取/解析失败（上游不可达、结构变化等）。"""


def _post_api(path: str, payload: dict) -> dict:
    """POST notion.site /api/v3/<path>，返回解析后的 JSON。4xx/5xx 原样抛 PreviewError。

    上游对 syncRecordValuesSpaceInitial 可能返回 fanoutData（要求带 x-notion-cell
    重试到指定 cell），此处自动重试（最多 3 次）；匿名访问偶发空 recordMap，也一并重试。
    """
    url = f"{UPSTREAM}/api/v3/{path}"
    body = json.dumps(payload).encode("utf-8")
    cell = None
    last = None
    for _ in range(3):
        headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT,
                   "Accept": "application/json", "Accept-Encoding": "identity"}
        if cell:
            headers["x-notion-cell"] = cell
        request = urllib.request.Request(url, method="POST", data=body, headers=headers)
        try:
            with _urlopen(request, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise PreviewError(f"Notion API {exc.code}: {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PreviewError(f"无法连接 notion.site（{exc}）") from exc
        except ValueError as exc:
            raise PreviewError(f"Notion API 返回无效 JSON（{exc}）") from exc
        last = data
        fanout = (data.get("fanoutData") or [{}])[0]
        learned = (fanout.get("headers") or {}).get("x-notion-cell")
        if learned and not _has_records(data):
            cell = learned
            continue
        if not _has_records(data) and "syncRecordValues" in path:
            continue  # 匿名空响应：重试
        return data
    return last or {}


def _has_records(data: dict) -> bool:
    record_map = data.get("recordMap") or {}
    return any(table for key, table in record_map.items() if key != "__version__")


def _record_value(rec: dict | None) -> dict:
    """recordMap 记录解包：{spaceId, value:{value:{...}}} 或 {value:{...}} → 内层 value。"""
    if not rec:
        return {}
    v = rec.get("value")
    if isinstance(v, dict) and isinstance(v.get("value"), dict):
        return v["value"]
    return v if isinstance(v, dict) else {}


def _plain_runs(runs) -> str:
    """[[text, [formats]]...] → 纯文本（用于标题匹配）。"""
    out = []
    for run in runs or []:
        if isinstance(run, list) and run and isinstance(run[0], str):
            out.append(run[0])
    return "".join(out)


def _chunk(page_id: str) -> dict:
    return _post_api("loadCachedPageChunkV2", {
        "page": {"id": page_id}, "cursor": {"stack": []}, "verticalColumns": False,
    })


def load_page_children(page_id: str) -> tuple[str, list[dict]]:
    """拉父页面块树 → (space_id, [{id,type,title,collection_id?}])，保序。"""
    data = _chunk(page_id)
    record_map = data.get("recordMap") or {}
    blocks = record_map.get("block") or {}
    page_rec = _record_value(blocks.get(page_id))
    if not page_rec:
        raise PreviewError(f"父页面 {page_id} 不存在或未发布")
    space_id = page_rec.get("space_id") or ""
    children = []
    for child_id in page_rec.get("content") or []:
        child = _record_value(blocks.get(child_id))
        if not child:
            continue
        children.append({
            "id": child_id,
            "type": child.get("type") or "",
            "title": _plain_runs((child.get("properties") or {}).get("title")),
            "collection_id": child.get("collection_id") or "",
        })
    return space_id, children


def _sync_views(space_id: str, view_ids: list[str]) -> dict[str, dict]:
    """syncRecordValuesSpaceInitial 批量换视图记录 → {view_id: 完整 value}。"""
    if not view_ids:
        return {}
    data = _post_api("syncRecordValuesSpaceInitial", {
        "requests": [{"pointer": {"table": "collection_view", "id": vid,
                                  "spaceId": space_id}, "version": -1}
                     for vid in view_ids],
        "spacePointer": {"table": "space", "id": space_id},
    })
    views = (data.get("recordMap") or {}).get("collection_view") or {}
    return {vid: _record_value(rec) for vid, rec in views.items()}


def discover_tables(parents: list[dict]) -> dict[str, dict]:
    """按父页面配置发现四张表 → {key: {title, space_id, view_id, view_block_id, columns}}。

    parents: [{page_id, tables: [{key, hint}]}]。hint 与「collection_view 块之前最近的
    标题块文本（去空格）」做包含匹配（进行中/已完成/资金分配/交易记录）。
    """
    now = time.time()
    if now - _discover_cache["at"] < _DISCOVER_TTL and _discover_cache["tables"]:
        return _discover_cache["tables"]
    tables: dict[str, dict] = {}
    for parent in parents:
        page_id = parent.get("page_id") or ""
        wanted = [t for t in (parent.get("tables") or []) if t.get("key") and t.get("hint")]
        if not page_id or not wanted:
            continue
        space_id, children = load_page_children(page_id)
        record_map = _chunk(page_id).get("recordMap") or {}
        view_ids = list((record_map.get("collection_view") or {}).keys())
        view_records = _sync_views(space_id, view_ids)
        # view_block_id（页面里的 collection_view 块）→ 标题（hint 匹配）与视图记录
        by_block: dict[str, tuple[str, dict]] = {}
        for vid, value in view_records.items():
            block_id = value.get("parent_id") or ""
            fmt = value.get("format") or {}
            pointer = fmt.get("collection_pointer") or {}  # collection_pointer 在 format 内
            columns = [
                {"key": p.get("property"), "width": p.get("width"),
                 "visible": p.get("visible", True)}
                for p in (fmt.get("table_properties") or []) if p.get("property")
            ]
            by_block[block_id] = (pointer.get("id") or "",
                                  {"columns": columns, "page_sort": fmt.get("page_sort") or []})
        heading = ""
        for child in children:
            if child["type"] == "collection_view":
                collection_id, extra = by_block.get(child["id"], ("", {}))
                if not collection_id:
                    continue
                compact_heading = heading.replace(" ", "")
                for want in wanted:
                    if want["key"] in tables:
                        continue
                    if want["hint"] in compact_heading:
                        tables[want["key"]] = {
                            "title": heading or want["hint"],
                            "space_id": space_id,
                            "collection_id": collection_id,
                            "view_id": next((vid for vid, v in view_records.items()
                                             if (v.get("parent_id") or "") == child["id"]), ""),
                            "view_block_id": child["id"],
                            "columns": extra.get("columns") or [],
                            "page_sort": extra.get("page_sort") or [],
                        }
                continue
            if child["title"] and child["type"] in {"header", "sub_header", "sub_sub_header"}:
                heading = child["title"]
    if len(tables) < sum(len(p.get("tables") or []) for p in parents):
        missing = [t["key"] for p in parents for t in (p.get("tables") or [])
                   if t.get("key") not in tables]
        raise PreviewError(f"未能定位数据表：{', '.join(missing)}（Notion 页面结构可能调整）")
    _discover_cache.update(at=now, tables=tables)
    return tables


def query_table(space_id: str, view_id: str, view_block_id: str,
                page_sort: list[str] | None = None) -> dict:
    """queryCollection(src=initial_load) → {rows: [{id, props}], incomplete}。

    匿名访问偶发 50 行截断（hasMore=true 且无游标）：用视图的 page_sort 作为权威行序，
    缺行逐条 sync 补齐；仍拿不到的行跳过并在结果里标记 incomplete。
    """
    data = _post_api("queryCollection?src=initial_load", {
        "collectionView": {"id": view_id, "spaceId": space_id},
        "collectionViewBlock": {"id": view_block_id, "spaceId": space_id},
        "clientType": "notion_app", "userTimeZone": "Asia/Shanghai",
        "isFullScreen": False, "isMobile": False,
    })
    block_ids = (((data.get("result") or {}).get("reducerResults") or {})
                 .get("collection_group_results") or {}).get("blockIds") or []
    blocks = (data.get("recordMap") or {}).get("block") or {}
    rows_by_id: dict[str, dict] = {}
    for bid in block_ids:
        row = _record_value(blocks.get(bid))
        if row:
            rows_by_id[bid] = {"id": bid, "props": row.get("properties") or {}}

    order = [bid for bid in (page_sort or []) if bid in rows_by_id]
    order += [bid for bid in block_ids if bid not in order]
    incomplete = False
    wanted = [bid for bid in (page_sort or []) if bid and bid not in rows_by_id]
    if wanted and len(rows_by_id) >= 50:  # 疑似截断：逐条补齐
        incomplete = True
        for bid in wanted[:100]:
            try:
                filled = _sync_block(space_id, bid)
            except PreviewError:
                continue
            if filled is not None:
                rows_by_id[bid] = {"id": bid, "props": filled}
                order.append(bid)
    return {"rows": [rows_by_id[bid] for bid in order if bid in rows_by_id],
            "incomplete": incomplete and len(wanted) > 0}


def _sync_block(space_id: str, block_id: str) -> dict | None:
    """syncRecordValuesSpaceInitial 单块补拉（行属性）；块不存在返回 None。"""
    data = _post_api("syncRecordValuesSpaceInitial", {
        "requests": [{"pointer": {"table": "block", "id": block_id,
                                  "spaceId": space_id}, "version": -1}],
        "spacePointer": {"table": "space", "id": space_id},
    })
    blocks = (data.get("recordMap") or {}).get("block") or {}
    rec = blocks.get(block_id)
    if not rec:
        return None
    value = _record_value(rec)
    return value.get("properties") or {}


def _sync_schema(space_id: str, collection_id: str) -> dict:
    """syncRecordValuesSpaceInitial 换库记录 → schema（属性 key → 名称/类型）。"""
    data = _post_api("syncRecordValuesSpaceInitial", {
        "requests": [{"pointer": {"table": "collection", "id": collection_id,
                                  "spaceId": space_id}, "version": -1}],
        "spacePointer": {"table": "space", "id": space_id},
    })
    collections = (data.get("recordMap") or {}).get("collection") or {}
    if not collections:
        return {}
    return (_record_value(next(iter(collections.values()))).get("schema")) or {}


def _format_date(payload: dict) -> str:
    start = payload.get("start_date") or ""
    end = payload.get("end_date") or ""
    if start and end and end != start:
        return f"{html.escape(str(start))} → {html.escape(str(end))}"
    return html.escape(str(start or end or ""))


def format_cell(value, schema_type: str) -> str:
    """行属性值 → 安全 HTML 片段。"""
    if not value:
        return ""
    parts: list[str] = []
    for run in value:
        if not isinstance(run, list) or not run:
            continue
        text = run[0] if isinstance(run[0], str) else ""
        formats = run[1] if len(run) > 1 and isinstance(run[1], list) else []
        href = None
        for fmt in formats:
            if isinstance(fmt, list) and fmt:
                tag = fmt[0]
                if tag == "a" and len(fmt) > 1:
                    href = str(fmt[1])
                elif tag == "d" and len(fmt) > 1 and isinstance(fmt[1], dict):
                    parts.append(_format_date(fmt[1]))
                    text = ""
                # u/p/e（用户/页面/表情）：保留文本即可
        if text:
            body = html.escape(text)
            if href:
                safe = html.escape(href, quote=True)
                parts.append(f'<a href="{safe}" target="_blank" rel="noreferrer">{body}</a>')
            else:
                parts.append(body)
    sep = " " if schema_type in {"multi_select", "people", "relation"} else ""
    return sep.join(p for p in parts if p)


def _clamp_width(width) -> int:
    try:
        return max(_WIDTH_MIN, min(WIDTH_MAX, int(width)))
    except (TypeError, ValueError):
        return 180


def table_json(key: str, config: dict, refresh: bool = False) -> dict:
    """对外主入口：表 key → {title, columns, rows, page_url, fetched_at}。"""
    now = time.time()
    if not refresh:
        cached = _table_cache.get(key)
        if cached and now - cached["at"] < _TABLE_TTL:
            return cached["data"]
    parents = []
    for parent in config.get("parents") or []:
        wanted = [{"key": t["key"], "hint": t.get("hint") or ""}
                  for t in (config.get("tabs") or [])
                  if t.get("key") in (parent.get("tables") or [])]
        parents.append({"page_id": parent.get("page_id"), "tables": wanted})
    tables = discover_tables(parents)
    info = tables.get(key)
    if not info:
        raise PreviewError(f"未配置的表 key：{key}")
    schema = _sync_schema(info["space_id"], info["collection_id"])
    data = query_table(info["space_id"], info["view_id"], info["view_block_id"],
                       page_sort=info.get("page_sort") or [])

    ordered_keys: list[str] = []
    for col in info.get("columns") or []:
        if col.get("visible", True) and col["key"] not in ordered_keys:
            ordered_keys.append(col["key"])
    for k in schema.keys():  # 视图里隐藏的列也兜底附上（排尾部）
        if k not in ordered_keys:
            ordered_keys.append(k)
    columns = []
    for k in ordered_keys:
        meta = schema.get(k) or {}
        view_col = next((c for c in (info.get("columns") or []) if c.get("key") == k), {})
        columns.append({"key": k, "name": meta.get("name") or k,
                        "type": meta.get("type") or "",
                        "width": _clamp_width(view_col.get("width"))})
    rows = []
    for row in data["rows"]:
        cells = {"_row_id": row["id"]}
        for k in ordered_keys:
            cells[k] = format_cell(row["props"].get(k), (schema.get(k) or {}).get("type") or "")
        rows.append(cells)
    tab = next((t for t in (config.get("tabs") or []) if t.get("key") == key), {})
    result = {
        "title": info["title"],
        "columns": columns,
        "rows": rows,
        "page_url": tab.get("url") or "",
        "fetched_at": int(now),
        "incomplete": bool(data.get("incomplete")),
    }
    _table_cache[key] = {"at": now, "data": result}
    return result


def invalidate_cache() -> None:
    _table_cache.clear()
    _discover_cache.update(at=0.0, tables={})


# ===== 预览单元格写回 =====
# 通过官方 API（集成 Token）按属性名更新行页面；不可写类型在此拒绝并给出中文说明。
_UNEDITABLE = {"formula": "公式属性由 Notion 自动计算", "rollup": "汇总属性由 Notion 自动计算",
               "created_time": "创建时间由 Notion 自动记录", "created_by": "创建者由 Notion 自动记录",
               "last_edited_time": "编辑时间由 Notion 自动记录", "last_edited_by": "编辑者由 Notion 自动记录",
               "people": "人员属性请直接在 Notion 中修改", "relation": "关联属性请直接在 Notion 中修改",
               "files": "附件属性请直接在 Notion 中修改"}


def build_property_payload(column: dict, raw: str) -> dict:
    """按列类型把用户输入转成官方 API 的属性值；空输入表示清空（按类型语义）。"""
    name = column.get("name") or ""
    ptype = column.get("type") or "text"
    value = (raw or "").strip()
    if ptype in _UNEDITABLE:
        raise PreviewError(f"该列不支持在工作台编辑：{_UNEDITABLE[ptype]}")
    if ptype == "title":
        return {name: {"title": [{"text": {"content": value}}]}}
    if ptype in {"text", "rich_text"}:
        return {name: {"rich_text": [{"text": {"content": value}}]}}
    if ptype == "number":
        if not value:
            return {name: {"number": None}}
        try:
            return {name: {"number": float(value.replace(",", "").replace(" ", ""))}}
        except ValueError:
            raise PreviewError(f"「{name}」需要数字，收到：{value!r}")
    if ptype == "date":
        return {name: {"date": {"start": value} if value else None}}
    if ptype == "select":
        return {name: {"select": {"name": value} if value else None}}
    if ptype == "multi_select":
        names = [x.strip() for x in value.replace("，", ",").replace("、", ",").split(",") if x.strip()]
        return {name: {"multi_select": [{"name": x} for x in names]}}
    if ptype == "checkbox":
        truthy = value.lower() in {"1", "true", "yes", "y", "是", "✓", "✔", "checked"}
        return {name: {"checkbox": truthy}}
    if ptype == "url":
        return {name: {"url": value or None}}
    if ptype == "email":
        return {name: {"email": value or None}}
    if ptype == "phone_number":
        return {name: {"phone_number": value or None}}
    raise PreviewError(f"暂不支持的属性类型：{ptype}（请直接在 Notion 中修改）")


def edit_cell(client, row_id: str, column: dict, raw: str) -> None:
    """官方 API 写回一行的一个属性；成功后作废该表缓存。"""
    payload = build_property_payload(column, raw)
    client.update_page(row_id, payload)
