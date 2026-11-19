"""One folder per model round. Deleted when the user ends, kept while paused."""
import json
import shutil
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "memory"
_lock = threading.Lock()
_accepting = True


def clear() -> bool:
    global _accepting
    with _lock:
        _accepting = False
        had_files = ROOT.exists() and (any(ROOT.rglob("*.png")) or (ROOT / "prompt.txt").exists())
        if ROOT.exists():
            shutil.rmtree(ROOT)
        ROOT.mkdir(parents=True, exist_ok=True)
        return had_files


def remember_prompt(task: str):
    global _accepting
    with _lock:
        _accepting = True
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / "prompt.txt").write_text(task, encoding="utf-8")
        _write_index()


def begin_round(png: bytes) -> dict:
    with _lock:
        ROOT.mkdir(parents=True, exist_ok=True)
        index = _next_index()
        folder = ROOT / f"{index:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        screen = folder / "screen.png"
        screen.write_bytes(png)
        _write_index()
        return {"index": index, "total": index, "folder": folder, "screen": screen}


def remember_input(index: int, payload: dict):
    with _lock:
        folder = ROOT / f"{index:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "input.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _write_index()


def remember_output(index: int, output: dict):
    with _lock:
        if not _accepting:
            return
        folder = ROOT / f"{index:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "output.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _write_index()


def earlier_turns(before: int) -> list[dict]:
    """Rounds already finished before this index. Each one has its image, text, and info."""
    with _lock:
        turns = []
        for folder in _rounds():
            index = int(folder.name)
            if index >= before:
                continue
            screen = folder / "screen.png"
            output = _read_json(folder / "output.json")
            recorded = _read_json(folder / "input.json")
            info = {}
            if isinstance(recorded, dict):
                if isinstance(recorded.get("info"), dict):
                    info = dict(recorded["info"])
                else:
                    recorded_turns = recorded.get("turns") or []
                    if recorded_turns and isinstance(recorded_turns[-1], dict):
                        info = dict(recorded_turns[-1].get("info") or {})
            text = ""
            command = None
            if isinstance(output, dict):
                text = str(output.get("progress") or "")
                command = output.get("command")
            info["width"] = int(info.get("width") or 0)
            info["height"] = int(info.get("height") or 0)
            info["command"] = command
            info["latest"] = False
            turns.append({
                "index": index,
                "image_png": screen.read_bytes() if screen.exists() else b"",
                "image_file": f"{folder.name}/screen.png",
                "text": text,
                "info": info,
            })
        return turns


def memory_notes() -> dict:
    with _lock:
        return {"urls": _lines("urls.txt"), "notes": _lines("notes.txt")}


def earlier_commands(before: int) -> list[dict]:
    """Every earlier model output: the command and the progress text, including empty ones."""
    with _lock:
        rows = []
        for folder in _rounds():
            index = int(folder.name)
            if index >= before:
                continue
            output = _read_json(folder / "output.json")
            if not isinstance(output, dict):
                output = {}
            progress = output.get("progress")
            row = {
                "step": index,
                "command": output.get("command"),
                "progress": "" if progress in (None, "null") else str(progress),
            }
            name = str(output.get("name") or "").strip()
            if name:
                row["name"] = name
            rows.append(row)
        return rows


def bind_record(record_id: str):
    with _lock:
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / "record_id.txt").write_text(record_id, encoding="utf-8")


def current_record() -> str:
    with _lock:
        file = ROOT / "record_id.txt"
        if not file.exists():
            return ""
        return file.read_text(encoding="utf-8").strip()


def read_prompt() -> str:
    with _lock:
        file = ROOT / "prompt.txt"
        if not file.exists():
            return ""
        return file.read_text(encoding="utf-8")


def replace_additions(items: list[dict]):
    with _lock:
        ROOT.mkdir(parents=True, exist_ok=True)
        _write_additions(items)
        _write_index()


def latest_step() -> int:
    with _lock:
        rounds = _rounds()
        if not rounds:
            return 0
        return int(rounds[-1].name)


def additions_before(index: int) -> list[dict]:
    with _lock:
        rows = []
        for item in _additions():
            text = str(item.get("text") or "").strip()
            if not text or int(item.get("after_step") or 0) >= index:
                continue
            rows.append({"step": int(item.get("after_step") or 0), "text": text})
        return rows


