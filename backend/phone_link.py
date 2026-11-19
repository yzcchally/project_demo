"""One outstanding request to the connected phone."""
import asyncio

from fastapi import WebSocket


class PhoneLink:
    def __init__(self):
        self.socket: WebSocket | None = None
        self.phase = "operating"
        self.retain_context = True
        self.user_action_pending = False
        self.risk_pending = False
        self.answer_pending = False
        self.refused = False
        self.end_source: str | None = None
        self.on_phase = None
        self._pending: asyncio.Future | None = None
        self._step: asyncio.Future | None = None
        self._phase_wait: asyncio.Future | None = None

    def connected(self) -> bool:
        return self.socket is not None

    async def request(self, payload: dict) -> dict:
        if self.socket is None:
            raise RuntimeError("手机未连接")
        if self._pending is not None and not self._pending.done():
            raise RuntimeError("上一条请求还没有返回")
        future = asyncio.get_running_loop().create_future()
        self._pending = future
        try:
            await self.socket.send_json(payload)
            return await asyncio.wait_for(future, 15)
        finally:
            self._pending = None

    def expect_step(self) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        self._step = future
        return future

    async def wait_until_running(self):
        while self.phase == "paused" and self.socket is not None:
            future = asyncio.get_running_loop().create_future()
            self._phase_wait = future
            await future

    def receive(self, message: dict):
        op = message.get("op")
        if op == "phase":
            self._set_phase(str(message.get("phase") or ""), message.get("source"))
            return
        if op == "step_done":
            self._set_phase(str(message.get("phase") or self.phase), message.get("source"))
            step, self._step = self._step, None
            if step is not None and not step.done():
                step.set_result(message)
            return
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(message)

    def _set_phase(self, phase: str, source: str | None = None):
        if not phase or phase == self.phase:
            return
        previous = self.phase
        self.phase = phase
        waiting, self._phase_wait = self._phase_wait, None
        if waiting is not None and not waiting.done():
            waiting.set_result(phase)
        if self.on_phase is not None:
            self.on_phase(previous, phase, source)

    def close(self):
        previous = self.phase
        self.phase = "disconnected"
        pending, self._pending = self._pending, None
        step, self._step = self._step, None
        waiting, self._phase_wait = self._phase_wait, None
        for future in (pending, step, waiting):
            if future is not None and not future.done():
                future.set_exception(RuntimeError("手机连接已断开"))
        self.socket = None
        if previous != "disconnected" and self.on_phase is not None:
            self.on_phase(previous, "disconnected", None)


link = PhoneLink()


async def capture_screen() -> dict:
    """Send one request and return the phone's current screenshot."""
    message = await link.request({"op": "capture"})
    if not message.get("ok") or not message.get("image_png_base64"):
        raise RuntimeError(message.get("error") or "没有拿到截图")
    return {
        "width": message.get("width"),
        "height": message.get("height"),
        "image_png_base64": message["image_png_base64"],
    }


async def execute_command(command) -> dict:
    """Send one command and return whether the phone executed it."""
    message = await link.request({"op": "command", "command": command.model_dump()})
    if "ok" not in message:
        raise RuntimeError("手机没有返回执行结果")
    return {"ok": bool(message["ok"]), "error": message.get("error")}
