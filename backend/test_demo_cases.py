"""App-selected demonstrations use device doubles, never a phone or mail API."""
import json
import xml.etree.ElementTree as ET
from unittest.mock import patch
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import gmail_demo
from agent.runtime import Engine
from api.schemas import DEMO_CASE_IDS, DemoCaseId, GoalRequest, TaskIntent
from device.demo import install_injection, task_injection
from domain import Cancelled, WorkflowError
from storage.tasks import Store
from test_workflow import Device
from verification.mail import ID, read_draft


HOME = '<hierarchy><node package="com.huawei.android.launcher"/></hierarchy>'
INSTRUCTION = "帮我把 https://example.org/ 发给 receiver@example.org，主题是演示，正文是测试。"


def composer(draft):
    root = ET.Element("hierarchy")
    to = ET.SubElement(root, "node", {"resource-id": ID + "to_content"})
    ET.SubElement(to, "node", {"text": draft["recipient"]})
    ET.SubElement(root, "node", {"resource-id": ID + "send_email_btn"})
    ET.SubElement(root, "node", {"resource-id": ID + "subject", "text": draft["subject"]})
    ET.SubElement(root, "node", {"resource-id": "main", "text": draft["body"]})
    return ET.tostring(root, encoding="unicode")


class DemoDevice(Device):
    def __init__(self, directory):
        super().__init__()
        self.evidence_root = directory
        self.task_id = "demo-task"
        self.recovery_stage = "draft"
        self.compose_activity = "com.huawei.email/.ComposeActivity"
        self._expected_draft = {"recipient": "receiver@example.org", "subject": "演示", "body": "测试。"}
        self.xml = composer(self._expected_draft)
        self.home_calls = 0
        self.ignore_home = False

    def observe(self, checkpoint=lambda: None, evidence=False):
        checkpoint()
        return self.xml

    def shell(self, *args):
        assert args == ("input", "keyevent", "KEYCODE_HOME")
        self.home_calls += 1
        if not self.ignore_home:
            self.xml = HOME

    def prepare(self, draft, checkpoint):
        checkpoint()
        self._expected_draft = dict(draft)
        self.xml = composer(draft)
        observed = self.observe(checkpoint)
        try:
            read_draft(observed)
        except WorkflowError:
            self.calls.append(("restore_original_composer",))
            self.xml = composer(draft)
            read_draft(self.observe(checkpoint))
        self.calls.append(("prepare",))


class Models:
    task_type = "share_url"

    def understand(self, instruction, aliases):
        return TaskIntent(task_type=self.task_type, recipient_address="receiver@example.org",
                          subject="演示", body="测试。")


@pytest.fixture
def engine(tmp_path):
    current = Engine(Store(tmp_path / "store"), DemoDevice(tmp_path / "evidence"), Models())
    yield current
    current.cancelled = True
    current.paused = False
    with current.condition:
        current.condition.notify_all()
    if current.worker:
        current.worker.join(3)
    current.close()


def settle(engine, task):
    engine.worker.join(3)
    assert not engine.worker.is_alive()
    return engine.store.get(task["id"])


def recovery_goal(engine, instruction=INSTRUCTION, key="demo-recovery-001"):
    return settle(engine, engine.create_goal(instruction, key, demo_case_id="T12",
                                             demo_fault="composer-background"))


@pytest.mark.parametrize("case_id", DEMO_CASE_IDS)
def test_request_case_and_fault_are_explicitly_paired(case_id):
    fault = "composer-background" if case_id == "T12" else None
    request = GoalRequest(instruction=INSTRUCTION, request_key="request-001", demo_case_id=case_id, demo_fault=fault)
    assert request.demo_case_id == case_id
    assert (request.demo_fault is not None) == (case_id == "T12")


@pytest.mark.parametrize("case_id,fault", [
    (None, "composer-background"), ("T03", "composer-background"), ("T12", None),
    ("T21", None), ("T99", None), ("T12", "send"), ("T12", "approve"), ("T12", "shell"),
])
def test_invalid_demo_configuration_cannot_create_a_request_or_task(engine, case_id, fault):
    with pytest.raises(ValidationError):
        GoalRequest(instruction=INSTRUCTION, request_key="invalid-request", demo_case_id=case_id, demo_fault=fault)
    with pytest.raises(WorkflowError):
        engine.create_goal(INSTRUCTION, "invalid-request", demo_case_id=case_id, demo_fault=fault)
    assert engine.store.all() == []
    assert engine.device.home_calls == 0


def test_ordinary_task_never_installs_or_executes_a_fault(engine):
    observer = engine.device.observe
    task = settle(engine, engine.create_goal(INSTRUCTION, "ordinary-request"))
    assert task["state"] == "awaiting_confirmation"
    assert task["demo_fault"] is None and not task["demo_fault_injected"]
    assert engine.device.home_calls == 0
    assert engine.device.observe == observer
    assert not list(engine.device.evidence_root.glob("*/recovery-fault-injection.json"))


