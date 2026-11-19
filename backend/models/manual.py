"""Human types the model output. Same packet as every other model."""
import json
import sys
from pathlib import Path

from models.base import AgentModel, ModelCancelled
from models.decision import normalize_decision
from models.registry import registry
from models.turn import Turn

BACKEND = Path(__file__).resolve().parents[1]


@registry.register("manual")
class ManualModel(AgentModel):
    def complete(self, turn: Turn) -> dict:
        print(f"坐标仍看: {BACKEND / 'last_screen.png'}  {turn.width}x{turn.height}")
        print(f"坐标: {BACKEND / 'pick.html'}  打开后看鼠标旁边的 x y")
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
            try:
                return normalize_decision(data)
            except ValueError as error:
                print(f"指令不合法: {error}")

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
