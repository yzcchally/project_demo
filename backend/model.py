"""Manual stand-in for the later vision or language model."""
import base64
import json
import sys
import threading
from pathlib import Path

from pydantic import ValidationError

from contract import parse_command
from memory_store import begin_round, remember_output

ROOT = Path(__file__).resolve().parent


class ModelCancelled(Exception):
    """Phone paused or stopped while the terminal was waiting for a line."""


class ManualModel:
    def __init__(self):
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def decide(self, task: str, screen: dict) -> dict:
        self._cancel.clear()
        png = base64.b64decode(screen["image_png_base64"])
        saved = begin_round(png)
        image_path = ROOT / "last_screen.png"
        image_path.write_bytes(png)
        print(f"\n任务: {task}")
        print(f"本轮: 第 {saved['index']} 轮，共 {saved['total']} 轮  {saved['folder']}")
        print(f"坐标仍看: {image_path}  {screen.get('width')}x{screen.get('height')}")
        print(f"坐标: {ROOT / 'pick.html'}  打开后看鼠标旁边的 x y")
        print('输入 {"command": {...} 或 null, "progress": "进度文字"}')
        while True:
            raw = self._read_line()
            if raw is None:
                raise ModelCancelled()
            if not raw:
                print("请输入 JSON")
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print("不是合法 JSON")
                continue
            if isinstance(data, dict) and "type" in data and "command" not in data:
                data = {"command": data, "progress": ""}
            if not isinstance(data, dict):
                print("需要一个 JSON 对象")
                continue
            progress = str(data.get("progress") or "").strip()
            command = data.get("command")
            if command in (None, "", {}):
                decision = {"command": None, "progress": progress or None}
                remember_output(saved["index"], decision)
                return decision
            try:
                parsed = parse_command(command)
            except ValidationError as error:
                print(f"指令不合法: {error.errors()[0]['msg']}")
                continue
            decision = {"command": parsed.model_dump(), "progress": progress or None}
            remember_output(saved["index"], decision)
            return decision

    def _read_line(self) -> str | None:
        print("模型输出> ", end="", flush=True)
        if sys.platform != "win32":
            if self._cancel.is_set():
                print()
                return None
            return input().strip()
        import msvcrt

        chars: list[str] = []
        while True:
            if self._cancel.is_set():
                print()
                return None
            if not msvcrt.kbhit():
                self._cancel.wait(0.05)
                continue
            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                msvcrt.getwch()
                continue
            if char == "\x03":
                print()
                raise KeyboardInterrupt
            if char in ("\r", "\n"):
                print()
                return "".join(chars).strip()
            if char in ("\b", "\x7f"):
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
                continue
            chars.append(char)
            print(char, end="", flush=True)
