"""Coordinate-free launch/wait do not borrow an exemption for coordinate actions.

All observations/devices here are offline doubles. These regressions are not
real API or phone acceptance and never read local credentials.
"""
import base64
import copy
import json
import xml.etree.ElementTree as ET
from unittest.mock import Mock

import pytest

from autoglm.client import Decision
from autoglm.executor import proposal_is_current
from autoglm.protocol import Action
from device.amap import AmapDevice, PACKAGE
from domain import Cancelled, WorkflowError
from test_autoglm_executor import REQUEST, create, node


def detail_animation_frame():
    from io import BytesIO
    from PIL import Image
    root=ET.Element('hierarchy',{'window-id':'1'})
    detail=node('',rect='[0,0][1000,2000]',**{'class':'android.view.ViewGroup','content-desc':'公交详情页面','clickable':'false'})
    root.append(detail)
    scroll=node('',rect='[0,1200][1000,1800]',**{'class':'com.autonavi.minimap.ajx3.widget.view.SpringHorizontalScrollView','clickable':'false'})
    group=node('',rect='[0,1200][1000,1800]',**{'class':'android.view.ViewGroup'})
    scroll.append(group);detail.append(scroll)
    for i,(name,eta,x) in enumerate([('第一班','即将进站',100),('第二班','预计14:01进站',700)]):
        owner=node('',rect=f'[{x},1300][{x+200},1500]',**{'class':'android.view.ViewGroup'})
        caption=node('',rect=f'[{x},1320][{x+100},1450]',**{'class':'android.view.ViewGroup'})
        caption.append(node(name,rect=f'[{x},1320][{x+100},1360]',**{'class':'android.view.View','clickable':'false'}))
        caption.append(node(eta,rect=f'[{x},1400][{x+100},1440]',**{'class':'android.view.View','clickable':'false'}))
        icon=node('',rect=f'[{x+150},1320][{x+180},1350]',**{'class':'android.widget.ImageView','clickable':'false'})
        owner.extend([caption,icon]);group.append(owner)
    img=Image.new('RGB',(1000,2000),'white'); out=BytesIO();img.save(out,format='PNG')
    return {'epoch':0,'width':1000,'height':2000,'screen_width':1000,'screen_height':2000,'rotation':0,
            'current_app':PACKAGE,'current_package':PACKAGE,'status_bar_frame':[0,0,1000,100],
            'xml':ET.tostring(root,encoding='unicode'),'screenshot_base64':base64.b64encode(out.getvalue()).decode(),
            'evidence':{'query_id':'q'}}


def paint_detail_pixels(frame, points):
    from io import BytesIO
    from PIL import Image
    img=Image.open(BytesIO(base64.b64decode(frame['screenshot_base64']))).convert('RGB')
    for point in points: img.putpixel(point,(0,180,100))
    out=BytesIO();img.save(out,format='PNG');frame['screenshot_base64']=base64.b64encode(out.getvalue()).decode()


@pytest.mark.parametrize('change',['none','pixels_outside','native','unselected','crosses_icon','other_query'])
@pytest.mark.parametrize('kind',['Swipe','Finish'])
def test_detail_action_ignores_only_bound_arrival_icons(change,kind):
    from types import SimpleNamespace
    from autoglm.freshness import visual_decorations_only
    before=detail_animation_frame();after=copy.deepcopy(before)
    # Both live icons and a permitted system status-bar pixel can animate.
    paint_detail_pixels(after,[(260,1330),(860,1330),(800,50)])
    adapter=SimpleNamespace(query_id='q',route_id='r')
    state={'adapter':adapter,'selected_list':{'query_id':'q','route_id':'r'}}
    action=(Action('Swipe',{'start':[500,800],'end':[500,300]}) if kind=='Swipe'
            else Action('Finish',{'message':'请求核验'}))
    if change=='pixels_outside':paint_detail_pixels(after,[(400,1400)])
    elif change=='native':after['xml']=after['xml'].replace('即将进站','预计14:05进站')
    elif change=='unselected':state['selected_list']=None
    elif change=='crosses_icon':
        if kind=='Finish': pytest.skip('Finish does not touch a swipe path')
        action=Action('Swipe',{'start':[260,800],'end':[260,300]})
    elif change=='other_query':after['evidence']['query_id']='other'
    assert visual_decorations_only(action,before,after,request=REQUEST,state=state) is (change=='none')


def actions():
    return [Action("Launch", {"app": "高德地图"}), Action("Wait", {"duration": .001}),
            Action("Help", {"message": "请本人核对"}), Action("Tap", {"element": [300, 125]}),
            Action("Type", {"text": "甲地铁站"}),
            Action("Swipe", {"start": [800, 800], "end": [800, 400]}),
            Action("Back", {}), Action("Finish", {"message": "请求核验"})]


def shot():
    return {"epoch": 0, "width": 1000, "height": 2000, "rotation": 0,
            "current_app": "com.example.demo",
            "xml": '<hierarchy window-id="host"><node text="准备查询"/></hierarchy>',
            "screenshot_base64": base64.b64encode(b"offline-host-frame-before").decode()}


