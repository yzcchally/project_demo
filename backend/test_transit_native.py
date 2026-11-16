"""Reduced Amap native-tree fixtures; these are offline extraction checks only."""
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from transit.native import extract_native
from transit.validation import validate_result

PACKAGE = "com.autonavi.minimap"
TASK, QUERY, ROUTE = "offline-task", "offline-query", "offline-route"


def n(text="", desc="", children=(), group=True, visible=True):
    item = ET.Element("node", {"package": PACKAGE, "class": "android.view.ViewGroup" if group else "android.view.View",
                              "text": text, "content-desc": desc, "bounds": "[176,700][1131,1500]" if visible else "[0,0][0,0]"})
    item.extend(children)
    return item


def station(name, visible=True):
    return n(desc=name, children=[n(text=name, group=False, visible=visible)], visible=visible)


def column(board="体育西路", alight="广州塔", line=("3", "号线"), direction="海傍方向", stops="2站4分钟"):
    # Reduced direct-child topology from Amap 15.13 detail-1, public stations.
    # Preserve stop→direction-with-line→metrics→stop relationships, not results.
    children = []
    if board: children.append(station(board))
    children.append(n(children=[n(desc=direction, children=[
        n(children=[n(text=x, group=False) for x in line]), n(text=direction, group=False)])]))
    children.append(n(children=[n(desc=stops, children=[n(text=stops, group=False)])]))
    if alight: children.append(station(alight))
    return n(children=children)


def observation(tmp_path, columns=None, *, index=1, summary="8分钟3号线无堵车风险2站¥2", extra=(), query=QUERY, route=ROUTE):
    root = ET.Element("hierarchy")
    detail = n(desc="公交详情页面", children=[n(desc=summary, children=[n(text=summary, group=False)]), *(columns or [column()]), *extra])
    root.append(detail)
    path = tmp_path / f"native-{index}.xml";ET.ElementTree(root).write(path, encoding="unicode")
    # This fixture's evidence is directly rendered from its native nodes.
    text = "\n".join(dict.fromkeys(x.get("content-desc") or x.get("text") for x in root.iter("node") if x.get("content-desc") or x.get("text")))
    return {"evidence_id": f"e-{index}", "query_id": query, "route_id": route, "observation_id": f"o-{index}",
            "page_kind": "route_detail", "text": text, "xml_path": str(path)}


def extract(obs, request=None):
    return extract_native(obs, request or {"origin": "体育西路地铁站", "destination": "广州塔地铁站", "city": "广州"}, task_id=TASK, query_id=QUERY, route_id=ROUTE)


def two_edge_walk_fixture(tmp_path, *, extra_tail=False, hidden_tail=False):
    ride = column("客村立交站", "体育中心站", ("夜", "61", "路"), "体育中心站方向", "3站9分钟")
    list(ride)[0].append(n(text="关注", group=False))
    lead = n(desc="步行 1.4公里 23分钟", children=[n(text="步行 1.4公里 · 23分钟", group=False), n(text="步行导航", group=False)])
    tail = n(desc="步行 403米 8分钟", children=[n(text="步行 403米 · 8分钟", group=False), n(text="步行导航", group=False)], visible=not hidden_tail)
    branches = [station("广州塔(地铁站)"), n(children=[lead]), n(children=[ride]), n(children=[tail]), station("体育西路(地铁站)")]
    if extra_tail:
        branches.insert(-1, station("其他地点"))
    return observation(tmp_path, [n(children=branches)], summary="49分钟步行23分钟转夜61路步行8分钟最快3站¥4步行1.8公里")


