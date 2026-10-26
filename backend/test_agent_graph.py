import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import gmail_demo
from agent.runtime import Engine
from api.schemas import TaskIntent, ScreenDecision, ActionSpec
from domain import WorkflowError
from storage.tasks import Store
from test_workflow import Device
from safety.actions import guard_preparation


class Models:
    def __init__(self, **changes):
        self.result = TaskIntent(task_type="scan_and_share", recipient_alias="女儿", **changes)
        self.gate = None
        self.entered = threading.Event()

    def understand(self, instruction, aliases):
        self.entered.set()
        if self.gate:
            self.gate.wait(2)
        return self.result


@pytest.fixture
def engine(tmp_path):
    device = Device()
    current = Engine(Store(tmp_path), device, Models())
    yield current
    current.cancelled = True
    current.paused = False
    if current.models.gate:
        current.models.gate.set()
    with current.condition:
        current.condition.notify_all()
    if current.worker:
        current.worker.join(3)
    current.close()


def settle(engine, task):
    engine.worker.join(3)
    assert not engine.worker.is_alive()
    return engine.store.get(task["id"])


def create_goal(engine):
    return settle(engine, engine.create_goal("帮我扫描二维码，把网址发给女儿", "agent-request-01"))


def prepared(engine):
    engine.contacts.put("女儿", "receiver@example.org")
    task = create_goal(engine)
    return settle(engine, engine.submit_qr(task["id"], "https://example.org/", task["scan_nonce"]))


def test_graph_interrupts_for_contact_and_real_qr_then_approval(engine):
    task = create_goal(engine)
    assert task["state"] == "needs_clarification"
    assert task["draft"]["recipient"] == ""
    assert engine.graph.get_state(engine._graph_config(task["id"])).next == ("clarify",)
    task = settle(engine, engine.clarify(task["id"], {"recipient": "receiver@example.org", "alias": "女儿", "save_contact": True}))
    assert task["state"] == "awaiting_qr"
    assert engine.contacts.resolve("女儿") == "receiver@example.org"
    with pytest.raises(WorkflowError):
        engine.submit_qr(task["id"], "https://example.org/", "stale-nonce")
    assert not engine.device.calls
    task = settle(engine, engine.submit_qr(task["id"], "https://example.org/", task["scan_nonce"]))
    assert task["state"] == "awaiting_confirmation"
    assert task["source"] == "qr" and task["webpage_verified"]
    assert engine.graph.get_state(engine._graph_config(task["id"])).next == ("approval",)
    assert task["send_attempts"] == 0
    task = settle(engine, engine.approve(task["id"], task["draft_digest"], True, task["approval_nonce"]))
    assert task["state"] == "completed" and task["send_attempts"] == 1
    assert sum(c[0] == "send" for c in engine.device.calls) == 1


def test_model_cannot_guess_an_address_or_qr_url(engine):
    engine.models.result = TaskIntent(task_type="scan_and_share", recipient_alias="女儿", recipient_address="invented@example.org", url="https://evil.example/")
    task = create_goal(engine)
    assert task["draft"]["recipient"] == task["draft"]["url"] == ""
    assert task["state"] == "needs_clarification"


@pytest.mark.parametrize("instruction", ["扫码发给 a@example.org 或 b@example.org", "扫码发给女儿或儿子"])
def test_ambiguous_recipients_require_human_choice_even_if_model_picks_one(engine, instruction):
    engine.contacts.put("女儿", "a@example.org")
    engine.contacts.put("儿子", "b@example.org")
    engine.models.result = TaskIntent(task_type="scan_and_share", recipient_alias="女儿", recipient_address="a@example.org")
    task = settle(engine, engine.create_goal(instruction, "ambiguous-request-01"))
    assert task["state"] == "needs_clarification"
    assert task["draft"]["recipient"] == "" and task["recipient_ambiguous"]
    assert "唯一收件人" in task["message"]
    assert not engine.device.calls


def test_waiting_for_qr_does_not_reopen_main_activity_and_dismiss_camera(engine):
    engine.contacts.put("女儿", "receiver@example.org")
    with patch.object(engine.device, "show_app") as show:
        task = create_goal(engine)
        assert task["state"] == "awaiting_qr"
        show.assert_not_called()