def test_recovery_case_records_one_actual_interruption_and_still_requires_approval(engine):
    observer = engine.device.observe
    task = recovery_goal(engine)
    assert task["state"] == "awaiting_confirmation" and task["send_attempts"] == 0
    assert task["draft_verified"] and task["demo_case_id"] == "T12"
    assert task["demo_fault_injected"] and task["demo_fault_status"] == "observed"
    assert engine.device.home_calls == 1
    assert engine.device.observe == observer
    assert sum(call[0] == "restore_original_composer" for call in engine.device.calls) == 1
    assert all(call[0] != "send" for call in engine.device.calls)
    event = json.loads((engine.device.evidence_root / task["id"] / "recovery-fault-injection.json").read_text())
    assert event["fault"] == "home_after_observed_composer"
    assert event["status"] == "observed" and event["trigger"] == "app"
    assert event["real_device"] is False and event["external_send_triggered_by_driver"] is False


def test_graph_qr_interrupt_keeps_the_fault_pending_until_the_composer(engine):
    engine.models.task_type = "scan_and_share"
    task = recovery_goal(engine, "帮我扫描二维码，把网址发给 receiver@example.org")
    assert task["state"] == "awaiting_qr"
    assert not task["demo_fault_injected"] and engine.device.home_calls == 0
    task = settle(engine, engine.submit_qr(task["id"], "https://example.org/", task["scan_nonce"]))
    assert task["state"] == "awaiting_confirmation"
    assert task["demo_fault_injected"] and task["demo_fault_status"] == "observed"
    assert engine.device.home_calls == 1 and task["send_attempts"] == 0


def test_approval_and_repeated_http_confirmation_do_not_inject_again_or_repeat_send(engine):
    task = recovery_goal(engine)
    task = settle(engine, engine.approve(task["id"], task["draft_digest"], True, task["approval_nonce"]))
    assert task["state"] == "completed" and task["send_attempts"] == 1
    engine.approve(task["id"], task["draft_digest"], True, task["approval_nonce"])
    assert engine.device.home_calls == 1
    assert sum(call[0] == "send" for call in engine.device.calls) == 1


def test_same_request_key_is_idempotent_but_cannot_change_demo_configuration(engine):
    task = recovery_goal(engine)
    repeated = engine.create_goal(INSTRUCTION, "demo-recovery-001", demo_case_id="T12", demo_fault="composer-background")
    assert repeated["id"] == task["id"] and engine.device.home_calls == 1
    for changes in [{}, {"demo_case_id": "T03"}]:
        with pytest.raises(WorkflowError, match="演示配置"):
            engine.create_goal(INSTRUCTION, "demo-recovery-001", **changes)
    assert len(engine.store.all()) == 1


def test_existing_active_task_cannot_be_replaced_by_a_recovery_case(engine):
    task = settle(engine, engine.create_goal(INSTRUCTION, "active-request"))
    with pytest.raises(WorkflowError, match="完成或取消"):
        engine.create_goal(INSTRUCTION, "recovery-request", demo_case_id="T12", demo_fault="composer-background")
    assert engine.latest()["id"] == task["id"] and len(engine.store.all()) == 1
    assert engine.device.home_calls == 0


def test_recovery_fault_cannot_be_disguised_as_an_open_webpage_only_success(engine):
    engine.models.task_type = "open_url"
    task = recovery_goal(engine, "帮我打开 https://example.org/")
    assert task["state"] == "failed" and "邮件草稿" in task["message"]
    assert task["send_attempts"] == 0 and engine.device.home_calls == 0


def test_demo_api_accepts_case_metadata_and_invalid_fault_is_rejected_before_execution(engine):
    with patch("workflow_api.get_engine", return_value=engine):
        client = TestClient(gmail_demo.app)
        invalid = client.post("/api/tasks", json={"instruction": INSTRUCTION, "request_key": "bad-api-request",
                                                   "demo_case_id": "T02", "demo_fault": "composer-background"})
        assert invalid.status_code == 422 and not engine.store.all()
        response = client.post("/api/tasks", json={"instruction": INSTRUCTION, "request_key": "good-api-request",
                                                    "demo_case_id": "T12", "demo_fault": "composer-background"})
        assert response.status_code == 200
        task = settle(engine, response.json()["task"])
        assert task["demo_case_id"] == "T12" and task["demo_fault_status"] == "observed"


def test_demo_capabilities_advertise_supported_case_ids_without_starting_a_task():
    data = TestClient(gmail_demo.app).get("/api/system/demo-capabilities").json()
    assert data["ok"] and data["recovery_supported"]
    assert data["case_ids"] == list(DEMO_CASE_IDS) == [f"T{number:02}" for number in range(1, 21)]
    assert tuple(get_args(DemoCaseId)) == DEMO_CASE_IDS


