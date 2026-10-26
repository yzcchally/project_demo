"""Typed API for the controlled mobile workflow."""

import threading
import asyncio
import anyio
from typing import Literal

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from workflow import WorkflowError, get_engine, validate_url
from api.schemas import GoalRequest, HumanReply, QRReply, Contact
from api.events import hub

START_LOCK = threading.RLock()


class CreateTask(BaseModel):
    recipient: str = Field(max_length=254)
    subject: str = Field(max_length=150)
    body: str = Field(default="", max_length=10000)
    url: str = Field(default="", max_length=2048)
    source: Literal["qr", "manual", "text"] = "text"
    request_key: str = Field(min_length=8, max_length=80)


class Approval(BaseModel):
    draft_digest: str = Field(min_length=64, max_length=64)
    confirmed: bool = False
    approval_nonce: str = Field(default="", max_length=100)


class Control(BaseModel):
    action: Literal["pause", "resume", "cancel", "retry_prepare", "verify_only"]


def reply(function):
    try:
        task = function()
        return {"ok": True, "task": task, "message": task["message"] if task else "还没有邮件任务"}
    except WorkflowError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


def register(app):
    @app.post("/tasks")
    def create_task(body: CreateTask):
        import gmail_demo
        import recognize
        with START_LOCK:
            if recognize.running() or gmail_demo.running():
                raise HTTPException(409, "请先停止现有 AI 任务")
            return reply(lambda: get_engine().create(**body.model_dump()))

    @app.get("/tasks/latest")
    def latest_task():
        return reply(lambda: get_engine().latest())

    @app.get("/tasks/{task_id}")
    def task_status(task_id: str):
        return reply(lambda: get_engine().store.get(task_id))

    @app.get("/tasks/{task_id}/events")
    def task_events(task_id: str, after: int = 0):
        try:
            return {"ok": True, "events": get_engine().store.events(task_id, max(0, after))}
        except WorkflowError as error:
            raise HTTPException(404, str(error)) from None

    @app.post("/tasks/{task_id}/approve")
    def approve_task(task_id: str, body: Approval):
        return reply(lambda: get_engine().approve(task_id, body.draft_digest, body.confirmed, body.approval_nonce))

    @app.post("/tasks/{task_id}/control")
    def control_task(task_id: str, body: Control):
        return reply(lambda: get_engine().control(task_id, body.action))

    @app.post("/tasks/{task_id}/received")
    def confirm_delivery(task_id: str, body: Approval):
        return reply(lambda: get_engine().confirm_delivery(task_id, body.draft_digest, body.confirmed))

    @app.post("/url/check")
    def check_url(body: dict):
        try:
            return {"ok": True, "url": validate_url(str(body.get("url", ""))), "message": "网址格式检查通过"}
        except WorkflowError as error:
            raise HTTPException(422, str(error)) from None

    @app.post("/api/tasks")
    def create_goal(body: GoalRequest):
        import recognize
        import gmail_demo
        with START_LOCK:
            if recognize.running() or gmail_demo.running():
                raise HTTPException(409, "请先停止基础 AI 测试")
            return reply(lambda: get_engine().create_goal(**body.model_dump()))

    @app.post("/api/tasks/{task_id}/clarify")
    def clarify_task(task_id: str, body: HumanReply):
        return reply(lambda: get_engine().clarify(task_id, body.model_dump()))

    @app.post("/api/tasks/{task_id}/qr")
    def qr_result(task_id: str, body: QRReply):
        return reply(lambda: get_engine().submit_qr(task_id, body.url, body.scan_nonce))

    @app.patch("/api/tasks/{task_id}/draft")
    def edit_draft(task_id: str, body: HumanReply):
        return reply(lambda: get_engine().edit(task_id, body.model_dump(exclude_none=True, exclude_unset=True)))

    @app.get("/api/contacts")
    def contacts():
        return {"ok": True, "contacts": get_engine().contacts.all()}

    @app.put("/api/contacts")
    def put_contact(body: Contact):
        try:
            return {"ok": True, "contact": get_engine().contacts.put(body.alias, body.address), "message": "联系人已保存"}
        except WorkflowError as error:
            raise HTTPException(422, str(error)) from None

    @app.delete("/api/contacts/{alias}")
    def delete_contact(alias: str):
        get_engine().contacts.delete(alias)
        return {"ok": True, "message": "联系人已删除"}

    @app.get("/api/tasks/{task_id}/explain")
    def explain(task_id: str):
        task = get_engine().store.get(task_id)
        from explanation.templates import explain as describe_task
        return {"ok": True, "message": describe_task(task)}

    @app.post("/api/tasks/{task_id}/pause")
    def pause_task(task_id: str):
        return reply(lambda: get_engine().control(task_id, "pause"))

    @app.post("/api/tasks/{task_id}/resume")
    def resume_task(task_id: str):
        return reply(lambda: get_engine().control(task_id, "resume"))

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        return reply(lambda: get_engine().control(task_id, "cancel"))

    @app.get("/api/system/graph")
    def graph_structure():
        return {"ok": True, "mermaid": get_engine().graph.get_graph().draw_mermaid()}

    @app.get("/api/evaluation")
    def evaluation():
        from evaluation.metrics import build_report
        return {"ok": True, "report": build_report(get_engine().store)}

    for route, endpoint, methods in [
        ("/api/tasks/latest", latest_task, ["GET"]), ("/api/tasks/{task_id}", task_status, ["GET"]),
        ("/api/tasks/{task_id}/events", task_events, ["GET"]), ("/api/tasks/{task_id}/confirm", approve_task, ["POST"]),
        ("/api/tasks/{task_id}/control", control_task, ["POST"]), ("/api/tasks/{task_id}/received", confirm_delivery, ["POST"])]:
        app.add_api_route(route, endpoint, methods=methods)

    @app.websocket("/ws/tasks/{task_id}")
    async def task_stream(websocket: WebSocket, task_id: str, after: int = 0):
        from urllib.parse import urlsplit
        origin = websocket.headers.get("origin")
        if origin and urlsplit(origin).hostname not in {"127.0.0.1", "localhost", "testserver"}:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = hub.subscribe(task_id)
        cursor = max(0, after)
        event_wait = None
        receive_wait = None
        try:
            store = get_engine().store
            # A snapshot must describe the same durable revision as its replay
            # cursor. Otherwise an event committed between these reads could
            # advance the cursor while leaving the client on an older task.
            with store.lock:
                task = store.get(task_id)
                history = []
                while True:
                    batch = store.events(task_id, cursor)
                    history.extend(batch)
                    if not batch:
                        break
                    cursor = batch[-1]["seq"]
                    if len(batch) < 100:
                        break
            await websocket.send_json({"event_type": "snapshot", "task": task, "events": history, "cursor": cursor})
            event_wait = asyncio.create_task(queue.get())
            receive_wait = asyncio.create_task(websocket.receive())
            while True:
                done, _ = await asyncio.wait({event_wait, receive_wait}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
                if receive_wait in done:
                    incoming = receive_wait.result()
                    if incoming["type"] == "websocket.disconnect":
                        break
                    receive_wait = asyncio.create_task(websocket.receive())
                if event_wait in done:
                    message = event_wait.result()
                    event_wait = asyncio.create_task(queue.get())
                    if message["cursor"] > cursor:
                        cursor = message["cursor"]
                        await websocket.send_json(message)
                if not done:
                    await websocket.send_json({"event_type": "heartbeat", "cursor": cursor})
        except (WebSocketDisconnect, WorkflowError, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            hub.unsubscribe(task_id, queue)
            pending = [task for task in (event_wait, receive_wait) if task is not None]
            for task in pending:
                task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*pending, return_exceptions=True)
