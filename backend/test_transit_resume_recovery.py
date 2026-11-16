"""Explicit map pause/resume recovery; all screens and sends are test doubles."""
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.runtime import Engine
from device.amap import AmapDevice
from device.huawei import ScreenContextChanged
from domain import Cancelled, WorkflowError
from storage.tasks import Store
from test_transit_workflow import Models, route_result, settle
from test_workflow import Device

REQUEST = {"origin": "广州塔", "destination": "体育西路", "city": "广州", "preference": "fewer_transfers"}


@pytest.fixture
def adapter(tmp_path):
    base = SimpleNamespace(context_epoch=0, evidence_root=tmp_path, transport=None,
                           recovery_callback=Mock())
    value = AmapDevice(base, Mock())
    value.check_ready = Mock()
    return value


def resume(base):
    base.context_epoch += 1
    base.transit_resume_epoch = base.context_epoch


def test_resume_discards_old_facts_choices_and_context_in_separate_attempt(adapter):
    old_choices = {"origin": {"name": "旧确认地点", "source": "current_location"}}
    def resolve(role, value, choices, checkpoint):
        if adapter.base.context_epoch == 0:
            assert choices == old_choices
            adapter.observations.append({"evidence_id": "stale", "text": "旧帧"})
            adapter.actions = 5
            adapter.route_id = "old-route"
            context = adapter.folder / "view-context.json"
            context.write_text('{"old":true}')
            adapter._detail_contexts[("task", "query", "old-route")] = {"old": True}
            resume(adapter.base)
            adapter._check(checkpoint)
        assert choices == {}
        assert adapter.actions == 5 and adapter.route_id is None
        assert adapter.folder.name == "resume-1"
        assert [e["page_kind"] for e in adapter.observations] == ["user_request"]
        assert not adapter._detail_contexts
        return {"kind": "clarification", "fields": ["origin"], "message": "请重新确认地点"}
    adapter._resolve_place = resolve
    result = adapter.query(REQUEST, "task", "query", lambda: None, old_choices)
    assert result["kind"] == "clarification" and result["context_epoch"] == 1
    root = adapter.base.evidence_root / "task" / "transit" / "query"
    discarded = json.loads(next(root.glob("discarded-attempt-*.json")).read_text())
    assert discarded["observations"][-1]["evidence_id"] == "stale"
    assert discarded["route_id"] == "old-route"
    assert not (root / "view-context.json").exists()
    assert len(list(root.glob("discarded-view-context-*.json"))) == 1
    event = adapter.base.recovery_callback.call_args.args[0]
    assert event["action"] == "restart_transit_query" and event["query_id"] == "query"


def test_long_pause_renews_read_time_but_does_not_renew_action_budget(adapter):
    def resolve(role, value, choices, checkpoint):
        if adapter.base.context_epoch == 0:
            adapter.started = time.monotonic() - 600
            adapter.actions = adapter.action_limit
            resume(adapter.base)
            adapter._check(checkpoint)
        assert time.monotonic() - adapter.started < 1
        adapter._action(checkpoint)
    adapter._resolve_place = resolve
    with pytest.raises(WorkflowError, match="操作已达"):
        adapter.query(REQUEST, "task", "query", lambda: None)
    assert adapter.base.recovery_callback.call_count == 1


def test_second_resume_stops_instead_of_looping_or_replaying(adapter):
    calls = []
    def resolve(role, value, choices, checkpoint):
        calls.append(adapter.base.context_epoch)
        resume(adapter.base)
        adapter._check(checkpoint)
    adapter._resolve_place = resolve
    with pytest.raises(ScreenContextChanged):
        adapter.query(REQUEST, "task", "query", lambda: None)
    assert calls == [0, 1]
    assert adapter.base.recovery_callback.call_count == 1
    assert adapter._query_in_progress is False


