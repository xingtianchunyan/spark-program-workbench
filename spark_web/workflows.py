from __future__ import annotations

import json
from typing import Any

from .notion import (
    NotionClient, NotionConflict, assert_properties, block_signatures, bullet,
    cloneable_block, divider, heading, paragraph, prop_date, prop_select, prop_text, prop_title,
    prop_url, property_value, values_match,
)
from .storage import Store, checksum, utcnow
import re


REQUIRED_NEW_PROJECT = (
    "topic_id", "project_name", "team", "proposal_url", "wallet", "start_date",
    "expected_completion", "total_funding", "allocated_amount", "distributed",
)


class WorkflowInputError(ValueError):
    pass


def require_fields(payload: dict, names: tuple[str, ...]) -> None:
    missing = [name for name in names if not payload.get(name)]
    if missing:
        raise WorkflowInputError("缺少必填字段: " + ", ".join(missing))


class NotionAutomation:
    def __init__(self, client: NotionClient, store: Store, config: dict):
        self.notion = client
        self.store = store
        self.config = config
        self.ds = config["data_sources"]
        self.props = config["properties"]
        self.select = config["select_values"]

    def _append_sequence_once(self, page_id: str, blocks: list[dict]) -> list[dict]:
        """Append only when the same semantic block sequence is not already present remotely."""
        remote = self.notion.list_children(page_id, recursive=True)
        wanted = block_signatures(blocks)
        actual = block_signatures(remote)
        width = len(wanted)
        if width and any(actual[i:i + width] == wanted for i in range(0, len(actual) - width + 1)):
            return []
        created = self.notion.append_children(page_id, blocks)
        verify = block_signatures(self.notion.list_children(page_id, recursive=True))
        if width and not any(verify[i:i + width] == wanted for i in range(0, len(verify) - width + 1)):
            raise RuntimeError("追加后回读内容不完整")
        return created

    def _ensure_page_content(self, page_id: str, blocks: list[dict]) -> None:
        """Recover a partially copied page by requiring the remote content to be an exact prefix."""
        actual_blocks = self.notion.list_children(page_id, recursive=True)
        # 内联数据库（child_database）由 _ensure_inline_databases 单独管理，不参与内容比对，
        # 否则结项流程补建内联库后再次执行会被误判为「内容不一致」。
        actual = [s for s in block_signatures(actual_blocks)
                  if s.get("type") != "child_database"]
        wanted = block_signatures(blocks)
        if actual == wanted:
            return
        if actual != wanted[:len(actual)]:
            raise NotionConflict("目标项目页已有与源页面不一致的内容，已停止自动覆盖")
        remaining = blocks[len(actual):]
        if remaining:
            self.notion.append_children(page_id, remaining)
        verify = [s for s in block_signatures(self.notion.list_children(page_id, recursive=True))
                  if s.get("type") != "child_database"]
        if verify != wanted:
            raise RuntimeError("项目页复制后回读验证失败")

    def _append_missing_template_sections(self, page_id: str, blocks: list[dict]) -> None:
        """Complete missing template sections without overwriting existing prose."""
        remote = block_signatures(self.notion.list_children(page_id, recursive=False))
        existing = {x.get("text") for x in remote if str(x.get("type", "")).startswith("heading_")}
        groups: list[list[dict]] = []
        for block in blocks:
            signature = block_signatures([block])[0]
            if str(signature.get("type", "")).startswith("heading_"):
                groups.append([block])
            elif groups:
                groups[-1].append(block)
        for group in groups:
            heading_text = block_signatures([group[0]])[0].get("text")
            if heading_text not in existing:
                self.notion.append_children(page_id, group)
        after = block_signatures(self.notion.list_children(page_id, recursive=False))
        after_headings = {x.get("text") for x in after if str(x.get("type", "")).startswith("heading_")}
        missing = [block_signatures([group[0]])[0].get("text") for group in groups
                   if block_signatures([group[0]])[0].get("text") not in after_headings]
        if missing:
            raise RuntimeError("项目模板补全后回读失败: " + ", ".join(str(x) for x in missing))

    def _existing_or_create(self, *, data_source: str, title_property: str, title: str,
                            properties: dict, expected: dict, children: list[dict] | None = None,
                            icon: str | None = None) -> tuple[dict, bool]:
        rows = self.notion.find_title(data_source, title_property, title)
        if len(rows) > 1:
            raise NotionConflict(f"发现多个同名 Notion 记录：{title}")
        if rows:
            assert_properties(rows[0], expected)
            return rows[0], False
        created = self.notion.create_page(data_source, properties, children, icon)
        fetched = self.notion.retrieve_page(created["id"])
        assert_properties(fetched, expected)
        return fetched, True

    @staticmethod
    def _norm_title(value: str) -> str:
        import re
        v = re.sub(r"^spark\s*program\s*[|｜\-–—:：]\s*", "", (value or "").strip(), flags=re.I)
        return re.sub(r"\s+", " ", v).casefold()

    def reconcile_notion(self, actor: str = "system") -> dict:
        """对账四个 Notion 表单与本地项目库：拉取各表现有行，按规范化标题匹配，
        回填 projects.notion_page_id，并把目标表中已存在项目的待执行作业标记为
        verified（解决「已录入 Notion 却仍显示待同步」的问题）。结果缓存到 meta
        供 Dashboard 展示。"""
        sources: dict[str, list[dict]] = {}
        for name in ("fund_allocations", "transactions", "ongoing_projects", "completed_projects"):
            props = self.props[name]
            rows = []
            for row in self.notion.query(self.ds[name]):
                summary = {"id": row["id"], "title": property_value(row, props["title"]) or "",
                           "url": row.get("url") or ""}
                if "status" in props:
                    summary["status"] = property_value(row, props["status"]) or ""
                rows.append(summary)
            sources[name] = rows

        ongoing_by_title: dict[str, list[dict]] = {}
        for row in sources["ongoing_projects"]:
            ongoing_by_title.setdefault(self._norm_title(row["title"]), []).append(row)
        completed_by_title: dict[str, list[dict]] = {}
        for row in sources["completed_projects"]:
            completed_by_title.setdefault(self._norm_title(row["title"]), []).append(row)

        matched: list[str] = []
        verified_jobs: list[str] = []
        for project in self.store.query("SELECT * FROM projects"):
            topic_id = str(project["topic_id"])
            norm = self._norm_title(project["title"])
            ongoing = ongoing_by_title.get(norm) or []
            if len(ongoing) == 1 and project.get("notion_page_id") != ongoing[0]["id"]:
                self.store.set_project_notion(topic_id, ongoing[0]["id"])
                matched.append(topic_id)
            has_ongoing = bool(ongoing)
            has_completed = bool(completed_by_title.get(norm))
            jobs = self.store.query(
                """SELECT * FROM sync_jobs WHERE topic_id=?
                   AND status IN ('queued','ready','needs_input','partial')""", (topic_id,))
            for job in jobs:
                wf = "in_progress" if job["workflow"] == "in-progress" else job["workflow"]
                covered = (wf == "in_progress" and has_ongoing) or \
                          (wf in ("completion", "closure") and has_completed)
                if covered:
                    self.store.set_job_status(job["idempotency_key"], "verified",
                                              "对账：Notion 表单中已存在该项目")
                    verified_jobs.append(job["idempotency_key"])
        if matched or verified_jobs:
            self.store.audit(actor, "notion_reconciled", "",
                             {"matched_projects": matched, "verified_jobs": verified_jobs})
        overview = {"updated_at": utcnow(),
                    "sources": {name: {"count": len(rows), "rows": rows}
                                for name, rows in sources.items()},
                    "matched_projects": matched, "verified_jobs": verified_jobs}
        self.store.set_meta("notion_overview", json.dumps(overview, ensure_ascii=False))
        return overview

    def read_ongoing_base(self, topic_id: str) -> dict:
        """Recover migration fields from the authoritative ongoing Notion row."""
        projects = self.store.query("SELECT * FROM projects WHERE topic_id=?", (str(topic_id),))
        if not projects or not projects[0].get("notion_page_id"):
            return {}
        page = self.notion.retrieve_page(projects[0]["notion_page_id"])
        op = self.props["ongoing_projects"]
        return {
            "topic_id": str(topic_id),
            "project_name": property_value(page, op["title"]),
            "team": property_value(page, op["team"]),
            "start_date": property_value(page, op["start_date"]),
            "expected_completion": property_value(page, op["expected_completion"]),
            "total_funding": property_value(page, op["total_funding"]),
            "funding_amount": property_value(page, op["total_funding"]),
            "wallet": property_value(page, op["wallet"]),
        }

    def execute_new_project(self, payload: dict, actor: str = "system") -> dict:
        require_fields(payload, REQUIRED_NEW_PROJECT)
        topic_id = str(payload["topic_id"])
        key = self.store.queue_job(topic_id, "in_progress", payload)
        remote_ids: list[str] = []
        try:
            p = self.props["fund_allocations"]
            expected = {
                p["title"]: payload["project_name"], p["amount"]: payload["allocated_amount"],
                p["status"]: self.select["ongoing"], p["wallet"]: payload["wallet"],
            }
            allocation, _ = self._existing_or_create(
                data_source=self.ds["fund_allocations"], title_property=p["title"],
                title=payload["project_name"], expected=expected,
                properties={p["title"]: prop_title(payload["project_name"]),
                            p["amount"]: prop_text(payload["allocated_amount"]),
                            p["status"]: prop_select(self.select["ongoing"]),
                            p["wallet"]: prop_text(payload["wallet"])})
            remote_ids.append(allocation["id"])
            self.store.record_external("notion", "fund_allocation", f"topic:{topic_id}",
                                       allocation["id"], checksum(expected))

            tp = self.props["transactions"]
            archived_dups: list[str] = []
            for tx in payload.get("transactions", []):
                require_fields(tx, ("hash", "date", "amount", "purpose"))
                tx_type = self._transaction_type(tx, payload)
                expected_tx = {
                    tp["title"]: tx["hash"], tp["type"]: tx_type,
                    tp["date"]: tx["date"], tp["amount"]: tx["amount"], tp["purpose"]: tx["purpose"],
                }
                rows = self.notion.find_title(self.ds["transactions"], tp["title"], tx["hash"])
                if len(rows) > 1:
                    # 历史原因产生重复交易行：保留第一行，归档其余
                    row = rows[0]
                    for extra in rows[1:]:
                        self.notion.archive_page(extra["id"])
                        archived_dups.append(extra["id"])
                    assert_properties(row, expected_tx)
                elif rows:
                    row = rows[0]
                    assert_properties(row, expected_tx)
                else:
                    row, _ = self._existing_or_create(
                        data_source=self.ds["transactions"], title_property=tp["title"],
                        title=tx["hash"], expected=expected_tx,
                        properties={tp["title"]: prop_title(tx["hash"]),
                                    tp["type"]: prop_select(tx_type),
                                    tp["date"]: prop_date(tx["date"]),
                                    tp["amount"]: prop_text(tx["amount"]),
                                    tp["purpose"]: prop_text(tx["purpose"])})
                remote_ids.append(row["id"])
                self.store.record_external("notion", "transaction", tx["hash"], row["id"], checksum(expected_tx))
            if archived_dups:
                self.store.audit(actor, "notion_transactions_deduped", key, {"archived": archived_dups})
            self.store.set_step(key, "fund_pool", "verified", remote_ids, value_checksum=checksum(payload))
        except NotionConflict as exc:
            self.store.set_step(key, "fund_pool", "conflict", remote_ids, str(exc))
            self.store.audit(actor, "notion_conflict", key, {"step": "fund_pool", "error": str(exc)})
            raise
        except Exception as exc:
            self.store.set_step(key, "fund_pool", "failed", remote_ids, str(exc))
            raise

        project_ids: list[str] = []
        try:
            op = self.props["ongoing_projects"]
            expected_project = {
                op["title"]: payload["project_name"], op["team"]: payload["team"],
                op["start_date"]: payload["start_date"],
                op["expected_completion"]: payload["expected_completion"],
                op["total_funding"]: payload["total_funding"], op["wallet"]: payload["wallet"],
                op["distributed"]: payload["distributed"], op["status"]: self.select["ongoing"],
            }
            field_mismatches: dict = {}
            adopted = False
            exact = self.notion.find_title(self.ds["ongoing_projects"], op["title"], payload["project_name"])
            if not exact:
                matches = self._find_existing_project_page(payload["project_name"])
                if len(matches) == 1:
                    # 复用手动维护的项目页，避免重复建页（如 NNCBN）
                    page, field_mismatches = self._adopt_project_page(matches[0], payload)
                    created = False
                    adopted = True
                elif len(matches) > 1:
                    raise NotionConflict("发现多个可能同名的项目页，请先人工合并："
                                         + ", ".join(str(m.get("url", "")) for m in matches))
                else:
                    matches = None
            if exact or matches is None:
                tpl = None if exact else self._template_children(payload)
                if tpl:
                    head, middle, tail = self._split_segments(tpl)
                else:
                    head, middle, tail = self._initial_project_segments(payload)
                page, created = self._existing_or_create(
                    data_source=self.ds["ongoing_projects"], title_property=op["title"],
                    title=payload["project_name"], expected=expected_project,
                    properties={
                        op["title"]: prop_title(payload["project_name"]), op["team"]: prop_text(payload["team"]),
                        op["start_date"]: prop_date(payload["start_date"]),
                        op["expected_completion"]: prop_date(payload["expected_completion"]),
                        op["total_funding"]: prop_text(payload["total_funding"]),
                        op["wallet"]: prop_text(payload["wallet"]),
                        op["distributed"]: prop_text(payload["distributed"]),
                        op["status"]: prop_select(self.select["ongoing"]),
                    }, children=head, icon="🛡️")
            if created:
                # Notion API 不接受通过 children 创建 child_database 块，内联数据库必须
                # 单独 POST /databases 创建（落在页面末尾）。分段交错写入以保证模板顺序：
                # head → 资金库 → middle → 调整库 → tail。
                self._ensure_inline_databases(page["id"], fund=True, adjust=False)
                if middle:
                    self.notion.append_children(page["id"], middle)
                self._ensure_inline_databases(page["id"], fund=False, adjust=True)
                if tail:
                    self.notion.append_children(page["id"], tail)
            else:
                self._append_missing_template_sections(page["id"], self._initial_project_blocks(payload))
                self._ensure_inline_databases(page["id"], fund=True, adjust=True)
            # 用提取到的真实拨款/退款/调整明细填充页内内联库（复用已建页与新建页两条路径都要）；
            # 库缺失时 helper 内部跳过，不阻断主流程。
            self._fill_fund_usage(page["id"], payload.get("disbursements") or [],
                                  refunds=payload.get("refunds") or [])
            self._fill_adjustments(page["id"], payload.get("adjustments") or [])
            project_ids.append(page["id"])
            self.store.set_project_notion(topic_id, page["id"])
            self.store.record_external("notion", "ongoing_project", f"topic:{topic_id}",
                                       page["id"], checksum(expected_project))
            self.store.set_step(key, "project_list", "verified", project_ids, value_checksum=checksum(payload))
        except NotionConflict as exc:
            # Even when legacy data conflicts with an existing row, retain the unique
            # page mapping so weekly updates and an operator-approved correction can proceed.
            try:
                rows = self.notion.find_title(self.ds["ongoing_projects"], op["title"], payload["project_name"])
                if len(rows) == 1:
                    self.store.set_project_notion(topic_id, rows[0]["id"])
            except Exception:
                pass
            self.store.set_step(key, "project_list", "conflict", project_ids, str(exc))
            self.store.audit(actor, "notion_conflict", key, {"step": "project_list", "error": str(exc)})
            raise
        except Exception as exc:
            self.store.set_step(key, "project_list", "failed", project_ids, str(exc))
            raise
        self.store.audit(actor, "notion_new_project_verified", key,
                         {"fund_pool_ids": remote_ids, "project_ids": project_ids,
                          "adopted_existing_page": adopted, "field_mismatches": field_mismatches})
        return {"status": "verified", "job": key, "fund_pool_ids": remote_ids, "project_ids": project_ids,
                "adopted_existing_page": adopted,
                "field_mismatches": field_mismatches or None}

    # 内联数据库列结构（对齐 Quantum-Purse 模板的「资金使用情况」「项目调整」内联库；
    # 模板内联库未共享给 integration，原始列名不可读，以下为按语义拟合的近似 schema）。
    FUND_DB_TITLE = "资金使用情况 / Fund Usage"
    ADJUST_DB_TITLE = "项目调整 / Project Adjustments"
    # 资金库统一列序：交易哈希 → 备注 → 日期 → 类型 → 金额（Name 为 Notion 自动提供的标题列）。
    # 2026-09-26 与线上 16 个内联库实际结构对齐（原「用途 / Purpose」「交易哈希 / TX Hash」已并入/迁移）。
    FUND_DB_SCHEMA = {
        "交易哈希 / Transaction Hash": {"url": {}},
        "备注 / Notes": {"rich_text": {}},
        "日期 / Date": {"date": {}},
        "类型 / Type": {"select": {"options": [{"name": "提款 Withdrawal"}, {"name": "存款 Deposit"}]}},
        "金额 / Amount": {"rich_text": {}},
    }
    ADJUST_DB_SCHEMA = {
        "日期 / Date": {"date": {}},
        "调整内容 / Adjustment": {"rich_text": {}},
        "原因 / Reason": {"rich_text": {}},
    }

    @staticmethod
    def _block_text(block: dict) -> str:
        body = block.get(block.get("type") or "", {}) or {}
        return "".join((item.get("plain_text") or (item.get("text") or {}).get("content") or "")
                       for item in body.get("rich_text") or [])

    def _create_inline_db(self, page_id: str, title: str, schema: dict) -> str:
        db = self.notion.create_database(page_id, title, schema, is_inline=True)
        # 双保险：创建请求已带 is_inline=True；个别 API 版本会静默忽略创建时的
        # 该字段（返回的库 is_inline=False），此时立即 PATCH 补上。
        try:
            if db.get("is_inline") is not True:
                self.notion.update_database(db["id"], {"is_inline": True})
        except Exception:
            pass
        # 双保险：即使 initial_data_source 被忽略，也 PATCH 一次列结构
        try:
            ds_id = self.notion.database_data_source_id(db["id"])
            if ds_id:
                self.notion.update_data_source(ds_id, schema)
        except Exception:
            pass
        return db["id"]

    def _ensure_inline_databases(self, page_id: str, *, fund: bool, adjust: bool) -> list[str]:
        """在页面内创建缺失的内联数据库。按「数据库块紧跟的最近标题」判断角色，
        兼容模板遗留的未命名库（"New database"）；标题已是规范名的库直接认可，
        避免结项补建后重跑被重复创建。"""
        roles: set[str] = set()
        last_heading = ""
        for block in self.notion.list_children(page_id, recursive=False):
            btype = block.get("type") or ""
            if btype.startswith("heading_"):
                last_heading = self._block_text(block)
            elif btype == "child_database":
                title = (block.get("child_database") or {}).get("title") or ""
                if title == self.FUND_DB_TITLE:
                    roles.add("fund")
                elif title == self.ADJUST_DB_TITLE:
                    roles.add("adjust")
                elif "资金" in last_heading or "Fund" in last_heading:
                    roles.add("fund")
                elif "调整" in last_heading or "Adjust" in last_heading:
                    roles.add("adjust")
                else:
                    roles.add("unknown")
        created: list[str] = []
        if fund and "fund" not in roles:
            created.append(self._create_inline_db(page_id, self.FUND_DB_TITLE, self.FUND_DB_SCHEMA))
        if adjust and "adjust" not in roles:
            created.append(self._create_inline_db(page_id, self.ADJUST_DB_TITLE, self.ADJUST_DB_SCHEMA))
        return created

    def _transaction_type(self, tx: dict, payload: dict) -> str:
        """规范化 transactions 行的类型：从金库拨给项目的款项（首款/里程碑款）必须是提款，
        退回金库的必须是存款；AI 返回的非法/缺失类型一律按提款处理。"""
        valid = {self.select["withdrawal"], self.select["deposit"]}
        tx_hash = tx.get("hash") or ""
        for item in payload.get("disbursements") or []:
            if item.get("tx_hash") and item["tx_hash"] == tx_hash:
                return self.select["withdrawal"]
        for item in payload.get("refunds") or []:
            if item.get("tx_hash") and item["tx_hash"] == tx_hash:
                return self.select["deposit"]
        return tx.get("type") if tx.get("type") in valid else self.select["withdrawal"]

    def _inline_database_source(self, page_id: str, title: str) -> str | None:
        """按规范标题找到页内内联库（child_database 块 → database id → data source id）。"""
        for block in self.notion.list_children(page_id, recursive=False):
            if block.get("type") != "child_database":
                continue
            if (block.get("child_database") or {}).get("title") != title:
                continue
            try:
                return self.notion.database_data_source_id(block["id"])
            except Exception:
                return None
        return None

    FUND_COL_DATE = "日期 / Date"
    FUND_COL_TYPE = "类型 / Type"
    FUND_COL_AMOUNT = "金额 / Amount"
    FUND_COL_HASH = "交易哈希 / Transaction Hash"
    FUND_COL_NOTES = "备注 / Notes"
    ADJUST_COL_DATE = "日期 / Date"
    ADJUST_COL_SUMMARY = "调整内容 / Adjustment"
    ADJUST_COL_REASON = "原因 / Reason"
    # 与 FUND_DB_SCHEMA 的 select 选项名保持一致（内联库独立于 transactions 表的 select 值）
    FUND_TYPE_WITHDRAWAL = "提款 Withdrawal"
    FUND_TYPE_DEPOSIT = "存款 Deposit"

    def _fill_fund_usage(self, page_id: str, disbursements: list[dict],
                         refunds: list[dict] | None = None) -> list[str]:
        """把委员会拨款/退款明细写入页内「资金使用情况」内联库；按 (日期, 金额) 去重。
        金库拨出记提款，退回金库记存款。库不存在时跳过不报错。"""
        entries = [("withdrawal", d) for d in disbursements or []]
        entries += [("deposit", r) for r in refunds or []]
        if not entries:
            return []
        source = self._inline_database_source(page_id, self.FUND_DB_TITLE)
        if not source:
            return []
        existing = {(property_value(row, self.FUND_COL_DATE), property_value(row, self.FUND_COL_AMOUNT))
                    for row in self.notion.query(source)}
        created: list[str] = []
        for kind, item in entries:
            date = (item.get("date") or "")[:10]
            amount = item.get("amount") or ""
            if not date or not amount or (date, amount) in existing:
                continue
            note = " ".join(x for x in (item.get("note") or "", item.get("post_url") or "") if x)
            properties = {
                self.FUND_COL_DATE: prop_date(date),
                self.FUND_COL_TYPE: prop_select(self.FUND_TYPE_DEPOSIT if kind == "deposit"
                                                else self.FUND_TYPE_WITHDRAWAL),
                self.FUND_COL_AMOUNT: prop_text(amount),
                self.FUND_COL_HASH: prop_url(item.get("tx_hash") or None),
                self.FUND_COL_NOTES: prop_text(note),
            }
            row = self.notion.create_page(source, properties)
            existing.add((date, amount))
            created.append(row["id"])
        return created

    def _fill_adjustments(self, page_id: str, adjustments: list[dict]) -> list[str]:
        """把项目调整事件写入页内「项目调整」内联库；按 (日期, 调整内容) 去重。
        库不存在时跳过不报错。"""
        if not adjustments:
            return []
        source = self._inline_database_source(page_id, self.ADJUST_DB_TITLE)
        if not source:
            return []
        existing = {(property_value(row, self.ADJUST_COL_DATE),
                     property_value(row, self.ADJUST_COL_SUMMARY)) for row in self.notion.query(source)}
        created: list[str] = []
        for item in adjustments:
            date = (item.get("date") or "")[:10]
            summary = item.get("summary") or ""
            if not date or not summary or (date, summary) in existing:
                continue
            properties = {
                self.ADJUST_COL_DATE: prop_date(date),
                self.ADJUST_COL_SUMMARY: prop_text(summary),
                self.ADJUST_COL_REASON: prop_text(item.get("post_url") or ""),
            }
            row = self.notion.create_page(source, properties)
            existing.add((date, summary))
            created.append(row["id"])
        return created

    @staticmethod
    def _paragraphs_from_text(text: str, limit: int = 1800) -> list[dict]:
        """把长正文按行边界切成多个 paragraph 块（每块不超过 limit 字符）。"""
        blocks: list[dict] = []
        current = ""
        for line in (text or "").splitlines() or [""]:
            candidate = line if not current else current + "\n" + line
            if current and len(candidate) > limit:
                blocks.append(paragraph(current))
                current = line
            else:
                current = candidate
            while len(current) > limit:  # 单行超长时硬切，保证块不超 limit
                blocks.append(paragraph(current[:limit]))
                current = current[limit:]
        if current or not blocks:
            blocks.append(paragraph(current))
        return blocks

    def _initial_project_segments(self, payload: dict) -> tuple[list[dict], list[dict], list[dict]]:
        """对齐 Quantum-Purse 模板的三段页面结构。所有项目链接一律使用 Nervos Talk 链接。
        内联数据库不在块里（API 不支持），由调用方在两段之间单独创建。"""
        proposal_url = payload["proposal_url"]
        head = [
            heading(1, "基本信息 / Basic Information"),
            bullet(f"项目申请案 / Project Proposal: {proposal_url}", link=proposal_url),
            bullet(f"团队钱包地址 / Team Wallet Address: {payload['wallet']}"),
            heading(2, "评估结果 / Evaluation Result"),
            bullet(f"批准日期 / Approval Date: {payload.get('approval_date', '')}"),
            bullet("批准理由 / Approval Rationale:"),
            paragraph(payload.get("approval_rationale", "")),
            paragraph("Nervos Talk 上公示链接 / Link to the announcement on Nervos Talk:"),
            paragraph(proposal_url, link=proposal_url),
            heading(2, "资金使用情况 / Fund Usage"),
        ]
        middle = [
            heading(2, "进度更新 / Progress Updates"),
            paragraph("每周更新以子页面形式记录在本节（Update YYYY-MM-DD）。"
                      "Weekly updates are recorded as child pages in this section."),
            heading(2, "项目调整（如适用）/ Project Adjustments (If applicable)"),
        ]
        tail = [
            heading(2, "结项评价 / Final Evaluation"),
            bullet("完成日期 / Completion Date:"),
            bullet("评价摘要 / Evaluation Summary:"),
            bullet("主要成果 / Key Deliverables:"),
            bullet("创新点与价值 / Innovation & Value:"),
            bullet("后续支持 / Follow-up Support:"),
            bullet("结项报告 / Final Report:"),
        ]
        return head, middle, tail

    def _find_existing_project_page(self, project_name: str) -> list[dict]:
        """模糊匹配进行中项目表里可能已存在的项目页（防止重复建页）。

        规范化标题相等，或一方包含另一方（长度 >10 才做包含判断，避免误配）。"""
        wanted = self._norm_title(project_name)
        title_prop = self.props["ongoing_projects"]["title"]
        matches = []
        for row in self.notion.query(self.ds["ongoing_projects"]):
            title = property_value(row, title_prop) or ""
            norm = self._norm_title(title)
            if norm == wanted:
                matches.append(row)
            elif len(wanted) > 10 and len(norm) > 10 and (wanted in norm or norm in wanted):
                matches.append(row)
        # 去重（同一页面可能因视图重复返回）
        seen, unique = set(), []
        for row in matches:
            if row["id"] not in seen:
                seen.add(row["id"])
                unique.append(row)
        return unique

    def _adopt_project_page(self, page: dict, payload: dict) -> tuple[dict, dict]:
        """复用手动维护的项目页：只回填空字段，绝不覆盖已有值；差异记入返回的
        mismatches 供调用方报告（保留人工维护数据优先）。"""
        op = self.props["ongoing_projects"]
        expected = {
            op["team"]: payload["team"],
            op["start_date"]: payload["start_date"],
            op["expected_completion"]: payload["expected_completion"],
            op["total_funding"]: payload["total_funding"],
            op["wallet"]: payload["wallet"],
            op["distributed"]: payload["distributed"],
            op["status"]: self.select["ongoing"],
        }
        wanted = {
            op["team"]: prop_text(payload["team"]),
            op["start_date"]: prop_date(payload["start_date"]),
            op["expected_completion"]: prop_date(payload["expected_completion"]),
            op["total_funding"]: prop_text(payload["total_funding"]),
            op["wallet"]: prop_text(payload["wallet"]),
            op["distributed"]: prop_text(payload["distributed"]),
            op["status"]: prop_select(self.select["ongoing"]),
        }
        updates, mismatches = {}, {}
        for name, prop in wanted.items():
            actual = property_value(page, name)
            if actual in (None, ""):
                updates[name] = prop
            elif not values_match(expected[name], actual):
                mismatches[name] = actual
        if updates:
            page = self.notion.update_page(page["id"], updates)
        return page, mismatches

    def _template_children(self, payload: dict) -> list[dict] | None:
        """复制 Quantum-Purse 模板页的全部内容块，并把模板里的项目专有信息替换为
        本项目实际数据（项目名、钱包、申请案链接、公示链接、批准日期、批准理由）。
        模板不可用或为空时返回 None，调用方回退到程序化块。"""
        tpl_id = self.config.get("quantum_purse_template_page_id")
        if not tpl_id:
            return None
        try:
            raw = self.notion.list_children(tpl_id, recursive=True)
        except Exception:
            return None
        blocks = [cloneable_block(block) for block in raw]
        blocks = [block for block in blocks if block]
        if not blocks:
            return None
        wallet_re = re.compile(r"\b(?:ckb1|ckt1)[a-z0-9]+\b")
        date_re = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

        def fix_items(items):
            for item in items or []:
                text = item.get("text") or {}
                content = text.get("content") or ""
                if "Quantum Purse" in content:
                    content = content.replace("Quantum Purse", payload["project_name"])
                text["content"] = content
                link = (text.get("link") or {}).get("url")
                if link:
                    new_link = link
                    if wallet_re.search(link):
                        new_link = payload["wallet"]
                    elif "discord" in link or "talk.nervos" in link:
                        new_link = payload["proposal_url"]
                    if new_link != link:
                        text["link"] = {"url": new_link}
            return items

        def walk(block_list):
            for block in block_list:
                kind = block.get("type")
                body = block.get(kind) or {}
                if "rich_text" in body:
                    body["rich_text"] = fix_items(body["rich_text"])
                if body.get("children"):
                    walk(body["children"])

        walk(blocks)
        # 批准日期 / 批准理由 定向改写
        for index, block in enumerate(blocks):
            kind = block.get("type")
            body = block.get(kind) or {}
            if "rich_text" not in body:
                continue
            text = self._block_text(block)
            if kind == "bulleted_list_item" and "批准日期" in text:
                body["rich_text"] = [{"type": "text", "text": {"content": date_re.sub(
                    payload.get("approval_date") or "", text, count=1)}}]
            elif kind == "paragraph" and index > 0:
                prev = blocks[index - 1]
                if prev.get("type") == "bulleted_list_item" and "批准理由" in self._block_text(prev):
                    body["rich_text"] = [{"type": "text", "text": {"content":
                        payload.get("approval_rationale") or "待补充 / TBD"}}]
        return blocks

    @staticmethod
    def _split_segments(blocks: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
        """把模板块按「资金使用情况」「项目调整」两个 h2 标题切成三段，
        便于在两段之间插入内联数据库。"""
        head_end = middle_end = None
        for index, block in enumerate(blocks):
            if block.get("type") != "heading_2":
                continue
            text = NotionAutomation._block_text(block)
            if head_end is None and ("资金" in text or "Fund" in text):
                head_end = index + 1
            elif head_end is not None and middle_end is None and ("调整" in text or "Adjust" in text):
                middle_end = index + 1
                break
        if head_end is None or middle_end is None:
            return blocks, [], []
        return blocks[:head_end], blocks[head_end:middle_end], blocks[middle_end:]

    def _initial_project_blocks(self, payload: dict) -> list[dict]:
        head, middle, tail = self._initial_project_segments(payload)
        return head + middle + tail

    def execute_weekly_update(self, payload: dict, actor: str = "system") -> dict:
        require_fields(payload, ("topic_id", "project_name", "update_key", "date", "progress"))
        topic_id = str(payload["topic_id"])
        natural_key = f"topic:{topic_id}:{payload['update_key']}"
        existing = self.store.get_external("notion", "weekly_update", natural_key)
        if existing:
            return {"status": "verified", "page_id": existing["remote_id"], "duplicate": True}
        project = self.store.query("SELECT * FROM projects WHERE topic_id=?", (topic_id,))
        if not project or not project[0].get("notion_page_id"):
            raise WorkflowInputError("未找到项目对应的 Notion 进行中项目页")
        page_id = project[0]["notion_page_id"]
        key = self.store.queue_job(topic_id, "weekly", payload)
        # 对齐模板：每条周报一个「Update YYYY-MM-DD」子页面
        blocks = [heading(3, f"周报 Weekly Update · {payload['date']}")]
        if payload.get("fund_usage"):
            blocks += [heading(3, "资金使用情况 / Fund Usage"), paragraph(payload["fund_usage"])]
        blocks += [heading(3, "进度更新 / Progress Update"), paragraph(payload["progress"])]
        if payload.get("adjustments"):
            blocks += [heading(3, "项目调整 / Project Adjustments"), paragraph(payload["adjustments"])]
        if payload.get("source_url"):
            blocks.append(paragraph("来源 / Source: " + payload["source_url"], link=payload["source_url"]))
        try:
            title = f"Update {payload['date']}"
            remote_id = None
            for block in self.notion.list_children(page_id, recursive=False):
                if block.get("type") == "child_page" and (block.get("child_page") or {}).get("title") == title:
                    remote_id = block["id"]  # 远程已有同名周报子页：本地记录丢失时防重
                    break
            if remote_id is None:
                child = self.notion.create_child_page(page_id, title, children=blocks)
                remote_id = child["id"]
                if not self.notion.list_children(remote_id, recursive=False):
                    raise RuntimeError("周报子页面创建后回读为空")
            self.store.record_external("notion", "weekly_update", natural_key, remote_id, checksum(payload))
            self.store.set_step(key, "weekly_update", "verified", [remote_id], value_checksum=checksum(payload))
            adjustments_text = (payload.get("adjustments") or "").strip()
            if adjustments_text and adjustments_text not in ("None", "none", "无", "N/A", "n/a"):
                # 周报里报告的调整同步登记到页内「项目调整」内联库；库不存在时跳过不报错
                self._fill_adjustments(page_id, [{
                    "date": payload["date"], "summary": adjustments_text,
                    "post_url": payload.get("source_url") or "",
                }])
            if payload.get("post_number"):
                self.store.advance_forum_cursor(topic_id, int(payload["post_number"]))
            self.store.audit(actor, "notion_weekly_update_verified", key, {"page_id": page_id, "child_page_id": remote_id})
            return {"status": "verified", "page_id": page_id, "child_page_id": remote_id}
        except Exception as exc:
            self.store.set_step(key, "weekly_update", "failed", [], str(exc))
            raise

    def execute_completion(self, payload: dict, actor: str = "system") -> dict:
        require_fields(payload, ("topic_id", "project_name", "team", "start_date", "completion_date",
                                 "funding_amount", "wallet", "total_funding", "expected_completion", "status"))
        topic_id = str(payload["topic_id"])
        workflow = "closure" if payload["status"] == self.select["shutdown"] else "completion"
        key = self.store.queue_job(topic_id, workflow, payload)
        projects = self.store.query("SELECT * FROM projects WHERE topic_id=?", (topic_id,))
        if not projects:
            raise WorkflowInputError("未找到项目记录，不能执行结项迁移")
        ongoing_id = projects[0].get("notion_page_id")
        cp = self.props["completed_projects"]
        expected = {
            cp["title"]: payload["project_name"], cp["team"]: payload["team"],
            cp["start_date"]: payload["start_date"], cp["completion_date"]: payload["completion_date"],
            cp["funding_amount"]: payload["funding_amount"], cp["wallet"]: payload["wallet"],
            cp["deliverables"]: (payload.get("deliverables") or [""])[0],
            cp["deliverables_2"]: (payload.get("deliverables") or ["", ""])[1] if len(payload.get("deliverables") or []) > 1 else "",
            cp["total_funding"]: payload["total_funding"], cp["status"]: payload["status"],
            cp["expected_completion"]: payload["expected_completion"],
        }
        properties = {
            cp["title"]: prop_title(payload["project_name"]), cp["team"]: prop_text(payload["team"]),
            cp["start_date"]: prop_date(payload["start_date"]),
            cp["completion_date"]: prop_date(payload["completion_date"]),
            cp["funding_amount"]: prop_text(payload["funding_amount"]), cp["wallet"]: prop_text(payload["wallet"]),
            cp["deliverables"]: prop_text(expected[cp["deliverables"]]),
            cp["deliverables_2"]: prop_text(expected[cp["deliverables_2"]]),
            cp["total_funding"]: prop_text(payload["total_funding"]),
            cp["status"]: prop_select(payload["status"]),
            cp["expected_completion"]: prop_date(payload["expected_completion"]),
        }
        try:
            existing = self.notion.find_title(self.ds["completed_projects"], cp["title"], payload["project_name"])
            if len(existing) > 1:
                raise NotionConflict("已完成项目表中存在多个同名项目")
            if existing:
                assert_properties(existing[0], expected)
                completed = existing[0]
            else:
                completed = self.notion.create_page(self.ds["completed_projects"], properties, None, "✅")
                assert_properties(self.notion.retrieve_page(completed["id"]), expected)
            source_blocks = self.notion.list_children(ongoing_id, recursive=True) if ongoing_id else []
            cloned = [cloneable_block(block) for block in source_blocks]
            cloned = [block for block in cloned if block]
            # 结项评价优先用委员会结项公告楼层原文（completion_post_text，忠实不改写）；
            # 没有原文时退回 AI 摘要 final_evaluation。
            evaluation = (payload.get("completion_post_text") or "").strip() \
                or payload.get("final_evaluation", "") or ""
            evaluation_blocks = self._paragraphs_from_text(evaluation, limit=1800)
            if payload.get("completion_post_url"):
                evaluation_blocks.append(
                    paragraph("来源 / Source: " + payload["completion_post_url"],
                              link=payload["completion_post_url"]))
            cloned += [divider(), heading(2, "结项评价 / Final Evaluation")] + evaluation_blocks
            self._ensure_page_content(completed["id"], cloned)
            # 克隆源页内容不含内联数据库（API 不支持复制 child_database），
            # 与 execute_new_project 保持一致，此处单独补建。
            self._ensure_inline_databases(completed["id"], fund=True, adjust=True)
            op = self.props["ongoing_projects"]
            ongoing_status = self.select["completed"]
            if ongoing_id:
                self.notion.update_page(ongoing_id, {op["status"]: prop_select(ongoing_status)})
            fp = self.props["fund_allocations"]
            allocations = self.notion.find_title(self.ds["fund_allocations"], fp["title"], payload["project_name"])
            for allocation in allocations:
                self.notion.update_page(allocation["id"], {fp["status"]: prop_select(self.select["completed"])})
            self.store.record_external("notion", "completed_project", f"topic:{topic_id}",
                                       completed["id"], checksum(expected))
            self.store.set_step(key, "completion", "verified", [completed["id"]], value_checksum=checksum(payload))
            with self.store.transaction() as conn:
                conn.execute("UPDATE projects SET lifecycle=?,updated_at=? WHERE topic_id=?",
                             ("closure" if workflow == "closure" else "completion", utcnow(), topic_id))
            self.store.audit(actor, "notion_completion_verified", key,
                             {"completed_page_id": completed["id"], "ongoing_page_id": ongoing_id})
            return {"status": "verified", "completed_page_id": completed["id"]}
        except NotionConflict as exc:
            self.store.set_step(key, "completion", "conflict", [], str(exc))
            raise
        except Exception as exc:
            self.store.set_step(key, "completion", "failed", [], str(exc))
            raise


def payload_from_json(raw: str | bytes) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise WorkflowInputError("请求体必须是 JSON 对象")
    return value
