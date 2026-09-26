from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import DATA_PATH, DB_PATH


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
  topic_id TEXT PRIMARY KEY, title TEXT NOT NULL, source_url TEXT NOT NULL,
  lifecycle TEXT NOT NULL, notion_page_id TEXT, last_forum_post_number INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, legacy_index INTEGER, topic_id TEXT,
  title TEXT NOT NULL, type TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
  deadline TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '',
  done INTEGER NOT NULL DEFAULT 0, automation_step TEXT, verification_gated INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, completed_at TEXT,
  UNIQUE(topic_id, automation_step)
);
CREATE TABLE IF NOT EXISTS sync_jobs (
  idempotency_key TEXT PRIMARY KEY, topic_id TEXT NOT NULL, workflow TEXT NOT NULL,
  status TEXT NOT NULL, payload_json TEXT, last_error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_steps (
  idempotency_key TEXT NOT NULL, step TEXT NOT NULL, status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0, remote_ids_json TEXT NOT NULL DEFAULT '[]',
  checksum TEXT, last_error TEXT, verified_at TEXT,
  PRIMARY KEY(idempotency_key, step),
  FOREIGN KEY(idempotency_key) REFERENCES sync_jobs(idempotency_key)
);
CREATE TABLE IF NOT EXISTS external_records (
  system TEXT NOT NULL, object_type TEXT NOT NULL, natural_key TEXT NOT NULL,
  remote_id TEXT NOT NULL, checksum TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY(system, object_type, natural_key)
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
  target TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL,
  forum_username TEXT, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, csrf_token TEXT NOT NULL,
  expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(username) REFERENCES users(username)
);
"""


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def checksum(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()
        self.import_legacy_once()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def import_legacy_once(self) -> None:
        if not DATA_PATH.exists():
            return
        with self.transaction() as conn:
            if conn.execute("SELECT 1 FROM meta WHERE key='legacy_imported_v1'").fetchone():
                return
            backup = DATA_PATH.with_name("spark_task_data.before_web.json")
            if not backup.exists():
                shutil.copy2(DATA_PATH, backup)
            data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            for index, task in enumerate(data.get("tasks", [])):
                conn.execute(
                    """INSERT OR IGNORE INTO tasks
                    (legacy_index, topic_id, title, type, project, deadline, note, link, done,
                     automation_step, verification_gated, created_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (index, str(task.get("nervos_topic_id", "")) or None, task.get("title", ""),
                     task.get("type", ""), task.get("project", ""), task.get("deadline", ""),
                     task.get("note", ""), task.get("link", ""), int(bool(task.get("done"))),
                     task.get("automation_step"), int(task.get("automation_step") in {"project_list", "fund_pool"}),
                     task.get("created", "") or utcnow(), task.get("completed_at"))
                )
            for topic_id, job in data.get("notion_sync_jobs", {}).items():
                key = job.get("idempotency_key") or f"nervos:{topic_id}:in-progress:v1"
                project_page_ids = job.get("steps", {}).get("project_list", {}).get("notion_page_ids", [])
                conn.execute("""INSERT OR IGNORE INTO projects
                    (topic_id,title,source_url,lifecycle,notion_page_id,last_forum_post_number,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (str(topic_id), job.get("project", ""), job.get("topic_url", ""), "in_progress",
                     project_page_ids[0] if project_page_ids else None, 0, utcnow(), utcnow()))
                conn.execute(
                    "INSERT OR IGNORE INTO sync_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (key, topic_id, "in_progress", job.get("status", "queued"), None, None,
                     job.get("created_at") or utcnow(), job.get("core_verified_at") or utcnow()))
                for step, state in job.get("steps", {}).items():
                    conn.execute(
                        """INSERT OR REPLACE INTO sync_steps
                        (idempotency_key, step, status, attempt_count, remote_ids_json, verified_at)
                        VALUES (?, ?, ?, 0, ?, ?)""",
                        (key, step, state.get("status", "pending"),
                         json.dumps(state.get("notion_page_ids", [])), state.get("verified_at"))
                    )
            conn.execute("INSERT INTO meta VALUES ('legacy_imported_v1', ?)", (utcnow(),))

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def audit(self, actor: str, action: str, target: str, details: Any) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT INTO audit_events(actor,action,target,details_json,created_at) VALUES(?,?,?,?,?)",
                         (actor, action, target, json.dumps(details, ensure_ascii=False), utcnow()))

    def get_meta(self, key: str) -> str | None:
        rows = self.query("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, value))

    def upsert_project(self, topic_id: str, title: str, source_url: str, lifecycle: str,
                       post_number: int = 0) -> bool:
        now = utcnow()
        with self.transaction() as conn:
            exists = conn.execute("SELECT 1 FROM projects WHERE topic_id=?", (topic_id,)).fetchone()
            conn.execute("""INSERT INTO projects(topic_id,title,source_url,lifecycle,last_forum_post_number,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(topic_id) DO UPDATE SET
                title=excluded.title,source_url=excluded.source_url,lifecycle=excluded.lifecycle,updated_at=excluded.updated_at""",
                (topic_id, title, source_url, lifecycle, post_number, now, now))
            return not bool(exists)

    def create_workflow_tasks(self, topic_id: str, project: str, links: dict[str, str]) -> int:
        definitions = [
            ("更新dashboard", "dashboard", False, links.get("dashboard", "")),
            ("更新项目列表", "project_list", True, links.get("project_list", "")),
            ("更新资金池状态", "fund_pool", True, links.get("fund_pool", "")),
            ("更新Spark联系人列表", "contacts", False, links.get("contacts", "")),
        ]
        count = 0
        with self.transaction() as conn:
            for title, step, gated, link in definitions:
                before = conn.total_changes
                conn.execute("""INSERT OR IGNORE INTO tasks
                    (topic_id,title,type,project,link,done,automation_step,verification_gated,created_at)
                    VALUES(?,?,?,?,?,0,?,?,?)""",
                    (topic_id, title, "更新 Notion Dashboard", project, link, step, int(gated), utcnow()))
                count += conn.total_changes - before
        return count

    def create_task(self, value: dict) -> dict:
        now = utcnow()
        with self.transaction() as conn:
            cursor = conn.execute("""INSERT INTO tasks
                (topic_id,title,type,project,deadline,note,link,done,automation_step,
                 verification_gated,created_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                str(value.get("topic_id") or "") or None,
                str(value.get("title") or "未命名任务"), str(value.get("type") or ""),
                str(value.get("project") or ""), str(value.get("deadline") or ""),
                str(value.get("note") or ""), str(value.get("link") or ""),
                int(bool(value.get("done"))), value.get("automation_step"),
                int(bool(value.get("verification_gated"))), now,
                now if value.get("done") else None,
            ))
            task_id = cursor.lastrowid
        self.export_legacy_status()
        return self.query("SELECT * FROM tasks WHERE id=?", (task_id,))[0]

    def update_task(self, task_id: int, value: dict) -> dict:
        rows = self.query("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            raise KeyError("任务不存在")
        current = rows[0]
        if current["verification_gated"] and "done" in value and bool(value["done"]) != bool(current["done"]):
            raise PermissionError("该任务只能在 Notion 回读验证成功后自动完成")
        allowed = ("title", "type", "project", "deadline", "note", "link", "done")
        changes = {key: value[key] for key in allowed if key in value}
        if not changes:
            return current
        if "done" in changes:
            changes["done"] = int(bool(changes["done"]))
            changes["completed_at"] = utcnow() if changes["done"] else None
        columns = ",".join(f"{key}=?" for key in changes)
        with self.transaction() as conn:
            conn.execute(f"UPDATE tasks SET {columns} WHERE id=?", (*changes.values(), task_id))
        self.export_legacy_status()
        return self.query("SELECT * FROM tasks WHERE id=?", (task_id,))[0]

    def delete_task(self, task_id: int) -> None:
        rows = self.query("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            raise KeyError("任务不存在")
        if rows[0]["verification_gated"]:
            raise PermissionError("Notion 自动化任务不能手动删除")
        with self.transaction() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self.export_legacy_status()

    def audit_events(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),))

    def queue_job(self, topic_id: str, workflow: str, payload: dict | None = None) -> str:
        suffix = ""
        if workflow == "weekly" and payload and payload.get("update_key"):
            suffix = ":" + str(payload["update_key"])
        key = f"nervos:{topic_id}:{workflow}{suffix}:v1"
        now = utcnow()
        steps = ({"in_progress": ["fund_pool", "project_list"],
                  "weekly": ["weekly_update"],
                  "completion": ["completion"],
                  "closure": ["completion"]})[workflow]
        with self.transaction() as conn:
            conn.execute("""INSERT INTO sync_jobs(idempotency_key,topic_id,workflow,status,payload_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO UPDATE SET
                payload_json=COALESCE(excluded.payload_json,sync_jobs.payload_json),updated_at=excluded.updated_at""",
                (key, topic_id, workflow, "queued", json.dumps(payload, ensure_ascii=False) if payload else None, now, now))
            for step in steps:
                conn.execute("INSERT OR IGNORE INTO sync_steps(idempotency_key,step,status) VALUES(?,?,?)",
                             (key, step, "pending"))
        return key

    def set_job_payload(self, key: str, payload: dict) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE sync_jobs SET payload_json=?,updated_at=? WHERE idempotency_key=?",
                         (json.dumps(payload, ensure_ascii=False), utcnow(), key))

    def set_job_status(self, key: str, status: str, error: str | None = None) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE sync_jobs SET status=?,last_error=?,updated_at=? WHERE idempotency_key=?",
                         (status, error, utcnow(), key))

    def set_step(self, key: str, step: str, status: str, remote_ids: list[str] | None = None,
                 error: str | None = None, value_checksum: str | None = None) -> None:
        now = utcnow()
        with self.transaction() as conn:
            conn.execute("""UPDATE sync_steps SET status=?,attempt_count=attempt_count+1,
                remote_ids_json=COALESCE(?,remote_ids_json),checksum=COALESCE(?,checksum),
                last_error=?,verified_at=? WHERE idempotency_key=? AND step=?""",
                (status, json.dumps(remote_ids) if remote_ids is not None else None, value_checksum,
                 error, now if status == "verified" else None, key, step))
            rows = conn.execute("SELECT status FROM sync_steps WHERE idempotency_key=?", (key,)).fetchall()
            job_status = "verified" if rows and all(r[0] == "verified" for r in rows) else (
                "conflict" if any(r[0] == "conflict" for r in rows) else "partial")
            conn.execute("UPDATE sync_jobs SET status=?,last_error=?,updated_at=? WHERE idempotency_key=?",
                         (job_status, error, now, key))
            job = conn.execute("SELECT topic_id,workflow FROM sync_jobs WHERE idempotency_key=?", (key,)).fetchone()
            if job and status == "verified":
                automation_step = step if step in {"project_list", "fund_pool"} else None
                if automation_step:
                    conn.execute("UPDATE tasks SET done=1,completed_at=? WHERE topic_id=? AND automation_step=?",
                                 (now, job[0], automation_step))
        self.export_legacy_status()

    def record_external(self, system: str, object_type: str, natural_key: str,
                        remote_id: str, value_checksum: str | None = None) -> None:
        with self.transaction() as conn:
            conn.execute("""INSERT INTO external_records(system,object_type,natural_key,remote_id,checksum,created_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(system,object_type,natural_key) DO UPDATE SET
                remote_id=excluded.remote_id,checksum=excluded.checksum""",
                (system, object_type, natural_key, remote_id, value_checksum, utcnow()))

    def get_external(self, system: str, object_type: str, natural_key: str) -> dict | None:
        rows = self.query("SELECT * FROM external_records WHERE system=? AND object_type=? AND natural_key=?",
                          (system, object_type, natural_key))
        return rows[0] if rows else None

    def set_project_notion(self, topic_id: str, page_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE projects SET notion_page_id=?,updated_at=? WHERE topic_id=?",
                         (page_id, utcnow(), topic_id))

    def advance_forum_cursor(self, topic_id: str, post_number: int) -> None:
        with self.transaction() as conn:
            conn.execute("""UPDATE projects SET last_forum_post_number=MAX(last_forum_post_number,?),
                updated_at=? WHERE topic_id=?""", (post_number, utcnow(), topic_id))

    def export_legacy_status(self) -> None:
        """Mirror browser changes and verified steps to the legacy desktop JSON."""
        if not DATA_PATH.exists():
            return
        with self._lock:
            data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            legacy_tasks = data.get("tasks", [])
            mirrored_tasks = []
            for row in self.query("SELECT * FROM tasks ORDER BY id"):
                index = row.get("legacy_index")
                base = dict(legacy_tasks[index]) if isinstance(index, int) and 0 <= index < len(legacy_tasks) else {}
                base.update({
                    "title": row["title"], "type": row["type"], "project": row["project"],
                    "deadline": row["deadline"], "note": row["note"], "link": row["link"],
                    "done": bool(row["done"]), "created": row["created_at"],
                    "completed_at": row.get("completed_at"),
                })
                if row.get("topic_id"):
                    base["nervos_topic_id"] = row["topic_id"]
                if row.get("automation_step"):
                    base["automation_step"] = row["automation_step"]
                mirrored_tasks.append(base)
            data["tasks"] = mirrored_tasks
            rows = self.query("""SELECT j.topic_id,s.step,s.status,s.remote_ids_json,s.verified_at
                FROM sync_jobs j JOIN sync_steps s USING(idempotency_key)
                WHERE s.step IN ('project_list','fund_pool')""")
            completed = set(str(x) for x in data.get("notion_tasks_created", []))
            jobs = data.setdefault("notion_sync_jobs", {})
            for row in rows:
                topic_id = str(row["topic_id"])
                job = jobs.setdefault(topic_id, {
                    "idempotency_key": f"nervos:{topic_id}:in-progress:v1",
                    "status": "queued", "steps": {}
                })
                state = job.setdefault("steps", {}).setdefault(row["step"], {})
                state.update({"status": row["status"],
                              "notion_page_ids": json.loads(row["remote_ids_json"] or "[]")})
                if row["verified_at"]:
                    state["verified_at"] = row["verified_at"]
                for task in data.get("tasks", []):
                    if (str(task.get("nervos_topic_id", "")) == topic_id and
                            task.get("automation_step") == row["step"] and row["status"] == "verified"):
                        task["done"] = True
                        task["completed_at"] = row["verified_at"]
                steps = job.get("steps", {})
                if all(steps.get(x, {}).get("status") == "verified" for x in ("project_list", "fund_pool")):
                    job["status"] = "core_verified"
                    completed.add(topic_id)
            data["notion_tasks_created"] = sorted(completed)
            temp = DATA_PATH.with_suffix(DATA_PATH.suffix + ".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(DATA_PATH)

    def list_state(self) -> dict:
        tasks = self.query("SELECT * FROM tasks ORDER BY done, created_at DESC, id DESC")
        jobs = self.query("SELECT * FROM sync_jobs ORDER BY updated_at DESC")
        steps = self.query("SELECT * FROM sync_steps ORDER BY idempotency_key, step")
        grouped: dict[str, list[dict]] = {}
        for step in steps:
            step["remote_ids"] = json.loads(step.pop("remote_ids_json") or "[]")
            grouped.setdefault(step["idempotency_key"], []).append(step)
        for job in jobs:
            job["steps"] = grouped.get(job["idempotency_key"], [])
            if job.get("payload_json"):
                job["payload"] = json.loads(job["payload_json"])
            job.pop("payload_json", None)
        return {"tasks": tasks, "jobs": jobs, "projects": self.query("SELECT * FROM projects ORDER BY updated_at DESC")}

    @staticmethod
    def hash_password(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240000)
        return f"pbkdf2_sha256$240000${salt.hex()}${digest.hex()}"

    @staticmethod
    def verify_password(password: str, encoded: str) -> bool:
        try:
            _, rounds, salt, expected = encoded.split("$", 3)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
            return hmac.compare_digest(actual.hex(), expected)
        except Exception:
            return False
