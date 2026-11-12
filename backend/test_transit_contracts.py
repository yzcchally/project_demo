"""Synthetic offline fixtures test grounding rules; they are not phone trials."""
from copy import deepcopy
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pytest
from pydantic import ValidationError

from api.schemas import GoalRequest, TaskIntent, TransitRequest, TransitResult, TransitScreenDecision
from agent.models import ModelGateway
from domain import WorkflowError
from perception.transit import describe_transit
from safety.transit import guard_transit
from transit.explain import answer_question, explain_result
from transit.validation import validate_result

TASK, QUERY, ROUTE = "test-task", "query-current", "route-one"


def fixture():
    raw = ("广州市\n广州塔地铁站\n体育西路地铁站\n3号线\n天河客运站方向\n"
           "广州塔站上车\n体育西路站下车\n3站\n预计25分钟\n步行0.3公里\n票价3元\n"
           "少换乘\n步行到广州塔站\n步行至体育西路地铁站")
    refs = lambda fields: {x: ["detail-one"] for x in fields}
    evidence = [{"evidence_id": "detail-one", "query_id": QUERY, "route_id": ROUTE,
                 "observation_id": "observed-one", "page_kind": "route_detail", "text": raw,
                 "xml_path": "evidence/one.xml", "screenshot_path": "evidence/one.png"}]
    result = {"task_id": TASK, "query_id": QUERY, "route_id": ROUTE,
              "queried_at": "2026-11-11T00:00:00Z", "collected_at": "2026-11-11T00:01:00Z",
              "origin": {"name": "广州塔地铁站", "city": "广州市", "evidence_refs": refs(["name", "city"])},
              "destination": {"name": "体育西路地铁站", "city": "广州市", "evidence_refs": refs(["name", "city"])},
              "duration_minutes": 25, "walking_meters": 300, "fare_yuan": 3,
              "applied_filter": "少换乘", "evidence_refs": refs(["duration_minutes", "walking_meters", "fare_yuan", "applied_filter"]),
              "classification": "COMPLETE", "route_verified": True, "evidence": evidence,
              "segments": [
                  {"segment_id": "walk-start", "mode": "walk", "instructions": "步行到广州塔站", "evidence_refs": refs(["instructions"])},
                  {"segment_id": "ride-one", "mode": "subway", "line": "3号线", "direction": "天河客运站", "board": "广州塔站", "alight": "体育西路站", "stops": 3,
                   "evidence_refs": refs(["line", "direction", "board", "alight", "stops"])},
                  {"segment_id": "walk-end", "mode": "walk", "instructions": "步行至体育西路地铁站", "evidence_refs": refs(["instructions"])},
              ]}
    return result, evidence


def verified(data=None, evidence=None, **kwargs):
    if data is None:
        data, evidence = fixture()
    return validate_result(data, TASK, QUERY, {"origin": "广州塔地铁站", "destination": "体育西路地铁站", "city": "广州"},
                           evidence=evidence, collection_complete=kwargs.pop("collection_complete", True), **kwargs)


def test_transit_intent_preserves_mail_compatibility_and_explicit_origin_wins():
    assert TaskIntent(task_type="open_url").transit is None
    intent = TaskIntent(task_type="plan_transit", transit={"origin": "广州塔", "destination": "体育西路", "city": "广州", "use_current_location": True})
    assert not intent.transit.use_current_location
    assert intent.recipient_address == "" and intent.url == ""
    assert TransitRequest(destination="体育西路", use_current_location=True).use_current_location


@pytest.mark.parametrize("value", ["T13", "T14"])
def test_route_demo_metadata_has_no_fault(value):
    assert GoalRequest(instruction="从A到B坐公交", request_key="test-route", demo_case_id=value).demo_fault is None
    with pytest.raises(ValidationError):
        GoalRequest(instruction="从A到B坐公交", request_key="test-route", demo_case_id=value, demo_fault="composer-background")


def test_grounded_detail_is_verified_and_optional_unknown_cost_is_ok():
    data = verified()
    assert data["classification"] == "COMPLETE" and data["route_verified"]
    assert data["transfers"] == 0 and data["walking_meters"] == 300
    assert "公里×1000" in data["derived"]["walking_meters"]
    raw, ev = fixture();raw["fare_yuan"] = 9
    assert verified(raw, ev)["fare_yuan"] is None
    assert verified(raw, ev)["classification"] == "COMPLETE"


def walking_summary_evidence(tmp_path, eid, *, with_xml=True, independent=None):
    """Anonymous reduction of the real T14 summary/child hierarchy."""
    aggregate = "19分钟3号线步行15分钟1站¥2步行876米"
    total = "站内外共步行 876米 · 15分钟"
    texts = [aggregate, "步行15分钟", "步行876米", total]
    record = {"evidence_id": eid, "query_id": QUERY, "route_id": ROUTE,
              "observation_id": eid, "page_kind": "route_detail", "text": "\n".join(texts)}
    if with_xml:
        root = ET.Element("hierarchy")
        def n(value, *, desc=False):
            return ET.Element("node", {"package": "com.autonavi.minimap", "class": "android.view.ViewGroup",
                "text": "" if desc else value, "content-desc": value if desc else "", "bounds": "[0,0][1200,400]"})
        strip = n(aggregate, desc=True)
        strip.extend([n("步行15分钟"), n("步行876米")])
        root.extend([strip, n(total, desc=True)])
        if independent:
            root.append(n(independent, desc=True));record["text"] += "\n" + independent
        path = tmp_path / (eid + ".xml")
        ET.ElementTree(root).write(path, encoding="unicode")
        record["xml_path"] = str(path)
    return record


@pytest.mark.parametrize("different_frames", [False, True])
@pytest.mark.parametrize("with_xml", [False, True])
def test_whole_route_walk_totals_cannot_ground_two_walking_legs(tmp_path, different_frames, with_xml):
    raw, ev = fixture()
    summary = walking_summary_evidence(tmp_path, "summary-first", with_xml=with_xml)
    end = walking_summary_evidence(tmp_path, "summary-later", with_xml=with_xml) if different_frames else summary
    ev.extend([summary] + ([end] if different_frames else []))
    raw.update(duration_minutes=19, walking_meters=876)
    raw["evidence_refs"].update({field: [summary["evidence_id"]] for field in ["duration_minutes", "walking_meters"]})
    for segment, quote, source in [(raw["segments"][0], "步行15分钟", summary),
                                   (raw["segments"][-1], "站内外共步行 876米 · 15分钟", end)]:
        segment.update(instructions=quote, duration_minutes=15, walking_meters=876,
                       evidence_refs={field: [source["evidence_id"]] for field in ["instructions", "duration_minutes", "walking_meters"]})
    checked = verified(raw, ev)
    assert checked["classification"] == "PARTIAL" and not checked["route_verified"]
    assert checked["walking_meters"] == 876 and checked["duration_minutes"] == 19
    assert len(checked["segments"]) == 3  # Keep the unknown legs; do not invent a merge.
    for segment in [checked["segments"][0], checked["segments"][-1]]:
        assert all(segment[field] is None for field in ["instructions", "duration_minutes", "walking_meters"])
        assert f"segments.{segment['segment_id']}.instructions.ungrounded" in checked["missing_fields"]


