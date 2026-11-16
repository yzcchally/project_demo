"""Executor ownership, mode provenance and original route validation, offline."""
import sys
import threading
from types import ModuleType

import pytest

from agent.runtime import Engine
from domain import Cancelled, WorkflowError
from storage.tasks import Store
from test_transit_workflow import Map, Models, create, settle
from test_workflow import Device


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("SILVERGUIDE_TRANSIT_EXECUTOR", raising=False)
    factories = []
    class AgentMap(Map):
        def __init__(self, device, models):
            super().__init__()
            factories.append(self)

        def query(self, request, task_id, query_id, checkpoint, choices=None):
            self.agent_progress_callback({"step": 1, "action": "Launch", "status": "executed",
                "model": "test-agent", "latency_ms": 2, "before_observation_id": "before",
                "after_observation_id": "after", "thought": "must-not-persist", "text": "private-place-text"})
            return super().query(request, task_id, query_id, checkpoint, choices)
    module = ModuleType("autoglm.executor")
    module.AutoGLMTransitDevice = AgentMap
    monkeypatch.setitem(sys.modules, "autoglm.executor", module)
    device = Device()
    device.show_app = lambda: device.calls.append(("show-app",))
    engine = Engine(Store(tmp_path), device, Models())
    engine.transit_device = Map()
    yield engine, factories, module
    engine.cancelled = True
    engine.paused = False
    for adapter in [engine.transit_device, *factories]:
        if adapter.gate: adapter.gate.set()
    with engine.condition: engine.condition.notify_all()
    if engine.worker: engine.worker.join(4)
    engine.close()


def test_old_configuration_still_uses_rules_without_constructing_an_agent(runtime):
    engine, factories, _ = runtime
    task = create(engine)
    assert task["state"] == "completed" and task["transit_executor"] == "rules"
    assert not factories and engine.transit_device.calls
    assert engine.store.events(task["id"])[-1]["transit_executor"] == "rules"


def test_autoglm_is_the_first_executor_and_preserves_independent_verification(runtime, monkeypatch):
    engine, factories, _ = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    task = create(engine)
    assert len(factories) == 1 and factories[0].calls[0] == "check-map"
    assert any(isinstance(call, tuple) and call[0] == "query" for call in factories[0].calls)
    assert not engine.transit_device.calls
    assert task["transit_executor"] == "autoglm" and task["route_verified"]
    assert task["transit_agent_step"] == 1
    summary = task["transit_agent_last_action"]
    assert summary["action"] == "Launch" and summary["query_id"] == task["query_id"]
    assert "thought" not in summary and "text" not in summary
    event = engine.store.events(task["id"])[-1]
    assert event["transit_executor"] == "autoglm" and event["transit_agent_step"] == 1


def test_missing_key_fails_before_device_precheck_or_any_gui_action(runtime, monkeypatch):
    engine, factories, module = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    class MissingConfig:
        def __init__(self, *args):
            raise WorkflowError("缺少 SILVERGUIDE_AUTOGLM_API_KEY")
    module.AutoGLMTransitDevice = MissingConfig
    task = create(engine)
    assert task["state"] == "failed" and "SILVERGUIDE_AUTOGLM_API_KEY" in task["message"]
    assert task["transit_executor"] == "autoglm" and not task["transit_executor_started"]
    assert not factories and not engine.transit_device.calls and not engine.device.calls


def test_agent_api_failure_stays_failed_without_rules_fallback(runtime, monkeypatch):
    engine, factories, module = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    original = module.AutoGLMTransitDevice
    class Failing(original):
        def query(self, *args, **kwargs):
            raise WorkflowError("AutoGLM API 请求超时")
    module.AutoGLMTransitDevice = Failing
    task = create(engine)
    assert task["state"] == "failed" and "API 请求超时" in task["message"]
    assert task["transit_executor"] == "autoglm" and not task["route_verified"]
    assert len(factories) == 1 and not engine.transit_device.calls


def test_unknown_mode_never_starts_a_route_query(runtime, monkeypatch):
    engine, factories, _ = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm-typo")
    task = create(engine)
    assert task["state"] == "failed" and "rules 或 autoglm" in task["message"]
    assert not factories and not engine.transit_device.calls


def test_place_choice_resume_keeps_same_mode_and_adapter_despite_new_process_default(runtime, monkeypatch):
    engine, factories, module = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    original = module.AutoGLMTransitDevice
    class Choosing(original):
        def __init__(self, *args):
            super().__init__(*args)
            self.choice = True
    module.AutoGLMTransitDevice = Choosing
    task = create(engine)
    assert task["state"] == "awaiting_place_choice"
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "rules")
    choice = task["place_choice"]
    task = settle(engine, engine.choose_transit_place(task["id"], {"query_id": task["query_id"],
        "choice_nonce": choice["choice_nonce"], "candidate_id": "east"}))
    assert len(factories) == 1 and task["transit_executor"] == "autoglm"
    assert task["state"] == "completed" and not engine.transit_device.calls
    assert len([call for call in factories[0].calls if isinstance(call, tuple)]) == 2