@pytest.mark.parametrize("cli_first", [True, False])
def test_cli_and_app_wrappers_share_deduplication_and_never_press_home_twice(tmp_path, cli_first):
    device = DemoDevice(tmp_path)
    task = {"id": device.task_id, "state": "preparing_draft", "send_attempts": 0,
            "demo_fault": "composer-background", "demo_fault_injected": False}
    events = []
    if cli_first:
        install_injection(device, before_inject=lambda e: events.append(dict(e)), trigger="cli")
    with task_injection(device, task, before_inject=lambda e: events.append(dict(e)), after_observed=lambda e: None):
        if not cli_first:
            install_injection(device, before_inject=lambda e: events.append(dict(e)), trigger="cli")
        assert device.observe() == HOME
    assert device.home_calls == 1 and len(events) == 1
    device.xml = composer(device._expected_draft)
    with task_injection(device, task, before_inject=lambda e: events.append(dict(e)), after_observed=lambda e: None):
        assert device.observe() != HOME
    assert device.home_calls == 1 and len(events) == 1


def test_wrong_composer_recipient_or_task_marker_never_causes_injection(tmp_path):
    device = DemoDevice(tmp_path)
    device._expected_draft["body"] = "测试。\n[SilverGuide:123abc]"
    wrong = {**device._expected_draft, "body": "测试。\n[SilverGuide:456def]"}
    device.xml = composer(wrong)
    install_injection(device)
    assert device.observe() == composer(wrong)
    wrong = {**device._expected_draft, "recipient": "another@example.org"}
    device.xml = composer(wrong)
    assert device.observe() == composer(wrong)
    assert device.home_calls == 0


def test_cancel_after_registration_prevents_home_dispatch(tmp_path):
    device = DemoDevice(tmp_path)
    registered = False

    def before(event):
        nonlocal registered
        registered = True

    def checkpoint():
        if registered:
            raise Cancelled("用户取消")

    install_injection(device, before_inject=before)
    with pytest.raises(Cancelled):
        device.observe(checkpoint)
    assert device.home_calls == 0
    event = json.loads((tmp_path / device.task_id / "recovery-fault-injection.json").read_text())
    assert event["status"] == "registered"


def test_unobserved_home_is_not_reported_as_a_successful_recovery_case(engine):
    engine.device.ignore_home = True
    task = recovery_goal(engine)
    assert task["state"] == "failed" and task["demo_fault_injected"]
    assert task["demo_fault_status"] == "registered" and not task["draft_verified"]
    assert engine.device.home_calls == 1 and task["send_attempts"] == 0
    event = json.loads((engine.device.evidence_root / task["id"] / "recovery-fault-injection.json").read_text())
    assert event["status"] == "not_observed"


@pytest.mark.parametrize("state,attempts", [("sending", 0), ("verifying", 1), ("completed", 1)])
def test_injection_is_never_installed_into_send_or_result_verification(tmp_path, state, attempts):
    device = DemoDevice(tmp_path)
    observer = device.observe
    task = {"id": device.task_id, "state": state, "send_attempts": attempts,
            "demo_fault": "composer-background", "demo_fault_injected": False}
    with task_injection(device, task, before_inject=lambda e: None, after_observed=lambda e: None):
        assert device.observe == observer
        assert device.observe() != HOME
    assert device.home_calls == 0



@pytest.mark.parametrize("case_id", [f"T{number:02}" for number in range(15, 21)])
def test_new_map_demo_metadata_is_ordinary_route_input_not_a_fault_driver(engine, case_id):
    instruction = "在广州，从家里去广东省博物馆，坐公交。"
    engine.models.understand = lambda text, aliases: TaskIntent(task_type="plan_transit", transit={
        "origin": "家里", "destination": "广东省博物馆", "city": "广州"})
    with patch("workflow_api.get_engine", return_value=engine):
        client = TestClient(gmail_demo.app)
        payload = {"instruction": instruction, "request_key": "map-demo-" + case_id, "demo_case_id": case_id}
        invalid = client.post("/api/tasks", json={**payload, "demo_fault": "composer-background"})
        assert invalid.status_code == 422 and engine.store.all() == []
        valid = client.post("/api/tasks", json=payload)
        assert valid.status_code == 200
        task = settle(engine, valid.json()["task"])
    assert task["demo_case_id"] == case_id and task["task_type"] == "plan_transit"
    assert task["state"] == "needs_clarification" and task["clarification_fields"] == ["origin"]
    assert task["demo_fault"] is None and not task["demo_fault_injected"]
    assert not engine.device.calls and engine.device.home_calls == task["send_attempts"] == 0


@pytest.mark.parametrize("case_id", [f"T{number:02}" for number in range(15, 21)])
def test_new_map_ids_cannot_enable_recovery_fault_through_the_engine(engine, case_id):
    with pytest.raises(WorkflowError):
        engine.create_goal("从家里去博物馆坐地铁", "map-fault-" + case_id, demo_case_id=case_id,
                           demo_fault="composer-background")
    assert not engine.store.all() and engine.device.home_calls == 0