def test_an_independent_walk_node_keeps_its_own_literal_instruction_and_metrics(tmp_path):
    raw, ev = fixture()
    quote = "从广州塔地铁站步行至广州塔站，100米3分钟"
    source = walking_summary_evidence(tmp_path, "independent-walk", independent=quote)
    ev.append(source)
    raw["segments"][0].update(instructions=quote, duration_minutes=3, walking_meters=100,
        evidence_refs={field: [source["evidence_id"]] for field in ["instructions", "duration_minutes", "walking_meters"]})
    checked = verified(raw, ev)
    walk = checked["segments"][0]
    assert checked["route_verified"]
    assert (walk["instructions"], walk["duration_minutes"], walk["walking_meters"]) == (quote, 3, 100)


def test_independent_short_walk_node_does_not_borrow_distance_from_another_summary_node(tmp_path):
    raw, ev = fixture()
    source = walking_summary_evidence(tmp_path, "short-walk", independent="步行15分钟")
    ev.append(source)
    raw["segments"][0].update(instructions="步行15分钟", duration_minutes=15, walking_meters=876,
        evidence_refs={field: [source["evidence_id"]] for field in ["instructions", "duration_minutes", "walking_meters"]})
    checked = verified(raw, ev)
    walk = checked["segments"][0]
    assert walk["instructions"] == "步行15分钟" and walk["duration_minutes"] == 15
    assert walk["walking_meters"] is None
    assert "segments.walk-start.walking_meters.ungrounded" in checked["missing_fields"]


def test_unreadable_existing_xml_cannot_fall_back_to_flat_summary_text(tmp_path):
    raw, ev = fixture()
    source = walking_summary_evidence(tmp_path, "broken-xml", independent="步行15分钟")
    from pathlib import Path
    Path(source["xml_path"]).write_text("<broken")
    ev.append(source)
    raw["segments"][0].update(instructions="步行15分钟", evidence_refs={"instructions": [source["evidence_id"]]})
    assert verified(raw, ev)["segments"][0]["instructions"] is None


def trailing_walk_fixture(tmp_path):
    """Anonymous reduction of the observed native ride/walk/POI triplet."""
    def n(label="", *, leaf=False):
        return ET.Element("node", {"package": "com.autonavi.minimap",
            "class": "android.view.View" if leaf else "android.view.ViewGroup",
            "text": label if leaf else "", "content-desc": "" if leaf else label,
            "bounds": "[20,400][1180,2200]"})
    def place(title, leaves):
        node = n(title);inner = n();node.append(inner)
        inner.extend(n(value, leaf=True) for value in leaves)
        return node
    root = ET.Element("hierarchy");marker = n("公交详情页面");root.append(marker)
    summary = "19分钟3号线步行15分钟1站¥2步行876米"
    marker.append(n(summary))
    owner = n();marker.append(owner)
    ride_branch = n();owner.append(ride_branch)
    row = n();ride_branch.append(row);row.append(n())
    column = n();row.append(column)
    column.append(place("起点站", ["起点站"]))
    line_branch = n();column.append(line_branch)
    direction = n("海傍方向");line_branch.append(direction)
    direction.extend([n("3", leaf=True), n("号线", leaf=True), n("海傍方向", leaf=True)])
    column.append(n("1站2分钟"))
    column.append(place("下车站C口", ["下车站", "C口"]))
    walk_branch = n();owner.append(walk_branch);walk_inner = n();walk_branch.append(walk_inner)
    total = n("站内外共步行 876米 15分钟");walk_inner.append(total)
    total.extend([n("站内外共步行 876米 · 15分钟", leaf=True), n("步行导航", leaf=True)])
    owner.append(place("公共博物馆", ["公共博物馆"]))
    path = tmp_path / "native-triplet.xml";ET.ElementTree(root).write(path, encoding="unicode")
    eid = "native-triplet"
    refs = lambda fields: {field: [eid] for field in fields}
    evidence = [{"evidence_id": eid, "query_id": QUERY, "route_id": ROUTE, "page_kind": "route_detail",
        "text": "\n".join([summary, "起点站", "下车站", "下车站C口", "C口", "公共博物馆", "3号线", "海傍", "站内外共步行"]),
        "xml_path": str(path)}]
    raw = {"task_id": TASK, "query_id": QUERY, "route_id": ROUTE, "classification": "COMPLETE", "evidence": evidence,
        "origin": {"name": "起点站", "evidence_refs": refs(["name"])},
        "destination": {"name": "公共博物馆", "evidence_refs": refs(["name"])},
        "duration_minutes": 19, "walking_meters": 876, "fare_yuan": 2,
        "evidence_refs": refs(["duration_minutes", "walking_meters", "fare_yuan"]),
        "segments": [
            {"segment_id": "native-ride", "mode": "subway", "line": "3号线", "direction": "海傍",
             "board": "起点站", "alight": "下车站", "exit": "C口", "evidence_refs": refs(["line", "direction", "board", "alight", "exit"])},
            {"segment_id": "native-tail", "mode": "walk", "board": "下车站C口", "alight": "公共博物馆",
             "instructions": "站内外共步行", "evidence_refs": refs(["board", "alight", "instructions"])}]}
    return raw, evidence


def checked_trailing_walk(raw, evidence):
    return validate_result(raw, TASK, QUERY, {"origin": "起点站", "destination": "公共博物馆"},
                           evidence=evidence, collection_complete=True)


def test_native_adjacent_triplet_verifies_tail_connection_without_assigning_total_metrics(tmp_path):
    raw, evidence = trailing_walk_fixture(tmp_path)
    checked = checked_trailing_walk(raw, evidence)
    assert checked["route_verified"] and checked["classification"] == "COMPLETE"
    tail = checked["segments"][-1]
    assert (tail["board"], tail["alight"], tail["instructions"]) == ("下车站C口", "公共博物馆", "站内外共步行")
    assert tail["walking_meters"] is None and tail["duration_minutes"] is None
    assert checked["walking_meters"] == 876 and checked["duration_minutes"] == 19
    assert "具体街道走法未显示" in checked["derived"]["segments.native-tail.walk_connection"]