def remember_addition(after_step: int, progress: int, text: str):
    line = text.strip()
    if not line:
        return
    with _lock:
        ROOT.mkdir(parents=True, exist_ok=True)
        items = _additions()
        items.append({"after_step": after_step, "progress": progress, "text": line})
        _write_additions(items)
        _write_index()


def truncate_after(step: int):
    """Drop rounds and added prompts that happened after the chosen step."""
    with _lock:
        if not ROOT.exists():
            return
        for folder in list(_rounds()):
            if int(folder.name) > step:
                shutil.rmtree(folder)
        _write_additions([item for item in _additions() if int(item.get("after_step") or 0) <= step])
        _write_extras([item for item in _extras() if int(item.get("step") or 0) <= step])
        _write_index()


def earlier_extras(before: int) -> list[dict]:
    """Extra results from earlier rounds. Not part of the prompt or the screenshot."""
    with _lock:
        rows = []
        for item in _extras():
            if int(item.get("step") or 0) >= before:
                continue
            rows.append(dict(item))
        return rows


def remember_extra(step: int, item: dict):
    kind = str(item.get("kind") or "").strip()
    if not kind:
        return
    stored = {"step": step, "kind": kind}
    for key, value in item.items():
        if key in ("step", "kind") or str(key).endswith("base64"):
            continue
        if isinstance(value, str) and len(value) > 2000:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            stored[key] = value
    with _lock:
        if not _accepting:
            return
        _put_extra(stored)
        _write_index()


def save_photo(step: int, data: bytes) -> str:
    with _lock:
        if not _accepting:
            return ""
        folder = ROOT / f"{step:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "photo.jpg").write_bytes(data)
        relative = f"{step:03d}/photo.jpg"
        _put_extra({"step": step, "kind": "photo", "file": relative})
        _write_index()
        return relative


def _put_extra(item: dict):
    kind = item.get("kind")
    step = int(item.get("step") or 0)
    items = [row for row in _extras() if not (int(row.get("step") or 0) == step and row.get("kind") == kind)]
    items.append(item)
    items.sort(key=lambda row: int(row.get("step") or 0))
    _write_extras(items)


def _extras() -> list[dict]:
    data = _read_json(ROOT / "extras.json")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _write_extras(items: list[dict]):
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "extras.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_url(url: str):
    _append("urls.txt", url)


def remember_note(text: str):
    _append("notes.txt", text)


def _append(name: str, text: str):
    line = text.strip()
    if not line:
        return
    with _lock:
        ROOT.mkdir(parents=True, exist_ok=True)
        file = ROOT / name
        previous = file.read_text(encoding="utf-8") if file.exists() else ""
        file.write_text(previous + line + "\n", encoding="utf-8")
        _write_index()


def _next_index() -> int:
    numbers = []
    for path in ROOT.iterdir():
        if path.is_dir() and path.name.isdigit():
            numbers.append(int(path.name))
    return max(numbers, default=0) + 1


def _additions() -> list[dict]:
    data = _read_json(ROOT / "additions.json")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _write_additions(items: list[dict]):
    (ROOT / "additions.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _lines(name: str) -> list[str]:
    file = ROOT / name
    if not file.exists():
        return []
    return [line for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rounds() -> list[Path]:
    if not ROOT.exists():
        return []
    return sorted((path for path in ROOT.iterdir() if path.is_dir() and path.name.isdigit()), key=lambda path: path.name)


def _write_index():
    rounds = _rounds()
    prompt = (ROOT / "prompt.txt").read_text(encoding="utf-8") if (ROOT / "prompt.txt").exists() else ""
    payload = {
        "prompt": prompt,
        "rounds": [
            {
                "index": int(folder.name),
                "dir": folder.name,
                "screen": f"{folder.name}/screen.png",
                "input": f"{folder.name}/input.json",
                "output": f"{folder.name}/output.json",
                "latest": folder == rounds[-1],
            }
            for folder in rounds
        ],
        "urls": _lines("urls.txt"),
        "notes": _lines("notes.txt"),
        "additions": _additions(),
        "extras": _extras(),
    }
    (ROOT / "index.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
