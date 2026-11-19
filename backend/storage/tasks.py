import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from domain import WorkflowError, now
from api.events import hub

class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.db = directory / "tasks.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_task_seq ON events(task_id, seq);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=10)
        try:
            c.execute("PRAGMA synchronous=FULL")
            with c:
                yield c
        finally:
            c.close()

    def all(self) -> list[dict]:
        with self.lock, self.connect() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT data FROM tasks ORDER BY rowid DESC")]

    def get(self, task_id: str) -> dict:
        with self.lock, self.connect() as c:
            r = c.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        if r is None:
            raise WorkflowError("没有找到这个任务")
        return json.loads(r[0])

    def save(self, task: dict, message: str) -> dict:
        if task.get("assistance_mode") == "basic":
            message = {"understanding": "正在处理需求", "opening_webpage": "正在打开网页", "preparing_draft": "正在准备邮件", "awaiting_qr": "请扫描二维码", "awaiting_confirmation": "请核对草稿并确认发送"}.get(task["state"], message)
        task["updated_at"] = now()
        task["message"] = message
        event = {"time": task["updated_at"], "state": task["state"], "message": message,
                 "send_attempts": task["send_attempts"], "evidence": {key: bool(task.get(key)) for key in ("webpage_verified", "draft_verified", "sent_record_verified", "delivery_confirmed", "route_verified")}}
        if task.get("task_type") == "plan_transit":
            event.update(task_type="plan_transit", query_id=task.get("query_id"), route_classification=(task.get("transit_result") or {}).get("classification"),
                         transit_executor=task.get("transit_executor", "rules"),
                         dingtalk_status=task.get("dingtalk_status", "not_requested"),
                         dingtalk_send_attempts=task.get("dingtalk_send_attempts", 0),
                         transit_navigation_attempts=task.get("transit_navigation_attempts", 0),
                         transit_navigation_status=task.get("transit_navigation_status"))
            if task.get("transit_executor") == "autoglm":
                event.update(transit_agent_step=task.get("transit_agent_step", 0),
                             transit_agent_last_action=task.get("transit_agent_last_action"))
        with self.lock, self.connect() as c:
            c.execute("INSERT INTO tasks VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                      (task["id"], task["request_key"], json.dumps(task, ensure_ascii=False)))
            c.execute("INSERT INTO events(task_id,data) VALUES(?,?)", (task["id"], json.dumps(event, ensure_ascii=False)))
            event["seq"] = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        folder = self.directory / task["id"]
        folder.mkdir(exist_ok=True)
        with (folder / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        hub.publish(task, event)
        return task

    def events(self, task_id: str, after: int = 0) -> list[dict]:
        self.get(task_id)
        with self.lock, self.connect() as c:
            return [{"seq": r[0], **json.loads(r[1])} for r in c.execute(
                "SELECT seq,data FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 100", (task_id, after))]

    def history_step(self, task_id: str, seq: int) -> tuple[dict, int]:
        with self.lock, self.connect() as c:
            row = c.execute(
                "SELECT data FROM events WHERE task_id=? AND seq=?", (task_id, seq)
            ).fetchone()
            if row is None:
                raise WorkflowError("没有找到这一步的记录")
            revision = c.execute("SELECT MAX(seq) FROM events WHERE task_id=?", (task_id,)).fetchone()[0]
        return {"seq": seq, **json.loads(row[0])}, revision