@pytest.mark.parametrize("field,value", [("duration_minutes", 15), ("walking_meters", 876)])
def test_native_tail_cannot_borrow_a_metric_from_the_total_row(tmp_path, field, value):
    raw, evidence = trailing_walk_fixture(tmp_path)
    raw["segments"][-1][field] = value
    raw["segments"][-1]["evidence_refs"][field] = ["native-triplet"]
    checked = checked_trailing_walk(raw, evidence)
    assert not checked["route_verified"] and checked["segments"][-1][field] is None
    assert checked["segments"][-1]["instructions"] == "站内外共步行"
    assert f"segments.native-tail.{field}.ungrounded" in checked["missing_fields"]


@pytest.mark.parametrize("change", ["wrong_exit", "wrong_endpoint", "wrong_direction", "wrong_line", "wrong_board", "wrong_alight",
    "cross_eid", "other_route", "other_query", "offscreen", "offscreen_leaf", "offscreen_inner", "offscreen_total", "nonadjacent",
    "missing_marker", "wrong_package", "false_visibility", "reversed_branches", "duplicate_ride"])
def test_native_tail_binding_rejects_unrelated_or_unreadable_structure(tmp_path, change):
    raw, evidence = trailing_walk_fixture(tmp_path)
    path = evidence[0]["xml_path"]
    root = ET.parse(path).getroot();marker = root[0];owner = marker[1]
    tail = raw["segments"][-1]
    if change == "wrong_exit":
        tail["board"] = "下车站B口";evidence[0]["text"] += "\n下车站B口"
    elif change == "wrong_endpoint":
        tail["alight"] = "公共博物馆东门";evidence[0]["text"] += "\n公共博物馆东门"
    elif change == "wrong_direction":
        raw["segments"][0]["direction"] = "天河客运站";evidence[0]["text"] += "\n天河客运站"
    elif change in {"wrong_line", "wrong_board", "wrong_alight"}:
        field = change.removeprefix("wrong_")
        raw["segments"][0][field] = {"line": "1号线", "board": "其他起点站", "alight": "其他下车站"}[field]
        evidence[0]["text"] += "\n" + raw["segments"][0][field]
    elif change == "cross_eid":
        other = deepcopy(evidence[0]);other["evidence_id"] = "other-frame";evidence.append(other)
        tail["evidence_refs"]["alight"] = ["other-frame"]
    elif change == "other_route":
        evidence[0]["route_id"] = "other-route"
    elif change == "other_query":
        evidence[0]["query_id"] = "other-query"
    elif change == "offscreen":
        owner[0][0][1][3].set("bounds", "[20,2700][1180,2400]")
    elif change == "offscreen_leaf":
        owner[0][0][1][3][0][1].set("bounds", "[20,2700][1180,2400]")
    elif change == "offscreen_inner":
        owner[0][0][1][3][0].set("bounds", "[20,2700][1180,2400]")
    elif change == "offscreen_total":
        owner[1][0][0][0].set("bounds", "[20,2700][1180,2400]")
    elif change == "nonadjacent":
        extra = ET.Element("node", dict(owner[0].attrib));owner.insert(1, extra)
    elif change == "missing_marker":
        marker.set("content-desc", "路线概览")
    elif change == "wrong_package":
        owner[1].set("package", "other.app")
    elif change == "false_visibility":
        owner[2].set("visible-to-user", "false")
    elif change == "reversed_branches":
        ride, walk = owner[0], owner[1];owner.remove(walk);owner.insert(0, walk)
    elif change == "duplicate_ride":
        owner[0].append(deepcopy(owner[0][0]))
    ET.ElementTree(root).write(path, encoding="unicode")
    if change == "other_query":
        with pytest.raises(WorkflowError): checked_trailing_walk(raw, evidence)
        return
    checked = checked_trailing_walk(raw, evidence)
    assert not checked["route_verified"]
    assert all(checked["segments"][-1][field] is None for field in ["instructions", "board", "alight"])


def current_origin_walk_fixture(tmp_path, *, split_frames=False):
    """Anonymous seven-branch itinerary, with independently trusted location."""
    raw, evidence = trailing_walk_fixture(tmp_path)
    root = ET.parse(evidence[0]["xml_path"]).getroot();owner = root[0][1]
    def n(label="", *, leaf=False):
        return ET.Element("node", {"package": "com.autonavi.minimap",
            "class": "android.view.View" if leaf else "android.view.ViewGroup", "text": label if leaf else "",
            "content-desc": "" if leaf else label, "bounds": "[20,400][1180,2200]"})
    def place(title, leaves):
        result = n(title);inner = n();result.append(inner);inner.extend(n(label, leaf=True) for label in leaves)
        return result
    # Retain the full terminal ride and construct a separate preceding ride.
    final_column = owner[0][0][1]
    final_column[0] = place("中转站", ["中转站"])
    first_ride = deepcopy(owner[0]);column = first_ride[0][1]
    column[0] = place("进站站C口", ["进站站", "C口"])
    column[3] = place("中转站", ["中转站"])
    direction = column[1][0];direction.set("content-desc", "美的大道方向")
    direction[0].set("text", "7");direction[2].set("text", "美的大道方向")
    leading_branch = n();leading_row = n("步行 2.4公里 37分钟");leading_branch.append(leading_row)
    leading_row.extend([n("步行 2.4公里 · 37分钟", leaf=True), n("步行导航", leaf=True)])
    transfer = n("站内换乘 238米5分钟");transfer.append(n("站内换乘 238米 · 5分钟", leaf=True))
    for index, branch in enumerate([place("我的位置", ["我的位置"]), leading_branch, first_ride, transfer]):
        owner.insert(index, branch)
    base_record = evidence[0]
    base_record["text"] += "\n我的位置\n进站站C口\n进站站\n中转站\n7号线\n美的大道\n步行 2.4公里 37分钟\n站内换乘 238米5分钟"
    leading_record = deepcopy(base_record);leading_record["evidence_id"] = "leading-frame"
    leading_record["xml_path"] = str(tmp_path / "leading-frame.xml")
    leading_root = deepcopy(root)
    if split_frames:
        for branch in list(leading_root[0][1])[3:]:
            for node in branch.iter("node"): node.set("bounds", "[20,2700][1180,2400]")
    ET.ElementTree(leading_root).write(leading_record["xml_path"], encoding="unicode")
    if split_frames:
        for branch in list(owner)[:3]:
            for node in branch.iter("node"): node.set("bounds", "[20,0][1180,-200]")
    ET.ElementTree(root).write(base_record["xml_path"], encoding="unicode")
    location = deepcopy(positioned_result()[1][-1])
    location["text"] = "公共起点A\n公开定位测试区\n广州市"
    evidence.extend([leading_record, location])
    raw["origin"] = {"name": "公共起点A", "address": "公开定位测试区", "city": "广州市", "source": "current_location",
        "evidence_refs": {field: [location["evidence_id"]] for field in ["name", "address", "city"]}}
    raw["segments"][0]["board"] = "中转站"
    lead_refs = lambda fields: {field: ["leading-frame"] for field in fields}
    raw["segments"][:0] = [
        {"segment_id": "native-leading", "mode": "walk", "board": "我的位置", "alight": "进站站C口",
         "instructions": "步行 2.4公里 37分钟", "duration_minutes": 37, "walking_meters": 2400,
         "evidence_refs": lead_refs(["board", "alight", "instructions", "duration_minutes", "walking_meters"])},
        {"segment_id": "first-ride", "mode": "subway", "board": "进站站", "entrance": "C口", "alight": "中转站",
         "line": "7号线", "direction": "美的大道", "evidence_refs": lead_refs(["board", "entrance", "alight", "line", "direction"])},
        {"segment_id": "station-transfer", "mode": "walk", "board": "中转站", "alight": "中转站",
         "instructions": "站内换乘 238米5分钟", "evidence_refs": {field: ["native-triplet"] for field in ["board", "alight", "instructions"]}}]
    return raw, evidence