@pytest.mark.parametrize("change_epoch", [False, True])
def test_unmarked_context_change_never_restarts(adapter, change_epoch):
    def resolve(*args):
        if change_epoch:
            adapter.base.context_epoch += 1
        raise ScreenContextChanged("未知页面变化")
    adapter._resolve_place = Mock(side_effect=resolve)
    with pytest.raises(ScreenContextChanged):
        adapter.query(REQUEST, "task", "query", lambda: None)
    assert adapter._resolve_place.call_count == 1
    adapter.base.recovery_callback.assert_not_called()


def test_cancel_after_resume_does_not_begin_recovery(adapter):
    cancelled = False
    def check():
        if cancelled:
            raise Cancelled("用户取消")
    def resolve(*args):
        nonlocal cancelled
        resume(adapter.base)
        cancelled = True
        raise ScreenContextChanged("旧帧已失效")
    adapter._resolve_place = resolve
    with pytest.raises(Cancelled):
        adapter.query(REQUEST, "task", "query", check)
    adapter.base.recovery_callback.assert_not_called()
    assert not list(adapter.base.evidence_root.rglob("discarded-attempt-*.json"))


def test_late_returned_result_is_discarded_on_resume(adapter):
    calls = []
    def query(request, task_id, query_id, checkpoint, choices, **kwargs):
        calls.append(kwargs.get("attempt", 0))
        if len(calls) == 1:
            adapter.observations = [{"evidence_id": "old-model-answer"}]
            resume(adapter.base)
            return {"kind": "result", "stale": True}
        return {"kind": "clarification", "fields": ["origin"]}
    adapter._query = query
    result = adapter.query(REQUEST, "task", "query", lambda: None)
    assert calls == [0, 1] and result["kind"] == "clarification"
    assert "stale" not in result


def test_pause_resume_restarts_same_query_and_does_not_send_dingtalk(tmp_path, monkeypatch):
    monkeypatch.setenv("SILVERGUIDE_TRANSIT_EXECUTOR", "rules")
    base = Device()
    base.evidence_root = tmp_path
    base.context_epoch = 0
    base.transport = None
    engine = Engine(Store(tmp_path), base, Models())
    adapter = AmapDevice(base, engine.models)
    engine.transit_device = adapter
    adapter.check_ready = Mock()
    entered = threading.Event()
    gate = threading.Event()
    attempts = []
    def query(request, task_id, query_id, checkpoint, choices, **kwargs):
        adapter._query_epoch = base.context_epoch
        adapter.task_id, adapter.query_id = task_id, query_id
        adapter.folder = tmp_path / task_id / "transit" / query_id
        adapter.folder.mkdir(parents=True, exist_ok=True)
        adapter.actions = kwargs.get("action_offset", 0)
        adapter.observations = []
        attempts.append((query_id, kwargs.get("attempt", 0), choices))
        if len(attempts) == 1:
            entered.set()
            assert gate.wait(3)
            adapter.actions = 4
            adapter._check(checkpoint)
            raise AssertionError("stale query continued")
        assert kwargs["action_offset"] == 4 and choices == {}
        return route_result(task_id, query_id)
    adapter._query = query
    engine._share_transit_to_self = Mock(side_effect=AssertionError("resume must not auto-send"))
    try:
        task = engine.create_goal("在广州从广州塔去体育西路坐地铁，并发送到我的钉钉文件助手", "resume-integration-01")
        assert entered.wait(2)
        task = engine.store.get(task["id"])
        old_query = task["query_id"]
        engine.control(task["id"], "pause")
        gate.set()
        engine.control(task["id"], "resume")
        task = settle(engine, task)
        assert task["state"] == "completed" and task["route_verified"]
        assert task["query_id"] == old_query
        assert [a[1] for a in attempts] == [0, 1]
        assert task["transit_resume_restarts"] == 1
        assert "自动钉钉分享已暂停" in task["transit_recovery_notice"]
        assert task["dingtalk_requested"] is False and task["dingtalk_send_attempts"] == 0
        engine._share_transit_to_self.assert_not_called()
        assert base.transit_resume_epoch == base.context_epoch == 1
    finally:
        engine.cancelled = True
        engine.paused = False
        gate.set()
        with engine.condition:
            engine.condition.notify_all()
        if engine.worker:
            engine.worker.join(4)
        engine.close()


