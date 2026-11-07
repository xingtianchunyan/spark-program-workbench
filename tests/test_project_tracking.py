from __future__ import annotations

import tempfile
import io
import zipfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spark_web import project_tracking
from spark_web import forum


def post(number, username, date, text):
    return {"post_number": number, "username": username, "created_at": date,
            "cooked": f"<p>{text}</p>"}


class ProjectTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(project_tracking, "PROJECT_TRACKING_PATH",
                                       Path(self.tmp.name) / "tracking.json")
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()

    @staticmethod
    def board():
        light = {"id": 11062, "title": "Spark Program | Light dApp Kit",
                 "url": "https://talk.nervos.org/t/light/11062",
                 "created_at": "2026-10-28T00:00:00Z", "tags": ["Pending"]}
        return {"all_posts": {}, "classified": {"pending": [light], "spark_program": {}}}

    @staticmethod
    def topic(_topic_id):
        return {"title": "Spark Program | Light dApp Kit", "post_stream": {"posts": [
            post(1, "akane", "2026-10-28T00:00:00Z", "Duration: 4 weeks"),
            post(2, "xingtianchunyan", "2026-10-29T00:00:00Z",
                 "The committee has moved this application to Pending status."),
            post(4, "xingtianchunyan", "2026-10-31T00:00:00Z",
                 "The proposal remains Pending while we review the update."),
        ]}}

    def test_refresh_preserves_multiple_pending_rounds(self):
        result = project_tracking.refresh_from_forum(self.board(), fetcher=self.topic)
        row = result["projects"][0]
        self.assertEqual(row["topic_id"], "11062")
        self.assertEqual(row["current_status"], "Pending")
        self.assertEqual([event["post_number"] for event in row["pending_rounds"]], [2, 4])

    def test_targeted_refresh_adds_only_selected_non_active_topic(self):
        board = self.board()
        board["classified"]["rejection"] = [{"id": 11079, "title": "TapFiber",
                                              "url": "https://talk.nervos.org/t/tap/11079",
                                              "created_at": "2026-10-30", "tags": ["Rejection"]}]
        result = project_tracking.refresh_from_forum(board, fetcher=self.topic, topic_ids={"11079"})
        self.assertEqual([row["topic_id"] for row in result["projects"]], ["11079"])
        self.assertEqual(result["projects"][0]["current_status"], "Rejection")
        with self.assertRaisesRegex(ValueError, "缓存"):
            project_tracking.refresh_from_forum(board, fetcher=self.topic, topic_ids={"99999"})

    def test_untagged_whitelisted_applicant_post_is_related_not_completion_proposal(self):
        board = self.board()
        related = {"id": 10101, "title": "UGMP 开发心得", "author": "akane", "tags": ["spark-program"],
                   "url": "https://talk.nervos.org/t/ugmp/10101", "created_at": "2026-11-01"}
        board["classified"]["completion"] = [related]
        board["board_whitelist"] = {"10101": "completion"}
        result = project_tracking.refresh_from_forum(board, fetcher=self.topic)
        row = next(x for x in result["projects"] if x["topic_id"] == "10101")
        self.assertEqual(row["current_status"], "申请人有话说")
        self.assertEqual(row["related_status"], "Completion")
        self.assertEqual(row["stages"][0]["status"], "申请人有话说")
        self.assertFalse(row["pending_rounds"])

    def test_real_status_tag_or_committee_author_does_not_become_related(self):
        board = self.board()
        tagged = {"id": 10102, "title": "真实结项", "author": "akane", "tags": ["Completion"]}
        committee = {"id": 10103, "title": "委员会通告", "author": "xingtianchunyan", "tags": []}
        board["classified"]["completion"] = [tagged, committee]
        board["board_whitelist"] = {"10102": "completion", "10103": "completion"}
        _, statuses = project_tracking._board_posts(board)
        self.assertEqual(statuses["10102"], "Completion")
        self.assertEqual(statuses["10103"], "Completion")

    def test_closure_whitelist_related_post_keeps_target_without_proposal_status(self):
        board = self.board()
        board["classified"]["closure"] = [{"id": 10104, "title": "关闭后的复盘", "username": "builder", "tags": []}]
        board["board_whitelist"] = {"10104": "closure"}
        _, statuses = project_tracking._board_posts(board)
        self.assertEqual(statuses["10104"], "申请人有话说")

    def test_all_statuses_sort_by_original_post_date(self):
        board = self.board()
        older = {"id": 11041, "title": "Fiber WiFi", "url": "https://talk.nervos.org/t/fiber/11041",
                 "created_at": "2026-10-20", "tags": ["In-Progress"]}
        board["classified"]["in_progress"] = [older]
        def fetch(topic_id):
            topic = self.topic(topic_id)
            topic["post_stream"]["posts"][0]["created_at"] = "2026-10-20" if topic_id == "11041" else "2026-10-28"
            return topic
        result = project_tracking.refresh_from_forum(
            board, fetcher=fetch, scope="all")
        self.assertEqual([row["topic_id"] for row in result["projects"]], ["11062", "11041"])

    def test_every_committee_pending_reply_counts_even_with_other_status_words(self):
        topic = {"url": "https://talk.nervos.org/t/light/11062", "post_stream": {"posts": [
            post(1, "akane", "2026-10-28", "Proposal"),
            post(2, "hanssen", "2026-10-29", "This proposal is Pending."),
            post(3, "yixiu.ckbfans.bit", "2026-10-30", "Pending; do not treat this as Rejection."),
            post(4, "akane", "2026-10-31", "I am Pending a response."),
        ]}}
        events = project_tracking._topic_events(topic)
        self.assertEqual([event["post_number"] for event in events if event["status"] == "Pending"], [2, 3])

    def test_long_topic_scans_past_overlapping_windows(self):
        def fetch(url):
            if url.endswith("/30.json"):
                return {"post_stream": {"posts": [{"id": 30, "post_number": 30}]}}
            return {"posts_count": 60, "post_stream": {"posts": [
                {"id": 1, "post_number": 1}, {"id": 60, "post_number": 60}]}}
        with patch.object(forum, "_request_json", side_effect=fetch):
            topic = forum.fetch_topic("11062")
        self.assertEqual([p["post_number"] for p in topic["post_stream"]["posts"]], [1, 30, 60])

    def test_read_only_funding_sync_uses_three_sources(self):
        project_tracking.save({"projects": [{"topic_id": "11062", "name": "Spark Program | Light dApp Kit",
                                             "current_status": "In-Progress", "actual_amount": "$900",
                                             "proposal_url": "https://talk.nervos.org/t/light/11062"},
                                            {"topic_id": "11041", "name": "Fiber WiFi",
                                             "current_status": "In-Progress"},
                                            {"topic_id": "11079", "name": "TapFiber",
                                             "current_status": "Closure"}]})
        def prop(value, kind="rich_text"):
            return {"type": kind, kind: [{"plain_text": value}]} if kind in {"title", "rich_text"} else {"type": kind, kind: value}
        class FakeNotion:
            def query(self, source):
                title = {"Name": prop("Light dApp Kit", "title")}
                return {"allocation": [{"properties": {**title, "Amount": prop("$1,000")}}],
                        "ongoing": [{"properties": {**title, "Distributed": prop("20%"), "Total": prop("$1,200")}},
                                    {"properties": {"Name": prop("Fiber WiFi", "title"), "Total": prop("$700")}}],
                        "completed": [{"properties": {**title, "Funding": prop("$800")}},
                                      {"properties": {"Name": prop("Fiber WiFi", "title"), "Funding": prop("$600")}},
                                      {"properties": {"Name": prop("TapFiber", "title"), "Funding": prop("$500")}}]}[source]
        config = {"data_sources": {"fund_allocations": "allocation", "ongoing_projects": "ongoing",
                                   "completed_projects": "completed"},
                  "properties": {"fund_allocations": {"title": "Name", "amount": "Amount"},
                                 "ongoing_projects": {"title": "Name", "distributed": "Distributed", "total_funding": "Total"},
                                 "completed_projects": {"title": "Name", "funding_amount": "Funding"}}}
        board = {"classified": {"in_progress": [{"id": 11062}], "completion": [{"id": 11041}],
                                "closure": [{"id": 11079}]}}
        value = project_tracking.sync_funding(FakeNotion(), config, board)
        rows = {row["topic_id"]: row for row in value["projects"]}
        self.assertEqual(rows["11062"]["proposed_amount"], "$1,000")
        self.assertEqual(rows["11062"]["proposed_amount_source"], "fund_allocations.amount")
        self.assertEqual(rows["11062"]["actual_amount"], "20%")
        self.assertEqual(rows["11062"]["actual_amount_source"], "ongoing_projects.distributed")
        self.assertEqual(rows["11041"]["actual_amount"], "$600")
        self.assertEqual(rows["11041"]["proposed_amount"], "$700")
        self.assertEqual(rows["11041"]["proposed_amount_source"], "ongoing_projects.total_funding")
        self.assertEqual(rows["11041"]["actual_amount_source"], "completed_projects.funding_amount")
        self.assertEqual(rows["11079"]["actual_amount"], "$500")
        self.assertEqual(value["funding_updated"], 3)

    def test_funding_read_failure_retains_existing_amount(self):
        project_tracking.save({"projects": [{"topic_id": "11062", "name": "Light dApp Kit",
                                             "proposed_amount": "$1,000"}]})
        class FailingNotion:
            def query(self, source):
                if source == "allocation":
                    raise RuntimeError("access denied")
                return []
        config = {"data_sources": {"fund_allocations": "allocation", "ongoing_projects": "ongoing",
                                   "completed_projects": "completed"},
                  "properties": {name: {"title": "Name", "amount": "Amount"} for name in
                                 ("fund_allocations", "ongoing_projects", "completed_projects")}}
        value = project_tracking.sync_funding(FailingNotion(), config)
        self.assertEqual(value["projects"][0]["proposed_amount"], "$1,000")
        self.assertEqual(value["funding_errors"][0]["source"], "fund_allocations")

    def test_completed_does_not_fallback_to_total_funding_or_ongoing(self):
        project_tracking.save({"projects": [{"topic_id": "11062", "name": "Light dApp Kit",
                                             "current_status": "Completion", "actual_amount": "$900"}]})
        class FakeNotion:
            def query(self, source):
                if source == "completed":
                    return [{"properties": {"Name": {"type": "title", "title": [{"plain_text": "Light dApp Kit"}]},
                                            "Total": {"type": "rich_text", "rich_text": [{"plain_text": "$1,000"}]}}}]
                return []
        config = {"data_sources": {"fund_allocations": "allocation", "ongoing_projects": "ongoing",
                                   "completed_projects": "completed"},
                  "properties": {"fund_allocations": {"title": "Name", "amount": "Amount"},
                                 "ongoing_projects": {"title": "Name", "distributed": "Distributed"},
                                 "completed_projects": {"title": "Name", "funding_amount": "Funding", "total_funding": "Total"}}}
        row = project_tracking.sync_funding(FakeNotion(), config)["projects"][0]
        self.assertEqual(row["actual_amount"], "")
        self.assertEqual(row["actual_amount_source"], "completed_projects.funding_amount")

    def test_update_schedule_uses_weekday_not_calendar_date(self):
        self.assertEqual(project_tracking._update_schedule("每周三帖内更新；2026年11月12日演示", "2026-11-05"), [
            {"text": "每周三帖内更新", "weekday": "周三"},
            {"text": "2026年11月12日演示", "weekday": ""},
        ])

    def test_manual_update_weekday_survives_forum_refresh(self):
        project_tracking.import_text("名称\t约定更新\nLight DApp Kit\t每周报告", self.board())
        project_tracking.set_update_weekday("11062", 0, "周五")
        value = project_tracking.refresh_from_forum(self.board(), fetcher=self.topic)
        self.assertEqual(value["projects"][0]["update_schedule"][0]["weekday"], "周五")
        with self.assertRaisesRegex(ValueError, "周一至周日"):
            project_tracking.set_update_weekday("11062", 0, "2026-11-06")

    def test_weekday_can_be_added_when_no_update_plan_exists(self):
        project_tracking.refresh_from_forum(self.board(), fetcher=self.topic)
        row = project_tracking.set_update_weekday("11062", 0, "周二")["projects"][0]
        self.assertEqual(row["update_schedule"], [{"text": "每周更新", "weekday": "周二"}])

    def test_unapproved_budget_is_not_replaced_by_notion(self):
        project_tracking.save({"projects": [{"topic_id": "11062", "name": "Light dApp Kit",
                                             "current_status": "Pending", "proposal_budget": "$1,300"}]})
        class FakeNotion:
            def query(self, source):
                return [{"properties": {"Name": {"type": "title", "title": [{"plain_text": "Light dApp Kit"}]},
                                        "Amount": {"type": "rich_text", "rich_text": [{"plain_text": "$1,000"}]}}}] if source == "allocation" else []
        config = {"data_sources": {"fund_allocations": "allocation", "ongoing_projects": "ongoing", "completed_projects": "completed"},
                  "properties": {key: {"title": "Name", "amount": "Amount"} for key in
                                 ("fund_allocations", "ongoing_projects", "completed_projects")}}
        row = project_tracking.sync_funding(FakeNotion(), config)["projects"][0]
        self.assertEqual(row["proposal_budget"], "$1,300")
        self.assertFalse(row.get("proposed_amount"))

    def test_pre_review_budget_is_from_first_post_only(self):
        topic = self.topic("11062")
        topic["post_stream"]["posts"][0]["cooked"] = "<p>Budget total: 12,000 CKB</p><p>Milestone: 5,000 CKB</p>"
        project_tracking.refresh_from_forum(self.board(), fetcher=lambda _: topic)
        row = project_tracking.load()["projects"][0]
        self.assertEqual(row["proposal_budget"], "12,000 CKB")
        self.assertEqual(row["current_status"], "Pending")

    def test_completion_demo_from_reply_without_demo_label(self):
        topic = self.topic("11062")
        topic["post_stream"]["posts"].append({"post_number": 7, "username": "akane", "created_at": "2026-11-03",
            "cooked": '<p>Final report: <a href="https://example.org/app">Try the result</a></p>'})
        board = self.board()
        board["classified"]["completion"] = board["classified"].pop("pending")
        row = project_tracking.refresh_from_forum(board, fetcher=lambda _: topic, scope="all")["projects"][0]
        self.assertEqual(row["demo_url"], "https://example.org/app")

    def test_unlabeled_unrelated_link_is_not_a_demo(self):
        topic = self.topic("11062")
        topic["post_stream"]["posts"][0]["cooked"] = '<p><a href="https://example.org/about">Homepage</a></p>'
        self.assertEqual(project_tracking._links_in_topic(topic, completed=True)[1], "")

    def test_text_import_matches_project_and_preserves_dates(self):
        text = "名称\t开始日期\t计划结项日期\t评审状态\nLight DApp Kit\t2026-11-01\t2026-11-29\tPending"
        result = project_tracking.import_text(text, self.board())
        row = result["projects"][0]
        self.assertEqual(row["topic_id"], "11062")
        self.assertEqual(row["start_date"], "2026-11-01")
        self.assertEqual(row["planned_completion"], "2026-11-29")

    def test_notion_zip_imports_nested_csv_and_ignores_unrelated_files(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("export/子页面/项目进度表.csv",
                                 "名称,开始日期,计划结项日期,评审状态\r\n"
                                 "Light DApp Kit,2026-11-01,2026-11-29,Pending\r\n")
            archive.writestr("export/子页面/其他表.csv", "Name,Note\r\nUnknown,irrelevant\r\n")
        result = project_tracking.import_notion_zip(buffer.getvalue(), self.board())
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["csv_scanned"], 2)
        self.assertEqual(result["projects"][0]["topic_id"], "11062")
        self.assertEqual(project_tracking.load()["projects"][0]["start_date"], "2026-11-01")

    def test_zip_rejects_invalid_and_without_csv(self):
        with self.assertRaisesRegex(ValueError, "有效"):
            project_tracking.import_notion_zip(b"not a ZIP", self.board())
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("page.md", "# Page")
        with self.assertRaisesRegex(ValueError, "没有 CSV"):
            project_tracking.import_notion_zip(buffer.getvalue(), self.board())


if __name__ == "__main__":
    unittest.main()