def checked_current_origin_walk(raw, evidence):
    return validate_result(raw, TASK, QUERY, {"destination": "公共博物馆", "use_current_location": True},
                           evidence=evidence, collection_complete=True)


@pytest.mark.parametrize("split_frames", [False, True])
def test_native_leading_walk_resolves_this_querys_healthy_current_location_and_keeps_its_own_metrics(tmp_path, split_frames):
    raw, evidence = current_origin_walk_fixture(tmp_path, split_frames=split_frames)
    checked = checked_current_origin_walk(raw, evidence)
    assert checked["route_verified"] and checked["transfers"] == 1
    leading = checked["segments"][0]
    assert (leading["board"], leading["alight"], leading["instructions"]) == ("公共起点A", "进站站C口", "步行 2.4公里 37分钟")
    assert leading["duration_minutes"] == 37 and leading["walking_meters"] == 2400
    assert leading["evidence_refs"]["board"] == ["leading-frame", "fresh-position"]
    assert "本次已核实当前位置" in checked["derived"]["segments.native-leading.walk_connection"]
    assert checked_current_origin_walk(checked, evidence)["route_verified"]


@pytest.mark.parametrize("change", ["explicit_alias", "unhealthy_location", "missing_address", "missing_origin", "wrong_location_query",
    "wrong_detail_query", "wrong_route", "cross_eid", "wrong_entrance", "wrong_ride", "nonadjacent", "offscreen",
    "offscreen_portal", "wrong_row", "borrowed_metric", "missing_walk"])
def test_native_leading_walk_cannot_invent_a_current_location_connection(tmp_path, change):
    raw, evidence = current_origin_walk_fixture(tmp_path, split_frames=True)
    leading = raw["segments"][0];record = evidence[1]
    root = ET.parse(record["xml_path"]).getroot();owner = root[0][1]
    if change == "explicit_alias": raw["origin"]["source"] = "explicit"
    elif change == "unhealthy_location": evidence[-1]["location_status"]["verified"] = False
    elif change == "missing_address": raw["origin"]["address"] = None
    elif change == "missing_origin": raw["origin"]["name"] = ""
    elif change == "wrong_location_query": evidence[-1]["query_id"] = "previous-query"
    elif change == "wrong_detail_query": record["query_id"] = "previous-query"
    elif change == "wrong_route": record["route_id"] = "other-route"
    elif change == "cross_eid": leading["evidence_refs"]["alight"] = ["native-triplet"]
    elif change == "wrong_entrance":
        leading["alight"] = "进站站B口";record["text"] += "\n进站站B口"
    elif change == "wrong_ride":
        raw["segments"][1]["line"] = "8号线";record["text"] += "\n8号线"
    elif change == "nonadjacent": owner.insert(1, deepcopy(owner[0]))
    elif change == "offscreen": owner[0].set("bounds", "[20,2700][1180,2400]")
    elif change == "offscreen_portal": owner[2][0][1][0][0][1].set("bounds", "[20,2700][1180,2400]")
    elif change == "wrong_row":
        owner[1][0].set("content-desc", "站内外共步行 2.4公里 37分钟")
    elif change == "borrowed_metric": leading["walking_meters"] = 876
    elif change == "missing_walk": raw["segments"].pop(0)
    ET.ElementTree(root).write(record["xml_path"], encoding="unicode")
    if change in {"wrong_location_query", "wrong_detail_query"}:
        with pytest.raises(WorkflowError): checked_current_origin_walk(raw, evidence)
        return
    checked = checked_current_origin_walk(raw, evidence)
    assert not checked["route_verified"]
    if change == "borrowed_metric":
        assert checked["segments"][0]["walking_meters"] is None
    else:
        assert "origin.walk_connection_not_verified" in checked["missing_fields"] or not checked["origin"]["name"]


@pytest.mark.parametrize("field", ["line", "direction", "board", "alight"])
def test_missing_or_model_invented_key_field_is_partial(field):
    raw, ev = fixture();raw["segments"][1][field] = "不存在的地名或线路"
    result = verified(raw, ev)
    assert result["classification"] == "PARTIAL" and not result["route_verified"]
    assert result["segments"][1][field] is None
    assert "尚未核对完整" in explain_result(result)


def test_summary_never_counts_as_route_detail():
    raw, ev = fixture();ev[0]["page_kind"] = "route_list"
    result = verified(raw, ev)
    assert not result["route_verified"] and result["classification"] == "PARTIAL"
    assert "route_detail_not_observed" in result["missing_fields"]


def test_model_evidence_is_not_trusted_and_incomplete_collection_stays_partial():
    raw, ev = fixture()
    assert not validate_result(raw, TASK, QUERY)["route_verified"]
    result = verified(raw, ev, collection_complete=False)
    assert not result["route_verified"] and result["transfers"] is None
    assert "detail_collection_incomplete" in result["missing_fields"]


