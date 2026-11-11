"""Explicit, one-shot foreground-loss demonstration on the actual device.

Normal tasks do not install this wrapper. It observes the native composer,
registers one HOME attempt, and returns an actual newly observed screen. Task
approval, normal recovery and sending remain the production runtime's job.
"""
from contextlib import contextmanager
import json
import re
from pathlib import Path

from domain import WorkflowError
from verification.mail import read_draft


def _eligible(device, xml):
    if device.recovery_stage != "draft" or not device.compose_activity:
        return False
    try:
        actual = read_draft(xml)
    except WorkflowError:
        return False
    expected = device._expected_draft
    if (not expected or actual["recipients"] != [expected["recipient"]] or actual["extra"]
            or actual["subject"] != expected["subject"]):
        return False
    marker = re.search(r"\[SilverGuide:[a-f0-9]+\]", expected["body"])
    return marker is None or marker.group() in actual["body"]


def install_injection(device, *, before_inject=None, after_observed=None,
                      only_task_id=None, trigger="cli"):
    """Install one wrapper and return the previous observer for scoped cleanup.

    Shared task-ID deduplication also covers a CLI wrapper nested with an App
    task wrapper. `before_inject` must persist the registration before HOME.
    """
    original = device.observe
    used = False
    seen = getattr(device, "_demo_injected_tasks", None)
    if seen is None:
        seen = device._demo_injected_tasks = set()

    def observe(checkpoint=lambda: None, evidence=False):
        nonlocal used
        xml = original(checkpoint, evidence=evidence)
        task_id = device.task_id
        if (used or task_id in seen or only_task_id is not None and only_task_id != task_id
                or not _eligible(device, xml)):
            return xml
        checkpoint()
        event = {"case": "composer-background", "task_id": task_id,
                 "fault": "home_after_observed_composer",
                 "real_device": device.__class__.__module__.startswith("device."),
                 "external_send_triggered_by_driver": False,
                 "trigger": trigger, "status": "registered"}
        # A durable registration precedes dispatch, so a retry cannot inject
        # again even if the device call's result becomes uncertain.
        if before_inject is not None and before_inject(event) is False:
            used = True
            return xml
        seen.add(task_id)
        used = True
        folder = Path(device.evidence_root) / task_id
        folder.mkdir(parents=True, exist_ok=True)
        record = folder / "recovery-fault-injection.json"
        record.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        checkpoint()
        device.shell("input", "keyevent", "KEYCODE_HOME")
        for _ in range(5):
            checkpoint()
            observed = original(checkpoint, evidence=True)
            try:
                read_draft(observed)
            except WorkflowError:
                event["status"] = "observed"
                record.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n")
                if after_observed is not None:
                    after_observed(event)
                print("RECOVERY_FAULT_INJECTED " + json.dumps(event), flush=True)
                return observed
        event["status"] = "not_observed"
        record.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        raise WorkflowError("故障注入未能观察到离开写信页，本轮不能算恢复测试通过")

    device.observe = observe
    return original


@contextmanager
def task_injection(device, task, *, before_inject, after_observed):
    """Keep injection armed across graph interrupts, but never into sending."""
    if (task.get("demo_fault") != "composer-background" or task.get("demo_fault_injected")
            or task.get("send_attempts") or task.get("state") in {"sending", "verifying", "completed", "outcome_unknown"}):
        yield
        return
    if not callable(getattr(device, "observe", None)):
        raise WorkflowError("当前设备接口不能执行真实写信页中断演示")
    original = install_injection(device, before_inject=before_inject,
                                 after_observed=after_observed,
                                 only_task_id=task["id"], trigger="app")
    try:
        yield
    finally:
        device.observe = original
