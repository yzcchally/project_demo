import asyncio
import base64

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from contract import parse_command
from memory_store import clear as clear_memory
from memory_store import current_record, latest_step, read_prompt, remember_addition, remember_extra, remember_note, remember_prompt, save_photo, truncate_after
from models import ModelCancelled, build_turn, load_model
from phone_link import capture_screen, execute_command, link
import records_store

app = FastAPI(title="Screen Agent", version="1.0.0")
model = load_model()
busy = False
active_task = ""
resume_when_idle = False


def launch(socket: WebSocket, task: str):
    global busy, active_task
    if busy or socket is None:
        return
    busy = True
    active_task = task
    asyncio.create_task(run_turn(socket, task))


def on_phase(previous: str, phase: str, source: str | None = None):
    global resume_when_idle
    if phase in ("paused", "revising"):
        resume_when_idle = False
        link.retain_context = True
        link.end_source = None
        if phase == "revising":
            link.user_action_pending = False
            link.risk_pending = False
        model.cancel()
        return
    if phase == "awaiting_user":
        resume_when_idle = False
        link.user_action_pending = True
        link.risk_pending = False
        link.retain_context = True
        link.end_source = None
        model.cancel()
        return
    if phase == "awaiting_risk":
        resume_when_idle = False
        link.risk_pending = True
        link.user_action_pending = False
        link.retain_context = True
        link.end_source = None
        model.cancel()
        return
    if phase == "awaiting_answer":
        resume_when_idle = False
        link.answer_pending = True
        link.retain_context = True
        link.end_source = None
        model.cancel()
        return
    if phase == "refused":
        resume_when_idle = False
        link.refused = True
        link.retain_context = True
        link.end_source = None
        model.cancel()
        return
    if phase == "stopped":
        resume_when_idle = False
        link.user_action_pending = False
        link.risk_pending = False
        link.answer_pending = False
        link.refused = False
        link.retain_context = False
        link.end_source = source if source in ("button", "command") else "button"
        if clear_memory():
            print("\n本轮记忆已删除")
        model.cancel()
        return
    if phase == "disconnected":
        model.cancel()
        return
    if link.refused or link.socket is None:
        return
    if previous == "awaiting_user" and phase == "operating" and source == "done":
        link.user_action_pending = False
        if busy:
            resume_when_idle = True
        else:
            launch(link.socket, active_task)
        return
    if previous == "awaiting_risk" and phase == "operating" and source == "continue":
        link.risk_pending = False
        if busy:
            resume_when_idle = True
        else:
            launch(link.socket, active_task)
        return
    if previous == "awaiting_answer" and phase == "operating" and source == "answer":
        link.answer_pending = False
        if busy:
            resume_when_idle = True
        else:
            launch(link.socket, active_task)
        return
    if previous == "revising" and phase == "operating" and source == "revise":
        if busy:
            resume_when_idle = True
        else:
            launch(link.socket, active_task)
        return
    if previous == "paused" and phase == "operating":
        if link.user_action_pending or link.risk_pending or link.answer_pending:
            return
        if busy:
            resume_when_idle = True
        else:
            launch(link.socket, active_task)


def apply_revision(message: dict):
    keep = message.get("keep_step")
    addition = str(message.get("addition") or "").strip()
    shown = message.get("progress")
    progress = int(shown) if shown not in (None, "") else None
    if keep is None or keep == "":
        after = latest_step()
    else:
        after = int(keep)
        truncate_after(after)
        print(f"\n进度已退回到第 {progress if progress is not None else after} 条")
    if addition:
        remember_addition(after, progress if progress is not None else after, addition)
        print(f"\n已追加提示: {addition}")


def store_step_extra(step: int, message: dict):
    """Keep a photo or scanned value for the next model input. It is not a prompt."""
    extra = message.get("extra")
    if not isinstance(extra, dict):
        return
    kind = str(extra.get("kind") or "").strip()
    if kind == "photo" and extra.get("image_jpeg_base64"):
        try:
            data = base64.b64decode(extra["image_jpeg_base64"])
        except Exception:
            remember_extra(step, {"kind": "photo", "error": "照片数据无效"})
            print("\n照片数据无效")
            return
        if not data:
            remember_extra(step, {"kind": "photo", "error": "没拍到照片"})
            return
        relative = save_photo(step, data)
        if relative:
            print(f"\n已保存照片 {relative}")
        return
    if not kind:
        return
    stored = {"kind": kind}
    for key, value in extra.items():
        if key == "kind" or str(key).endswith("base64"):
            continue
        if isinstance(value, str) and len(value) > 2000:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            stored[key] = value
    remember_extra(step, stored)
    if kind == "url" and stored.get("text"):
        print(f"\n已记录网址 {stored['text']}")
    elif stored.get("error"):
        print(f"\n额外内容没有拿到: {stored['error']}")


def apply_answer(message: dict):
    text = str(message.get("text") or "").strip()
    if not text:
        return
    shown = message.get("progress")
    progress = int(shown) if shown not in (None, "") else 0
    step = message.get("step")
    after = latest_step() if step in (None, "") else int(step)
    remember_addition(after, progress, text)
    print(f"\n已记录提问:\n{text}")