@pytest.mark.parametrize("change", ["task", "query", "evidence_query", "duplicate_id"])
def test_old_query_and_duplicate_proof_cannot_be_accepted(change):
    raw, ev = fixture()
    if change == "task": raw["task_id"] = "old-task"
    if change == "query": raw["query_id"] = "old-query"
    if change == "evidence_query": ev[0]["query_id"] = "old-query"
    if change == "duplicate_id": ev.append(deepcopy(ev[0]))
    with pytest.raises(WorkflowError): verified(raw, ev)


def test_other_candidate_route_facts_are_rejected():
    raw, ev = fixture();ev[0]["route_id"] = "different-route"
    result = verified(raw, ev)
    assert not result["route_verified"] and result["segments"][1]["line"] is None


def test_explicit_origin_and_city_are_independently_checked():
    raw, ev = fixture()
    r = validate_result(raw, TASK, QUERY, {"origin": "广州南站", "destination": "体育西路", "city": "广州"}, evidence=ev, collection_complete=True)
    assert "origin.does_not_match_request" in r["missing_fields"] and not r["route_verified"]
    r = validate_result(raw, TASK, QUERY, {"origin": "广州塔", "destination": "体育西路", "city": "深圳"}, evidence=ev, collection_complete=True)
    assert "origin.city_not_verified" in r["missing_fields"]


def test_generic_my_location_label_does_not_prove_real_location():
    raw, ev = fixture();raw["origin"] = {"name": "我的位置", "source": "current_location", "evidence_refs": {"name": ["detail-one"]}};ev[0]["text"] += "\n我的位置"
    r = validate_result(raw, TASK, QUERY, {"destination": "体育西路", "use_current_location": True}, evidence=ev, collection_complete=True)
    assert "origin.current_location_not_verified" in r["missing_fields"]


def positioned_result():
    raw, ev = fixture()
    location = {"evidence_id": "fresh-position", "query_id": QUERY, "route_id": None,
        "observation_id": "position-observation", "page_kind": "location", "text": "广州塔地铁站\n广州市海珠区\n广州市",
        "location_status": {"verified": True, "reason": "offline healthy device fixture", "enabled": True,
            "permission_granted": True, "app_access_recent": True, "provider": "fused", "age_seconds": 10,
            "accuracy_m": 20, "is_mock": False, "checked_at": "2026-11-12T04:00:10Z", "fix_time": "2026-11-12T04:00:00Z",
            "appops_mode": "foreground", "app_access_age_seconds": 10}}
    ev.append(location)
    raw["origin"] = {"name": "广州塔地铁站", "address": "广州市海珠区", "city": "广州市",
        "source": "current_location", "evidence_refs": {field: [location["evidence_id"]] for field in ["name", "address", "city"]}}
    return raw, ev


def test_current_location_requires_actual_name_address_and_city_even_without_a_requested_city():
    raw, ev = positioned_result()
    result = validate_result(raw, TASK, QUERY, {"destination": "体育西路地铁站", "use_current_location": True}, evidence=ev, collection_complete=True)
    assert result["route_verified"]
    for field in ["name", "address", "city"]:
        changed = deepcopy(raw);changed["origin"][field] = None if field != "name" else ""
        result = validate_result(changed, TASK, QUERY, {"destination": "体育西路地铁站", "use_current_location": True}, evidence=ev, collection_complete=True)
        assert not result["route_verified"]
        assert f"origin.{field}.current_location_not_verified" in result["missing_fields"]


@pytest.mark.parametrize("field", ["name", "address", "city"])
def test_an_unrelated_location_reference_cannot_certify_a_fact_supported_only_by_another_page(field):
    raw, ev = positioned_result()
    ev[-1]["text"] = "定位页面已打开"
    ev[0]["text"] += "\n广州市海珠区"
    raw["origin"]["evidence_refs"][field] = ["detail-one", "fresh-position"]
    result = validate_result(raw, TASK, QUERY, {"destination": "体育西路地铁站", "use_current_location": True}, evidence=ev, collection_complete=True)
    assert not result["route_verified"]
    assert f"origin.{field}.current_location_not_verified" in result["missing_fields"]


@pytest.mark.parametrize("field,value", [("verified", False), ("enabled", False), ("permission_granted", False),
    ("app_access_recent", False), ("provider", "passive"), ("age_seconds", 121), ("age_seconds", -1),
    ("accuracy_m", 101), ("accuracy_m", 0), ("is_mock", True), ("is_mock", None),
    ("checked_at", "not-a-time"), ("checked_at", "2026-11-12T04:00:10"),
    ("fix_time", "2026-11-12T04:00:00+08:00"), ("fix_time", "2026-11-12T03:55:00Z"),
    ("fix_time", "2026-11-12T04:00:20Z"), ("appops_mode", "ignore"), ("app_access_age_seconds", 121)])
def test_current_location_requires_healthy_recent_accurate_nonmock_device_fix(field, value):
    raw, evidence = positioned_result();evidence[-1]["location_status"][field] = value
    if (field == "age_seconds" and value < 0) or (field == "accuracy_m" and value == 0):
        with pytest.raises(WorkflowError):
            validate_result(raw, TASK, QUERY, {"destination": "体育西路地铁站", "use_current_location": True},
                            evidence=evidence, collection_complete=True)
        return
    checked = validate_result(raw, TASK, QUERY, {"destination": "体育西路地铁站", "use_current_location": True},
                              evidence=evidence, collection_complete=True)
    assert not checked["route_verified"]
    assert all(checked["origin"][field] in {None, ""} for field in ["name", "address", "city"])
    assert "origin.current_location_not_verified" in checked["missing_fields"]


def test_model_location_health_cannot_replace_missing_trusted_device_health():
    raw, evidence = positioned_result();raw["evidence"] = deepcopy(evidence)
    evidence[-1].pop("location_status")
    checked = validate_result(raw, TASK, QUERY, {"destination": "体育西路地铁站", "use_current_location": True},
                              evidence=evidence, collection_complete=True)
    assert not checked["route_verified"] and "origin.current_location_not_verified" in checked["missing_fields"]


def test_current_location_accepts_subsecond_device_epoch_rounding():
    raw, evidence = positioned_result()
    evidence[-1]["location_status"].update(age_seconds=0, checked_at="2026-11-12T04:00:00Z", fix_time="2026-11-12T04:00:00.900Z")
    checked = validate_result(raw, TASK, QUERY, {"destination": "体育西路地铁站", "use_current_location": True},
                              evidence=evidence, collection_complete=True)
    assert checked["route_verified"]