def test_two_measured_edge_walks_are_bound_independently_without_swapping_metrics(tmp_path):
    e = two_edge_walk_fixture(tmp_path)
    e["text"] += "\n广州"
    req = {"origin": "广州塔地铁站", "destination": "体育西路地铁站", "city": "广州"}
    result = extract([e], req)
    assert result and [s["mode"] for s in result["segments"]] == ["walk", "bus", "walk"]
    a, ride, b = result["segments"]
    assert (a["walking_meters"], a["duration_minutes"], a["alight"]) == (1400, 23, "客村立交站")
    assert (b["walking_meters"], b["duration_minutes"], b["board"]) == (403, 8, "体育中心站")
    result["origin"] = {"name": "广州塔(地铁站)", "city": "广州", "evidence_refs": {"name": [e["evidence_id"]], "city": [e["evidence_id"]]}}
    result["destination"] = {"name": "体育西路(地铁站)", "city": "广州", "evidence_refs": {"name": [e["evidence_id"]], "city": [e["evidence_id"]]}}
    checked = validate_result(result, TASK, QUERY, req, evidence=[e], collection_complete=True)
    assert checked["classification"] == "COMPLETE" and checked["route_verified"]
    # Even if flattened page text still includes all names, a new branch
    # inserted between the walk and endpoint invalidates the connection.
    tree = ET.parse(e["xml_path"])
    for owner in tree.getroot().iter("node"):
        children = list(owner)
        if len(children) == 5 and children[-1].get("content-desc") == "体育西路(地铁站)":
            owner.insert(4, station("其他地点")); break
    tree.write(e["xml_path"], encoding="unicode")
    rejected = validate_result(result, TASK, QUERY, req, evidence=[e], collection_complete=True)
    assert rejected["classification"] != "COMPLETE" and not rejected["route_verified"]


@pytest.mark.parametrize("issue", ["intervening_branch", "hidden_tail"])
def test_two_measured_walks_reject_missing_adjacency_or_visibility(tmp_path, issue):
    e = two_edge_walk_fixture(tmp_path, extra_tail=issue == "intervening_branch", hidden_tail=issue == "hidden_tail")
    assert extract([e], {"origin": "广州塔地铁站", "destination": "体育西路地铁站", "city": "广州"}) is None


def test_identical_walking_rows_in_one_frame_are_not_collapsed(tmp_path):
    e = two_edge_walk_fixture(tmp_path, extra_tail=True)
    tree = ET.parse(e["xml_path"])
    for item in tree.getroot().iter("node"):
        for field in ("text", "content-desc"):
            item.set(field, item.get(field, "").replace("403米", "1.4公里").replace("8分钟", "23分钟"))
    tree.write(e["xml_path"], encoding="unicode")
    assert extract([e], {"origin": "广州塔地铁站", "destination": "体育西路地铁站", "city": "广州"}) is None


def test_actual_public_station_topology_yields_only_observed_facts(tmp_path):
    e = observation(tmp_path);r = extract([e])
    assert r and not r["route_verified"]
    assert r["duration_minutes"] == 8 and r["fare_yuan"] == 2
    assert r["walking_meters"] is None
    ride = r["segments"][0]
    assert (ride["mode"], ride["line"], ride["direction"], ride["board"], ride["alight"], ride["stops"]) == ("subway", "3号线", "海傍", "体育西路", "广州塔", 2)
    assert ride["duration_minutes"] == 4
    assert all(v == [e["evidence_id"]] for v in ride["evidence_refs"].values())
    assert not ride.get("entrance") and not ride.get("exit")


def test_a_real_split_station_and_portal_group_preserves_both_observed_fields(tmp_path):
    ride = column("东城站", "西园站", ("3", "号线"))
    ride.remove(list(ride)[-1])
    ride.append(n(desc="西园站C口", children=[n(text="西园站", group=False), n(text="C口", group=False)]))
    e = observation(tmp_path, [ride]);r = extract([e], {"origin": "东城站", "destination": "西园站"})
    assert r and r["segments"][0]["alight"] == "西园站" and r["segments"][0]["exit"] == "C口"
    assert r["segments"][0]["evidence_refs"]["exit"] == [e["evidence_id"]]


def test_a_portal_label_elsewhere_does_not_become_the_riding_exit(tmp_path):
    e = observation(tmp_path, extra=[n(text="C口", group=False)])
    r = extract([e])
    assert r and not r["segments"][0].get("exit")


