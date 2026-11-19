"""Rolling summary of earlier steps. The vision call reads this file, not the raw history."""
import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from memory_store import current_record
from models.base import ModelCancelled
from models.deepseek.prompt import SUMMARY
from models.turn import Turn

WORK = Path(__file__).resolve().parent / "work"
FILE = WORK / "summary.txt"


def prepare_file() -> Path:
    """Create the temp file when the model is loaded. Later calls overwrite it."""
    WORK.mkdir(parents=True, exist_ok=True)
    if not FILE.exists():
        _write("", -1, "")
    return FILE


def refresh(llm, turn: Turn, cancel) -> str:
    """Fold anything newer than the file into one summary, then overwrite the file."""
    if cancel.is_set():
        raise ModelCancelled()
    prepare_file()
    record = current_record()
    stored_record, covered, text = _read()
    if stored_record != record or covered >= turn.index:
        covered = -1
        text = ""
    if covered >= turn.index - 1 and text:
        return text
    segment = _segment(turn, covered)
    if not segment:
        _write(record, turn.index - 1, text)
        return text
    if covered < 0 and turn.index == 1:
        return _save(record, 0, segment)
    print("\nDeepSeek 正在总结前文")
    reply = llm.invoke([
        SystemMessage(content=SUMMARY),
        HumanMessage(content=_request(text, segment)),
    ])
    if cancel.is_set():
        raise ModelCancelled()
    summary = _plain(reply.content).strip() or text
    return _save(record, turn.index - 1, summary)


def _request(previous: str, segment: str) -> str:
    old = previous.strip() or "（无）"
    return f"已有总结:\n{old}\n\n新的一段:\n{segment}\n\n合成一篇新的总结，只输出正文。"


def _segment(turn: Turn, after: int) -> str:
    lines = []
    for item in turn.prompt_lines():
        step = int(item.get("step") or 0)
        if step <= after:
            continue
        lines.append(f"第 {step} 步提示：{item['text']}")
    for item in turn.commands:
        step = int(item.get("step") or 0)
        if step <= after:
            continue
        command = item.get("command")
        progress = str(item.get("progress") or "").strip()
        if command is None and not progress:
            continue
        shown = "无指令" if command is None else json.dumps(command, ensure_ascii=False)
        lines.append(f"第 {step} 步已做：{progress or '无文字'} {shown}")
    for item in turn.extra:
        step = int(item.get("step") or 0)
        if step <= after:
            continue
        kind = item.get("kind") or "内容"
        body = item.get("text") or item.get("file") or item.get("error") or ""
        lines.append(f"第 {step} 步额外：{kind} {body}")
    return "\n".join(lines)


def _read() -> tuple[str, int, str]:
    if not FILE.exists():
        return "", -1, ""
    raw = FILE.read_text(encoding="utf-8")
    head, _, body = raw.partition("\n---\n")
    record = ""
    covered = -1
    for line in head.splitlines():
        if line.startswith("record:"):
            record = line.split(":", 1)[1].strip()
        elif line.startswith("covered:"):
            try:
                covered = int(line.split(":", 1)[1].strip())
            except ValueError:
                covered = -1
    return record, covered, body.strip()


def _save(record: str, covered: int, text: str) -> str:
    cleaned = text.strip()
    _write(record, covered, cleaned)
    print(f"前文总结已写入 {FILE}")
    if cleaned:
        print(cleaned)
    return cleaned


def _write(record: str, covered: int, text: str) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    body = f"record: {record}\ncovered: {covered}\n---\n{text.strip()}\n"
    temporary = FILE.with_suffix(".txt.tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(FILE)


def _plain(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return str(content or "")
