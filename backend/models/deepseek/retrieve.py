"""Pick earlier steps that match the task. This stays on disk and does not call the model."""
from pathlib import Path

from models.turn import Turn

FILE = Path(__file__).resolve().parent / "work" / "retrieved.txt"


def retrieve(turn: Turn) -> str:
    """Every earlier step, in one text block. No extra model call."""
    parts = []
    prompts = [f"第 {item['step']} 步提示：{item['text']}" for item in turn.prompt_lines()]
    parts.append("提示:\n" + ("\n".join(prompts) if prompts else "（没有提示）"))
    actions = _actions(turn)
    if actions:
        parts.append("已经做过:\n" + "\n".join(_line(item) for item in actions))
        stuck = _stuck(actions)
        if stuck:
            parts.append("走不通的路:\n" + "\n".join(stuck))
        written = _written(actions)
        if written:
            parts.append("已写入的进度（相同的不要再写）:\n" + "\n".join(written))
        guard = _guard(actions)
        if guard:
            parts.append(guard)
    got = _extras(turn)
    if got:
        parts.append("已经得到（直接使用，不要再向用户索要）:\n" + "\n".join(got))
    text = "\n".join(parts) if parts else "（没有更早的步骤）"
    _write(text)
    print("已整理前面的步骤")
    return text


def _actions(turn: Turn) -> list[dict]:
    rows = []
    for item in turn.commands:
        command = item.get("command")
        progress = str(item.get("progress") or "").strip()
        if command is None and not progress:
            continue
        rows.append({
            "step": int(item.get("step") or 0),
            "command": command if isinstance(command, dict) else None,
            "progress": progress,
        })
    rows.sort(key=lambda item: item["step"])
    return rows


def _line(item: dict) -> str:
    progress = f"，进度：{item['progress']}" if item["progress"] else ""
    return f"第 {item['step']} 步 {_brief(item['command'])}{progress}"


def _written(actions: list[dict]) -> list[str]:
    seen = []
    for item in actions:
        text = item["progress"]
        if text and text not in seen:
            seen.append(text)
    return seen


def _extras(turn: Turn) -> list[str]:
    lines = []
    for item in turn.extra:
        step = int(item.get("step") or 0)
        kind = str(item.get("kind") or "内容")
        body = str(item.get("text") or item.get("file") or item.get("error") or "").strip()
        if kind == "url" and body:
            lines.append(f"第 {step} 步扫到网址：{body}")
        elif kind == "photo" and body:
            lines.append(f"第 {step} 步拍了照片：{body}")
        elif kind == "text" and body:
            lines.append(f"第 {step} 步扫到文字：{body}")
        elif body:
            lines.append(f"第 {step} 步得到{kind}：{body}")
    return lines


POINTER = {"tap", "swipe", "long_press", "zoom"}


def guard(turn: Turn) -> dict:
    """How hard to stop aimless tapping. Computed here so the graph can reject the command."""
    actions = _actions(turn)
    streak = _pointer_streak(actions)
    return {
        "block_pointer": streak >= 4,
        "must_refuse": streak >= 8,
    }


def _pointer_streak(actions: list[dict]) -> int:
    streak = 0
    for item in reversed(actions):
        kind = str((item["command"] or {}).get("type") or "")
        if kind in POINTER and not item["progress"]:
            streak += 1
            continue
        break
    return streak


def _guard(actions: list[dict]) -> str:
    streak = _pointer_streak(actions)
    if streak >= 8:
        return "强制换路:\n连续点击或滑动已经很多，而且没有新进度。这一步只能输出 refuse，说明做不到的原因。"
    if streak >= 4:
        return "强制换路:\n最近连续在点击或滑动，没有新进度。禁止再输出 tap、swipe、long_press、zoom。改用 home、back、input_text、launch、ask 或 refuse。"
    return ""


def _stuck(actions: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    for item in actions:
        key = _key(item["command"])
        kind, _, extra = key.partition(":")
        if kind in ("wait", "tap", "swipe", "long_press", "zoom") and not extra:
            continue
        if kind == "wait":
            continue
        counts[key] = counts.get(key, 0) + 1
    notes = []
    for key, count in counts.items():
        if count >= 3:
            notes.append(f"{_label(key)} 已经试了 {count} 次。这条路不通，换一条路，例如回到桌面找别的软件。")
    return notes


def _key(command) -> str:
    if not isinstance(command, dict):
        return "none:"
    kind = str(command.get("type") or "")
    for name in ("package", "text", "direction", "instruction", "message", "question"):
        value = str(command.get(name) or "").strip()
        if value:
            return f"{kind}:{value[:40]}"
    return f"{kind}:"


def _label(key: str) -> str:
    kind, _, extra = key.partition(":")
    name = _NAMES.get(kind, kind or "操作")
    return f"{name} {extra}".strip()


_NAMES = {
    "tap": "点击",
    "long_press": "长按",
    "swipe": "滑动",
    "zoom": "缩放",
    "input_text": "输入",
    "back": "返回",
    "home": "回到桌面",
    "launch": "打开应用",
    "wait": "等待",
    "camera": "拍照",
    "scan": "扫码",
    "home_page": "翻桌面页",
    "user_action": "交给用户",
    "end": "结束并留在当前页面",
    "end_return": "结束并回到本应用",
}


def _brief(command) -> str:
    if not isinstance(command, dict):
        return "无指令"
    kind = _NAMES.get(str(command.get("type") or ""), str(command.get("type") or "操作"))
    for key in ("text", "instruction", "package", "message", "question", "direction"):
        value = str(command.get(key) or "").strip()
        if value:
            return f"{kind} {value}"
    return kind


def _write(text: str) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = FILE.with_suffix(".txt.tmp")
    temporary.write_text(text.strip() + "\n", encoding="utf-8")
    temporary.replace(FILE)