def poi_tail_fixture(tmp_path, *, index=1, portal="C口", destination="公共展馆", hidden_destination=False, extra_branch=False):
    """Anonymous reduction of the actual T14 ride/walking-slot/POI owner."""
    ride = column("东城站", "中心站", ("3", "号线"), "南城方向", "1站2分钟")
    ride.remove(list(ride)[-1])
    ride.append(n(desc="中心站" + portal, children=[n(text="中心站", group=False), n(text=portal, group=False)]))
    walk = n(desc="站内外共步行 876米 15分钟", children=[
        n(text="站内外共步行 876米 · 15分钟", group=False), n(text="步行导航", group=False),
        n(children=[n(text="其他出行方式", group=False), n(text="6分钟", group=False)])])
    branches = [n(children=[ride]), n(children=[walk]), station(destination, visible=not hidden_destination)]
    if extra_branch: branches.insert(2, station("另一处地点"))
    return observation(tmp_path, [n(children=branches)], index=index, summary="19分钟3号线步行15分钟1站¥2步行876米")


def test_a_real_native_tail_slot_binds_the_actual_exit_to_the_poi_without_allocating_aggregate_numbers(tmp_path):
    e = poi_tail_fixture(tmp_path)
    result = extract([e], {"origin": "东城站", "destination": "公共展馆"})
    assert result and [s["mode"] for s in result["segments"]] == ["subway", "walk"]
    ride, walk = result["segments"]
    assert ride["alight"] == "中心站" and ride["exit"] == "C口"
    assert (walk["board"], walk["alight"], walk["instructions"]) == ("中心站C口", "公共展馆", "站内外共步行")
    assert walk["duration_minutes"] is None and walk["walking_meters"] is None
    assert result["duration_minutes"] == 19 and result["walking_meters"] == 876
    assert all(walk["evidence_refs"][field] == [e["evidence_id"]] for field in ["instructions", "board", "alight"])
    from transit.explain import segment_text
    spoken = segment_text(walk, 2)
    assert "从中心站C口步行至公共展馆" in spoken and "具体街道走法未显示" in spoken
    assert "876" not in spoken and "15分钟" not in spoken and "6分钟" not in spoken
    result["origin"] = {"name": "东城站", "evidence_refs": {"name": [e["evidence_id"]]}}
    result["destination"] = {"name": "公共展馆", "evidence_refs": {"name": [e["evidence_id"]]}}
    checked = validate_result(result, TASK, QUERY, {"origin": "东城站", "destination": "公共展馆"},
                              evidence=[e], collection_complete=True)
    assert checked["route_verified"] and checked["classification"] == "COMPLETE"
    assert checked["segments"][1]["walking_meters"] is None
    assert "具体街道走法未显示" in checked["derived"][f"segments.{walk['segment_id']}.walk_connection"]


@pytest.mark.parametrize("problem", ["hidden_terminal", "intervening_branch", "another_destination"])
def test_native_tail_slot_never_stitches_nonadjacent_or_unverified_endpoints(tmp_path, problem):
    e = poi_tail_fixture(tmp_path, hidden_destination=problem == "hidden_terminal", extra_branch=problem == "intervening_branch")
    destination = "另一处展馆" if problem == "another_destination" else "公共展馆"
    assert extract([e], {"origin": "东城站", "destination": destination}) is None


def test_repeated_tail_frames_deduplicate_the_same_observed_riding_and_walking_relationship(tmp_path):
    a, b = poi_tail_fixture(tmp_path, index=1), poi_tail_fixture(tmp_path, index=2)
    result = extract([a, b], {"origin": "东城站", "destination": "公共展馆"})
    assert result and len(result["segments"]) == 2 and result["transfers"] == 0
    assert result["segments"][1]["evidence_refs"]["instructions"] == [b["evidence_id"]]


def test_a_changed_portal_across_frames_is_not_silently_deduplicated_as_one_tail_walk(tmp_path):
    a, b = poi_tail_fixture(tmp_path, index=1), poi_tail_fixture(tmp_path, index=2, portal="D口")
    assert extract([a, b], {"origin": "东城站", "destination": "公共展馆"}) is None


def test_a_terminal_marked_hidden_with_nonzero_bounds_does_not_prove_a_tail_connection(tmp_path):
    e = poi_tail_fixture(tmp_path)
    tree = ET.parse(e["xml_path"])
    for item in tree.getroot().iter("node"):
        if item.get("content-desc") == "公共展馆" or item.get("text") == "公共展馆":
            item.set("visible-to-user", "false")
    tree.write(e["xml_path"], encoding="unicode")
    assert extract([e], {"origin": "东城站", "destination": "公共展馆"}) is None