def test_open_only_goal_never_enters_mail_or_approval(engine):
    engine.models.result = TaskIntent(task_type="open_url")
    task = settle(engine, engine.create_goal("帮我打开 https://example.org/", "open-only-request"))
    assert task["state"] == "completed" and task["webpage_verified"]
    assert task["send_attempts"] == 0 and not task["draft_verified"]
    assert all(call[0] not in {"send", "prepare"} for call in engine.device.calls)
    assert "准备草稿" not in task["plan"]


def test_resuming_qr_requires_new_scan_nonce_and_rejects_old_callback(engine):
    engine.contacts.put("女儿", "receiver@example.org")
    task = create_goal(engine)
    old_nonce = task["scan_nonce"]
    engine.control(task["id"], "pause")
    task = engine.control(task["id"], "resume")
    assert task["state"] == "awaiting_qr" and task["scan_nonce"] != old_nonce
    with pytest.raises(WorkflowError):
        engine.submit_qr(task["id"], "https://example.org/", old_nonce)
    task = settle(engine, engine.submit_qr(task["id"], "https://example.org/", task["scan_nonce"]))
    assert task["state"] == "awaiting_confirmation" and task["send_attempts"] == 0


def test_invalid_clarification_keeps_current_question(engine):
    task = create_goal(engine)
    with pytest.raises(WorkflowError):
        engine.clarify(task["id"], {"recipient": "not an email"})
    assert engine.store.get(task["id"])["state"] == "needs_clarification"


def test_edit_invalidates_nonce_and_requires_new_graph_approval(engine):
    task = prepared(engine)
    old_digest, old_nonce = task["draft_digest"], task["approval_nonce"]
    task = settle(engine, engine.edit(task["id"], {"subject": "修改后的主题"}))
    assert task["graph_run"] == 2 and task["approval_nonce"] != old_nonce
    with pytest.raises(WorkflowError):
        engine.approve(task["id"], old_digest, True, old_nonce)
    with pytest.raises(WorkflowError):
        engine.approve(task["id"], task["draft_digest"], True, old_nonce)
    assert task["send_attempts"] == 0


def test_cancel_during_model_call_blocks_all_late_device_actions(engine):
    engine.models.gate = threading.Event()
    task = engine.create_goal("帮我扫描二维码，把网址发给女儿", "agent-request-01")
    assert engine.models.entered.wait(1)
    engine.control(task["id"], "cancel")
    engine.models.gate.set()
    task = settle(engine, task)
    assert task["state"] == "cancelled" and not engine.device.calls


