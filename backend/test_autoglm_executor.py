"""Offline screen-reactive GUI doubles; no phone, screenshots or API are faked as live acceptance."""
import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from autoglm.client import Decision
from autoglm.config import Config
from autoglm.protocol import Action
from autoglm.executor import AutoGLMTransitDevice, _NativeCycleGuard, png_size, screen_signature
from device.amap import AmapDevice, ID, PACKAGE
from device.huawei import ScreenContextChanged
from domain import WorkflowError, Cancelled


REQUEST = {"origin": "甲地铁站", "destination": "乙地铁站", "city": "广州市", "preference": "recommended"}


def node(text, rect="[100,200][600,300]", **attrs):
    return ET.Element("node", {"text": text, "package": PACKAGE, "class": "android.widget.EditText",
                              "bounds": rect, "clickable": "true", "enabled": "true", **attrs})


class Phone:
    def __init__(self, path, stage=0):
        self.evidence_root = path; self.stage = stage; self.context_epoch = 0; self.effects = []
        self.transport = SimpleNamespace(prefer_transit_bridge=False, tap=self.tap)

    def tap(self, x, y):
        self.effects.append(("Tap", x, y)); self.stage = 2

    def shell(self, *args):
        self.effects.append(tuple(args))
        if args[0] == "monkey": self.stage = 1
        elif args[:2] == ("input", "keyevent"): self.stage = 4
        elif args[:2] == ("input", "swipe"): self.stage = 5
        return ""


class ScreenReactiveModel:
    """Its next action changes with actual observations, not a fixed iterator."""
    def __init__(self): self.calls = []

    def decide(self, task, observation, history, checkpoint):
        marker = base64.b64decode(observation["screenshot_base64"]).decode()
        self.calls.append((marker, list(history)))
        stage = int(marker.split(":")[-1])
        if stage == 0: action = Action("Launch", {"app": "高德地图"})
        elif stage == 1: action = Action("Tap", {"element": [300, 125]})
        elif stage == 2: action = Action("Type", {"text": "甲地铁站"})
        elif stage == 3: action = Action("Back", {})
        elif stage == 4: action = Action("Swipe", {"start": [800, 800], "end": [800, 400]})
        elif not history or history[-1]["action"] != "Wait": action = Action("Wait", {"duration": .001})
        else: action = Action("Finish", {"message": "请求核验"})
        return Decision(action, "offline-screen-double", 1)


class Device(AutoGLMTransitDevice):
    def check_ready(self, checkpoint=lambda: None): checkpoint()

    def _agent_observe(self, checkpoint):
        checkpoint()
        stage = self.base.stage
        if stage in {1, 2, 3}:
            ns = [node("起点" if stage < 3 else "甲地铁站", **{"resource-id": ID + "route_edit_summary_start", "focused": "true" if stage == 2 else "false"}),
                  node("乙地铁站", "[100,320][600,420]", **{"resource-id": ID + "route_edit_summary_end"})]
        elif stage >= 4:
            ns = [node("", "[0,100][1000,1900]", **{"content-desc": "公交详情页面", "class": "android.view.ViewGroup"})]
        else: ns = []
        root = ET.Element("hierarchy", nonce=str(len(self.observations))); root.extend(ns)
        oid = "test-observation-" + str(len(self.observations))
        path = self.folder / (oid + ".xml"); path.write_text(ET.tostring(root, encoding="unicode"))
        evidence = {"evidence_id": oid, "observation_id": oid, "query_id": self.query_id,
                    "route_id": self.route_id, "page_kind": "route_detail" if stage >= 4 else "screen",
                    "text": str(stage), "xml_path": str(path)}
        self.observations.append(evidence)
        return {"nodes": ns, "xml": path.read_text(), "epoch": self.base.context_epoch, "evidence": evidence,
                "observation_id": oid, "width": 1000, "height": 2000, "screen_width": 1000, "screen_height": 2000,
                "rotation": 0, "current_app": PACKAGE if stage else "com.example.demo",
                "current_package": PACKAGE if stage else "com.example.demo",
                "screenshot_base64": base64.b64encode(f"offline-test-screen:{stage}".encode()).decode()}

    def _enter(self, shot, target, text, role, checkpoint):
        checkpoint(); assert target.get("focused") == "true" and role == "origin"
        self.base.effects.append(("Type", text)); self.base.stage = 3


def create(tmp_path, model=None, stage=0, steps=20):
    phone = Phone(tmp_path, stage)
    adapter = Device(phone, Mock(), client=model or ScreenReactiveModel(),
                     config=Config(api_key="unit-test-placeholder", max_steps=steps))
    return adapter, phone