def long_position_route_frame(tmp_path, *, index=1, front=True):
    """Anonymous two-screen reduction of the real GPS itinerary's seven rows."""
    first = column("北园站", "中心站", ("7", "号线"), "西城方向", "3站10分钟")
    first.remove(list(first)[0])
    first.insert(0, n(desc="北园站C口", children=[n(text="北园站", group=False), n(text="C口", group=False)]))
    second = column("中心站", "南园站", ("3", "号线"), "北城方向", "7站21分钟")
    second.remove(list(second)[-1])
    second.append(n(desc="南园站C口", children=[n(text="南园站", group=False), n(text="C口", group=False)]))
    origin = station("我的位置")
    start_walk = n(desc="步行 2.4公里 37分钟", children=[n(text="步行 2.4公里 · 37分钟", group=False),
        n(text="步行导航", group=False), n(children=[n(text="其他出行方式", group=False), n(text="16分钟", group=False)])])
    transfer = n(desc="站内换乘 238米5分钟", children=[n(text="站内换乘 238米 · 5分钟", group=False)])
    tail = n(desc="站内外共步行 876米 15分钟", children=[n(text="站内外共步行 876米 · 15分钟", group=False)])
    terminal = station("公共展馆")
    def hide(branch):
        for item in branch.iter("node"): item.set("bounds", "[0,0][0,0]")
    if front:
        for branch in [second,tail,terminal]: hide(branch)
    else:
        hide(origin);hide(start_walk)
        for branch in list(first)[:-1]: hide(branch)  # Only the actual alighting stop remains visible.
    owner = n(children=[origin,n(children=[start_walk]),n(children=[first]),n(children=[transfer]),
                        n(children=[second]),n(children=[tail]),terminal])
    return observation(tmp_path, [owner], index=index, summary="1小时34分步行37分钟转7号线转3号线步行15分钟10站¥5步行3.5公里")


def test_long_location_route_keeps_all_five_observed_legs_across_complete_riding_frames(tmp_path):
    a, b = long_position_route_frame(tmp_path), long_position_route_frame(tmp_path, index=2, front=False)
    request = {"origin": "", "destination": "公共展馆", "use_current_location": True}
    result = extract([a,b], request)
    assert result and [s["mode"] for s in result["segments"]] == ["walk","subway","walk","subway","walk"]
    start, first, transfer, second, end = result["segments"]
    assert (start["board"], start["alight"], start["instructions"]) == ("我的位置","北园站C口","步行 2.4公里 37分钟")
    assert start["duration_minutes"] == 37 and start["walking_meters"] == 2400
    assert (first["line"],first["board"],first["alight"]) == ("7号线","北园站","中心站")
    assert transfer["instructions"] == "站内换乘 238米5分钟" and transfer["walking_meters"] == 238
    assert (second["line"],second["board"],second["alight"]) == ("3号线","中心站","南园站")
    assert end["board"] == "南园站C口" and end["alight"] == "公共展馆"
    assert end["walking_meters"] is None and end["duration_minutes"] is None
    assert result["duration_minutes"] == 94 and result["walking_meters"] == 3500 and result["transfers"] == 1
    position = {"evidence_id":"position", "query_id":QUERY, "route_id":None, "page_kind":"location",
        "observation_id":"position-o", "text":"公共入口\n示例市新区公共路1号\n示例市",
        "location_status":{"verified":True,"enabled":True,"permission_granted":True,"app_access_recent":True,
            "provider":"gps","age_seconds":1,"accuracy_m":5,"is_mock":False,
            "checked_at":"2026-11-12T00:00:01+00:00","fix_time":"2026-11-12T00:00:00+00:00"}}
    result["origin"] = {"name":"公共入口","address":"示例市新区公共路1号","city":"示例市","source":"current_location",
        "evidence_refs":{field:["position"] for field in ["name","address","city"]}}
    result["destination"] = {"name":"公共展馆","evidence_refs":{"name":[b["evidence_id"]]}}
    checked = validate_result(result,TASK,QUERY,request,evidence=[a,b,position],collection_complete=True)
    assert checked["route_verified"] and checked["classification"] == "COMPLETE"
    assert checked["segments"][0]["board"] == "公共入口"
    from transit.explain import segment_text
    spoken = segment_text(checked["segments"][0],1)
    assert "从公共入口步行至北园站C口" in spoken and "37分钟" in spoken and "16分钟" not in spoken


