"""Offline endpoint/collector regressions; no API, handset or live claims.

Reduced native route columns exercise the real collector and validator. Only
the optional Android task-reopening metadata capture is omitted; no successful
route result or classification is supplied by a test double.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import time
import xml.etree.ElementTree as ET

import pytest

from autoglm.executor import AutoGLMTransitDevice
from device.amap import PACKAGE, screen_text
from transit.validation import validate_result


TASK, QUERY, ROUTE = "offline-endpoint-task", "offline-endpoint-query", "offline-endpoint-route"
WHEN = "2026-11-15T00:00:00+00:00"
NOTICE = ("本页仅用于离线验证终点来源与当前查询证据绑定，不代表真实手机或实时交通。"
          "所有结构化路线字段都必须有独立原生节点证据，完成信号不能替代结果校验。"
          "本例的乘车字段保留在原生路线列中，不从测试说明生成。")


def node(text="", *, desc="", group=True, rect="[176,900][1131,1500]", children=(), **attrs):
    item = ET.Element("node", {"package": PACKAGE, "class": "android.view.ViewGroup" if group else "android.view.View",
                               "text": text, "content-desc": desc, "bounds": rect,
                               "visible-to-user": "true", "enabled": "true", "clickable": "false", **attrs})
    item.extend(children)
    return item


def station(name):
    return node(desc=name, children=[node(name, group=False)])


def detail_evidence(tmp_path, eid, children):
    root = ET.Element("hierarchy")
    # A literal fixture notice keeps these readable native frames above the
    # sparse-image threshold without inventing any structured route facts.
    root.append(node(desc="公交详情页面", rect="[0,600][1200,2640]", children=[*children, node(NOTICE, group=False)]))
    path = tmp_path / (eid + ".xml")
    ET.ElementTree(root).write(path, encoding="unicode")
    nodes = list(root.iter("node"))
    visible = AutoGLMTransitDevice._visible_place_nodes(nodes)
    evidence = {"evidence_id": eid, "observation_id": eid, "query_id": QUERY, "route_id": ROUTE,
                "page_kind": "route_detail", "text": screen_text(visible), "xml_path": str(path)}
    return {"xml": ET.tostring(root, encoding="unicode"), "nodes": nodes, "evidence": evidence,
            "observation_id": eid, "width": 1200, "height": 2640, "screen_width": 1200,
            "screen_height": 2640, "rotation": 0, "epoch": 0,
            "current_app": PACKAGE, "current_package": PACKAGE}


def offline_collector(tmp_path, ending="广州塔(地铁站)", *, group=True, hidden=False,
                      hidden_parent=False, top=False, city="广州市"):
    adapter = AutoGLMTransitDevice.__new__(AutoGLMTransitDevice)
    adapter.task_id, adapter.query_id, adapter.route_id = TASK, QUERY, ROUTE
    adapter.request = {"origin": "体育西路地铁站", "destination": "广州塔地铁站", "city": city,
                       "preference": "recommended", "use_current_location": False}
    adapter.folder, adapter.actions, adapter.started = tmp_path, 0, time.monotonic()
    adapter.queried_at = WHEN
    adapter._check = lambda checkpoint: checkpoint()
    adapter._progress = lambda stage, message, checkpoint: checkpoint()
    # A failed/unavailable reopening identity does not supply route facts and
    # is explicitly optional in AmapDevice._remember_detail_context itself.
    adapter._remember_detail_context = lambda result, checkpoint: None
    adapter.models = SimpleNamespace(extract_transit=Mock(side_effect=AssertionError("native fixture must not call a model")))
    places, poi = {}, []
    for role, name, district in (("origin", "体育西路(地铁站)", "天河区"),
                                 ("destination", "广州塔(地铁站)", "海珠区")):
        eid = role + "-poi"
        poi.append({"evidence_id": eid, "observation_id": eid, "query_id": QUERY, "route_id": None,
                    "page_kind": "place_detail", "text": name + "\n广州市" + district})
        places[role] = {"name": name, "city": "广州市", "source": "explicit",
                        "evidence_refs": {"name": [eid], "city": [eid]}}
    summary = "8分钟3号线无堵车风险2站¥2"
    ride = node(children=[station("体育西路"),
        node(children=[node(desc="海傍方向", children=[
            node(children=[node("3", group=False), node("号线", group=False)]),
            node("海傍方向", group=False)])]),
        node(children=[node(desc="2站4分钟", children=[node("2站4分钟", group=False)])]), station("广州塔")])
    first = detail_evidence(tmp_path, "full-ride-frame", [
        node(desc=summary, children=[node(summary, group=False)]), ride])
    terminal = node(ending if not group else "", desc=ending if group else "", group=group,
                    rect="[50,450][1120,600]" if top else "[50,2100][1120,2230]",
                    children=[node(ending, group=False)] if group else (),
                    **{"visible-to-user": "false" if hidden else "true"})
    if hidden_parent:
        terminal = node(children=[terminal], **{"visible-to-user": "false"})
    last = detail_evidence(tmp_path, "terminal-frame", [terminal])
    adapter.observations = [*poi, first["evidence"], last["evidence"]]
    adapter.state = {"places": places, "choices": {}, "selected_list": {
        "evidence_id": "selected-list", "observation_id": "selected-list", "query_id": QUERY, "route_id": ROUTE,
        "page_kind": "route_list", "text": summary + "\n本轮已点击筛选：高德推荐"}}
    return adapter, last


@pytest.mark.parametrize("ending,options,complete", [
    ("广州塔(地铁站)", {}, True),
    ("广州市广州塔(地铁站)", {}, True),
    ("广州市广州塔（地铁站）", {"city": "广州"}, True),
    ("北京市广州塔(地铁站)", {}, False),
    ("广州塔地铁站B口", {}, False),
    ("广州市广州塔(地铁站)", {"city": ""}, False),
    ("广州市广州塔(地铁站)", {"group": False}, False),
    ("广州市广州塔(地铁站)", {"hidden": True}, False),
    ("广州市广州塔(地铁站)", {"hidden_parent": True}, False),
    ("广州市广州塔(地铁站)", {"top": True}, False),
])
def test_metadata_and_finish_agree_without_bypassing_the_real_collector_or_validator(tmp_path, ending, options, complete):
    adapter, last = offline_collector(tmp_path, ending, **options)
    info = adapter._model_screen_info(last)
    assert info["visible_destination_endpoint"] is complete
    outcome = adapter._finish(last, lambda: None)
    assert outcome["collection_complete"] is complete
    adapter.models.extract_transit.assert_not_called()
    assert not outcome["result"]["route_verified"]  # Extractor never certifies itself.
    checked = validate_result(outcome["result"], TASK, QUERY, request=adapter.request,
                              evidence=outcome["evidence"], collection_complete=outcome["collection_complete"])
    assert checked["classification"] == ("COMPLETE" if complete else "PARTIAL")
    assert checked["route_verified"] is complete
    if not complete:
        assert "detail_collection_incomplete" in checked["missing_fields"]


def test_a_city_qualified_endpoint_cannot_certify_a_missing_riding_field(tmp_path):
    adapter, last = offline_collector(tmp_path, "广州市广州塔(地铁站)")
    outcome = adapter._finish(last, lambda: None)
    assert outcome["collection_complete"]
    result = deepcopy(outcome["result"])
    result["segments"][0]["direction"] = None
    checked = validate_result(result, TASK, QUERY, request=adapter.request,
                              evidence=outcome["evidence"], collection_complete=True)
    assert checked["classification"] == "PARTIAL" and not checked["route_verified"]
    assert any(field.endswith(".direction") for field in checked["missing_fields"])
