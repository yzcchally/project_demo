import asyncio

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from contract import parse_command
from memory_store import clear as clear_memory
from memory_store import remember_output, remember_prompt
from models import ModelCancelled, build_turn, load_model
from phone_link import capture_screen, execute_command, link

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
    if phase == "paused":
        resume_when_idle = False
        link.retain_context = True
        link.end_source = None
        model.cancel()
        return
    if phase == "awaiting_user":
        resume_when_idle = False
        link.user_action_pending = True
        link.retain_context = True
        link.end_source = None
        model.cancel()
        return
    if phase == "stopped":
        resume_when_idle = False
        link.user_action_pending = False
        link.retain_context = False
        link.end_source = source if source in ("button", "command") else "button"
        if clear_memory():
            print("\n本轮记忆已删除")
        model.cancel()
        return
    if phase == "disconnected":
        model.cancel()
        return
    if previous == "awaiting_user" and phase == "operating" and source == "done" and link.socket is not None:
        link.user_action_pending = False
        if busy:
            resume_when_idle = True
        else:
            launch(link.socket, active_task)
        return
    if previous == "paused" and phase == "operating" and link.socket is not None:
        if link.user_action_pending:
            return
        if busy:
            resume_when_idle = True
        else:
            launch(link.socket, active_task)


link.on_phase = on_phase


@app.get("/health")
def health():
    return {"ok": True, "phone_connected": link.connected()}


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
            turn = build_turn(task, screen)
            if link.phase != "operating":
                break
            try:
                decision = await asyncio.to_thread(model.decide, turn)
            except ModelCancelled:
                break
            remember_output(turn.index, decision)
            if link.phase != "operating":
                break
            step = link.expect_step()
            await socket.send_json({
                "op": "result",
                "command": decision["command"],
                "progress": decision["progress"] or "",
            })
            await asyncio.wait_for(step, 30)
            if link.phase != "operating":
                break
            print("本步已执行，继续截图")
    except Exception as error:
        if link.phase not in ("paused", "stopped", "awaiting_user"):
            print(f"\n本轮停止: {error}")
    finally:
        phase = link.phase
        restart = resume_when_idle and phase == "operating" and link.socket is not None
        resume_when_idle = False
        busy = False
        if restart:
            launch(link.socket, active_task)
        elif phase == "paused":
            print("\n已暂停")
        elif phase == "awaiting_user":
            print("\n请用户操作")
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
            if message.get("op") == "start":
                task = str(message.get("task") or "")
                clear_memory()
                remember_prompt(task)
                launch(socket, task)
            else:
                link.receive(message)
    except WebSocketDisconnect:
        pass
    finally:
        if link.socket is socket:
            link.close()