def test_position_placeholder_cannot_be_used_as_the_prefix_of_an_explicit_origin_route(tmp_path):
    a, b = long_position_route_frame(tmp_path), long_position_route_frame(tmp_path,index=2,front=False)
    assert extract([a,b], {"origin":"别处入口","destination":"公共展馆"}) is None


def test_a_long_route_cannot_borrow_the_next_complete_ride_from_another_query(tmp_path):
    a, b = long_position_route_frame(tmp_path), long_position_route_frame(tmp_path,index=2,front=False)
    b["query_id"] = "another-query"
    assert extract([a,b], {"origin":"","destination":"公共展馆","use_current_location":True}) is None


def test_native_candidate_still_requires_the_independent_full_collection_signal(tmp_path):
    e = observation(tmp_path);r = extract([e])
    for role, name in [("origin", "体育西路"), ("destination", "广州塔")]:
        r[role] = {"name": name, "evidence_refs": {"name": [e["evidence_id"]]}}
    checked = validate_result(r, TASK, QUERY, {"origin": "体育西路", "destination": "广州塔"}, evidence=[e], collection_complete=True)
    assert checked["route_verified"] and checked["classification"] == "COMPLETE"
    checked = validate_result(r, TASK, QUERY, {"origin": "体育西路", "destination": "广州塔"}, evidence=[e], collection_complete=False)
    assert not checked["route_verified"] and checked["classification"] == "PARTIAL"


def test_station_names_line_and_direction_come_from_the_tree_not_a_database(tmp_path):
    e = observation(tmp_path, [column("东城站", "西园站", ("6", "号线"), "北城方向", "5站12分钟")], summary="20分钟6号线5站¥4")
    r = extract([e], {"origin": "东城站", "destination": "西园站"})
    assert r["segments"][0]["line"] == "6号线" and r["segments"][0]["direction"] == "北城"
    assert r["segments"][0]["stops"] == 5 and r["duration_minutes"] == 20


def test_partial_frames_are_not_combined_to_invent_a_complete_riding_column(tmp_path):
    a = observation(tmp_path, [column(alight=None)], index=0)
    b = observation(tmp_path, [column(board=None)], index=1)
    assert extract([a, b]) is None


def test_later_complete_frame_and_repeated_frames_create_only_one_ride(tmp_path):
    a = observation(tmp_path, [column(alight=None)], index=0)
    b = observation(tmp_path, index=1)
    c = observation(tmp_path, index=2)
    r = extract([a, b, c])
    assert len(r["segments"]) == 1 and r["transfers"] == 0
    assert r["segments"][0]["evidence_refs"]["alight"] == ["e-1"]


def test_same_station_transfer_can_preserve_two_complete_native_legs(tmp_path):
    a = column("甲站", "乙站", ("1", "号线"), "东城方向")
    b = column("乙站", "丙站", ("2", "号线"), "西城方向", "3站5分钟")
    e = observation(tmp_path, [a, b], summary="30分钟1号线换乘2号线5站¥4")
    r = extract([e], {"origin": "甲站", "destination": "丙站"})
    assert r and r["transfers"] == 1
    assert [s["line"] for s in r["segments"]] == ["1号线", "2号线"]
    assert r["segments"][0]["alight"] == r["segments"][1]["board"] == "乙站"


def test_bus_then_subway_keeps_observed_modes_without_assuming_a_walking_transfer(tmp_path):
    e = observation(tmp_path, [column("甲站", "乙站", ("10路",), "东城方向"), column("乙站", "丙站", ("2", "号线"), "西城方向")], summary="30分钟10路换乘2号线5站¥4")
    r = extract([e], {"origin": "甲站", "destination": "丙站"})
    assert r and [s["mode"] for s in r["segments"]] == ["bus", "subway"]


