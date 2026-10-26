"""Generate a redacted pilot report from durable task evidence."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from storage.tasks import Store
from evaluation.metrics import build_report

parser = argparse.ArgumentParser()
parser.add_argument("--database-dir", type=Path, default=ROOT / ".run" / "workflows")
parser.add_argument("--output", type=Path, default=ROOT / ".run" / "evaluation" / "pilot.json")
args = parser.parse_args()
store = Store(args.database_dir)
report = build_report(store)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(f"Saved {report['valid_started_tasks']} pilot task records to {args.output}")