def test_duplicate_screens_do_not_add_another_ride_or_transfer():
    raw, ev = fixture();duplicate = deepcopy(raw["segments"][1]);duplicate["segment_id"] = "same-ride-later-frame";raw["segments"].insert(2, duplicate)
    r = verified(raw, ev)
    assert r["route_verified"] and len(r["segments"]) == 3 and r["transfers"] == 0


def test_three_instant_questions_use_the_same_verified_facts():
    r = verified()
    assert "广州塔站" in answer_question(r, "第一步怎么走？")
    assert "体育西路站" in answer_question(r, "我应该在哪一站下车？")
    assert "换乘0次" in answer_question(r, "需要换乘几次？")
    assert "重新查询" in answer_question(r, "换一个步行更少的方案")
    r["route_verified"] = False
    assert "尚未核对完整" in answer_question(r, "哪站下车？")


def test_no_route_needs_an_observed_message():
    raw, ev = fixture();raw.update(classification="NO_ROUTE", segments=[])
    assert verified(raw, ev)["classification"] == "PARTIAL"
    ev[0]["text"] += "\n暂无公交路线"
    r = verified(raw, ev)
    assert r["classification"] == "NO_ROUTE" and not r["route_verified"]


def tree():
    return '<hierarchy><node package="com.autonavi.minimap" class="android.widget.EditText" enabled="true" bounds="[10,20][300,100]" text="起点"/><node package="com.autonavi.minimap" class="android.view.ViewGroup" enabled="true" scrollable="true" bounds="[0,100][400,700]"/><node package="other.app" text="返回" bounds="[0,0][30,30]"/></hierarchy>'


def action(candidates, role="origin", **changes):
    target = next(c for c in candidates if c["role"] == role)
    raw = {"observation_id": "observation-one", "page": "route_input", "actions": [{"action": "enter_text", "target_id": target["target_id"], "role": role, "text": "广州塔"}]}
    raw.update(changes)
    return raw


def test_targets_bind_to_observation_epoch_package_and_literal_input():
    cs = describe_transit(tree(), "observation-one", context_epoch=2)
    assert len(cs) == 2
    a = action(cs)
    assert guard_transit(a, cs, {"origin": "广州塔"}, observation_id="observation-one", context_epoch=2)
    with pytest.raises(WorkflowError): guard_transit(a, cs, {"origin": "广州南站"}, observation_id="observation-one", context_epoch=2)
    with pytest.raises(WorkflowError): guard_transit(a, cs, {"origin": "广州塔"}, observation_id="observation-one", context_epoch=3)
    with pytest.raises(WorkflowError): guard_transit(a, cs, {"origin": "广州塔"}, observation_id="observation-two", context_epoch=2)
    assert describe_transit(tree(), "observation-two", context_epoch=2)[0]["target_id"] != cs[0]["target_id"]


def test_unknown_or_permission_screen_does_not_grant_tap_or_input():
    cs = describe_transit(tree(), "observation-one")
    with pytest.raises(WorkflowError): guard_transit(action(cs, page="permission"), cs, {"origin": "广州塔"}, observation_id="observation-one")
    with pytest.raises(ValidationError): TransitScreenDecision.model_validate({"observation_id": "one", "page": "search", "actions": [{"action": "shell", "text": "am start"}]})


def test_models_have_separate_transit_perception_and_evidence_extraction_prompts():
    gateway = ModelGateway()
    with patch.object(gateway, "structured", return_value=None) as call:
        gateway.understand("从广州塔到体育西路坐地铁", [])
        assert "plan_transit" in call.call_args.args[1]
        gateway.perceive_transit({"observation_id": "one", "candidates": []})
        assert call.call_args.args[0] is TransitScreenDecision
        gateway.extract_transit([], TransitRequest(), task_id=TASK, query_id=QUERY, route_id=ROUTE)
        assert call.call_args.args[0] is TransitResult and "不能凭知识" in call.call_args.args[1]


def test_bus_plus_subway_transfer_preserves_segment_order_and_names_both_alighting_stops():
    raw, ev = fixture()
    ev[0]["text"] += "\n公交10路\n东站方向\n公园站\n广州塔站\n在公园站内步行到地铁站"
    refs = {x: ["detail-one"] for x in ["line", "direction", "board", "alight", "instructions"]}
    raw["segments"].insert(1, {"segment_id": "bus-first", "mode": "bus", "line": "公交10路", "direction": "东站", "board": "广州塔站", "alight": "公园站", "evidence_refs": refs})
    raw["segments"][2]["board"] = "公园站"
    raw["segments"].insert(2, {"segment_id": "transfer-walk", "mode": "walk", "instructions": "在公园站内步行到地铁站", "evidence_refs": refs})
    r = verified(raw, ev)
    assert r["route_verified"] and r["transfers"] == 1
    assert [s["mode"] for s in r["segments"]] == ["walk", "bus", "walk", "subway", "walk"]
    answer = answer_question(r, "在哪一站下车？")
    assert "第1段" in answer and "第2段" in answer and "体育西路站" in answer


def test_real_probe_text_format_split_line_labels_and_currency_symbol_are_supported():
    # Public station names and label formats from the 2026-11-11 Amap probe.
    # This is a parser fixture, not another live query or an E2E claim.
    raw, ev = fixture()
    ev[0]["text"] = ev[0]["text"].replace("3号线", "3\n号线").replace("票价3元", "¥3")
    r = verified(raw, ev)
    assert r["route_verified"] and r["fare_yuan"] == 3 and r["segments"][1]["line"] == "3号线"


def test_ungrounded_optional_exit_and_elevator_claims_are_not_narrated():
    raw, ev = fixture();raw["segments"][1]["exit"] = "E口有电梯"
    raw["segments"][1]["evidence_refs"]["exit"] = ["detail-one"]
    raw["selection_basis"] = "全市最优无台阶方案"
    r = verified(raw, ev)
    assert r["segments"][1]["exit"] is None
    assert "有电梯" not in explain_result(r) and "全市最优" not in r["selection_basis"]