def test_a_different_station_interchange_goes_to_the_full_parser(tmp_path):
    e = observation(tmp_path, [column("甲站", "乙站", ("1", "号线")), column("丁站", "丙站", ("2", "号线"))])
    assert extract([e], {"origin": "甲站", "destination": "丙站"}) is None


@pytest.mark.parametrize("walk", ["步行至地铁站", "步行约100米", "总步行300米"])
def test_any_observed_walking_leg_is_not_silently_dropped(tmp_path, walk):
    e = observation(tmp_path, extra=[n(desc=walk)])
    assert extract([e]) is None


def test_an_unknown_additional_riding_structure_does_not_leave_a_false_direct_route(tmp_path):
    e = observation(tmp_path, [column(), column("广州塔", "终点站", ("无法识别线路",), "终点方向")])
    assert extract([e]) is None


def test_repeated_identical_legs_inside_one_frame_are_ambiguous_not_screen_dedup(tmp_path):
    e = observation(tmp_path, [column(), column()])
    assert extract([e]) is None


@pytest.mark.parametrize("bad", ["wrong_query", "wrong_route", "summary_only", "missing_file", "malformed_file"])
def test_wrong_query_or_missing_native_details_never_yield_a_route(tmp_path, bad):
    e = observation(tmp_path)
    if bad == "wrong_query": e["query_id"] = "old-query"
    if bad == "wrong_route": e["route_id"] = "old-route"
    if bad == "summary_only": e["page_kind"] = "route_list"
    if bad == "missing_file": e["xml_path"] = str(tmp_path / "absent.xml")
    if bad == "malformed_file": Path(e["xml_path"]).write_text("not XML")
    assert extract([e]) is None


def test_missing_noncritical_summary_does_not_guess_duration_or_fare(tmp_path):
    e = observation(tmp_path, summary="本次公交方案")
    r = extract([e])
    assert r and r["duration_minutes"] is None and r["fare_yuan"] is None


def test_explicit_endpoints_cannot_be_replaced_by_a_different_station_route(tmp_path):
    e = observation(tmp_path)
    assert extract([e], {"origin": "客村", "destination": "广州塔"}) is None
    assert extract([e], {"origin": "体育西路", "destination": "客村"}) is None


@pytest.mark.parametrize("summary,minutes", [("1小时15分3号线2站¥2", 75), ("1小时0分3号线2站¥2", 60), ("8分钟3号线2站¥2", 8), ("2小时3号线2站¥2", 120)])
def test_native_duration_supports_current_amap_hour_and_minute_labels(tmp_path, summary, minutes):
    r = extract([observation(tmp_path, summary=summary)])
    assert r and r["duration_minutes"] == minutes


def test_untyped_airport_express_is_not_assumed_to_be_subway(tmp_path):
    e = observation(tmp_path, [column(line=("机场快线",))])
    assert extract([e]) is None


@pytest.mark.parametrize("loop", ["内环", "外环"])
def test_observed_numeric_loop_line_suffix_is_retained_and_grounded(tmp_path, loop):
    e = observation(tmp_path, [column("甲站", "乙站", ("11", "号线", loop), direction=f"{loop}方向")],
                    summary=f"30分钟11号线{loop}2站¥4")
    result = extract([e], {"origin": "甲站", "destination": "乙站"})
    assert result and result["segments"][0]["line"] == f"11号线{loop}"
    assert result["segments"][0]["direction"] == loop
    assert result["segments"][0]["evidence_refs"]["line"] == [e["evidence_id"]]
    result["origin"] = {"name": "甲站", "evidence_refs": {"name": [e["evidence_id"]]}}
    result["destination"] = {"name": "乙站", "evidence_refs": {"name": [e["evidence_id"]]}}
    checked = validate_result(result, TASK, QUERY, {"origin": "甲站", "destination": "乙站"}, evidence=[e], collection_complete=True)
    assert checked["route_verified"] and checked["segments"][0]["line"] == f"11号线{loop}"