def test_failed_read_returning_after_resume_is_not_misreported_as_current_error(adapter):
    def observe(checkpoint):
        resume(adapter.base)
        raise WorkflowError("暂停前发出的读取返回失败")
    adapter.base.observe = observe
    def resolve(role, value, choices, checkpoint):
        if adapter.base.context_epoch == 0:
            adapter._observe(checkpoint)
        return {"kind": "clarification", "fields": ["origin"]}
    adapter._resolve_place = resolve
    assert adapter.query(REQUEST, "task", "query", lambda: None)["kind"] == "clarification"
    assert adapter.base.recovery_callback.call_args_list[0].args[0]["outcome"] == "attempting"


def test_place_choice_after_restart_keeps_remaining_action_allowance(adapter):
    def resolve(role, value, choices, checkpoint):
        if adapter.base.context_epoch == 0:
            adapter.actions = adapter.action_limit - 1
            resume(adapter.base)
            adapter._check(checkpoint)
        adapter._action(checkpoint)
        return {"kind": "choice", "role": "origin", "candidates": [{"name": "需重新确认的当前地点"}]}
    adapter._resolve_place = resolve
    assert adapter.query(REQUEST, "task", "query", lambda: None)["kind"] == "choice"
    assert adapter.actions == adapter.action_limit
    with pytest.raises(WorkflowError, match="操作已达"):
        adapter.query(REQUEST, "task", "query", lambda: None, {"origin": {"name": "用户已确认"}})
    assert adapter.base.recovery_callback.call_count == 2  # attempting + reobserved only
    assert [call.args[0]["outcome"] for call in adapter.base.recovery_callback.call_args_list] == ["attempting", "reobserved"]


def test_failed_restart_has_no_reobserved_or_success_event(adapter):
    def resolve(role, value, choices, checkpoint):
        if adapter.base.context_epoch == 0:
            resume(adapter.base)
            adapter._check(checkpoint)
        raise WorkflowError("新查询真实网络不可用")
    adapter._resolve_place = resolve
    with pytest.raises(WorkflowError, match="新查询真实网络不可用"):
        adapter.query(REQUEST, "task", "query", lambda: None)
    events = [call.args[0] for call in adapter.base.recovery_callback.call_args_list]
    assert [e["outcome"] for e in events] == ["attempting"]
    assert not any("成功" in e["message"] for e in events)


def home_environment(adapter, *, foreground="com.huawei.android.launcher/.UniHomeLauncher", policy="showing=false", missing=False, focus_success=True):
    from test_amap_adapter import map_activity_snapshot, node, xml
    state = {"home": False, "focused": False}
    adapter.base.transport = SimpleNamespace(last_bridge_reason="root_package", prefer_transit_bridge=False)
    def shell(*args):
        if args == ("dumpsys", "window", "policy"):
            return policy
        if args == ("dumpsys", "activity", "activities"):
            current = map_activity_snapshot(resumed=not state["home"])
            if state["home"]:
                current += f"mResumedActivity: ActivityRecord{{launcher u0 {foreground} t1}}\n"
                if missing:
                    current = current.replace("#71", "#99").replace(" t71", " t99")
            return current
        if args == ("dumpsys", "window", "displays"):
            return f"mCurrentFocus=Window{{launcher u0 {foreground}}}"
        if args == ("am", "task", "focus", "71"):
            state["focused"] = True
            if focus_success:
                state["home"] = False
            return ""
        raise AssertionError(args)
    adapter.base.shell = Mock(side_effect=shell)
    def observe(checkpoint):
        if state["home"]:
            raise WorkflowError("root package mismatch")
        return xml([node("高德有效观察")])
    adapter.base.observe = observe
    return state


