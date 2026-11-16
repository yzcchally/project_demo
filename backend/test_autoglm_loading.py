"""Offline native loading-dialog and executor recovery checks, never live proof."""
import base64
import json
import xml.etree.ElementTree as ET

import pytest

from autoglm.client import Decision
from autoglm.loading import loading_scene
from autoglm.protocol import Action
from device.amap import ID, PACKAGE
from domain import Cancelled, WorkflowError
from test_autoglm_executor import REQUEST, create


def loading_shot():
    def node(cls, rect, *, rid="", text="", clickable="false"):
        return ET.Element("node", {"class": cls, "bounds": rect, "package": PACKAGE,
                                   "resource-id": ID + rid if rid else "", "text": text,
                                   "content-desc": "", "visible-to-user": "true", "enabled": "true", "clickable": clickable})
    root = node("android.widget.FrameLayout", "[0,0][1200,2640]")
    view = node("android.widget.LinearLayout", "[322,1294][878,1499]", rid="loading_view")
    panel = node("android.view.ViewGroup", "[322,1294][878,1499]", rid="id_root")
    panel.extend([node("android.widget.ImageView", "[362,1350][455,1443]", rid="lottie_view"),
                  node("android.widget.TextView", "[495,1366][712,1428]", rid="loading_text", text="正在加载..."),
                  node("android.widget.ImageView", "[752,1354][838,1440]", rid="close", clickable="true")])
    view.append(panel); root.append(view)
    return {"nodes": list(root.iter("node")), "xml": ET.tostring(root, encoding="unicode"),
            "epoch": 0, "width": 1200, "height": 2640, "screen_width": 1200, "screen_height": 2640,
            "rotation": 0, "current_app": PACKAGE, "current_package": PACKAGE}


def loading_device(tmp_path):
    adapter, phone = create(tmp_path, stage=100)
    observe = adapter._agent_observe
    def current(checkpoint):
        if phone.stage != 100:
            shot = observe(checkpoint)
            shot.update(width=1200, height=2640, screen_width=1200, screen_height=2640)
            return shot
        checkpoint()
        shot = loading_shot(); oid = "offline-loading-" + str(len(adapter.observations))
        evidence = {"evidence_id": oid, "observation_id": oid, "query_id": adapter.query_id,
                    "route_id": None, "page_kind": "screen", "text": "正在加载..."}
        adapter.observations.append(evidence)
        shot.update(epoch=phone.context_epoch, observation_id=oid, evidence=evidence,
                    screenshot_base64=base64.b64encode(b"offline-native-loading-dialog").decode())
        return shot
    adapter._agent_observe = current
    return adapter, phone


def test_exact_native_loading_dialog_reports_only_actual_geometry():
    scene = loading_scene(loading_shot())
    assert scene["bounds"] == [322, 1294, 878, 1499]
    assert scene["cancel_bounds"] == [752, 1354, 838, 1440]
    assert scene["spinner_bounds"] == [362, 1350, 455, 1443]
    assert scene["cancel_node"].get("resource-id") == ID + "close"


@pytest.mark.parametrize("mutation", ["text", "class", "id", "hidden", "foreign", "large_spinner", "extra_child", "overlap", "foreign_app"])
def test_generic_spinners_or_unproved_dialogs_do_not_get_loading_authority(mutation):
    shot = loading_shot(); panel = shot["nodes"][2]
    if mutation == "text": panel[1].set("text", "支付加载中")
    elif mutation == "class": panel[1].set("class", "android.view.View")
    elif mutation == "id": panel[0].set("resource-id", ID + "another_spinner")
    elif mutation == "hidden": shot["nodes"][1].set("visible-to-user", "false")
    elif mutation == "foreign": panel[2].set("package", "other.app")
    elif mutation == "large_spinner": panel[0].set("bounds", "[323,1295][750,1498]")
    elif mutation == "extra_child": panel.append(ET.Element("node"))
    elif mutation == "overlap": panel[2].set("bounds", "[650,1354][736,1440]")
    else: shot["current_app"] = "other.app"
    assert loading_scene(shot) is None