@pytest.mark.parametrize("suffix", ["快车", "临时运营", "未知支路"])
def test_unobserved_numeric_line_suffix_rules_are_not_guessed(tmp_path, suffix):
    e = observation(tmp_path, [column(line=("11", "号线", suffix))])
    assert extract([e]) is None


def in_station_fixture(tmp_path, *, index=1, transfer_station="乙站", next_board="乙站", distance=145, summary_distance=145, detached=False, unknown_endpoint_walk=False):
    first = column("甲站", transfer_station, ("3", "号线"), "北城方向", "2站6分钟")
    second = column(next_board, "丙站", ("1", "号线"), "西城方向", "5站10分钟")
    # Anonymous reduction of D's actual hierarchy: a list has three sibling
    # sections; the native transfer row is between two complete riding columns.
    quote = f"站内换乘 {distance}米4分钟"
    transfer = n(desc=quote, children=[n(text=f"站内换乘 {distance}米 · 4分钟", group=False)])
    sections = [n(children=[first]), n(children=[transfer]), n(children=[second])]
    if detached:
        sections = [n(children=[first]), n(children=[second])]
    extras = [transfer] if detached else []
    if unknown_endpoint_walk: extras.append(n(desc="步行至起点入口"))
    e = observation(tmp_path, [n(children=sections)], index=index,
                    summary=f"25分钟3号线转1号线7站¥3步行{summary_distance}米", extra=extras)
    return e, quote


def test_anonymous_native_same_station_transfer_keeps_the_exact_walk_and_independent_metrics(tmp_path):
    e, quote = in_station_fixture(tmp_path)
    result = extract([e], {"origin": "甲站", "destination": "丙站"})
    assert result and [s["mode"] for s in result["segments"]] == ["subway", "walk", "subway"]
    walk = result["segments"][1]
    assert walk["instructions"] == quote == "站内换乘 145米4分钟"
    assert walk["board"] == walk["alight"] == "乙站"
    assert walk["walking_meters"] == 145 and walk["duration_minutes"] == 4
    assert walk["evidence_refs"]["walking_meters"] == walk["evidence_refs"]["duration_minutes"] == [e["evidence_id"]]
    assert result["walking_meters"] == 145 and result["transfers"] == 1
    result["origin"] = {"name": "甲站", "evidence_refs": {"name": [e["evidence_id"]]}}
    result["destination"] = {"name": "丙站", "evidence_refs": {"name": [e["evidence_id"]]}}
    checked = validate_result(result, TASK, QUERY, {"origin": "甲站", "destination": "丙站"}, evidence=[e], collection_complete=True)
    assert checked["route_verified"] and checked["classification"] == "COMPLETE" and checked["missing_fields"] == []


def test_repeated_station_transfer_frames_do_not_duplicate_the_walk_or_transfer_count(tmp_path):
    a, _ = in_station_fixture(tmp_path, index=1)
    b, _ = in_station_fixture(tmp_path, index=2)
    result = extract([a, b], {"origin": "甲站", "destination": "丙站"})
    assert result and len(result["segments"]) == 3 and result["transfers"] == 1
    assert result["segments"][1]["evidence_refs"]["instructions"] == [b["evidence_id"]]


@pytest.mark.parametrize("case", ["different_stations", "unbound_row", "unaccounted_walking", "endpoint_walk"])
def test_unknown_or_nonstation_transfer_walk_is_never_silently_omitted(tmp_path, case):
    e, _ = in_station_fixture(tmp_path, next_board="丁站" if case == "different_stations" else "乙站",
                              detached=case == "unbound_row", summary_distance=300 if case == "unaccounted_walking" else 145,
                              unknown_endpoint_walk=case == "endpoint_walk")
    assert extract([e], {"origin": "甲站", "destination": "丙站"}) is None


def test_summary_only_walk_distance_cannot_be_turned_into_a_station_walk(tmp_path):
    e = observation(tmp_path, [column("甲站", "乙站", ("3", "号线")), column("乙站", "丙站", ("1", "号线"))],
                    summary="25分钟3号线转1号线7站¥3步行145米")
    assert extract([e], {"origin": "甲站", "destination": "丙站"}) is None