def test_verified_home_restores_only_original_task_then_reads_everything_again(adapter):
    state = home_environment(adapter)
    attempts = []
    def resolve(role, value, choices, checkpoint):
        attempts.append(adapter.query_attempt)
        if len(attempts) == 1:
            adapter._observe(checkpoint)
            assert adapter._query_map_context["android_task_id"] == 71
            adapter.actions = 3
            state["home"] = True
            adapter._observe(checkpoint)
        assert choices == {} and adapter.actions == 4  # includes original-task focus
        assert adapter._query_map_context is None
        assert len(adapter.observations) == 1 and adapter.observations[0]["page_kind"] == "user_request"
        return {"kind": "clarification", "fields": ["origin"]}
    adapter._resolve_place = resolve
    result = adapter.query(REQUEST, "task", "query", lambda: None, {"origin": {"name": "旧选择"}})
    assert result["kind"] == "clarification" and result["context_epoch"] == 1
    assert attempts == [0, 1] and state["focused"]
    assert adapter.base.shell.call_args_list.count((("am", "task", "focus", "71"),)) == 1
    assert not any(call.args[:2] == ("am", "start") for call in adapter.base.shell.call_args_list)
    assert [c.args[0]["cause"] for c in adapter.base.recovery_callback.call_args_list] == ["home", "home"]


@pytest.mark.parametrize("options", [
    {"foreground": "com.huawei.email/.Composer"},
    {"foreground": "com.other.launcher/.Home"},
    {"foreground": "com.android.permissioncontroller/.GrantPermissionsActivity"},
    {"policy": "showing=true"},
    {"policy": "mDreamingLockscreen=true"},
    {"missing": True},
])
def test_home_recovery_rejects_foreign_app_lock_permissions_and_replaced_task(adapter, options):
    state = home_environment(adapter, **options)
    def resolve(role, value, choices, checkpoint):
        adapter._observe(checkpoint)
        state["home"] = True
        adapter._observe(checkpoint)
    adapter._resolve_place = resolve
    with pytest.raises(WorkflowError):
        adapter.query(REQUEST, "task", "query", lambda: None)
    assert not state["focused"]
    adapter.base.recovery_callback.assert_not_called()


def test_home_recovery_requires_recorded_identity_before_interruption(adapter):
    state = home_environment(adapter)
    state["home"] = True
    adapter._resolve_place = lambda *args: adapter._observe(args[-1])
    with pytest.raises(WorkflowError, match="不在前台"):
        adapter.query(REQUEST, "task", "query", lambda: None)
    assert not state["focused"]
    adapter.base.recovery_callback.assert_not_called()


def test_home_focus_failure_does_not_restart_or_report_reobservation(adapter, monkeypatch):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    state = home_environment(adapter, focus_success=False)
    def resolve(role, value, choices, checkpoint):
        adapter._observe(checkpoint)
        state["home"] = True
        adapter._observe(checkpoint)
    adapter._resolve_place = Mock(side_effect=resolve)
    with pytest.raises(WorkflowError, match="未恢复到原先记录"):
        adapter.query(REQUEST, "task", "query", lambda: None)
    assert adapter._resolve_place.call_count == 1
    assert [call.args[0]["outcome"] for call in adapter.base.recovery_callback.call_args_list] == ["attempting"]


def test_resume_and_home_share_one_total_recovery_allowance(adapter):
    state = home_environment(adapter)
    calls = []
    def resolve(role, value, choices, checkpoint):
        calls.append(adapter.query_attempt)
        adapter._observe(checkpoint)
        if len(calls) == 1:
            resume(adapter.base)
            adapter._check(checkpoint)
        state["home"] = True
        adapter._observe(checkpoint)
    adapter._resolve_place = resolve
    with pytest.raises(ScreenContextChanged):
        adapter.query(REQUEST, "task", "query", lambda: None)
    assert calls == [0, 1] and not state["focused"]
    assert [call.args[0]["cause"] for call in adapter.base.recovery_callback.call_args_list] == ["resume"]