def test_websocket_replays_then_streams_controls_with_cursor(engine):
    task = prepared(engine)
    with patch("workflow_api.get_engine", return_value=engine):
        with TestClient(gmail_demo.app).websocket_connect(f"/ws/tasks/{task['id']}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["event_type"] == "snapshot"
            cursor = snapshot["cursor"]
            engine.control(task["id"], "pause")
            event = ws.receive_json()
            assert event["task"]["state"] == "paused" and event["cursor"] > cursor
    assert not __import__("api.events", fromlist=["hub"]).hub.listeners


def test_replayed_events_are_not_truncated_to_first_hundred(engine):
    task = prepared(engine)
    for i in range(110):
        engine.store.save(task, f"event {i}")
    with patch("workflow_api.get_engine", return_value=engine):
        with TestClient(gmail_demo.app).websocket_connect(f"/ws/tasks/{task['id']}") as ws:
            result = ws.receive_json()
            assert len(result["events"]) > 110
            assert result["events"][-1]["message"] == "event 109"


def test_snapshot_cannot_consume_cursor_of_a_newer_task_revision(engine):
    task = prepared(engine)
    started, finished = threading.Event(), threading.Event()
    original_events = engine.store.events
    writer = None
    def update():
        started.set()
        engine.control(task["id"], "pause")
        finished.set()
    def racing_events(task_id, after=0):
        nonlocal writer
        if writer is None:
            writer = threading.Thread(target=update)
            writer.start()
            assert started.wait(1)
            finished.wait(.1)
        return original_events(task_id, after)
    with patch("workflow_api.get_engine", return_value=engine), patch.object(engine.store, "events", side_effect=racing_events):
        with TestClient(gmail_demo.app).websocket_connect(f"/ws/tasks/{task['id']}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["task"]["state"] == snapshot["events"][-1]["state"]
            assert snapshot["task"]["message"] == snapshot["events"][-1]["message"]
            event = ws.receive_json()
            assert event["task"]["state"] == "paused" and event["cursor"] > snapshot["cursor"]
    writer.join(1)
    assert finished.is_set()


def test_external_webpage_cannot_read_private_task_websocket(engine):
    from starlette.websockets import WebSocketDisconnect
    task = prepared(engine)
    with patch("workflow_api.get_engine", return_value=engine):
        with pytest.raises(WebSocketDisconnect) as error:
            with TestClient(gmail_demo.app).websocket_connect(f"/ws/tasks/{task['id']}", headers={"origin": "https://untrusted.example"}):
                pass
        assert error.value.code == 1008


def test_gui_candidate_guard_rejects_send_shell_and_changed_text():
    draft = {"recipient": "receiver@example.org", "subject": "test", "body": "safe body"}
    candidates = [{"target_id": "body-1", "resource_id": "main"}]
    valid = ScreenDecision(page="composer", actions=[ActionSpec(action="enter_text", target_id="body-1", role="body", text="safe body")])
    assert guard_preparation(valid, candidates, draft) is valid
    for changes in [{"text": "attacker body"}, {"target_id": "stale"}, {"role": "subject"}]:
        altered = valid.model_copy(deep=True)
        altered.actions[0] = altered.actions[0].model_copy(update=changes)
        with pytest.raises(WorkflowError):
            guard_preparation(altered, candidates, draft)
    for operation in ["send", "shell", "approve"]:
        with pytest.raises(ValidationError):
            ActionSpec(action=operation)


@pytest.mark.parametrize("valid_retry", [True, False])
def test_invalid_visual_plan_is_never_executed_and_reobservation_is_bounded(tmp_path, valid_retry):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from device.huawei import HuaweiDevice
    from domain import make_draft
    draft = make_draft("receiver@example.org", "test", "正文")
    xml = '<hierarchy><node resource-id="com.huawei.email:id/to_content"><node text="receiver@example.org"/></node><node resource-id="com.huawei.email:id/send_email_btn"/><node resource-id="com.huawei.email:id/subject" text="test"/><node resource-id="main" text="正文"/></hierarchy>'
    bad = ScreenDecision(page="composer", actions=[ActionSpec(action="enter_text", role="body", text="正文", target_id="not-observed")])
    good = ScreenDecision(page="composer", actions=[])
    models = SimpleNamespace(perceive=Mock(side_effect=[bad, good if valid_retry else bad]))
    device = HuaweiDevice(tmp_path, transport=SimpleNamespace(screenshot=lambda: "image")); device.agent_models = models
    with patch.object(device, "observe", return_value=xml) as observe, patch.object(device, "shell", return_value=""), patch.object(device, "_set_field") as write:
        if valid_retry:
            device.prepare(draft, lambda: None)
        else:
            with pytest.raises(WorkflowError):
                device.prepare(draft, lambda: None)
        assert models.perceive.call_count == 2
        assert observe.call_count >= 2
        write.assert_not_called()


def test_visual_candidates_explicitly_bind_roles_and_include_recipient_description():
    import xml.etree.ElementTree as ET
    from perception.observe import describe
    nodes = [ET.Element("node", {"resource-id": "com.huawei.email:id/to_content", "content-desc": "收件人 receiver@example.org"}),
             ET.Element("node", {"resource-id": "main", "class": "android.widget.EditText"}),
             ET.Element("node", {"resource-id": "unrelated", "class": "android.widget.EditText"})]
    result = describe(nodes)
    assert [n["role"] for n in result] == ["to", "body"]
    assert "receiver@example.org" in result[0]["content_description"]


def test_contacts_survive_reopen_and_reject_multiple_addresses(engine):
    engine.contacts.put("家人", "family@example.org")
    from storage.contacts import Contacts
    assert Contacts(Store(engine.store.directory)).resolve("家人") == "family@example.org"
    with pytest.raises(WorkflowError):
        engine.contacts.put("家人", "a@example.org,b@example.org")


def test_unified_api_creates_goal_and_accepts_clarification(engine):
    with patch("workflow_api.get_engine", return_value=engine):
        client = TestClient(gmail_demo.app)
        response = client.post("/api/tasks", json={"instruction": "帮我扫描二维码，把网址发给女儿", "request_key": "unified-request-1"})
        assert response.status_code == 200
        task = settle(engine, response.json()["task"])
        response = client.post(f"/api/tasks/{task['id']}/clarify", json={"recipient": "receiver@example.org"})
        assert response.status_code == 200
        assert settle(engine, task)["state"] == "awaiting_qr"
