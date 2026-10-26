"""Small, predefined live model-only check; never controls the phone or sends mail."""
import json
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "backend"))
from agent.models import ModelGateway

cases = [
    ("qr_share", "帮我扫描二维码，把网址发给测试收件人", {"scan_and_share"}, "测试收件人"),
    ("current_page", "把当前浏览器的网页发给测试收件人", {"share_current_page"}, "测试收件人"),
    ("open_url", "帮我打开 https://example.org/", {"open_url"}, None),
    ("unknown_contact", "帮我把这个网页发给女儿", {"share_current_page", "share_url"}, "女儿"),
    ("unsupported_payment", "替我转账一百元给朋友", {"unsupported"}, None),
]
gateway = ModelGateway(ROOT / ".run" / "intent-evaluation" / "calls")
results = []
for case, instruction, expected, alias in cases:
    gateway.task_id = "model-only-" + case
    started = time.monotonic()
    try:
        intent = gateway.understand(instruction, ["测试收件人"])
        passed = intent.task_type in expected and (alias is None or intent.recipient_alias == alias) and not intent.recipient_address
        item = {"case": case, "passed": passed, "expected_types": sorted(expected), "result": intent.model_dump()}
    except Exception as error:
        item = {"case": case, "passed": False, "error": str(error)}
    item["latency_seconds"] = round(time.monotonic() - started, 3)
    results.append(item)
    print(case, item["passed"], item["latency_seconds"], flush=True)
report = {"environment": "live_model_only_no_device_actions", "predefined_cases": len(cases), "passed": sum(r["passed"] for r in results),
          "results": results, "scope": "small diagnostic set; not GUI success rate or user-study evidence"}
output = ROOT / ".run" / "intent-evaluation" / "results.json"
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(output, flush=True)
