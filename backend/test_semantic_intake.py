"""Exercise semantic dispatch through the durable task workflow with no real device or provider."""

import pytest
from fastapi.testclient import TestClient

import gmail_demo
from agent.models import IntentModelUnavailable
from agent.runtime import Engine
from api.schemas import TaskIntent, TransitRequest
from storage.tasks import Store
from test_workflow import Device


class ConfiguredModel:
    intent_model_configured = True

    def __init__(self, result):
        self.result = result
        self.instructions = []

    def understand(self, instruction, aliases):
        self.instructions.append(instruction)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ObservedDevice(Device):
    def check_ready(self):
        self.calls.append(("check_ready",))
        super().check_ready()

    def show_app(self):
        self.calls.append(("show_app",))


def settle(engine, task):
    engine.worker.join(3)
    assert not engine.worker.is_alive()
    return engine.store.get(task["id"])


@pytest.fixture
def workflow(tmp_path):
    engines = []

    def open_workflow(result):
        engine = Engine(Store(tmp_path), ObservedDevice(), ConfiguredModel(result))
        engines.append(engine)
        return engine

    yield open_workflow

    for engine in engines:
        if engine.worker:
            engine.worker.join(3)
        engine.close()


@pytest.mark.parametrize(("instruction", "modeled_alias", "expected_alias"), [
    ("替我接通一下闺女吧，我想和她说两句。", "闺女", "闺女"),
    ("让小娟接个电话。", "小娟", "小娟"),
    ("我想给家里拨一下。", "女儿", ""),
])
def test_family_intent_hands_off_to_the_phone_without_running_device_actions(
    workflow, instruction, modeled_alias, expected_alias,
):
    engine = workflow(TaskIntent(task_type="contact_family", family_recipient_alias=modeled_alias))

    task = settle(engine, engine.create_goal(instruction, "family-intake-request"))

    assert engine.models.instructions == [instruction]
    assert task["task_type"] == "contact_family" and task["state"] == "completed"
    assert task.get("family_recipient_alias", "") == expected_alias
    assert task["parser"] == "llm"
    assert {"draft", "draft_digest", "scan_nonce", "approval_nonce"}.isdisjoint(task)
    assert not task.get("request_qr") and task["send_attempts"] == 0
    assert not engine.device.calls


def test_family_handoff_survives_reload_and_idempotent_api_retry(workflow, monkeypatch):
    engine = workflow(TaskIntent(task_type="contact_family", family_recipient_alias="女儿"))
    monkeypatch.setattr("workflow_api.get_engine", lambda: engine)
    client = TestClient(gmail_demo.app)
    payload = {"instruction": "打给女儿", "request_key": "family-repeat-request"}

    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 200
    task = settle(engine, response.json()["task"])
    assert engine.models.instructions == [payload["instruction"]]

    engine = workflow(AssertionError("A stored handoff must not rerun the model"))
    repeated = client.post("/api/tasks", json=payload)
    persisted = client.get(f"/api/tasks/{task['id']}")

    assert repeated.status_code == persisted.status_code == 200
    assert repeated.json()["task"] == persisted.json()["task"] == task
    assert task["state"] == "completed" and task["family_recipient_alias"] == "女儿"
    assert not engine.models.instructions and not engine.device.calls


def test_configured_model_is_used_before_even_the_explicit_scan_fallback(workflow):
    engine = workflow(TaskIntent(task_type="open_url", needs_qr=True))

    task = settle(engine, engine.create_goal("扫码", "configured-scan-request"))

    assert engine.models.instructions == ["扫码"]
    assert task["parser"] == "llm"
    assert task["task_type"] == "open_url" and task["state"] == "awaiting_qr"
    assert not engine.device.calls


def test_unavailable_model_uses_the_explicit_scan_fallback(workflow):
    engine = workflow(IntentModelUnavailable("provider unavailable"))

    task = settle(engine, engine.create_goal("扫码", "fallback-scan-request"))

    assert engine.models.instructions == ["扫码"]
    assert task["parser"] == "explicit_input"
    assert task["task_type"] == "open_url" and task["state"] == "awaiting_qr"
    assert not engine.device.calls


@pytest.mark.parametrize("instruction", [
    "怎么坐车去省博物馆比较省腿？",
    "查去省博物馆的公交，不要用当前位置。",
])
def test_model_cannot_enable_location_without_user_authorization(workflow, instruction):
    engine = workflow(TaskIntent(task_type="plan_transit", transit=TransitRequest(
        destination="省博物馆", use_current_location=True,
    )))

    task = settle(engine, engine.create_goal(instruction, "route-location-request"))

    assert engine.models.instructions == [instruction]
    assert task["parser"] == "llm"
    assert task["state"] == "needs_clarification"
    assert task["transit_request"]["destination"] == "省博物馆"
    assert not task["transit_request"]["use_current_location"]
    assert "origin" in task["clarification_fields"]
    assert all(call[0] == "show_app" for call in engine.device.calls)


def test_available_model_can_refuse_a_request_matching_the_offline_short_route(workflow):
    instruction = "去三楼缴费"
    engine = workflow(TaskIntent(task_type="unsupported"))

    task = settle(engine, engine.create_goal(instruction, "unsupported-payment-request"))

    assert engine.models.instructions == [instruction]
    assert task["state"] == "failed"
    assert "transit_request" not in task and not task["send_attempts"]
    assert all(call[0] == "show_app" for call in engine.device.calls)


@pytest.mark.parametrize("instruction", ["别给女儿打电话", "请不要联系闺女", "暂时不用拨给家人"])
def test_declined_family_call_cannot_become_a_handoff(workflow, instruction):
    engine = workflow(TaskIntent(task_type="contact_family", family_recipient_alias="女儿"))

    task = settle(engine, engine.create_goal(instruction, "declined-family-request"))

    assert task["state"] == "failed"
    assert task.get("task_type") != "contact_family" and not task["send_attempts"]
    assert all(call[0] == "show_app" for call in engine.device.calls)