def test_loading_skips_background_poi_facts_and_suppresses_background_focus():
    from test_autoglm_evidence import collector, compact_poi_shot
    shot = compact_poi_shot(); shot["nodes"][0].append(loading_shot()["nodes"][1])
    shot["nodes"] = list(shot["nodes"][0].iter("node"))
    adapter = collector()
    adapter.route_id, adapter.query_id, adapter.observations = None, "offline-query", []
    assert loading_scene(shot) is not None
    assert adapter._facts(shot, lambda: None) is None and adapter.state["places"] == {}
    info = adapter._model_screen_info(shot)
    assert info["loading"]["visible"] and not info["unique_focused_input"] and info["focused_input"] is None


def test_underlay_rejection_gets_new_decision_then_actual_cancel_and_type_recover(tmp_path):
    adapter, phone = loading_device(tmp_path)
    seen = []
    def decide(task, shot, history, checkpoint):
        info = shot["screen_info"]; seen.append((info, list(history)))
        if info["loading"]["visible"]:
            action = (Action("Tap", {"element": [662, 529]}) if info["loading_correction"]
                      else Action("Tap", {"element": [300, 125]}))
        elif phone.stage == 2:
            assert info["loading_recovery"] == {"cancelled": True, "action": "Tap"}
            action = Action("Type", {"text": REQUEST["origin"]})
        else:
            assert info["loading_recovery"] is None
            action = Action("Help", {"message": "结束离线恢复检查"})
        return Decision(action, "offline-loading-recovery", 1)
    adapter.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        adapter.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert phone.effects == [("Tap", 794, 1396), ("Type", REQUEST["origin"])]
    assert len(seen) == 4 and not seen[1][1]  # Rejected underlying Tap is absent from history.
    records = [json.loads(line) for line in (adapter.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert records[0]["reason_code"] == "loading_overlay_active" and records[0]["status"] == "rejected"
    confirmed = [record for record in records if record.get("loading_cancelled")]
    assert len(confirmed) == 1 and confirmed[0]["status"] == "executed"


def test_three_loading_blocked_proposals_stop_without_any_gui_effect(tmp_path):
    adapter, phone = loading_device(tmp_path)
    adapter.client.decide = lambda *args: Decision(Action("Type", {"text": REQUEST["origin"]}), "offline-loading-bound", 1)
    with pytest.raises(WorkflowError, match="有限重新决策上限"):
        adapter.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert not phone.effects and not adapter.history and adapter.steps == 3
    assert not adapter.state.get("loading_recovery")
    records = [json.loads(line) for line in (adapter.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert [record["recovery_scheduled"] for record in records] == [True, True, False]


def test_stuck_loading_timeout_stops_without_auto_cancel(tmp_path, monkeypatch):
    adapter, phone = loading_device(tmp_path)
    clock = [0.0]
    def monotonic():
        clock[0] += .01
        return clock[0]
    monkeypatch.setattr("autoglm.executor.time.monotonic", monotonic)
    calls = []
    def decide(*args):
        calls.append(1); clock[0] += 61
        return Decision(Action("Wait", {"duration": .001}), "offline-loading-timeout", 1)
    adapter.client.decide = decide
    with pytest.raises(WorkflowError, match="加载弹窗持续超过"):
        adapter.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert not phone.effects and len(calls) == 1 and not adapter.state.get("loading_recovery")
    records = [json.loads(line) for line in (adapter.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert records[-1]["reason_code"] == "loading_timeout" and records[-1]["status"] == "stopped"


@pytest.mark.parametrize("error", [Cancelled("用户取消"), WorkflowError("query_id 已变化")])
def test_cancel_or_query_replacement_before_loading_cancel_still_prevents_dispatch(tmp_path, error):
    adapter, phone = loading_device(tmp_path); cancelled = [False]
    def checkpoint():
        if cancelled[0]: raise error
    def decide(*args):
        cancelled[0] = True
        return Decision(Action("Tap", {"element": [662, 529]}), "offline-loading-cancel-guard", 1)
    adapter.client.decide = decide
    with pytest.raises(type(error)):
        adapter.query(REQUEST, "offline-task", "offline-query", checkpoint)
    assert not phone.effects and not adapter.state.get("loading_recovery")
