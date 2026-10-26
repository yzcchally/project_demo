"""Preview expiry of old screen evidence. --apply explicitly opts into deletion."""
import argparse
import json
import sqlite3
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent / ".run" / "workflows"
parser = argparse.ArgumentParser()
parser.add_argument("--days", type=int, default=30)
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
if args.days < 1:
    parser.error("--days must be at least 1")
cutoff = time.time() - args.days * 86400
selected = []
with sqlite3.connect(root / "tasks.sqlite3") as db:
    for row in db.execute("SELECT data FROM tasks"):
        task = json.loads(row[0])
        if task["state"] not in {"completed", "failed", "cancelled", "outcome_unknown", "interrupted"}:
            continue
        for path in (root / task["id"]).glob("screen-*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                selected.append(path)
for path in selected:
    if args.apply:
        path.unlink()
print(json.dumps({"mode": "apply" if args.apply else "dry_run", "eligible_screen_files": len(selected), "task_database_preserved": True}))