link.on_phase = on_phase


@app.get("/health")
def health():
    return {"ok": True, "phone_connected": link.connected()}


@app.get("/records")
def records():
    return {"records": records_store.list_records()}


@app.post("/records")
def new_record():
    return records_store.create()


@app.get("/records/{record_id}")
def record_detail(record_id: str):
    try:
        return records_store.detail(record_id)
    except ValueError:
        raise HTTPException(404, "记录不存在") from None


@app.delete("/records/{record_id}")
def remove_record(record_id: str):
    try:
        records_store.delete(record_id)
    except ValueError:
        raise HTTPException(404, "记录不存在") from None
    return {"ok": True}


@app.post("/capture")
async def capture():
    try:
        return {"ok": True, **await capture_screen()}
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from None


@app.post("/execute")
async def execute(body: dict):
    try:
        command = parse_command(body)
    except ValidationError as error:
        raise HTTPException(422, "指令不在定义内") from error
    try:
        return await execute_command(command)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from None


async def run_turn(socket: WebSocket, task: str):
    global busy, resume_when_idle
    link.phase = "operating"
    link.retain_context = True
    link.end_source = None
    try:
        while link.connected() and link.phase == "operating":
            screen = await capture_screen()
            if link.phase != "operating":
                break
            try:
                turn = build_turn(task, screen)
                decision = await asyncio.to_thread(model.decide, turn)
            except ModelCancelled:
                break
            if link.phase != "operating":
                break
            title = str(decision.get("name") or "").strip()
            record_id = current_record()
            if title and turn.index == 1 and record_id:
                named = records_store.name_record(record_id, title)
                if named:
                    await socket.send_json({"op": "record_name", "name": named})
            step = link.expect_step()
            await socket.send_json({
                "op": "result",
                "command": decision["command"],
                "progress": decision["progress"] or "",
                "step": turn.index,
            })
            kind = decision["command"].get("type") if isinstance(decision.get("command"), dict) else ""
            timeout = 120 if kind in ("camera", "scan") else 30
            finished = await asyncio.wait_for(step, timeout)
            store_step_extra(turn.index, finished if isinstance(finished, dict) else {})
            if link.phase != "operating":
                break
            print("本步已执行，继续截图")
    except Exception as error:
        if link.phase not in ("paused", "stopped", "awaiting_user", "revising", "awaiting_risk", "awaiting_answer", "refused"):
            print(f"\n本轮停止: {error}")
    finally:
        phase = link.phase
        restart = resume_when_idle and phase == "operating" and link.socket is not None and not link.refused
        resume_when_idle = False
        busy = False
        if restart:
            launch(link.socket, active_task)
        elif phase == "paused":
            print("\n已暂停")
        elif phase == "revising":
            print("\n正在修改指令")
        elif phase == "awaiting_user":
            print("\n请用户操作")
        elif phase == "awaiting_risk":
            print("\n等待确认风险")
        elif phase == "awaiting_answer":
            print("\n等待用户回答")
        elif phase == "refused":
            print("\n任务无法完成")
        elif phase == "stopped" and link.end_source == "command":
            print("\n已结束（指令）")
        elif phase == "stopped":
            print("\n已结束（按钮）")
        elif phase == "disconnected":
            print("\n连接已断开")


@app.websocket("/phone")
async def phone(socket: WebSocket):
    await socket.accept()
    link.socket = socket
    try:
        while True:
            message = await socket.receive_json()
            op = message.get("op")
            if op == "start":
                task = str(message.get("task") or "")
                record = str(message.get("record") or "").strip()
                if not record:
                    record = records_store.create()["id"]
                link.refused = False
                link.risk_pending = False
                link.answer_pending = False
                link.user_action_pending = False
                clear_memory()
                try:
                    records_store.bind(record)
                except ValueError:
                    record = records_store.create()["id"]
                    records_store.bind(record)
                remember_prompt(task)
                launch(socket, task)
            elif op == "archive":
                additions = message.get("additions")
                progress = message.get("progress")
                task = str(message.get("task") or active_task)
                try:
                    saved = records_store.archive(
                        task,
                        additions if isinstance(additions, list) else [],
                        progress if isinstance(progress, list) else [],
                    )
                    print(f"\n已保存记录 {saved['name']}，第 {saved['dialogue']} 轮对话，工作记忆已清空")
                except Exception as error:
                    print(f"\n保存记录失败: {error}")
            elif op == "resume":
                record = str(message.get("record") or "").strip()
                extra = str(message.get("task") or "").strip()
                link.refused = False
                link.risk_pending = False
                link.answer_pending = False
                link.user_action_pending = False
                try:
                    last = records_store.restore(record)
                except ValueError:
                    print("\n记录不存在，无法继续")
                    continue
                if extra:
                    remember_addition(last, 0, extra)
                launch(socket, read_prompt() or extra)
            elif op == "revise":
                apply_revision(message)
            elif op == "answer":
                apply_answer(message)
            elif op == "note":
                remember_note(str(message.get("text") or ""))
            else:
                link.receive(message)
    except WebSocketDisconnect:
        pass
    finally:
        if link.socket is socket:
            link.close()
