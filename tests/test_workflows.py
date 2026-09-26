import tempfile
import unittest
from pathlib import Path

from spark_web.config import load_workspace_config
from spark_web.notion import NotionConflict, property_value
from spark_web.storage import Store
from spark_web.workflows import NotionAutomation


def normalize_properties(properties):
    result = {}
    for name, value in properties.items():
        kind = next(iter(value))
        result[name] = {"id": name, "type": kind, kind: value[kind]}
        if kind in {"title", "rich_text"}:
            for item in result[name][kind]:
                item["plain_text"] = item.get("text", {}).get("content", "")
    return result


class FakeNotion:
    def __init__(self):
        self.pages = {}
        self.by_source = {}
        self.counter = 0

    def find_title(self, source, property_name, value):
        return [page for page in self.by_source.get(source, [])
                if "".join(x.get("plain_text", "") for x in page["properties"][property_name]["title"]) == value]

    def query(self, data_source_id, filter_body=None, page_size=100):
        rows = []
        for page in self.by_source.get(data_source_id, []):
            if filter_body and filter_body.get("property"):
                prop = filter_body["property"]
                wanted = (filter_body.get("title") or {}).get("equals")
                actual = "".join(x.get("plain_text", "") for x in
                                 page["properties"].get(prop, {}).get("title", []))
                if wanted is not None and actual != wanted:
                    continue
            rows.append(page)
        return rows

    def create_page(self, source, properties, children=None, icon=None):
        self.counter += 1
        page = {"id": f"page-{self.counter}", "properties": normalize_properties(properties),
                "children": list(children or [])}
        self.pages[page["id"]] = page
        self.by_source.setdefault(source, []).append(page)
        return page

    def retrieve_page(self, page_id):
        return self.pages[page_id]

    def list_children(self, page_id, recursive=False):
        return self.pages[page_id].get("children", [])

    def append_children(self, page_id, children):
        created = []
        for index, child in enumerate(children):
            item = dict(child)
            item["id"] = f"{page_id}-block-{len(self.pages[page_id]['children']) + index}"
            created.append(item)
        self.pages[page_id]["children"].extend(created)
        return created

    def update_page(self, page_id, properties):
        self.pages[page_id]["properties"].update(normalize_properties(properties))
        return self.pages[page_id]

    def create_child_page(self, parent_page_id, title, children=None):
        self.counter += 1
        page = {"id": f"page-{self.counter}",
                "properties": {"title": {"id": "title", "type": "title",
                                         "title": [{"plain_text": title, "text": {"content": title}}]}},
                "children": list(children or [])}
        self.pages[page["id"]] = page
        self.pages[parent_page_id].setdefault("children", []).append(
            {"object": "block", "type": "child_page", "id": page["id"],
             "child_page": {"title": title}})
        return page

    def update_data_source(self, data_source_id, properties):
        return {"id": data_source_id, "properties": properties}

    def archive_page(self, page_id):
        for rows in self.by_source.values():
            rows[:] = [r for r in rows if r["id"] != page_id]
        self.pages.pop(page_id, None)
        return {"id": page_id, "archived": True}

    def create_database(self, page_id, title, properties):
        db_id = f"db-{1000 + len(self.pages) + getattr(self, 'db_seq', 0)}"
        self.db_seq = getattr(self, "db_seq", 0) + 1
        self.pages[page_id].setdefault("children", []).append(
            {"object": "block", "type": "child_database", "id": db_id,
             "child_database": {"title": title}})
        return {"id": db_id}

    def database_data_source_id(self, database_id):
        return f"ds-{database_id}"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")
        self.store.export_legacy_status = lambda: None
        self.fake = FakeNotion()
        self.config = load_workspace_config()
        self.auto = NotionAutomation(self.fake, self.store, self.config)
        self.store.upsert_project("90001", "Example", "https://talk.nervos.org/t/90001", "in_progress")
        self.store.create_workflow_tasks("90001", "Example", {})

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_project_is_idempotent_and_verifies_tasks(self):
        payload = self.payload()
        first = self.auto.execute_new_project(payload)
        second = self.auto.execute_new_project(payload)
        self.assertEqual(first["status"], "verified")
        self.assertEqual(second["status"], "verified")
        self.assertEqual(self.fake.counter, 3)
        tasks = self.store.query("SELECT automation_step,done FROM tasks WHERE topic_id='90001'")
        done = {row["automation_step"]: row["done"] for row in tasks}
        self.assertEqual(done["fund_pool"], 1)
        self.assertEqual(done["project_list"], 1)
        self.assertEqual(done["dashboard"], 0)
        self.assertEqual(done["contacts"], 0)

    def payload(self):
        return {
            "topic_id": "90001", "project_name": "Example", "team": "alice",
            "proposal_url": "https://talk.nervos.org/t/90001", "github_url": "https://github.com/x/y",
            "approval_date": "2026-01-01", "approval_rationale": "approved",
            "acceptance_criteria": "working release", "wallet": "ckb1example",
            "start_date": "2026-01-02", "expected_completion": "2026-02-27",
            "total_funding": "$700 USD (700 CKB)", "allocated_amount": "700 CKB",
            "distributed": "20%", "transactions": [{"hash": "0xabc", "date": "2026-01-02",
                "amount": "140 CKB", "purpose": "To Example Multisig Wallet",
                "type": "提款 / Withdrawal"}],
        }
    def test_weekly_and_completion_are_idempotent(self):
        base = self.payload()
        self.auto.execute_new_project(base)
        update = {"topic_id": "90001", "project_name": "Example", "update_key": "post-8",
                  "post_number": 8, "date": "2026-01-09", "fund_usage": "No change",
                  "progress": "SDK prototype completed", "adjustments": "None"}
        one = self.auto.execute_weekly_update(update)
        two = self.auto.execute_weekly_update(update)
        self.assertFalse(one.get("duplicate", False))
        self.assertTrue(two["duplicate"])
        completion = {**{k: base[k] for k in ("topic_id", "project_name", "team", "start_date",
                      "wallet", "total_funding", "expected_completion")},
                      "completion_date": "2026-02-27", "funding_amount": "700 CKB",
                      "deliverables": ["https://github.com/x/y/releases/v1"],
                      "final_evaluation": "Accepted", "status": "已完成 / Completed"}
        first = self.auto.execute_completion(completion)
        second = self.auto.execute_completion(completion)
        self.assertEqual(first["completed_page_id"], second["completed_page_id"])

    def test_weekly_remote_readback_prevents_duplicate_when_local_record_is_missing(self):
        base = self.payload()
        self.auto.execute_new_project(base)
        update = {"topic_id": "90001", "project_name": "Example", "update_key": "post-9",
                  "post_number": 9, "date": "2026-01-16", "fund_usage": "No change",
                  "progress": "Integration tests passed", "adjustments": "None"}
        self.auto.execute_weekly_update(update)
        page_id = self.store.query("SELECT notion_page_id FROM projects WHERE topic_id='90001'")[0]["notion_page_id"]
        before = len(self.fake.pages[page_id]["children"])
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM external_records WHERE object_type='weekly_update'")
        self.auto.execute_weekly_update(update)
        self.assertEqual(before, len(self.fake.pages[page_id]["children"]))

    def test_completion_recovers_a_partial_page_copy(self):
        base = self.payload()
        self.auto.execute_new_project(base)
        completion = {**{k: base[k] for k in ("topic_id", "project_name", "team", "start_date",
                      "wallet", "total_funding", "expected_completion")},
                      "completion_date": "2026-02-27", "funding_amount": "700 CKB",
                      "deliverables": ["https://github.com/x/y/releases/v1"],
                      "final_evaluation": "Accepted", "status": "已完成 / Completed"}
        result = self.auto.execute_completion(completion)
        page_id = result["completed_page_id"]
        expected_count = len(self.fake.pages[page_id]["children"])
        self.fake.pages[page_id]["children"] = self.fake.pages[page_id]["children"][:3]
        self.auto.execute_completion(completion)
        self.assertEqual(expected_count, len(self.fake.pages[page_id]["children"]))

    def test_conflicting_existing_row_stops_without_duplicate(self):
        payload = self.payload()
        self.auto.execute_new_project(payload)
        payload["wallet"] = "ckb1different"
        with self.assertRaises(NotionConflict):
            self.auto.execute_new_project(payload)
        self.assertEqual(self.fake.counter, 3)

    def _project_page_id(self):
        self.auto.execute_new_project(self.payload())
        return self.store.query(
            "SELECT notion_page_id FROM projects WHERE topic_id='90001'")[0]["notion_page_id"]

    def _inline_rows(self, page_id, title):
        source = self.auto._inline_database_source(page_id, title)
        return self.fake.query(source) if source else None

    def test_fill_fund_usage_inserts_withdrawals_deposits_and_dedupes(self):
        page_id = self._project_page_id()
        disbursements = [{"date": "2026-01-02", "amount": "140 CKB", "percent": "20%",
                          "tx_hash": "0xabc",
                          "post_url": "https://talk.nervos.org/t/x/90001?u=alice#2",
                          "note": "首期拨款"}]
        refunds = [{"date": "2026-01-10", "amount": "70 CKB", "percent": "10%",
                    "tx_hash": "0xdef",
                    "post_url": "https://talk.nervos.org/t/x/90001?u=bob#5",
                    "note": "退回金库"}]
        created = self.auto._fill_fund_usage(page_id, disbursements, refunds=refunds)
        self.assertEqual(len(created), 2)
        rows = self._inline_rows(page_id, self.auto.FUND_DB_TITLE)
        self.assertEqual(len(rows), 2)
        by_amount = {property_value(r, "金额 / Amount"): r for r in rows}
        self.assertEqual(property_value(by_amount["140 CKB"], "类型 / Type"), "提款 Withdrawal")
        self.assertEqual(property_value(by_amount["70 CKB"], "类型 / Type"), "存款 Deposit")
        self.assertEqual(property_value(by_amount["140 CKB"], "日期 / Date"), "2026-01-02")
        self.assertEqual(property_value(by_amount["140 CKB"], "交易哈希 / Transaction Hash"), "0xabc")
        self.assertIn("首期拨款", property_value(by_amount["140 CKB"], "备注 / Notes"))
        self.assertIn("?u=alice#2", property_value(by_amount["140 CKB"], "备注 / Notes"))
        # 再插一遍：按 (日期, 金额) 去重，不新增行
        again = self.auto._fill_fund_usage(page_id, disbursements, refunds=refunds)
        self.assertEqual(again, [])
        self.assertEqual(len(self._inline_rows(page_id, self.auto.FUND_DB_TITLE)), 2)

    def test_fill_adjustments_inserts_and_dedupes(self):
        page_id = self._project_page_id()
        adjustments = [{"date": "2026-01-15", "summary": "延期两周获批",
                        "post_url": "https://talk.nervos.org/t/x/90001?u=carol#9"}]
        created = self.auto._fill_adjustments(page_id, adjustments)
        self.assertEqual(len(created), 1)
        rows = self._inline_rows(page_id, self.auto.ADJUST_DB_TITLE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(property_value(rows[0], "调整内容 / Adjustment"), "延期两周获批")
        self.assertEqual(property_value(rows[0], "原因 / Reason"),
                         "https://talk.nervos.org/t/x/90001?u=carol#9")
        self.assertEqual(self.auto._fill_adjustments(page_id, adjustments), [])
        self.assertEqual(len(self._inline_rows(page_id, self.auto.ADJUST_DB_TITLE)), 1)

    def test_new_project_fills_inline_dbs_and_first_disbursement_is_withdrawal(self):
        payload = self.payload()
        payload["transactions"] = [{"hash": "0xabc", "date": "2026-01-02", "amount": "140 CKB",
                                    "purpose": "To Example Multisig Wallet", "type": "存款 / Deposit"}]
        payload["disbursements"] = [{"date": "2026-01-02", "amount": "140 CKB", "percent": "20%",
                                     "tx_hash": "0xabc",
                                     "post_url": "https://talk.nervos.org/t/x/90001?u=alice#2",
                                     "note": "首期拨款"}]
        payload["adjustments"] = [{"date": "2026-01-15", "summary": "范围变更获批",
                                   "post_url": "https://talk.nervos.org/t/x/90001?u=carol#9"}]
        self.auto.execute_new_project(payload)
        page_id = self.store.query(
            "SELECT notion_page_id FROM projects WHERE topic_id='90001'")[0]["notion_page_id"]
        fund_rows = self._inline_rows(page_id, self.auto.FUND_DB_TITLE)
        self.assertEqual(len(fund_rows), 1)
        self.assertEqual(property_value(fund_rows[0], "类型 / Type"), "提款 Withdrawal")
        adjust_rows = self._inline_rows(page_id, self.auto.ADJUST_DB_TITLE)
        self.assertEqual(len(adjust_rows), 1)
        # transactions 表：金库拨出的首款必须记为提款，即使 AI 给了存款类型
        tx_rows = self.fake.find_title(
            self.auto.ds["transactions"], self.auto.props["transactions"]["title"], "0xabc")
        self.assertEqual(property_value(tx_rows[0], self.auto.props["transactions"]["type"]),
                         "提款 / Withdrawal")

    def test_completion_prefers_verbatim_text_splits_and_links_source(self):
        base = self.payload()
        self.auto.execute_new_project(base)
        lines = [f"第 {i:03d} 行 committee verdict 内容" for i in range(120)]  # 约 3500 字符
        verbatim = "\n".join(lines)
        completion = {**{k: base[k] for k in ("topic_id", "project_name", "team", "start_date",
                      "wallet", "total_funding", "expected_completion")},
                      "completion_date": "2026-02-27", "funding_amount": "700 CKB",
                      "deliverables": ["https://github.com/x/y/releases/v1"],
                      "final_evaluation": "AI 摘要不该出现",
                      "completion_post_text": verbatim,
                      "completion_post_url": "https://talk.nervos.org/t/x/90001?u=alice#33",
                      "status": "已完成 / Completed"}
        result = self.auto.execute_completion(completion)
        children = self.fake.pages[result["completed_page_id"]]["children"]
        eval_start = max(i for i, b in enumerate(children)
                         if b.get("type") == "heading_2"
                         and "结项评价" in self.auto._block_text(b))
        # 结项评价小节 = 标题后的连续 paragraph 块（内联库块在其后，不属于小节）
        section = []
        for block in children[eval_start + 1:]:
            if block.get("type") != "paragraph":
                break
            section.append(block)
        source = section[-1]
        self.assertEqual(self.auto._block_text(source),
                         "来源 / Source: https://talk.nervos.org/t/x/90001?u=alice#33")
        paragraphs = section[:-1]
        self.assertGreater(len(paragraphs), 1)  # 长文按行边界切成多块
        for block in paragraphs:
            self.assertLessEqual(len(self.auto._block_text(block)), 1800)
        joined = "\n".join(self.auto._block_text(b) for b in paragraphs)
        self.assertEqual(joined, verbatim)  # 原文完整、未被改写
        self.assertNotIn("AI 摘要不该出现", joined)

    def test_weekly_update_appends_adjustment_row(self):
        base = self.payload()
        self.auto.execute_new_project(base)
        page_id = self.store.query(
            "SELECT notion_page_id FROM projects WHERE topic_id='90001'")[0]["notion_page_id"]
        update = {"topic_id": "90001", "project_name": "Example", "update_key": "post-20",
                  "post_number": 20, "date": "2026-01-20", "fund_usage": "No change",
                  "progress": "Milestone 2 done", "adjustments": "里程碑二延期一周获批",
                  "source_url": "https://talk.nervos.org/t/x/90001?u=dave#20"}
        self.auto.execute_weekly_update(update)
        rows = self._inline_rows(page_id, self.auto.ADJUST_DB_TITLE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(property_value(rows[0], "调整内容 / Adjustment"), "里程碑二延期一周获批")
        self.assertEqual(property_value(rows[0], "原因 / Reason"),
                         "https://talk.nervos.org/t/x/90001?u=dave#20")
        self.assertEqual(property_value(rows[0], "日期 / Date"), "2026-01-20")
        # 幂等：同一周报重复执行不产生重复调整行
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM external_records WHERE object_type='weekly_update'")
        self.auto.execute_weekly_update(update)
        self.assertEqual(len(self._inline_rows(page_id, self.auto.ADJUST_DB_TITLE)), 1)


if __name__ == "__main__":
    unittest.main()