def repaint(before, change):
    after = copy.deepcopy(before)
    if change == "xml":
        after["xml"] = after["xml"].replace("准备查询", "正在观察并决定下一步")
    else:
        after["screenshot_base64"] = base64.b64encode(b"offline-host-pixels-after").decode()
    return after


@pytest.mark.parametrize("action", actions()[:3], ids=lambda action: action.kind)
@pytest.mark.parametrize("change", ["xml", "pixels"])
def test_host_repaint_does_not_stale_authorized_coordinate_free_proposals(action, change):
    before = shot()
    assert proposal_is_current(action, before, repaint(before, change))


@pytest.mark.parametrize("action", actions()[3:], ids=lambda action: action.kind)
@pytest.mark.parametrize("change", ["xml", "pixels"])
def test_coordinate_input_back_and_finish_stay_bound_to_full_observation(action, change):
    before = shot()
    before["current_app"] = PACKAGE
    assert not proposal_is_current(action, before, repaint(before, change))


@pytest.mark.parametrize("field,value", [("epoch", 1), ("width", 1200), ("height", 2200),
                                         ("rotation", 1), ("current_app", PACKAGE)])
def test_no_action_can_use_the_repaint_exception_across_global_context_changes(field, value):
    before = shot()
    after = repaint(before, "pixels")
    after[field] = value
    for action in actions():
        assert not proposal_is_current(action, before, after), action.kind


def test_identical_current_observation_does_not_spuriously_stale_any_action():
    before = shot()
    for action in actions():
        assert proposal_is_current(action, before, copy.deepcopy(before)), action.kind


def repainting_host(adapter):
    """A new progress label/pixel image on each capture while App is foreground."""
    original = adapter._agent_observe
    counter = [0]
    def observe(checkpoint):
        current = original(checkpoint)
        if current["current_app"] == "com.example.demo":
            counter[0] += 1
            native = ET.Element("node", {"text": f"模型查询进度 {counter[0]}", "package": "com.example.demo",
                                        "class": "android.widget.TextView", "bounds": "[0,200][1000,300]"})
            root = ET.Element("hierarchy", {"window-id": "same-host"}); root.append(native)
            current.update(nodes=[native], xml=ET.tostring(root, encoding="unicode"),
                           screenshot_base64=base64.b64encode(f"offline-host-paint:{counter[0]}".encode()).decode())
        return current
    adapter._agent_observe = observe


