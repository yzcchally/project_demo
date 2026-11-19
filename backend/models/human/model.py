"""Human types the command. The input is the same dialogue every other model will receive."""
import json
from pathlib import Path

from models.base import AgentModel, ModelCancelled
from models.decision import normalize_decision
from models.turn import Turn
from models.human.terminal import read_line
from models.registry import registry

BACKEND = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "work"


@registry.register("human")
class Human(AgentModel):
    def __init__(self, options: dict):
        super().__init__(options)
        self.work = WORK
        self.work.mkdir(parents=True, exist_ok=True)

    def complete(self, turn: Turn) -> dict:
        self._show(turn)
        while True:
            raw = read_line(self._cancel)
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

    def _show(self, turn: Turn):
        print("\n提示词:")
        for item in turn.prompt_lines():
            print(f"  第 {item['step']} 步  {item['text']}")
        print(f"本轮: 第 {turn.index} 轮，前面还有 {len(turn.previous)} 轮")
        for item in turn.previous:
            print(f"  第 {item.index} 轮  {self._words(item)}")
        if turn.commands:
            print("前面的命令:")
            for item in turn.commands:
                command = item.get("command")
                shown = "无指令" if command is None else json.dumps(command, ensure_ascii=False)
                progress = str(item.get("progress") or "").strip() or "无文字"
                print(f"  第 {item['step']} 步  {progress}  {shown}")
        if turn.extra:
            print("额外:")
            for item in turn.extra:
                print(f"  {self._extra(item)}")
        if turn.index == 1:
            print('第一次输出请加上 "name": "这段记录的名字"')
        print(f"坐标仍看: {BACKEND / 'last_screen.png'}  {turn.info.get('width')}x{turn.info.get('height')}")
        print(f"坐标: {BACKEND / 'pick.html'}  打开后看鼠标旁边的 x y")
        print('输入 {"command": {...} 或 null, "progress": "进度文字"}')

    def _extra(self, item: dict) -> str:
        step = item.get("step")
        kind = item.get("kind")
        if kind == "photo":
            if item.get("file"):
                return f"第 {step} 步  照片  {BACKEND / 'memory' / item['file']}"
            return f"第 {step} 步  照片  {item.get('error') or '没有照片'}"
        if kind == "url":
            opened = "已打开" if item.get("opened") else "未打开"
            return f"第 {step} 步  网址  {item.get('text') or item.get('error') or ''}  {opened}".rstrip()
        text = str(item.get("text") or item.get("error") or "").strip()
        label = "文字" if kind == "text" else str(kind or "内容")
        return f"第 {step} 步  {label}  {text}".rstrip()

    def _words(self, turn) -> str:
        text = turn.text.strip() or "无文字"
        command = turn.info.get("command")
        if command is None:
            return f"{text}  无指令"
        return f"{text}  {json.dumps(command, ensure_ascii=False)}"