def test_explicit_requery_can_choose_new_default_and_archives_old_mode(runtime, monkeypatch):
    engine, factories, _ = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    first = create(engine)
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "rules")
    second = settle(engine, engine.requery_transit(first["id"], {"query_id": first["query_id"]}))
    assert second["query_id"] != first["query_id"] and second["transit_executor"] == "rules"
    assert second["transit_history"][-1]["transit_executor"] == "autoglm"
    assert not second["transit_agent_events"] and second["transit_agent_step"] == 0
    assert len(factories) == 1 and engine.transit_device.calls


def test_new_autoglm_query_has_a_new_adapter_not_a_replayed_action_session(runtime, monkeypatch):
    engine, factories, _ = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    first = create(engine)
    second = settle(engine, engine.requery_transit(first["id"], {"query_id": first["query_id"]}))
    assert second["state"] == "completed" and len(factories) == 2
    assert factories[0] is not factories[1]


def test_agent_finish_result_still_uses_original_partial_verifier(runtime, monkeypatch):
    engine, factories, module = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    original = module.AutoGLMTransitDevice
    class Partial(original):
        def __init__(self, *args):
            super().__init__(*args)
            self.kind = "PARTIAL"
    module.AutoGLMTransitDevice = Partial
    task = create(engine)
    assert task["state"] == "partial" and not task["route_verified"]
    assert task["transit_result"]["missing_fields"] and not engine.transit_device.calls


def test_old_query_agent_events_cannot_change_new_query(runtime, monkeypatch):
    engine, _, _ = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    first = create(engine)
    callback = engine.autoglm_transit_device.agent_progress_callback
    second = settle(engine, engine.requery_transit(first["id"], {"query_id": first["query_id"]}))
    before = engine.store.events(second["id"])
    with pytest.raises(Cancelled): callback({"step": 99, "action": "Tap", "status": "executed"})
    assert engine.store.events(second["id"]) == before


def test_agent_and_another_goal_cannot_own_the_phone_together(runtime, monkeypatch):
    engine, factories, module = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    original = module.AutoGLMTransitDevice
    entered = threading.Event()
    gate = threading.Event()
    class Blocked(original):
        def __init__(self, *args):
            super().__init__(*args)
            self.entered = entered
            self.gate = gate
    module.AutoGLMTransitDevice = Blocked
    task = engine.create_goal("在广州从广州塔去体育西路，坐地铁", "agent-ownership-01")
    assert entered.wait(2)
    try:
        with pytest.raises(WorkflowError, match="完成或取消"):
            engine.create_goal("在广州从广州塔去体育西路，坐地铁", "agent-ownership-02")
        assert len(factories) == 1 and not engine.transit_device.calls
    finally:
        gate.set()
        settle(engine, task)


def test_legacy_task_without_mode_does_not_adopt_autoglm_during_source_view(runtime, monkeypatch):
    engine, factories, _ = runtime
    task = create(engine)
    del task["transit_executor"]
    engine.store.save(task, "legacy fixture")
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    task = settle(engine, engine.show_transit(task["id"], task["query_id"]))
    assert task["state"] == "completed" and not factories
    assert engine.transit_device.calls[-1] == ("show", task["query_id"])


def test_agent_public_progress_is_bounded_and_cancelled_steps_cannot_publish(runtime, monkeypatch):
    engine, factories, module = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    original = module.AutoGLMTransitDevice
    entered, gate = threading.Event(), threading.Event()
    class Blocked(original):
        def __init__(self, *args):
            super().__init__(*args)
            self.entered, self.gate = entered, gate
    module.AutoGLMTransitDevice = Blocked
    task = engine.create_goal("在广州从广州塔去体育西路，坐地铁", "agent-progress-01")
    assert entered.wait(2)
    callback = factories[0].agent_progress_callback
    try:
        for step in range(2, 25):
            callback({"step": step, "action": "Wait", "status": "executed", "executed_point": None,
                      "reason": "no_screen_change", "thought": "must-not-persist"})
        current = engine.store.get(task["id"])
        assert len(current["transit_agent_events"]) == 20
        assert current["transit_agent_events"][0]["step"] == 5
        assert current["transit_agent_last_action"]["reason_code"] == "no_screen_change"
        assert "thought" not in current["transit_agent_last_action"]
        before = engine.store.events(task["id"])
        with pytest.raises(WorkflowError): callback({"step": 25, "model": {"sensitive": "not-string"}})
        assert engine.store.events(task["id"]) == before
        engine.control(task["id"], "cancel")
        before = engine.store.events(task["id"])
        with pytest.raises(Cancelled): callback({"step": 25, "action": "Tap", "status": "executed"})
        assert engine.store.events(task["id"]) == before
    finally:
        gate.set()
        settle(engine, task)


def test_restart_preserves_mode_but_never_replays_an_agent_query(runtime, monkeypatch):
    engine, factories, _ = runtime
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "autoglm")
    task = create(engine)
    task.update(state="reading_transit", transit_result=None, route_verified=False)
    engine.store.save(task, "interrupted process fixture")
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "rules")
    restarted_device = Device()
    restarted = Engine(Store(engine.store.directory), restarted_device, Models())
    try:
        current = restarted.store.get(task["id"])
        assert current["state"] == "interrupted" and current["transit_executor"] == "autoglm"
        assert restarted.worker is None and restarted.autoglm_transit_device is None
        assert len(factories) == 1 and not restarted_device.calls
    finally:
        restarted.close()
