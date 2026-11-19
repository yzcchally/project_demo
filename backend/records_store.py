"""Saved conversations. Working memory is cleared on end; the record keeps the copy."""
import json
import shutil
from pathlib import Path

import memory_store

ROOT = Path(__file__).resolve().parent / "records"


def create() -> dict:
    ROOT.mkdir(parents=True, exist_ok=True)
    number = _next_id()
    record_id = f"{number:03d}"
    folder = ROOT / record_id
    (folder / "dialogues").mkdir(parents=True, exist_ok=True)
    meta = {"id": record_id, "name": "未命名"}
    _write_meta(folder, meta)
    return meta


def list_records() -> list[dict]:
    if not ROOT.exists():
        return []
    rows = []
    for folder in sorted((path for path in ROOT.iterdir() if path.is_dir() and path.name.isdigit()), key=lambda path: path.name, reverse=True):
        meta = _read_meta(folder)
        rows.append({"id": folder.name, "name": str(meta.get("name") or "未命名")})
    return rows


def detail(record_id: str) -> dict:
    folder = _folder(record_id)
    meta = _read_meta(folder)
    dialogues = []
    last_step = 0
    for dialogue in _dialogues(folder):
        summary = _read_json(dialogue / "summary.json")
        if not isinstance(summary, dict):
            summary = {}
        for round_dir in _round_dirs(dialogue / "memory"):
            last_step = max(last_step, int(round_dir.name))
        dialogues.append({
            "index": int(dialogue.name),
            "prompt": str(summary.get("prompt") or ""),
            "additions": summary.get("additions") if isinstance(summary.get("additions"), list) else [],
            "progress": summary.get("progress") if isinstance(summary.get("progress"), list) else [],
        })
    return {
        "id": folder.name,
        "name": str(meta.get("name") or "未命名"),
        "last_step": last_step,
        "dialogues": dialogues,
    }


def delete(record_id: str) -> None:
    folder = _folder(record_id)
    shutil.rmtree(folder)


def name_record(record_id: str, name: str) -> str | None:
    title = name.strip()
    if not title:
        return None
    folder = _folder(record_id)
    meta = _read_meta(folder)
    if str(meta.get("name") or "未命名") not in ("", "未命名"):
        return None
    meta["name"] = title[:40]
    _write_meta(folder, meta)
    return str(meta["name"])


def bind(record_id: str) -> None:
    _folder(record_id)
    memory_store.bind_record(record_id)


def active_id() -> str:
    return memory_store.current_record()


def archive(task: str, additions: list, progress: list) -> dict:
    record_id = active_id()
    if not record_id or not (ROOT / record_id).is_dir():
        record_id = create()["id"]
        memory_store.bind_record(record_id)
    folder = ROOT / record_id
    dialogues = folder / "dialogues"
    dialogues.mkdir(parents=True, exist_ok=True)
    number = _next_dialogue(dialogues)
    dest = dialogues / str(number)
    dest.mkdir()
    if memory_store.ROOT.exists():
        shutil.copytree(memory_store.ROOT, dest / "memory")
    prompt = task.strip() or memory_store.read_prompt()
    summary = {"prompt": prompt, "additions": additions, "progress": progress}
    (dest / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    memory_store.clear()
    meta = _read_meta(folder)
    return {"id": record_id, "name": str(meta.get("name") or "未命名"), "dialogue": number}


def restore(record_id: str) -> int:
    """Put the latest saved dialogue back into working memory, keeping the old step numbers."""
    folder = _folder(record_id)
    dialogues = _dialogues(folder)
    memory_store.clear()
    memory_store.bind_record(record_id)
    if not dialogues:
        return 0
    snapshot = dialogues[-1] / "memory"
    last_step = 0
    if snapshot.exists():
        for child in snapshot.iterdir():
            target = memory_store.ROOT / child.name
            if child.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(child, target)
                if child.name.isdigit():
                    last_step = max(last_step, int(child.name))
            elif child.is_file():
                shutil.copy2(child, target)
    memory_store.bind_record(record_id)
    memory_store.remember_prompt(memory_store.read_prompt())
    return last_step


def _folder(record_id: str) -> Path:
    if not record_id.isdigit():
        raise ValueError("记录不存在")
    folder = ROOT / record_id
    if not folder.is_dir():
        raise ValueError("记录不存在")
    return folder


def _next_id() -> int:
    numbers = [int(path.name) for path in ROOT.iterdir() if path.is_dir() and path.name.isdigit()] if ROOT.exists() else []
    return max(numbers, default=0) + 1


def _next_dialogue(folder: Path) -> int:
    numbers = [int(path.name) for path in folder.iterdir() if path.is_dir() and path.name.isdigit()]
    return max(numbers, default=0) + 1


def _dialogues(folder: Path) -> list[Path]:
    parent = folder / "dialogues"
    if not parent.exists():
        return []
    return sorted((path for path in parent.iterdir() if path.is_dir() and path.name.isdigit()), key=lambda path: int(path.name))


def _round_dirs(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted((path for path in folder.iterdir() if path.is_dir() and path.name.isdigit()), key=lambda path: path.name)


def _read_meta(folder: Path) -> dict:
    data = _read_json(folder / "meta.json")
    return data if isinstance(data, dict) else {"id": folder.name, "name": "未命名"}


def _write_meta(folder: Path, meta: dict) -> None:
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