def test_same_name_place_choices_need_explicit_allowed_current_target():
    cs = describe_transit('<hierarchy><node package="com.autonavi.minimap" class="android.view.ViewGroup" bounds="[0,10][200,100]" content-desc="图书馆东馆"/></hierarchy>', "observation-one", role_map={0: "place"})
    decision = {"observation_id": "observation-one", "page": "place_list", "actions": [{"action": "tap_element", "target_id": cs[0]["target_id"], "role": "place"}]}
    with pytest.raises(WorkflowError): guard_transit(decision, cs, {}, observation_id="observation-one")
    assert guard_transit(decision, cs, {}, observation_id="observation-one", allowed_place_ids=[cs[0]["target_id"]])


def test_scroll_must_target_an_actual_current_visible_scroll_container():
    cs = describe_transit(tree(), "observation-one")
    c = next(x for x in cs if x["role"] == "scroll")
    d = {"observation_id": "observation-one", "page": "route_detail", "actions": [{"action": "scroll", "target_id": c["target_id"], "role": "scroll", "direction": "up"}]}
    assert guard_transit(d, cs, {}, observation_id="observation-one")
    cs[1]["scrollable"] = False
    with pytest.raises(WorkflowError): guard_transit(d, cs, {}, observation_id="observation-one")


def test_model_cannot_change_requested_preference_or_drop_a_selected_branch():
    raw, ev = fixture();raw["preference"] = "less_walking"
    assert verified(raw, ev)["preference"] == "fewer_transfers"
    r = verified(raw, ev, selected_places={"origin": {"name": "广州塔地铁站", "address": "未匹配的另一入口"}})
    assert not r["route_verified"] and "origin.selected_address_does_not_match" in r["missing_fields"]
    r = validate_result(raw, TASK, QUERY, {"origin": "广州塔地铁站东口", "destination": "体育西路"}, evidence=ev, collection_complete=True)
    assert "origin.does_not_match_request" in r["missing_fields"]


def test_hour_conversion_does_not_accept_the_minutes_component_as_the_whole_time():
    raw, ev = fixture();ev[0]["text"] = ev[0]["text"].replace("25分钟", "1小时12分钟")
    raw["duration_minutes"] = 72
    assert verified(raw, ev)["duration_minutes"] == 72
    raw["duration_minutes"] = 12
    assert verified(raw, ev)["duration_minutes"] is None


def test_navigation_controls_are_forbidden_even_if_a_resolver_assigns_a_query_role():
    cs = describe_transit('<hierarchy><node package="com.autonavi.minimap" class="android.view.ViewGroup" bounds="[0,10][200,100]" content-desc="开始导航，开启下车提醒"/></hierarchy>', "observation-one", role_map={0: "details"})
    decision = {"observation_id": "observation-one", "page": "route_detail", "actions": [{"action": "tap_element", "target_id": cs[0]["target_id"], "role": "details"}]}
    with pytest.raises(WorkflowError): guard_transit(decision, cs, {}, observation_id="observation-one")


def test_explicit_city_prefix_is_compatible_with_canonical_station_labels():
    raw, ev = fixture()
    r = validate_result(raw, TASK, QUERY, {"origin": "广州市广州塔地铁站", "destination": "广州市体育西路地铁站", "city": "广州"}, evidence=ev, collection_complete=True)
    assert r["route_verified"]
    raw["origin"]["name"] = "塔";ev[0]["text"] += "\n塔"
    r = validate_result(raw, TASK, QUERY, {"origin": "广州塔", "destination": "体育西路", "city": "广州"}, evidence=ev, collection_complete=True)
    assert not r["route_verified"]


def explicit_campus_route(actual_name):
    raw, evidence = fixture()
    address = "广州市天河区示例路1号"
    quote = f"从{actual_name}步行到广州塔站"
    evidence[0]["text"] += f"\n{actual_name}\n{address}\n{quote}"
    raw["origin"] = {"name": actual_name, "address": address, "city": "广州市", "source": "explicit",
        "evidence_refs": {field: ["detail-one"] for field in ["name", "address", "city"]}}
    raw["segments"][0]["instructions"] = quote
    return raw, evidence


@pytest.mark.parametrize("requested,observed", [
    ("校园国际校区", "校园(国际校区)"),
    ("校园（国际校区）", "校园国际校区"),
    ("校园(国际校区)", "校园（国际校区）"),
    ("广州市校园国际校区", "校园(国际校区)"),
])
def test_request_parentheses_are_punctuation_only_in_a_complete_grounded_route(requested, observed):
    raw, evidence = explicit_campus_route(observed)
    result = validate_result(raw, TASK, QUERY, {"origin": requested, "destination": "体育西路地铁站", "city": "广州市"},
        evidence=evidence, collection_complete=True)
    assert result["classification"] == "COMPLETE" and result["route_verified"]
    assert result["origin"]["name"] == observed  # Preserve the actual readback.


@pytest.mark.parametrize("observed", [
    "校园国际校区(西南门)", "校园(国际校区西南门)", "校园(国际校区)停车场",
    "校园(国际校区分校区)", "校园(北校区)",
])
def test_parentheses_never_hide_a_gate_parking_or_other_campus_in_route_verification(observed):
    raw, evidence = explicit_campus_route(observed)
    result = validate_result(raw, TASK, QUERY, {"origin": "校园国际校区", "destination": "体育西路地铁站", "city": "广州市"},
        evidence=evidence, collection_complete=True)
    assert result["classification"] == "PARTIAL" and not result["route_verified"]
    assert "origin.does_not_match_request" in result["missing_fields"]


@pytest.mark.parametrize("selected", [
    {"name": "校园国际校区", "address": "广州市天河区示例路1号"},
    {"name": "校园(国际校区)", "address": "广州市天河区示例路10号"},
])
def test_request_punctuation_compatibility_does_not_weaken_the_current_selected_identity(selected):
    raw, evidence = explicit_campus_route("校园(国际校区)")
    result = validate_result(raw, TASK, QUERY, {"origin": "校园国际校区", "destination": "体育西路地铁站", "city": "广州市"},
        evidence=evidence, collection_complete=True, selected_places={"origin": selected})
    assert result["classification"] == "PARTIAL" and not result["route_verified"]
    field = "name" if selected["name"] != raw["origin"]["name"] else "address"
    assert f"origin.selected_{field}_does_not_match" in result["missing_fields"]


def test_extractors_explicit_incompleteness_is_not_overridden_by_visible_destination():
    raw, ev = fixture();raw["classification"] = "PARTIAL"
    raw["missing_fields"] = ["segments.second_ride_not_read"]
    r = verified(raw, ev, collection_complete=True)
    assert not r["route_verified"] and r["classification"] == "PARTIAL"
    assert "segments.second_ride_not_read" in r["missing_fields"]
    raw["missing_fields"] = []
    r = verified(raw, ev, collection_complete=True)
    assert not r["route_verified"] and "extractor_reported_partial" in r["missing_fields"]