def test_repainted_host_launch_really_dispatches_then_model_observes_amap(tmp_path, monkeypatch):
    adapter, phone = create(tmp_path)
    repainting_host(adapter)
    seen = []
    def decide(task, observation, history, checkpoint):
        seen.append((observation["current_app"], list(history)))
        action = (Action("Launch", {"app": "高德地图"}) if observation["current_app"] == "com.example.demo"
                  else Action("Help", {"message": "停止离线检查"}))
        return Decision(action, "offline-freshness-double", 1)
    adapter.client.decide = decide
    monkeypatch.setattr(AmapDevice, "query", Mock(side_effect=AssertionError("rules fallback forbidden")))
    with pytest.raises(WorkflowError, match="用户协助"):
        adapter.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert len(phone.effects) == 1 and phone.effects[0][0] == "monkey"
    assert PACKAGE in phone.effects[0]
    assert [app for app, _ in seen] == ["com.example.demo", PACKAGE]
    assert seen[1][1][-1]["action"] == "Launch"
    records = [json.loads(line) for line in (adapter.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert any(record["action"] == "Launch" and record["status"] == "executed" for record in records)


def test_repaint_exemption_cannot_launch_an_unrelated_app(tmp_path):
    adapter, phone = create(tmp_path)
    repainting_host(adapter)
    adapter.client.decide = Mock(return_value=Decision(Action("Launch", {"app": "支付宝"}), "offline-test", 1))
    with pytest.raises(WorkflowError):
        adapter.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert phone.effects == []


def test_cancelled_launch_still_never_dispatches_despite_host_repaint(tmp_path):
    adapter, phone = create(tmp_path)
    repainting_host(adapter)
    cancelled = [False]
    def checkpoint():
        if cancelled[0]: raise Cancelled("用户取消")
    def decide(*args):
        cancelled[0] = True
        return Decision(Action("Launch", {"app": "高德地图"}), "offline-test", 1)
    adapter.client.decide = decide
    with pytest.raises(Cancelled):
        adapter.query(REQUEST, "offline-task", "offline-query", checkpoint)
    assert phone.effects == []


def test_foreground_change_during_launch_decision_discards_old_proposal(tmp_path):
    adapter, phone = create(tmp_path, steps=1)
    def decide(*args):
        phone.stage = 1  # Another actor changed foreground before this answer.
        return Decision(Action("Launch", {"app": "高德地图"}), "offline-test", 1)
    adapter.client.decide = decide
    with pytest.raises(WorkflowError, match="预算"):
        adapter.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert phone.effects == []
    records = [json.loads(line) for line in (adapter.folder / "autoglm-actions.jsonl").read_text().splitlines()]
    assert records[-1]["status"] == "discarded"


def test_factual_context_distinguishes_partial_poi_fields_and_current_route_frames(tmp_path):
    adapter, _ = create(tmp_path)
    adapter.query_id, adapter.route_id = "current-query", "selected-route"
    adapter.state = {"places": {
        "origin": {"name": "甲地铁站", "source": "explicit", "address": None, "city": None,
                   "evidence_refs": {"name": ["observed-origin"]}},
        "destination": {"name": "乙地铁站", "source": "selected_ui", "city": "广州市", "address": None,
                        "evidence_refs": {"name": ["observed-end"], "city": ["observed-end"]}}},
        "choices": {"destination": {"name": "乙地铁站", "address": "本人已确认的地址"}},
        "filter": "换乘少", "transit_applied": True}
    adapter.observations = [{"query_id": query, "route_id": route, "page_kind": kind} for query, route, kind in [
        ("current-query", "selected-route", "route_detail"),
        ("current-query", "selected-route", "route_detail"),
        ("old-query", "selected-route", "route_detail"),
        ("current-query", "other-route", "route_detail"),
        ("current-query", "selected-route", "route_list")]]
    endpoint = node("乙地铁站", "[80,1500][700,1650]", **{"class": "android.view.ViewGroup"})
    info = adapter._model_screen_info({"current_app": PACKAGE, "nodes": [endpoint]})
    assert info["checked_places"] == ["origin", "destination"]
    assert info["place_status"]["origin"]["missing_fields"] == ["city"]
    assert info["place_status"]["origin"]["missing_optional_fields"] == ["address"]
    assert info["place_status"]["destination"]["missing_fields"] == ["address"]
    assert info["place_status"]["destination"]["address"] is None
    assert info["applied_filter"] == "换乘少" and info["public_transit_applied"]
    assert info["collected_detail_frames"] == 2 and info["selected_route"]
    assert info["visible_destination_endpoint"]
    endpoint.set("visible-to-user", "false")
    assert not adapter._model_screen_info({"current_app": PACKAGE, "nodes": [endpoint]})["visible_destination_endpoint"]


def test_model_receives_human_selected_address_without_treating_it_as_observed_proof(tmp_path):
    adapter, phone = create(tmp_path)
    selection = {"destination": {"name": "乙地铁站", "address": "广州市本人确认的站口", "city": "广州市",
                                  "candidate_id": "this-query-choice"}}
    captured = []
    def decide(task, observation, history, checkpoint):
        captured.append((copy.deepcopy(task), copy.deepcopy(observation["screen_info"])))
        return Decision(Action("Help", {"message": "停止离线测试"}), "offline-context-test", 1)
    adapter.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        adapter.query(REQUEST, "offline-task", "offline-query", lambda: None, choices=selection)
    task, info = captured[0]
    assert task["request"] == REQUEST and task["transit_place_selections"] == selection
    assert task["task_id"] == "offline-task" and task["query_id"] == "offline-query"
    assert info["checked_places"] == [] and not info["selected_route"]
    assert info["place_status"]["destination"]["missing_fields"] == ["address", "city", "name"]
    assert info["place_status"]["destination"]["address"] is None
    assert info["allowed_inputs"] and info["allowed_actions"] == ["Launch", "Wait", "Take_over"]
    assert phone.effects == []


def test_model_input_focus_context_uses_actual_enabled_foreground_nodes(tmp_path):
    adapter, _ = create(tmp_path)
    adapter.state = {"input_role": "origin"}
    target = node("我的位置", **{"focused": "true"})
    shot = {"current_app": PACKAGE, "nodes": [target]}
    assert adapter._model_screen_info(shot)["unique_focused_input"]
    assert adapter._model_screen_info(shot)["last_input_role"] == "origin"
    target.set("enabled", "false")
    assert not adapter._model_screen_info(shot)["unique_focused_input"]
    target.set("enabled", "true")
    shot["current_app"] = "com.example.demo"
    assert not adapter._model_screen_info(shot)["unique_focused_input"]


def test_discarded_repaint_does_not_erase_confirmed_execution_history(tmp_path):
    adapter, phone = create(tmp_path)
    observe = adapter._agent_observe
    painted = False
    calls = []
    def current(checkpoint):
        shot = observe(checkpoint)
        if painted:
            shot["screenshot_base64"] += "-new-visual-frame"
        return shot
    adapter._agent_observe = current
    def decide(task, shot, history, checkpoint):
        nonlocal painted
        calls.append(copy.deepcopy(history))
        if len(calls) == 1:
            return Decision(Action("Launch", {"app": "高德地图"}), "offline-history", 1)
        if len(calls) == 2:
            painted = True
            return Decision(Action("Tap", {"element": [300, 125]}), "offline-history", 1)
        assert history[-1]["action"] == "Launch"
        assert len(history) == 1
        return Decision(Action("Help", {"message": "结束离线检查"}), "offline-history", 1)
    adapter.client.decide = decide
    with pytest.raises(WorkflowError, match="用户协助"):
        adapter.query(REQUEST, "offline-task", "offline-query", lambda: None)
    assert len(calls) == 3
    assert [effect[0] for effect in phone.effects] == ["monkey"]