def test_real_loop_dispatches_screen_dependent_actions_and_feeds_new_observations(tmp_path, monkeypatch):
    monkeypatch.setattr(AmapDevice, "query", Mock(side_effect=AssertionError("rules fallback forbidden")))
    d, phone = create(tmp_path)
    with pytest.raises(WorkflowError, match="证据不足"):
        d.query(REQUEST, "task", "query", lambda: None)
    assert phone.effects[0][0] == "monkey"
    assert phone.effects[1] == ("Tap", 300, 250)
    assert phone.effects[2] == ("Type", "甲地铁站")
    assert phone.effects[3][:2] == ("input", "keyevent")
    assert phone.effects[4][:2] == ("input", "swipe")
    assert [x[0] for x in d.client.calls] == [f"offline-test-screen:{s}" for s in [0, 1, 2, 3, 4, 5, 5]]
    assert d.client.calls[2][1][-1]["action"] == "Tap"
    assert all(x["observation_id"].startswith("test-observation-") for x in d.history)
    records = [json.loads(l) for l in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert len([r for r in records if r["status"] == "executed"]) == 6
    assert all(r.get("after_observation_id") != r["before_observation_id"] for r in records if r["status"] == "executed")
    assert phone.transport.prefer_transit_bridge is False
    # Starting on an already-focused field changes the model's actual trace.
    d2, p2 = create(tmp_path / "second", stage=2)
    with pytest.raises(WorkflowError, match="证据不足"): d2.query(REQUEST, "task", "query", lambda: None)
    assert p2.effects[0] == ("Type", "甲地铁站")


@pytest.mark.parametrize("change", ["screen", "epoch", "orientation", "image_only"])
def test_late_proposal_is_discarded_after_screen_or_pause_change(tmp_path, change):
    d, phone = create(tmp_path, stage=1, steps=1)
    original = d._agent_observe
    def decide(*args):
        if change == "screen": phone.stage = 2
        elif change == "epoch": phone.context_epoch += 1
        elif change == "orientation":
            def rotated(checkpoint):
                s = original(checkpoint); s.update(rotation=1, width=2000, height=1000); return s
            d._agent_observe = rotated
        else:
            def new_pixels(checkpoint):
                s = original(checkpoint); s["screenshot_base64"] = "changed-pixels-same-native-tree"; return s
            d._agent_observe = new_pixels
        return Decision(Action("Tap", {"element": [300, 125]}), "test", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="预算"): d.query(REQUEST, "task", "query", lambda: None)
    assert phone.effects == []
    assert '"status": "discarded"' in (d.folder / "autoglm-actions.jsonl").read_text()


@pytest.mark.parametrize("error", [Cancelled("用户取消"), WorkflowError("query_id 已变化")])
def test_cancellation_or_query_replacement_during_model_call_never_dispatches(tmp_path, error):
    d, phone = create(tmp_path, stage=1)
    cancelled = [False]
    def checkpoint():
        if cancelled[0]: raise error
    def decide(*args):
        cancelled[0] = True
        return Decision(Action("Tap", {"element": [300, 125]}), "test", 1)
    d.client.decide = decide
    with pytest.raises(type(error)): d.query(REQUEST, "task", "query", checkpoint)
    assert phone.effects == []


def test_model_failure_and_rejected_dangerous_input_have_no_rule_fallback(tmp_path):
    d, phone = create(tmp_path, stage=2)
    d.client.decide = Mock(side_effect=WorkflowError("AutoGLM 请求超时"))
    with pytest.raises(WorkflowError, match="超时"): d.query(REQUEST, "task", "query", lambda: None)
    assert not phone.effects
    d.client.decide = Mock(return_value=Decision(Action("Type", {"text": "未经授权的其他地点"}), "test", 1))
    with pytest.raises(WorkflowError, match="不一致"): d.query(REQUEST, "task", "second-query", lambda: None)
    assert not phone.effects


def test_grounded_type_mismatch_gets_fresh_factual_correction_then_model_action_dispatches(tmp_path, monkeypatch):
    d, phone = create(tmp_path, stage=2)
    calls = []
    def decide(task, shot, history, checkpoint):
        info = shot["screen_info"]
        calls.append((json.loads(json.dumps(info)), list(history)))
        if phone.stage == 2:
            if not info["input_correction"]:
                action = Action("Type", {"text": REQUEST["destination"]})
            else:
                focused = info["focused_input"]
                assert focused["role"] == "origin" and focused["current_text"] == "起点"
                assert REQUEST["origin"] in focused["allowed_texts"]
                assert REQUEST["destination"] not in focused["allowed_texts"]
                assert not focused["already_matches"]
                assert info["input_correction"]["executed"] is False
                assert not history  # Rejection must not masquerade as execution.
                action = Action("Type", {"text": REQUEST["origin"]})
        else:
            assert phone.stage == 3
            assert info["focused_input"] is None  # Field no longer focused.
            action = Action("Help", {"message": "结束离线验证"})
        return Decision(action, "offline-input-recovery", 1)
    d.client.decide = decide
    monkeypatch.setattr(AmapDevice, "query", Mock(side_effect=AssertionError("rules fallback forbidden")))
    with pytest.raises(WorkflowError, match="用户协助"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert phone.effects == [("Type", REQUEST["origin"])]
    assert len(calls) == 3 and calls[0][0]["input_correction"] is None
    assert calls[1][0]["input_correction"]["reason_code"] == "input_value_mismatch"
    assert calls[2][1][-1]["action"] == "Type" and calls[2][1][-1]["params"] == {"text": REQUEST["origin"]}
    records = [json.loads(line) for line in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    rejection = next(record for record in records if record["status"] == "rejected")
    assert rejection["reason_code"] == "input_value_mismatch" and rejection["recovery_scheduled"]
    assert "text" not in rejection and "params" not in rejection
    assert [record["action"] for record in records if record["status"] == "executed"] == ["Type"]
    assert d.state["input_value_recoveries"] == 1


def test_three_grounded_type_value_rejections_stop_with_zero_gui_effects(tmp_path):
    d, phone = create(tmp_path, stage=2)
    d.client.decide = Mock(return_value=Decision(Action("Type", {"text": "任务以外的地点"}), "offline-rejection-bound", 1))
    with pytest.raises(WorkflowError, match="重新决策上限"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert not phone.effects and d.client.decide.call_count == 3
    assert not d.history and d.state["input_value_recoveries"] == 2
    records = [json.loads(line) for line in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert len(records) == 3 and all(record["status"] == "rejected" for record in records)
    assert [record["recovery_scheduled"] for record in records] == [True, True, False]
    assert "任务以外的地点" not in (d.folder / "autoglm-actions.jsonl").read_text()


def test_successful_waits_do_not_reset_query_wide_type_recovery_budget(tmp_path):
    d, phone = create(tmp_path, stage=2)
    calls = []
    def decide(task, shot, history, checkpoint):
        calls.append(shot["screen_info"])
        action = (Action("Wait", {"duration": .001}) if shot["screen_info"]["input_correction"]
                  else Action("Type", {"text": REQUEST["destination"]}))
        return Decision(action, "offline-query-recovery-bound", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="重新决策上限"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert not phone.effects and len(calls) == 5
    assert d.state["input_value_recoveries"] == 2
    assert [entry["action"] for entry in d.history] == ["Wait", "Wait"]


def test_redecided_correct_type_still_discards_a_changed_screen_before_dispatch(tmp_path):
    d, phone = create(tmp_path, stage=2, steps=2)
    calls = []
    def decide(task, shot, history, checkpoint):
        calls.append(shot["screen_info"])
        if len(calls) == 1:
            return Decision(Action("Type", {"text": REQUEST["destination"]}), "offline-recovery-freshness", 1)
        assert shot["screen_info"]["input_correction"]
        phone.stage = 1  # The observed focus disappeared while the model answered.
        return Decision(Action("Type", {"text": REQUEST["origin"]}), "offline-recovery-freshness", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="预算"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert not phone.effects and len(calls) == 2
    records = [json.loads(line) for line in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert [record["status"] for record in records] == ["rejected", "discarded"]


def test_unfocused_type_stops_and_unknown_tap_with_hints_has_bounded_redecisions(tmp_path):
    for stage, action in [(1, Action("Type", {"text": REQUEST["origin"]})),
                          (2, Action("Tap", {"element": [950, 950]}))]:
        d, phone = create(tmp_path / str(stage), stage=stage)
        d.client.decide = Mock(return_value=Decision(action, "offline-hard-stop", 1))
        with pytest.raises(WorkflowError):
            d.query(REQUEST, "offline-task", "offline-query", lambda: None)
        assert not phone.effects and d.client.decide.call_count == (1 if stage == 1 else 3)
        assert not d.state.get("input_value_recoveries")


def test_unknown_tap_without_verified_alternatives_remains_a_hard_stop(tmp_path):
    d, phone = create(tmp_path, stage=0)
    original = d._agent_observe
    def observe(checkpoint):
        frame = original(checkpoint)
        return {**frame, "current_app": PACKAGE, "current_package": PACKAGE}
    d._agent_observe = observe
    d.client.decide = Mock(return_value=Decision(Action("Tap", {"element": [950, 950]}), "offline-no-alternative", 1))
    with pytest.raises(WorkflowError):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert not phone.effects and d.client.decide.call_count == 1


def test_truncated_response_is_never_dispatched_and_next_decision_uses_fresh_screen(tmp_path):
    from autoglm.client import IncompleteResponse
    d, phone = create(tmp_path, stage=1)
    observed = []
    def decide(task, frame, history, checkpoint):
        observed.append(frame["observation_id"])
        if len(observed) == 1:
            raise IncompleteResponse("模型回复被截断")
        if phone.stage == 1:
            assert not history and frame["screen_info"]["model_correction"]["executed"] is False
            return Decision(Action("Tap", {"element": [300, 125]}), "offline-after-truncation", 1)
        return Decision(Action("Help", {"message": "结束离线验证"}), "offline-after-truncation", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert observed[0] != observed[1] and phone.effects == [("Tap", 300, 250)]
    assert d.state["incomplete_response_recoveries"] == 1
    assert [entry["action"] for entry in d.history] == ["Tap"]


def test_truncated_response_recovery_is_bounded_without_any_device_action(tmp_path):
    from autoglm.client import IncompleteResponse
    d, phone = create(tmp_path, stage=1)
    d.client.decide = Mock(side_effect=IncompleteResponse("模型回复被截断"))
    with pytest.raises(WorkflowError, match="这些回复均未执行"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert d.client.decide.call_count == 3 and not phone.effects and not d.history


def test_invalid_protocol_retries_with_fresh_observation_without_dispatching_invalid_output(tmp_path):
    from autoglm.protocol import ProtocolError
    d, phone = create(tmp_path, stage=1)
    observed = []
    def decide(task, frame, history, checkpoint):
        observed.append(frame["observation_id"])
        if len(observed) == 1:
            raise ProtocolError("invalid_syntax")
        if phone.stage == 1:
            assert not history
            assert frame["screen_info"]["model_correction"] == {"reason_code": "model_protocol_invalid", "executed": False}
            return Decision(Action("Tap", {"element": [300, 125]}), "offline", 1)
        return Decision(Action("Help", {"message": "结束离线验证"}), "offline", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert observed[0] != observed[1] and phone.effects == [("Tap", 300, 250)]
    assert [entry["action"] for entry in d.history] == ["Tap"]
    records = [json.loads(line) for line in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert records[0]["reason_code"] == "model_protocol_invalid"
    assert records[0]["protocol_error_code"] == "invalid_syntax"


def test_invalid_protocol_retries_are_bounded_and_cannot_execute(tmp_path):
    from autoglm.protocol import ProtocolError
    d, phone = create(tmp_path, stage=1)
    d.client.decide = Mock(side_effect=ProtocolError("invalid_syntax"))
    with pytest.raises(WorkflowError, match="这些回复均未执行"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert d.client.decide.call_count == 3 and not phone.effects and not d.history


def test_unterminated_finish_only_sends_safe_boolean_correction_on_fresh_observation(tmp_path):
    from autoglm.protocol import parse_action
    d, phone = create(tmp_path, stage=1)
    seen = []
    def decide(task, frame, history, checkpoint):
        seen.append(frame["observation_id"])
        if len(seen) == 1:
            parse_action('finish(message="private-token\n第二行")')
        correction = frame["screen_info"]["model_correction"]
        assert correction == {"reason_code": "model_protocol_invalid", "executed": False,
                              "finish_unterminated": True}
        assert 'private-token' not in json.dumps(correction) and not history
        return Decision(Action("Help", {"message": "测试结束"}), "offline", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert seen[0] != seen[1] and not phone.effects


def test_invalid_swipe_coordinates_cannot_dispatch_and_get_fresh_bounded_correction(tmp_path):
    from autoglm.protocol import parse_action
    d, phone = create(tmp_path, stage=1)
    observed = []
    def decide(task, frame, history, checkpoint):
        observed.append(frame['observation_id'])
        if len(observed) == 1:
            parse_action('do(action="Swipe", start=[1000,230], end=[450,230])')
        assert frame['screen_info']['model_correction']['invalid_swipe_coordinates'] is True
        assert not history
        return Decision(Action('Help', {'message': '测试结束'}), 'offline', 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match='用户协助'):
        d.query(REQUEST, 'offline-task', 'offline-query', lambda: None)
    assert observed[0] != observed[1] and not phone.effects


def test_invalid_swipe_coordinates_retries_are_bounded(tmp_path):
    from autoglm.protocol import parse_action
    d, phone = create(tmp_path, stage=1)
    def decide(*args):
        parse_action('do(action="Swipe", start=[1000,230], end=[450,230])')
    d.client.decide = Mock(side_effect=decide)
    with pytest.raises(WorkflowError, match='这些回复均未执行'):
        d.query(REQUEST, 'offline-task', 'offline-query', lambda: None)
    assert d.client.decide.call_count == 3 and not phone.effects and not d.history


def test_cancel_during_invalid_protocol_cannot_retry(tmp_path):
    from autoglm.protocol import ProtocolError
    d, phone = create(tmp_path, stage=1)
    cancelled = [False]
    def checkpoint():
        if cancelled[0]: raise Cancelled("用户取消")
    def decide(*args):
        cancelled[0] = True
        raise ProtocolError("invalid_syntax")
    d.client.decide = Mock(side_effect=decide)
    with pytest.raises(Cancelled):
        d.query(REQUEST, "offline-task", "offline-query", checkpoint)
    assert d.client.decide.call_count == 1 and not phone.effects
    assert not d.state.get("protocol_error_recoveries")


def test_cancel_after_truncation_cannot_schedule_another_model_call(tmp_path):
    from autoglm.client import IncompleteResponse
    d, phone = create(tmp_path, stage=1)
    cancelled = [False]
    def checkpoint():
        if cancelled[0]:
            raise Cancelled("用户取消")
    def decide(*args):
        cancelled[0] = True
        raise IncompleteResponse("模型回复被截断")
    d.client.decide = Mock(side_effect=decide)
    with pytest.raises(Cancelled):
        d.query(REQUEST, "offline-task", "offline-query", checkpoint)
    assert d.client.decide.call_count == 1 and not phone.effects
    assert not d.state.get("incomplete_response_recoveries")


def test_other_model_errors_remain_immediate_failures(tmp_path):
    d, phone = create(tmp_path, stage=1)
    d.client.decide = Mock(side_effect=WorkflowError("模型返回非法协议"))
    with pytest.raises(WorkflowError, match="非法协议"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert d.client.decide.call_count == 1 and not phone.effects


@pytest.mark.parametrize("error", [Cancelled("用户取消"), WorkflowError("query_id 已变化")])
def test_redecision_checks_cancel_and_query_identity_before_dispatch(tmp_path, error):
    d, phone = create(tmp_path, stage=2)
    cancelled = [False]
    def checkpoint():
        if cancelled[0]: raise error
    calls = []
    def decide(task, shot, history, checkpoint):
        calls.append(shot["screen_info"])
        if len(calls) == 1:
            return Decision(Action("Type", {"text": REQUEST["destination"]}), "offline-cancel-recovery", 1)
        assert shot["screen_info"]["input_correction"]
        cancelled[0] = True
        return Decision(Action("Type", {"text": REQUEST["origin"]}), "offline-cancel-recovery", 1)
    d.client.decide = decide
    with pytest.raises(type(error)):
        d.query(REQUEST, "offline-task", "offline-query", checkpoint)
    assert not phone.effects and len(calls) == 2


def home_observations(adapter, phone):
    """Actual known home controls in an offline observation, not model boxes."""
    from test_autoglm_safety import home_poi_search, home_transit_tile
    original = adapter._agent_observe
    def observe(checkpoint):
        if phone.stage != 1:
            return original(checkpoint)
        checkpoint()
        transit, _, _ = home_transit_tile()
        search, _ = home_poi_search()
        root = ET.Element("hierarchy"); root.extend([transit["nodes"][0], search["nodes"][0]])
        oid = "offline-home-" + str(len(adapter.observations))
        evidence = {"evidence_id": oid, "observation_id": oid, "query_id": adapter.query_id,
                    "route_id": None, "page_kind": "screen", "text": "公交地铁\n查找地点、公交、地铁"}
        adapter.observations.append(evidence)
        return {**transit, "nodes": list(root.iter("node")), "xml": ET.tostring(root, encoding="unicode"),
                "current_app": PACKAGE, "observation_id": oid, "evidence": evidence,
                "screenshot_base64": base64.b64encode(b"offline-home-controls").decode()}
    adapter._agent_observe = observe


def test_premature_known_home_transit_is_rejected_then_model_can_choose_actual_search(tmp_path):
    d, phone = create(tmp_path, stage=1)
    home_observations(d, phone)
    seen = []
    def decide(task, shot, history, checkpoint):
        info = shot["screen_info"]
        seen.append(info)
        if phone.stage != 1:
            return Decision(Action("Help", {"message": "结束离线验证"}), "offline-poi-prerequisite", 1)
        if not info["evidence_correction"]:
            action = Action("Tap", {"element": [120, 691]})
        else:
            assert info["evidence_correction"] == {"missing_roles": ["origin", "destination"],
                    "missing_fields": {"origin": ["city", "name"], "destination": ["city", "name"]}}
            assert not history  # The rejected transit entry was never executed.
            action = Action("Tap", {"element": [400, 610]})
        return Decision(action, "offline-poi-prerequisite", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert phone.effects == [("Tap", 480, 1610)]
    assert d.history[0]["params"] == {"element": [400, 610]}
    assert not d.state.get("transit_applied")
    records = [json.loads(line) for line in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert records[0]["status"] == "rejected" and records[0]["reason_code"] == "place_evidence_required"
    assert records[0]["recovery_scheduled"] and d.state["place_evidence_recoveries"] == 1


def test_unrecognized_point_can_reobserve_verified_alternatives_without_blind_dispatch(tmp_path):
    d, phone = create(tmp_path, stage=1)
    home_observations(d, phone)
    def decide(task, frame, history, checkpoint):
        if phone.stage != 1:
            return Decision(Action("Help", {"message": "结束离线验证"}), "offline-unknown-target", 1)
        correction = frame["screen_info"]["target_correction"]
        if correction:
            assert correction == {"reason_code": "unrecognized_target", "executed": False,
                                  "proposed_point": [800, 300]}
            assert not history and frame["screen_info"]["native_control_hints"]
            return Decision(Action("Tap", {"element": [400, 610]}), "offline-unknown-target", 1)
        return Decision(Action("Tap", {"element": [800, 300]}), "offline-unknown-target", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert phone.effects == [("Tap", 480, 1610)]
    assert d.state["unknown_target_recoveries"] == 1
    records = [json.loads(line) for line in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert records[0]["status"] == "rejected" and records[0]["reason_code"] == "unrecognized_target"
    assert not any(r.get("status") == "dispatching" and r.get("proposed_point") == [800, 300] for r in records)


def test_unrecognized_point_redecisions_are_bounded_and_do_not_execute(tmp_path):
    d, phone = create(tmp_path, stage=1)
    home_observations(d, phone)
    d.client.decide = Mock(return_value=Decision(Action("Tap", {"element": [800, 300]}), "offline-unknown-target", 1))
    with pytest.raises(WorkflowError, match="这些点击均未执行"):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert d.client.decide.call_count == 3 and not phone.effects and not d.history


@pytest.mark.parametrize("valid_point", [False, True])
def test_animation_does_not_hide_invalid_point_feedback_or_authorize_a_valid_stale_point(tmp_path, valid_point):
    d, phone = create(tmp_path, stage=1, steps=3)
    home_observations(d, phone)
    observe = d._agent_observe
    captures = []
    def animated(checkpoint):
        frame = observe(checkpoint)
        captures.append(frame["observation_id"])
        frame["screenshot_base64"] = base64.b64encode(f"offline-repaint-{len(captures)}".encode()).decode()
        return frame
    d._agent_observe = animated
    point = [400, 610] if valid_point else [800, 300]
    d.client.decide = Mock(return_value=Decision(Action("Tap", {"element": point}), "offline-animation", 1))
    with pytest.raises(WorkflowError):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    records = [json.loads(line) for line in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert len(records) == 3 and d.client.decide.call_count == 3
    assert all(r["status"] == ("discarded" if valid_point else "rejected") for r in records)
    assert bool(d.state.get("target_correction")) is not valid_point
    assert not phone.effects and not d.history


def test_changed_context_discards_an_invalid_original_point_without_old_feedback(tmp_path):
    d, phone = create(tmp_path, stage=1, steps=1)
    home_observations(d, phone)
    def decide(*args):
        phone.context_epoch += 1
        return Decision(Action("Tap", {"element": [800, 300]}), "offline-context-change", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError):
        d.query(REQUEST, "offline-task", "offline-query", lambda: None)
    records = [json.loads(line) for line in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert len(records) == 1 and records[0]["status"] == "discarded"
    assert not d.state.get("target_correction") and not phone.effects and not d.history


def checked_places():
    return {role: {"name": REQUEST[role], "city": REQUEST["city"], "source": "explicit",
                   "evidence_refs": {"name": [role + "-detail"], "city": [role + "-detail"]}}
            for role in ("origin", "destination")}


def native_route_list(origin="我的位置", *, selected=True):
    from test_autoglm_safety import node as ui_node, shot as ui_shot
    return ui_shot(
        ui_node(origin, desc="起点输入栏", rect="[100,100][600,200]", cls="android.widget.EditText",
                **{"resource-id": ID + "route_edit_summary_start"}),
        ui_node(REQUEST["destination"], desc="终点输入栏", rect="[600,100][1100,200]", cls="android.widget.EditText",
                **{"resource-id": ID + "route_edit_summary_end"}),
        ui_node("公共交通", rect="[200,250][400,350]", cls="android.widget.TextView",
                **{"resource-id": ID + "route_input_conbine_text", "selected": str(selected).lower()}),
        ui_node(desc="9分钟3号线2站¥2", rect="[0,600][1200,950]"), current_app=PACKAGE)


@pytest.mark.parametrize("origin,selected", [("我的位置", True), (REQUEST["origin"], True), (REQUEST["origin"], False)])
def test_route_context_reads_actual_native_fields_and_selected_mode(tmp_path, origin, selected):
    d, phone = create(tmp_path)
    d.request = REQUEST
    d.state = {"places": checked_places(), "choices": {}, "adapter": d}
    info = d._model_screen_info(native_route_list(origin, selected=selected))
    assert info["on_route_page"]
    assert info["route_inputs"] == {"origin": origin, "destination": REQUEST["destination"]}
    assert info["route_inputs_match"] == {"origin": origin == REQUEST["origin"], "destination": True}
    assert info["observed_public_transit"] is selected
    assert not phone.effects


@pytest.mark.parametrize("origin,accepted", [("广州市甲(地铁站)", True), ("甲地铁站", True),
                                             ("北京市甲地铁站", False), ("甲地铁站A口", False),
                                             ("我的位置", False), ("广州市其他地铁站", False)])
def test_selected_route_keeps_resolved_poi_binding_with_exact_administrative_city_prefix(tmp_path, origin, accepted):
    d, phone = create(tmp_path)
    d.request = REQUEST
    d.state = {"places": checked_places(), "filter": "高德推荐"}
    d.query_id = "current-query"
    frame = native_route_list(origin)
    frame["evidence"] = {"evidence_id": "actual-list", "query_id": d.query_id}
    card = frame["nodes"][-1]
    d._route_cards = lambda shot: [card]
    grounded = SimpleNamespace(node=card, role="route")
    if accepted:
        source = d._selected_route(frame, grounded)
        assert source["query_id"] == d.query_id and source["route_id"] == d.route_id
        assert "起点：" + origin in source["text"]
    else:
        with pytest.raises(WorkflowError, match="起终点.*不一致"):
            d._selected_route(frame, grounded)
    assert not phone.effects


def test_rejected_wrong_route_can_reobserve_and_let_model_correct_origin(tmp_path):
    d, phone = create(tmp_path, stage=1)
    original = d._agent_observe
    def observe(checkpoint):
        if phone.stage != 1:
            return original(checkpoint)
        frame = native_route_list()
        oid = "offline-route-" + str(len(d.observations))
        evidence = {"evidence_id": oid, "observation_id": oid, "query_id": d.query_id,
                    "route_id": None, "page_kind": "route_list", "text": "我的位置\n乙地铁站\n9分钟3号线2站¥2"}
        d.observations.append(evidence)
        return {**frame, "evidence": evidence, "observation_id": oid,
                "screenshot_base64": base64.b64encode(b"offline-wrong-route").decode()}
    d._agent_observe = observe
    d.query_id = "offline-query"
    d.state = {"adapter": d, "places": checked_places(), "choices": {}, "selected_list": None, "filter": "高德推荐"}
    def decide(task, frame, history, checkpoint):
        if phone.stage != 1:
            return Decision(Action("Help", {"message": "结束离线验证"}), "offline-route-correction", 1)
        if frame["screen_info"]["route_correction"]:
            assert not history and frame["screen_info"]["route_inputs_match"]["origin"] is False
            return Decision(Action("Tap", {"element": [200, 62]}), "offline-route-correction", 1)
        return Decision(Action("Tap", {"element": [500, 300]}), "offline-route-correction", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        d.query(REQUEST, "offline-task", d.query_id, lambda: None)
    assert phone.effects == [("Tap", 240, 148)]
    assert d.state["route_precondition_recoveries"] == 1
    assert d.history[0]["params"] == {"element": [200, 62]}


@pytest.mark.parametrize("both_places_checked", [False, True])
def test_actual_poi_route_footer_uses_same_place_proof_prerequisite(tmp_path, both_places_checked):
    from test_autoglm_safety import fixed_footer_frame
    d, phone = create(tmp_path, stage=1)
    request = {"origin": "体育西路地铁站", "destination": "广州塔地铁站", "city": "广州市",
               "preference": "fewer_transfers"}
    original = d._agent_observe
    def observe(checkpoint):
        if phone.stage != 1:
            return original(checkpoint)
        frame, _ = fixed_footer_frame()
        oid = "offline-footer-" + str(len(d.observations))
        evidence = {"evidence_id": oid, "observation_id": oid, "query_id": d.query_id,
                    "route_id": None, "page_kind": "screen", "text": "体育西路(地铁站)\n广州市天河区"}
        d.observations.append(evidence)
        return {**frame, "current_app": PACKAGE, "observation_id": oid, "evidence": evidence,
                "xml": ET.tostring(frame["nodes"][0], encoding="unicode"),
                "screenshot_base64": base64.b64encode(b"offline-native-footer").decode()}
    d._agent_observe = observe
    d.query_id = "offline-query"
    places = ({role: {"name": request[role], "city": request["city"], "source": "explicit",
                     "evidence_refs": {"name": [role + "-detail"], "city": [role + "-detail"]}}
               for role in ("origin", "destination")} if both_places_checked else {})
    d.state = {"adapter": d, "places": places, "choices": {}, "selected_list": None}
    def decide(task, frame, history, checkpoint):
        if phone.stage != 1:
            return Decision(Action("Help", {"message": "结束离线验证"}), "offline-footer-proof", 1)
        info = frame["screen_info"]
        if not both_places_checked:
            assert not any(h["role"] == "route" for h in info["native_control_hints"])
        if info["evidence_correction"]:
            assert not history and info["evidence_correction"]["missing_roles"] == ["destination"]
            return Decision(Action("Back", {}), "offline-footer-proof", 1)
        return Decision(Action("Tap", {"element": [904, 961]}), "offline-footer-proof", 1)
    d.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        d.query(request, "offline-task", d.query_id, lambda: None)
    assert phone.effects == ([("Tap", 1084, 2537)] if both_places_checked
                             else [("input", "keyevent", "KEYCODE_BACK")])
    assert bool(d.state.get("place_evidence_recoveries")) is not both_places_checked


@pytest.mark.parametrize("proof", ["complete", "values_without_refs", "confirmed_address_missing"])
def test_home_transit_prerequisite_uses_required_field_proof_not_just_names(tmp_path, proof):
    d, phone = create(tmp_path, stage=1)
    home_observations(d, phone)
    places = checked_places()
    choices = {}
    if proof == "values_without_refs": places["origin"]["evidence_refs"] = {}
    if proof == "confirmed_address_missing": choices = {"destination": {"name": REQUEST["destination"], "address": "用户确认的站口"}}
    # A resumed query retains previously collected, query-bound POI facts.
    d.query_id = "offline-query"
    d.state = {"places": places, "choices": choices, "adapter": d, "selected_list": None}
    d.observations = [{"evidence_id": role + "-detail", "query_id": d.query_id,
                       "page_kind": "place_detail", "text": REQUEST[role] + "\n广州市"}
                      for role in ("origin", "destination")]
    def decide(task, shot, history, checkpoint):
        if phone.stage != 1:
            return Decision(Action("Help", {"message": "结束离线验证"}), "offline-proof-gate", 1)
        return Decision(Action("Tap", {"element": [120, 691]}), "offline-proof-gate", 1)
    d.client.decide = Mock(side_effect=decide)
    if proof == "complete":
        with pytest.raises(WorkflowError, match="用户协助"):
            d.query(REQUEST, "offline-task", d.query_id, lambda: None, choices=choices)
        assert phone.effects == [("Tap", 144, 1824)] and d.state["transit_applied"]
        assert not d.state.get("place_evidence_recoveries")
    else:
        with pytest.raises(WorkflowError, match="地点详情证据仍不完整"):
            d.query(REQUEST, "offline-task", d.query_id, lambda: None, choices=choices)
        assert not phone.effects and d.client.decide.call_count == 3
        assert d.state["place_evidence_recoveries"] == 2
        missing = d._missing_place_fields()
        assert missing == ({"origin": ["city", "name"]} if proof == "values_without_refs" else {"destination": ["address"]})
        records = [json.loads(line) for line in (d.folder / "autoglm-actions.jsonl").read_text().splitlines()]
        assert len(records) == 3 and all(record["reason_code"] == "place_evidence_required" for record in records)
        assert [record["recovery_scheduled"] for record in records] == [True, True, False]


def test_place_retry_budget_renews_only_for_strictly_reduced_missing_evidence(tmp_path):
    d, _ = create(tmp_path)
    d.state = {"place_evidence_recoveries": 2}
    d._missing_place_fields = Mock(return_value={"origin": ["name", "city"], "destination": ["name", "city"]})
    assert d._place_evidence_retry_count() == 2
    # Re-observing without newly verified fields does not renew anything.
    assert d._place_evidence_retry_count() == 2
    # A verified origin changes the missing set from four fields to two.
    d._missing_place_fields.return_value = {"destination": ["name", "city"]}
    assert d._place_evidence_retry_count() == 0
    d.state["place_evidence_recoveries"] = 2
    assert d._place_evidence_retry_count() == 2
    # Losing or oscillating evidence must not let a loop replenish retries.
    d._missing_place_fields.return_value = {"origin": ["name", "city"], "destination": ["name", "city"]}
    assert d._place_evidence_retry_count() == 2
    d._missing_place_fields.return_value = {"destination": ["name", "city"]}
    assert d._place_evidence_retry_count() == 2
    d._missing_place_fields.return_value = {"destination": ["city"]}
    assert d._place_evidence_retry_count() == 0


def test_no_progress_is_bounded_and_help_is_not_success(tmp_path):
    d, phone = create(tmp_path, stage=4)
    d.client.decide = Mock(return_value=Decision(Action("Wait", {"duration": .001}), "test", 1))
    with pytest.raises(WorkflowError, match="重复操作"): d.query(REQUEST, "task", "query", lambda: None)
    assert d.steps == 3 and not phone.effects
    d.client.decide = Mock(return_value=Decision(Action("Help", {"message": "接管"}), "test", 1))
    with pytest.raises(WorkflowError, match="用户协助"): d.query(REQUEST, "task", "new-query", lambda: None)
    assert not phone.effects


def test_native_cycle_guard_stops_repeated_executed_taps_despite_pixel_animation():
    guard = _NativeCycleGuard(); tap = Action('Tap', {'element':[499,86]})
    target = SimpleNamespace(role='query')
    def frame(pixel):
        return {'epoch':0,'width':1200,'height':2640,'rotation':0,'current_app':PACKAGE,
                'xml':'<hierarchy window-id="1"><node text="广州塔地铁站" /></hierarchy>',
                'screenshot_base64':pixel}
    assert not guard.repeated(tap,target,frame('1'),frame('2'))
    assert not guard.repeated(tap,target,frame('3'),frame('4'))
    assert guard.repeated(tap,target,frame('5'),frame('6'))


def test_native_cycle_guard_handles_alternation_progress_and_visual_swipes():
    guard = _NativeCycleGuard(); tap = Action('Tap', {'element':[499,86]})
    target = SimpleNamespace(role='query')
    def frame(text,epoch=0):
        return {'epoch':epoch,'width':1200,'height':2640,'rotation':0,'current_app':PACKAGE,
                'xml':f'<hierarchy window-id="1"><node text="{text}" /></hierarchy>',
                'screenshot_base64':text}
    a,b=frame('甲'),frame('乙')
    for _ in range(2):
        assert not guard.repeated(tap,target,a,b)
        assert not guard.repeated(tap,target,b,a)
    assert guard.repeated(tap,target,a,b)
    guard.note_progress({('place','origin','city','广州市')})
    assert not guard.repeated(tap,target,a,b)
    # Rediscovering the same place never replenishes the budget again.
    guard.note_progress({('place','origin','city','广州市')})
    assert not guard.repeated(tap,target,a,b)
    assert guard.repeated(tap,target,a,b)
    swipe=Action('Swipe',{'start':[500,800],'end':[500,300]})
    assert all(not guard.repeated(swipe,target,a,a) for _ in range(5))
    assert not guard.repeated(tap,target,a,frame('甲',epoch=1))


def test_bridge_metadata_is_not_screen_change_but_window_and_content_are(tmp_path):
    d, _ = create(tmp_path, stage=1); d.query_id = "q"; d.folder = tmp_path
    a = d._agent_observe(lambda: None); b = d._agent_observe(lambda: None)
    assert a["xml"] != b["xml"] and screen_signature(a) == screen_signature(b)
    b["xml"] = b["xml"].replace('<hierarchy ', '<hierarchy window-id="new" ')
    assert screen_signature(a) != screen_signature(b)


def test_missing_key_is_rejected_before_device_access(tmp_path, monkeypatch):
    monkeypatch.delenv("SILVERGUIDE_AUTOGLM_API_KEY", raising=False)
    base = Mock()
    with pytest.raises(WorkflowError, match="缺少"): AutoGLMTransitDevice(base, Mock())
    assert not base.mock_calls


def test_execute_rechecks_epoch_after_pause_checkpoint_before_dispatch(tmp_path):
    d, phone = create(tmp_path)
    def checkpoint(): phone.context_epoch += 1
    with pytest.raises(ScreenContextChanged):
        d._execute(SimpleNamespace(kind="Launch"), checkpoint)
    assert not phone.effects


def test_screenshot_geometry_rejects_non_png_or_zero_dimensions():
    import struct
    with pytest.raises(WorkflowError): png_size(b"not-png")
    with pytest.raises(WorkflowError): png_size(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 0, 2000))


@pytest.mark.parametrize('rotation,expected',[(0,(1000,2000,0)),(1,(2000,1000,1))])
def test_actual_geometry_uses_override_and_current_orientation(tmp_path,rotation,expected):
    d,phone=create(tmp_path)
    phone.shell=lambda *a:'Physical size: 1200x2400\nOverride size: 1000x2000' if a==('wm','size') else f'SurfaceOrientation: {rotation}'
    assert d._geometry()==expected


def test_bitmap_device_size_mismatch_stops_before_any_model_decision(tmp_path):
    import struct
    d,phone=create(tmp_path)
    phone.shell=lambda *a: ('Physical size: 1000x2000' if a==('wm','size') else
                            'SurfaceOrientation: 0' if a==('dumpsys','input') else
                            'mResumedActivity: ActivityRecord{abc u0 com.autonavi.minimap/.Main t1}')
    phone.observe=Mock(return_value='<hierarchy><node package="com.autonavi.minimap" bounds="[0,0][1000,2000]"/></hierarchy>')
    d.folder=tmp_path;d.query_id='q'
    d._png=lambda:b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',1200,2400)
    with pytest.raises(WorkflowError,match='尺寸不一致'):
        AutoGLMTransitDevice._agent_observe(d,lambda:None)
    assert not d.client.calls


def test_real_ambiguous_poi_rows_return_existing_choice_contract_without_clicking(tmp_path):
    from test_amap_adapter import place_rows
    d,phone=create(tmp_path)
    d.request={**REQUEST,'origin':'市图书馆'};d.state={'places':{},'choices':{}}
    shot=place_rows(['广州市东区东路1号','广州市西区西路2号'])
    shot['nodes'].append(node('搜索框，市图书馆'));shot['current_app']=PACKAGE
    out=d._facts(shot,lambda:None)
    assert out['kind']=='choice' and out['role']=='origin' and len(out['candidates'])==2
    assert not phone.effects