def connection_fixture():
    raw, ev = fixture()
    raw["segments"] = [raw["segments"][1], {
        "segment_id": "ride-two", "mode": "subway", "line": "5号线", "direction": "黄埔", "board": "公园站", "alight": "文化中心站",
        "evidence_refs": {field: ["detail-one"] for field in ["line", "direction", "board", "alight"]}}]
    raw["destination"]["name"] = "文化中心站"
    ev[0]["text"] += "\n5号线\n黄埔方向\n公园站\n文化中心站"
    return raw, ev


def checked_connections(raw, ev):
    return validate_result(raw, TASK, QUERY, evidence=ev, collection_complete=True)


def test_two_grounded_but_disconnected_rides_are_partial_not_a_complete_itinerary():
    raw, ev = connection_fixture()
    result = checked_connections(raw, ev)
    assert not result["route_verified"] and result["classification"] == "PARTIAL"
    assert "segments.ride-two.transfer_connection_not_verified" in result["missing_fields"]
    assert result["transfers"] is None


@pytest.mark.parametrize("quote", ["步行200米", "从公园站步行至体育西路站", "从体育西路站步行到广场"])
def test_walk_must_explicitly_connect_the_actual_stations_in_the_correct_order(quote):
    raw, ev = connection_fixture();ev[0]["text"] += "\n" + quote
    raw["segments"].insert(1, {"segment_id": "transfer", "mode": "walk", "instructions": quote, "evidence_refs": {"instructions": ["detail-one"]}})
    assert not checked_connections(raw, ev)["route_verified"]


def test_explicit_grounded_station_to_station_walk_closes_the_transfer_gap():
    raw, ev = connection_fixture();quote = "从体育西路站步行至公园站"
    ev[0]["text"] += "\n" + quote
    raw["segments"].insert(1, {"segment_id": "transfer", "mode": "walk", "instructions": quote, "evidence_refs": {"instructions": ["detail-one"]}})
    result = checked_connections(raw, ev)
    assert result["route_verified"] and result["transfers"] == 1


def test_same_station_transfer_needs_no_invented_walking_segment():
    raw, ev = connection_fixture();raw["segments"][1]["board"] = "体育西路站"
    result = checked_connections(raw, ev)
    assert result["route_verified"] and result["transfers"] == 1


def test_similar_station_prefix_is_not_a_same_station_transfer():
    raw, ev = connection_fixture();raw["segments"][1]["board"] = "体育西路东站";ev[0]["text"] += "\n体育西路东站"
    assert not checked_connections(raw, ev)["route_verified"]


def test_nonstation_endpoints_need_explicit_first_and_last_walking_links():
    raw, ev = fixture()
    raw["origin"]["name"] = "市博物馆东馆";raw["destination"]["name"] = "社区服务中心"
    ev[0]["text"] += "\n市博物馆东馆\n社区服务中心"
    raw["segments"] = [raw["segments"][1]]
    result = checked_connections(raw, ev)
    assert not result["route_verified"]
    assert "origin.walk_connection_not_verified" in result["missing_fields"]
    assert "destination.walk_connection_not_verified" in result["missing_fields"]
    first = "从市博物馆东馆步行到广州塔站";last = "从体育西路站步行至社区服务中心"
    ev[0]["text"] += "\n" + first + "\n" + last
    def walk(segment_id, text):
        return {"segment_id": segment_id, "mode": "walk", "instructions": text, "evidence_refs": {"instructions": ["detail-one"]}}
    raw["segments"].insert(0, walk("start", first));raw["segments"].append(walk("end", last))
    assert checked_connections(raw, ev)["route_verified"]


def test_ungrounded_link_cannot_connect_the_endpoints_even_when_it_sounds_plausible():
    raw, ev = connection_fixture();quote = "从体育西路站步行到公园站"
    raw["segments"].insert(1, {"segment_id": "transfer", "mode": "walk", "instructions": quote, "evidence_refs": {"instructions": ["detail-one"]}})
    result = checked_connections(raw, ev)
    assert not result["route_verified"] and result["segments"][1]["instructions"] is None


def test_explicit_multiple_walk_links_must_form_a_continuous_chain():
    raw, ev = connection_fixture()
    def walk(segment_id, start, end):
        quote = f"从{start}步行到{end}";ev[0]["text"] += "\n" + quote
        return {"segment_id": segment_id, "mode": "walk", "board": start, "alight": end, "instructions": quote,
                "evidence_refs": {field: ["detail-one"] for field in ["board", "alight", "instructions"]}}
    raw["segments"][1:1] = [walk("walk-a", "体育西路站", "广场东门"), walk("walk-b", "广场东门", "公园站")]
    assert checked_connections(raw, ev)["route_verified"]
    raw["segments"][2]["board"] = "广场西门";ev[0]["text"] += "\n广场西门"
    assert not checked_connections(raw, ev)["route_verified"]


def test_a_grounded_bus_instruction_cannot_be_relabelled_as_a_walking_connection():
    raw, ev = connection_fixture();quote = "从体育西路站乘坐公交到公园站"
    ev[0]["text"] += "\n" + quote
    raw["segments"].insert(1, {"segment_id": "mislabelled-bus", "mode": "walk", "instructions": quote,
                               "evidence_refs": {"instructions": ["detail-one"]}})
    assert not checked_connections(raw, ev)["route_verified"]


@pytest.mark.parametrize("kind,tag", [("user_request", None), ("place_detail", None), ("route_list", "different-route"), ("route_detail", None)])
def test_user_constraints_poi_distances_and_other_routes_are_not_trip_result_metrics(kind, tag):
    raw, ev = fixture()
    ev.append({"evidence_id": "wrong-metric-source", "query_id": QUERY, "route_id": tag,
               "observation_id": "wrong-metric-source", "page_kind": kind,
               "text": "希望10分钟内到，预算1元，步行最多100米，少换乘"})
    raw.update(duration_minutes=10, fare_yuan=1, walking_meters=100, applied_filter="少换乘")
    for field in ["duration_minutes", "fare_yuan", "walking_meters", "applied_filter"]:
        raw["evidence_refs"][field] = ["wrong-metric-source"]
    result = verified(raw, ev)
    assert all(result[field] is None for field in ["duration_minutes", "fare_yuan", "walking_meters", "applied_filter"])
    # The independently complete itinerary is retained; omitted noncritical
    # measurements do not turn it into a failed route.
    assert result["route_verified"]