def test_home_focus_consumes_action_budget_and_does_not_exceed_limit(adapter):
    state = home_environment(adapter)
    def resolve(role, value, choices, checkpoint):
        adapter._observe(checkpoint)
        adapter.actions = adapter.action_limit
        state["home"] = True
        adapter._observe(checkpoint)
    adapter._resolve_place = resolve
    with pytest.raises(WorkflowError, match="操作已达"):
        adapter.query(REQUEST, "task", "query", lambda: None)
    assert not state["focused"] and adapter.actions == adapter.action_limit


def test_cancel_after_home_detection_never_focuses_task(adapter):
    state = home_environment(adapter)
    cancelled = False
    def check():
        if cancelled:
            raise Cancelled("用户取消")
    def callback(event):
        nonlocal cancelled
        cancelled = True
    adapter.base.recovery_callback = Mock(side_effect=callback)
    def resolve(role, value, choices, checkpoint):
        adapter._observe(checkpoint)
        state["home"] = True
        adapter._observe(checkpoint)
    adapter._resolve_place = resolve
    with pytest.raises(Cancelled):
        adapter.query(REQUEST, "task", "query", check)
    assert not state["focused"]


def test_foreground_changed_after_home_detection_cannot_focus_old_task(adapter):
    state = home_environment(adapter)
    original = adapter.base.shell.side_effect
    changed = False
    def shell(*args):
        if changed and args == ("dumpsys", "activity", "activities"):
            return "mResumedActivity: ActivityRecord{foreign u0 com.huawei.email/.Composer t3}"
        return original(*args)
    adapter.base.shell.side_effect = shell
    def callback(event):
        nonlocal changed
        changed = True
    adapter.base.recovery_callback = Mock(side_effect=callback)
    def resolve(role, value, choices, checkpoint):
        adapter._observe(checkpoint)
        state["home"] = True
        adapter._observe(checkpoint)
    adapter._resolve_place = resolve
    with pytest.raises(WorkflowError, match="其他应用"):
        adapter.query(REQUEST, "task", "query", lambda: None)
    assert not state["focused"]


def test_home_animation_null_focus_is_reread_with_all_guards(adapter, monkeypatch):
    from test_amap_adapter import map_activity_snapshot
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    state = home_environment(adapter)
    state["home"] = True
    original = adapter.base.shell.side_effect
    reads = 0
    def shell(*args):
        nonlocal reads
        if args == ("dumpsys", "window", "displays"):
            reads += 1
            if reads == 1:
                return "  mCurrentFocus=null\n  mFocusedApp=ActivityRecord{x u0 com.huawei.android.launcher/.UniHomeLauncher t1}"
        return original(*args)
    adapter.base.shell.side_effect = shell
    context = {"android_task_id": 71, "activity": "com.autonavi.minimap/.ObservedMapActivity"}
    adapter._check_home_recovery(context, lambda: None)
    assert reads == 2
    assert sum(c.args == ("dumpsys", "window", "policy") for c in adapter.base.shell.call_args_list) == 2


@pytest.mark.parametrize("display", [
    "mCurrentFocus=null\nmFocusedApp=ActivityRecord{x u0 com.huawei.android.launcher/.UniHomeLauncher t1}",
    "mFocusedApp=ActivityRecord{x u0 com.huawei.android.launcher/.UniHomeLauncher t1}",
    "mCurrentFocus=Window{x u0 com.android.permissioncontroller/.GrantPermissionsActivity}\nmFocusedApp=ActivityRecord{x u0 com.huawei.android.launcher/.UniHomeLauncher t1}",
    "mCurrentFocus=Window{x u0 com.huawei.email/.Composer}",
])
def test_display_without_verified_launcher_focus_cannot_restore(adapter, monkeypatch, display):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    state = home_environment(adapter)
    state["home"] = True
    original = adapter.base.shell.side_effect
    adapter.base.shell.side_effect = lambda *args: display if args == ("dumpsys", "window", "displays") else original(*args)
    context = {"android_task_id": 71, "activity": "com.autonavi.minimap/.ObservedMapActivity"}
    with pytest.raises(WorkflowError, match="当前窗口不是可核对"):
        adapter._check_home_recovery(context, lambda: None)
    assert not state["focused"]
