"""One folder per model round. Deleted when the user ends, kept while paused."""
import json
import shutil
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "memory"
_lock = threading.Lock()


def clear() -> bool:
    with _lock:
        had_files = ROOT.exists() and (any(ROOT.rglob("*.png")) or (ROOT / "prompt.txt").exists())
        if ROOT.exists():
            shutil.rmtree(ROOT)
        ROOT.mkdir(parents=True, exist_ok=True)
        return had_files


def remember_prompt(task: str):
    with _lock:
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


def remember_output(index: int, output: dict):
    with _lock:
        folder = ROOT / f"{index:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "output.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _write_index()


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
                "output": f"{folder.name}/output.json",
                "latest": folder == rounds[-1],
            }
            for folder in rounds
        ],
        "urls": _lines("urls.txt"),
        "notes": _lines("notes.txt"),
    }
    (ROOT / "index.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
