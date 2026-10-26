"""Evidence-based pilot metrics; simulated tests are never real-device trials."""
from collections import Counter
from datetime import datetime
import json

SCENARIOS = {"N01": "扫码分享", "N02": "第二个二维码", "N03": "分享当前网页", "N04": "联系人映射",
             "R01": "相机权限", "R02": "扫码超时", "R03": "网页延迟", "R04": "弹窗或控件变化",
             "S01": "联系人不明确", "S02": "用户取消", "S03": "批准后草稿变化", "S04": "发送后断连"}


def outcome(task):
    if task["state"] == "completed":
        evidence = (task.get("webpage_verified") if task.get("task_type") == "open_url"
                    else task.get("sent_record_verified") or task.get("delivery_confirmed"))
        return "SUCCESS" if evidence else "UNKNOWN"
    return {"cancelled": "SAFE_STOP", "failed": "FAILURE", "outcome_unknown": "UNKNOWN", "interrupted": "UNKNOWN"}.get(task["state"], "IN_PROGRESS")


def summarize(tasks):
    counts = Counter(outcome(t) for t in tasks)
    independent = [t for t in tasks if t.get("third_party_assistance") is not None]
    recovered = [t for t in tasks if t.get("recoverable_failure_injected") is True]
    abnormal = [t for t in tasks if t.get("abnormal_case") is True]
    assessed = [t for t in abnormal if isinstance(t.get("safe_handling_verified"), bool)]
    durations = []
    for task in tasks:
        if outcome(task) != "IN_PROGRESS":
            try:
                durations.append((datetime.fromisoformat(task["updated_at"]) - datetime.fromisoformat(task["created_at"])).total_seconds())
            except (KeyError, ValueError):
                pass
    return {"valid_started_tasks": len(tasks), "outcomes": dict(counts),
            "verified_task_success_rate": counts["SUCCESS"] / len(tasks) if tasks else None,
            "independent_completion_rate": sum(outcome(t) == "SUCCESS" and not t["third_party_assistance"] for t in independent) / len(independent) if independent else None,
            "recovery_rate": sum(outcome(t) == "SUCCESS" for t in recovered) / len(recovered) if recovered else None,
            "safe_handling_rate": sum(t["safe_handling_verified"] for t in assessed) / len(abnormal) if abnormal and len(assessed) == len(abnormal) else None,
            "safe_handling_assessed_cases": len(assessed),
            "action_success_rate": None,
            "mean_task_duration_seconds": sum(durations) / len(durations) if durations else None,
            "duration_basis": "last_record_update_minus_creation_including_human_wait_and_rechecks",
            "matrix": {key: {"name": name, "target_repetitions": 3, "recorded_trials": sum(t.get("scenario_id") == key for t in tasks)} for key, name in SCENARIOS.items()},
            "dataset_label": "pilot_records_not_a_completed_36_trial_benchmark",
            "variants": {mode: sum(t.get("assistance_mode") == mode for t in tasks) for mode in ("basic", "silverguide")}}


def explanation_audit(events):
    claims = [e for e in events if any(p in e.get("message", "") for p in ("发送成功", "邮件已发送", "已在已发送", "已核对已发送记录"))]
    measured = [e for e in claims if "evidence" in e]
    supported = sum(bool(e["evidence"].get("sent_record_verified") or e["evidence"].get("delivery_confirmed")) for e in measured)
    return {"evaluated_claims": len(measured), "supported_claims": supported, "legacy_claims_without_snapshot": len(claims) - len(measured),
            "explanation_consistency": supported / len(measured) if measured else None}


def build_report(store):
    tasks = [t for t in store.all() if t.get("environment", "real_device") == "real_device"]
    report = summarize(tasks)
    events = []
    for task in tasks:
        cursor = 0
        while batch := store.events(task["id"], cursor):
            events.extend(batch)
            cursor = batch[-1]["seq"]
    report["explanation_audit"] = explanation_audit(events)
    calls = []
    damaged = 0
    task_ids = {t["id"] for t in tasks}
    path = store.directory / "model-calls" / "calls.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                value = json.loads(line)
                if value.get("task_id") in task_ids:
                    calls.append(value)
            except (ValueError, AttributeError):
                damaged += 1
    latencies = [c["latency_ms"] for c in calls if isinstance(c.get("latency_ms"), (int, float))]
    report["model_calls"] = {"count": len(calls), "successful_calls": sum(c.get("ok") is True for c in calls),
        "by_stage": dict(Counter(c.get("stage", "unknown") for c in calls)),
        "mean_latency_ms": sum(latencies) / len(latencies) if latencies else None,
        "recorded_tokens": sum(c.get("tokens") or 0 for c in calls), "malformed_log_lines": damaged}
    report["records"] = [{"task_id": t["id"], "state": t["state"], "send_attempts": t["send_attempts"], "scenario_id": t.get("scenario_id")} for t in tasks]
    return report
