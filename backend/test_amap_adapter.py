"""Offline adapter boundary checks. No phone, model API, or route service is used."""
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess
import xml.etree.ElementTree as ET

import pytest

from device.amap import AmapDevice, PACKAGE, ID, TransitTreeChanging, bounds, clean, label, place_key, screen_text
from device.huawei import ScreenContextChanged
from domain import Cancelled, WorkflowError


def node(text="", *, desc="", rect="[0,100][1200,300]", cls="android.view.ViewGroup", **attrs):
    return ET.Element("node", {"package": PACKAGE, "class": cls, "text": text, "content-desc": desc,
                              "bounds": rect, "clickable": "true", **attrs})


def xml(ns):
    root = ET.Element("hierarchy")
    root.extend(ns)
    return ET.tostring(root, encoding="unicode")


@pytest.fixture
def adapter(tmp_path):
    base = SimpleNamespace(context_epoch=0, evidence_root=tmp_path, transport=None,
                           observe=Mock(), tap=Mock(), shell=Mock(return_value=""))
    current = AmapDevice(base, Mock())
    current.folder = tmp_path / "query"
    current.folder.mkdir()
    current.query_id = "query-one"
    current.request = {"origin": "广州塔地铁站", "destination": "体育西路地铁站", "city": "广州", "preference": "fewer_transfers"}
    return current


def test_visible_native_text_keeps_order_and_cleans_html_without_hidden_facts():
    ns = [node(desc='<font size="28"><b>广州塔(地铁站)</b></font>'), node("3"), node("号线"),
          node("不应看到的出口", rect="[0,0][0,0]"), node("3"), node("A&amp;B")]
    text = screen_text(ns)
    assert text.splitlines() == ["广州塔(地铁站)", "3", "号线", "A&B"]
    assert "不应看到" not in text
    assert label(ns[0]) == "广州塔(地铁站)"
    assert clean("<b>体育西路</b>") == "体育西路"


@pytest.mark.parametrize("bad", ["", "[0,0][0,0]", "[-1,2][30,40]", "[30,2][10,40]", "[0,4][20,2]"])
def test_invisible_or_reversed_bounds_never_produce_a_target(bad):
    assert bounds(node(rect=bad)) is None


def test_place_key_canonicalizes_station_suffix_but_keeps_branch_distinctions():
    assert place_key("广州市体育西路（地铁站）", "广州市") == place_key("体育西路(地铁站)")
    assert place_key("图书馆东馆") != place_key("图书馆西馆")


def place_rows(addresses):
    ns = []
    for i, address in enumerate(addresses):
        n = node(desc="市图书馆", rect=f"[0,{400+i*400}][1200,{700+i*400}]")
        if address:
            n.append(node(address, cls="android.widget.TextView", rect=f"[20,{500+i*400}][900,{560+i*400}]"))
        ns.append(n)
    return {"nodes": ns, "epoch": 0, "evidence": {"evidence_id": "observed-list"}}


def test_same_name_candidates_bind_identity_to_the_observed_address(adapter):
    values = adapter._place_candidates(place_rows(["广州市东区东路1号", "广州市西区西路2号"]), "市图书馆")
    candidates = [v[0] for v in values]
    assert len(candidates) == 2
    assert candidates[0]["candidate_id"] != candidates[1]["candidate_id"]
    assert {c["address"] for c in candidates} == {"广州市东区东路1号", "广州市西区西路2号"}
    assert all(c["city"] == "广州" for c in candidates)


def test_undistinguishable_same_name_places_ask_for_specific_address(adapter):
    shot = place_rows([None, None])
    values = adapter._place_candidates(shot, "市图书馆")
    adapter._wait = Mock(return_value=(shot, values))
    result = adapter._resolve_place("destination", "市图书馆", {}, lambda: None)
    assert result["kind"] == "clarification" and result["fields"] == ["destination"]
    adapter.base.tap.assert_not_called()


def test_stale_place_selection_cannot_reuse_a_row_index(adapter):
    shot = place_rows(["广州市东区东路1号", "广州市西区西路2号"])
    values = adapter._place_candidates(shot, "市图书馆")
    adapter._wait = Mock(return_value=(shot, values))
    old = {"destination": {"candidate_id": "old-query-row-0", "name": "市图书馆"}}
    with pytest.raises(WorkflowError, match="已选地点"):
        adapter._resolve_place("destination", "市图书馆", old, lambda: None)
    adapter.base.tap.assert_not_called()


def museum_search_shot(*, query="广州市广东省博物馆新馆", name="广东省博物馆",
                       address="珠江东路2号(大剧院地铁站B口步行180米)", loading=False, eid="museum-list"):
    """Minimal public POI structure from the failed T14 real search readback."""
    card = node(desc=name, rect="[0,1430][1200,2349]")
    card.extend([node(desc=name, cls="android.view.View", rect="[403,1463][721,1538]", clickable="false"),
                 node(address, cls="android.view.View", rect="[403,1544][993,1597]", clickable="false")])
    parking = node(desc="广东省博物馆地面停车场", rect="[0,2457][1200,2640]")
    parking.extend([node(desc="广东省博物馆地面停车场", cls="android.view.View", rect="[403,2490][986,2565]", clickable="false"),
                    node("珠江东路2号广东省博物馆", cls="android.view.View", rect="[403,2571][867,2624]", clickable="false")])
    # The real tree contains off-screen gates with clamped/reversed bounds.
    hidden_gate = node(desc="广东省博物馆(西门)", rect="[0,2803][1200,2640]")
    visible_gate = node(desc="广东省博物馆(东门)", rect="[0,2350][1200,2450]")
    visible_gate.extend([node(desc="广东省博物馆(东门)", cls="android.view.View", rect="[403,2350][860,2390]"),
                         node("珠江东路2号", cls="android.view.View", rect="[403,2400][860,2440]")])
    results = node(cls="androidx.recyclerview.widget.RecyclerView", rect="[0,1386][1200,2640]", clickable="false")
    results.extend([card, parking, hidden_gate, visible_gate])
    ns = [node(desc="搜索框，" + query, rect="[194,163][907,322]"), results]
    if loading:
        ns.append(node("正在搜索", cls="android.widget.TextView", rect="[100,500][500,600]"))
    root = ET.fromstring(xml(ns))
    return {"nodes": list(root.iter("node")), "epoch": 0,
            "evidence": {"evidence_id": eid, "observation_id": eid, "page_kind": "place_list"}}


def test_real_new_museum_alias_mismatch_offers_only_the_observed_primary_poi(adapter, monkeypatch):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    adapter.request["city"] = "广州市"
    first, fresh = museum_search_shot(eid="first"), museum_search_shot(eid="fresh")
    adapter._observe = Mock(side_effect=[first, fresh])
    adapter._tap = Mock()
    result = adapter._resolve_place("destination", "广东省博物馆新馆", {}, lambda: None)
    assert result["kind"] == "choice" and result["role"] == "destination"
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["name"] == "广东省博物馆"
    assert candidate["address"] == "珠江东路2号(大剧院地铁站B口步行180米)"
    assert candidate["city"] is None and candidate["evidence_id"] == "fresh"
    adapter._tap.assert_not_called()


def test_search_loading_or_previous_query_cannot_count_toward_stable_mismatch_choices(adapter, monkeypatch):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    adapter.request["city"] = "广州市"
    frames = [museum_search_shot(query="广州市旧博物馆"), museum_search_shot(),
              museum_search_shot(loading=True), museum_search_shot(), museum_search_shot(eid="settled")]
    adapter._observe = Mock(side_effect=frames)
    result = adapter._resolve_place("destination", "广东省博物馆新馆", {}, lambda: None)
    assert adapter._observe.call_count == 5
    assert result["candidates"][0]["evidence_id"] == "settled"


def test_explicit_canonical_choice_is_researched_and_verified_in_the_current_list_and_detail(adapter, monkeypatch):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    shot = museum_search_shot(query="广州广东省博物馆")
    selected = adapter._place_candidates(shot, "广东省博物馆")[0][0]
    detail = {"nodes": [node(desc="广东省博物馆", rect="[53,339][1147,423]"),
                         node("附近搜索", rect="[20,600][300,700]")],
              "epoch": 0, "evidence": {"evidence_id": "fresh-detail"}}
    adapter._observe = Mock(side_effect=[shot, shot, detail])
    adapter._tap = Mock()
    adapter._read_place = Mock(return_value={"name": "广东省博物馆", "address": selected["address"]})
    result = adapter._resolve_place("destination", "广东省博物馆", {"destination": selected}, lambda: None)
    assert result["kind"] == "place" and result["place"]["name"] == "广东省博物馆"
    from urllib.parse import parse_qs, urlsplit
    assert parse_qs(urlsplit(adapter.base.shell.call_args.args[6]).query)["keywords"] == ["广州广东省博物馆"]
    adapter._tap.assert_called_once()
    adapter._read_place.assert_called_once_with(detail, "广东省博物馆", selected=selected, candidate=selected)


def test_explicit_choice_with_changed_observed_address_is_rejected_before_tapping(adapter, monkeypatch):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    old = adapter._place_candidates(museum_search_shot(), "广东省博物馆新馆")[0][0]
    changed = museum_search_shot(query="广州广东省博物馆", address="文明路新地址99号")
    adapter._observe = Mock(side_effect=[changed, changed]);adapter._tap = Mock()
    with pytest.raises(WorkflowError, match="已选地点"):
        adapter._resolve_place("destination", "广东省博物馆", {"destination": old}, lambda: None)
    adapter._tap.assert_not_called()


def test_settled_related_gates_do_not_become_a_main_destination(adapter, monkeypatch):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    adapter.request["city"] = "广州市"
    shot = museum_search_shot(name="广东省博物馆(西门)")
    adapter._observe = Mock(side_effect=[shot, shot]);adapter._tap = Mock()
    result = adapter._resolve_place("destination", "广东省博物馆新馆", {}, lambda: None)
    assert result["kind"] == "clarification" and result["fields"] == ["destination"]
    adapter._tap.assert_not_called()


def test_a_bare_unrelated_clickable_label_is_not_an_alias_candidate(adapter):
    shot = place_rows(["广州市文明路1号"])
    assert adapter._place_candidates(shot, "完全不同的学校") == []


def test_detail_address_can_be_an_observed_road_without_inventing_the_requested_city(adapter):
    address = "珠江东路2号(大剧院地铁站B口步行180米)"
    shot = {"nodes": [node("广东省博物馆", rect="[53,339][1147,423]"),
                       node("3号线", rect="[53,430][300,470]"),
                       node(address, cls="android.view.View", rect="[53,500][1147,600]")],
            "evidence": {"evidence_id": "detail-road"}}
    value = adapter._read_place(shot, "广东省博物馆")
    assert value["address"] == address and value["city"] is None
    assert value["evidence_refs"]["address"] == ["detail-road"]


@pytest.mark.parametrize("name,glyph,address,expected_city", [
    ("体育西路(地铁站)", "路", "广州市天河区", "广州"),
    ("广东省博物馆", "省", "天河区珠江东路2号(大剧院地铁站B口步行180米)", None),
])
def test_real_split_title_glyphs_and_header_metadata_do_not_become_addresses(adapter, name, glyph, address, expected_city):
    shot = {"nodes": [node(name, rect="[53,339][1147,423]"),
                       node(glyph, rect="[159,339][212,423]"),
                       node(name.replace("(地铁站)", "地铁站"), rect="[53,430][1000,480]"),
                       node("广州市博物馆榜", rect="[62,515][315,565]"),
                       node("广州市评价推荐", rect="[62,570][315,600]"),
                       node("在广东省博物馆附近搜索", rect="[53,610][1000,660]"),
                       node(address, rect="[53,751][833,797]")],
            "evidence": {"evidence_id": "actual-address"}}
    place = adapter._read_place(shot, name)
    assert place["address"] == address and place["city"] == expected_city


def test_detail_can_prove_the_exact_selected_address_with_only_an_administrative_prefix_added(adapter):
    selected = "珠江东路2号(大剧院地铁站B口步行180米)"
    shot = {"nodes": [node("省", rect="[159,339][212,423]"),
                       node("广州市天河区" + selected, rect="[53,751][833,797]")],
            "evidence": {"evidence_id": "current-full-address"}}
    place = adapter._read_place(shot, "广东省博物馆", selected={"address": selected})
    assert place["address"] == selected and place["city"] == "广州"
    assert place["evidence_refs"]["address"] == place["evidence_refs"]["city"] == ["current-full-address"]


@pytest.mark.parametrize("actual", [
    "天河区珠江东路20号(大剧院地铁站B口步行180米)",
    "天河区珠江东路2号(大剧院地铁站B口步行80米)",
    "天河区珠江东路2号(大剧院地铁站C口步行180米)",
    "文明路珠江东路2号(大剧院地铁站B口步行180米)",
    "天河区珠江东路2号(大剧院地铁站B口步行180米)东门",
])
def test_detail_does_not_equate_a_changed_address_or_nonadministrative_prefix(adapter, actual):
    selected = "珠江东路2号(大剧院地铁站B口步行180米)"
    shot = {"nodes": [node(actual, rect="[53,751][833,797]")], "evidence": {"evidence_id": "changed-address"}}
    place = adapter._read_place(shot, "广东省博物馆", selected={"address": selected})
    assert place["address"] == actual and place["address"] != selected


def test_a_city_substring_in_a_road_name_is_not_an_administrative_city_proof(adapter):
    shot = {"nodes": [node("广州塔路12号", rect="[53,751][833,797]")], "evidence": {"evidence_id": "road-only"}}
    place = adapter._read_place(shot, "公共建筑")
    assert place["address"] == "广州塔路12号" and place["city"] is None


@pytest.mark.parametrize("reason, expected", [
    ("root_package", "前台"), ("service_unavailable", "无障碍"),
    ("bridge_busy", "繁忙"), ("bridge_timeout", "超时"), ("bridge_unavailable", "更新"),
    ("tree_changed", "页面正在变化"),
])
def test_map_observation_reports_known_bridge_failure_without_claiming_usb_is_disconnected(adapter, reason, expected):
    import json
    adapter.base.transport = SimpleNamespace(last_bridge_reason=reason)
    adapter.base.observe.side_effect = WorkflowError("请检查 USB 连接")
    with pytest.raises(WorkflowError, match=expected): adapter._observe(lambda: None)
    assert json.loads((adapter.folder / "observation-error.json").read_text())["reason"] == reason


def test_unknown_bridge_failure_keeps_the_original_exception(adapter):
    adapter.base.transport = SimpleNamespace(last_bridge_reason="future_reason")
    error = WorkflowError("original failure")
    adapter.base.observe.side_effect = error
    with pytest.raises(WorkflowError) as caught: adapter._observe(lambda: None)
    assert caught.value is error


def test_cancelled_or_expired_budget_never_taps(adapter):
    control = node("公共交通")
    shot = {"nodes": [control], "epoch": 0}
    def cancelled(): raise Cancelled("cancelled")
    with pytest.raises(Cancelled): adapter._tap(shot, control, cancelled)
    adapter.base.observe.assert_not_called();adapter.base.tap.assert_not_called()
    adapter.actions = adapter.action_limit
    with pytest.raises(WorkflowError, match="上限"): adapter._tap(shot, control, lambda: None)
    adapter.base.observe.assert_not_called();adapter.base.tap.assert_not_called()
    adapter.actions = 0;adapter.started -= adapter.total_seconds + 1
    with pytest.raises(WorkflowError, match="时限"): adapter._tap(shot, control, lambda: None)
    adapter.base.tap.assert_not_called()


@pytest.mark.parametrize("operation", ["tap", "input"])
def test_pause_resume_during_fresh_observation_invalidates_old_control(adapter, operation):
    target = node("起点", cls="android.widget.EditText")
    shot = {"nodes": [target], "epoch": 0}
    def fresh(_checkpoint):
        adapter.base.context_epoch += 1
        return xml([target])
    adapter.base.observe.side_effect = fresh
    with pytest.raises(WorkflowError):
        if operation == "tap": adapter._tap(shot, target, lambda: None)
        else: adapter._enter(shot, target, "广州塔", "origin", lambda: None)
    adapter.base.tap.assert_not_called();adapter.base.shell.assert_not_called()


def test_changed_or_duplicate_controls_cannot_be_tapped(adapter):
    target = node("公共交通")
    shot = {"nodes": [target], "epoch": 0}
    adapter.base.observe.return_value = xml([node("开始导航")])
    with pytest.raises(WorkflowError, match="已变化"): adapter._tap(shot, target, lambda: None)
    adapter.base.observe.return_value = xml([target, node("公共交通")])
    with pytest.raises(WorkflowError, match="已变化"): adapter._tap(shot, target, lambda: None)
    adapter.base.tap.assert_not_called()


def test_cancel_after_fresh_read_discards_the_pending_tap(adapter):
    target = node("公共交通")
    adapter.base.observe.return_value = xml([target])
    calls = 0
    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 3: raise Cancelled("cancel while reading")
    with pytest.raises(Cancelled): adapter._tap({"nodes": [target], "epoch": 0}, target, checkpoint)
    adapter.base.tap.assert_not_called()


def ocr_shots(adapter):
    return [{"nodes": [], "epoch": 0, "evidence": {"screenshot_path": str(adapter.folder / f"{i}.png")}} for i in range(3)]


def ocr_result(*, text="路线", confidence=.99, rect=None):
    return {"width": 1200, "height": 2640, "items": [{"text": text, "confidence": confidence, "bounds": rect or [900, 2300, 1100, 2400]}]}


def test_ocr_fallback_revalidates_same_label_and_stops_after_epoch_change(adapter):
    adapter._observe = Mock(side_effect=ocr_shots(adapter))
    count = 0
    def read(_path):
        nonlocal count
        count += 1
        if count == 2: adapter.base.context_epoch = 1
        return ocr_result()
    adapter._ocr = Mock(side_effect=read)
    with pytest.raises(WorkflowError, match="页面已变化"): adapter._tap_label("路线", lambda: None, "place_detail")
    adapter.base.shell.assert_not_called()


@pytest.mark.parametrize("failure", ["moved", "duplicate", "outside_image", "low_confidence"])
def test_ocr_fallback_rejects_ambiguous_or_changed_or_invalid_targets(adapter, failure):
    adapter._observe = Mock(side_effect=ocr_shots(adapter))
    first = ocr_result();second = ocr_result()
    if failure == "moved": second = ocr_result(rect=[300, 200, 400, 300])
    if failure == "duplicate": first["items"].append(dict(first["items"][0]))
    if failure == "outside_image": first["items"][0]["bounds"] = [1100, 2400, 1300, 2700]
    if failure == "low_confidence": first["items"][0]["confidence"] = .2
    adapter._ocr = Mock(side_effect=[first, second])
    with pytest.raises(WorkflowError): adapter._tap_label("路线", lambda: None, "place_detail")
    adapter.base.shell.assert_not_called()


def test_ocr_fallback_is_only_for_route_entry_not_navigation_or_unknown_buttons(adapter):
    adapter._observe = Mock(return_value={"nodes": [], "epoch": 0})
    adapter._ocr = Mock()
    for text, page in [("开始导航", "place_detail"), ("路线", "screen"), ("登录", "place_detail")]:
        with pytest.raises(WorkflowError): adapter._tap_label(text, lambda: None, page)
    adapter._ocr.assert_not_called();adapter.base.shell.assert_not_called()


def test_route_cards_exclude_cycling_and_taxi_and_sort_observed_transit_options(adapter):
    first = node(desc="25分钟地铁1号线", rect="[0,300][1200,600]")
    second = node(desc="30分钟公交10路", rect="[0,650][1200,950]")
    shot = {"nodes": [second, node(desc="8分钟打车", rect="[0,150][1200,290]"),
                      node(desc="15分钟骑行", rect="[0,100][1200,250]"), first,
                      node(desc="60分钟地铁2号线", rect="[0,0][0,0]")]}
    assert adapter._route_cards(shot) == [first, second]


def huawei_route_list(line="3号线", *, hidden_line=False):
    origin = node(desc="起点，广州塔(地铁站)", rect="[165,174][891,277]")
    origin.append(node("广州塔(地铁站)", rect="[224,174][891,277]", cls="android.view.View"))
    destination = node(desc="终点，客村(地铁站)", rect="[165,276][891,379]")
    destination.append(node("客村(地铁站)", rect="[224,276][891,379]", cls="android.view.View"))
    transit = node(desc="公共交通，6分钟，已选中，按钮", rect="[375,406][686,499]")
    transit.append(node("公共交通", rect="[471,419][657,485]", cls="android.view.View"))
    card = node(desc="方案1，6分钟，无堵车风险，1站，2元，广州塔进站", rect="[72,743][1033,917]")
    line_node = node(desc=line, rect="[72,933][1126,1036]", **{"visible-to-user": "false" if hidden_line else "true"})
    line_node.append(node(line, rect="[95,950][224,1016]", cls="android.view.View"))
    owner = node(rect="[72,743][1126,1168]")
    owner.extend([card, line_node])
    return [origin, destination, transit, owner], card


def test_huawei_route_headers_current_mode_and_numbered_choice(adapter):
    nodes, card = huawei_route_list()
    adapter.base.observe.return_value = xml(nodes)
    shot = adapter._observe(lambda: None, "route_list")
    origin = {"name": "广州塔地铁站"}
    destination = {"name": "客村地铁站"}
    assert adapter._checked_route_values(shot, {"origin": origin, "destination": destination}) == (
        "广州塔(地铁站)", "客村(地铁站)"
    )
    assert [label(found) for found in adapter._route_cards(shot)] == [label(card)]
    assert adapter._route_card_summary(shot, adapter._route_cards(shot)[0]).splitlines() == [label(card), "3号线"]
    adapter._select_public_transit({"origin": origin, "destination": destination}, lambda: None)
    adapter._set_origin(origin, lambda: None)
    adapter.base.tap.assert_not_called()


def test_huawei_numbered_choice_rechecks_line_and_rejects_hidden_or_unassociated_lines(adapter):
    rejected_nodes, _ = huawei_route_list(hidden_line=True)
    adapter.base.observe.return_value = xml(rejected_nodes)
    assert adapter._route_cards(adapter._observe(lambda: None, "route_list")) == []
    nodes, _ = huawei_route_list()
    owner = nodes[-1]
    line = owner[-1]
    owner.remove(line)
    separate = node(rect="[0,0][1200,2486]")
    separate.extend([owner, line])
    adapter.base.observe.return_value = xml([*nodes[:-1], separate])
    assert adapter._route_cards(adapter._observe(lambda: None, "route_list")) == []
    nodes, _ = huawei_route_list()
    nodes[-1].append(node(desc="方案2，15分钟，2站，2元，广州塔进站", rect="[72,1017][1033,1191]"))
    adapter.base.observe.return_value = xml(nodes)
    assert adapter._route_cards(adapter._observe(lambda: None, "route_list")) == []
    nodes, _ = huawei_route_list()
    adapter.base.observe.return_value = xml(nodes)
    shot = adapter._observe(lambda: None, "route_list")
    card = adapter._route_cards(shot)[0]
    changed, _ = huawei_route_list("8号线")
    adapter.base.observe.return_value = xml(changed)
    with pytest.raises(WorkflowError, match="线路已变化"):
        adapter._tap(shot, card, lambda: None, role="route", page="route_list")
    adapter.base.tap.assert_not_called()


def test_map_readiness_checks_amap_and_device_without_mail_dependency(adapter, monkeypatch):
    import gmail_demo
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-test")
    monkeypatch.setattr(gmail_demo, "_accessibility_enabled", lambda adb: True)
    monkeypatch.setattr("device.amap.subprocess.run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "device\n", ""))
    adapter.base.shell.side_effect = lambda *args: "showing=false" if args[:2] == ("dumpsys", "window") else "package:/data/app/amap/base.apk"
    adapter.check_ready(lambda: None)
    assert any(c.args == ("pm", "path", PACKAGE) for c in adapter.base.shell.call_args_list)
    assert all("com.huawei.email" not in c.args for c in adapter.base.shell.call_args_list)


def map_activity_snapshot(task_id=71, activity="com.autonavi.minimap/.ObservedMapActivity", *, resumed=True):
    record = f"ActivityRecord{{offline u0 {activity} t{task_id}}}"
    return (f"* Task{{offline #{task_id} type=standard A=10123:com.autonavi.minimap U=0}}\n"
            f"  * Hist #0: {record}\n" + (f"mResumedActivity: {record}\n" if resumed else ""))


def seed_view_context(adapter, result):
    result.setdefault("task_id", "offline-task");result.setdefault("query_id", "offline-query");result.setdefault("route_id", "offline-route")
    key = adapter._context_key(result)
    context = {"task_id": key[0], "query_id": key[1], "route_id": key[2], "android_task_id": 71,
               "activity": "com.autonavi.minimap/.ObservedMapActivity"}
    adapter._detail_contexts[key] = context
    adapter.base.shell.return_value = map_activity_snapshot()
    return context


@pytest.mark.parametrize("unrelated", ["home", "wrong_line", "wrong_destination"])
def test_show_details_does_not_accept_an_unrelated_amap_page(adapter, unrelated):
    route = {"segments": [{"mode": "subway", "line": "3号线", "board": "体育西路", "direction": "海傍", "alight": "广州塔"}]}
    ns = [node(desc="公交详情页面"), node("3"), node("号线"), node("体育西路"), node("海傍方向"), node("广州塔")]
    if unrelated == "home": ns = [node("搜索目的地")]
    if unrelated == "wrong_line": ns = [node(desc="公交详情页面"), node("5号线"), node("体育西路"), node("海傍方向"), node("广州塔")]
    if unrelated == "wrong_destination": ns = [node(desc="公交详情页面"), node("3号线"), node("体育西路"), node("海傍方向"), node("客村")]
    adapter.base.observe.return_value = xml(ns)
    seed_view_context(adapter, route)
    with pytest.raises(WorkflowError): adapter.show_details(route, lambda: None)
    assert not any("tap" in c.args or "swipe" in c.args for c in adapter.base.shell.call_args_list)


def test_show_details_reopens_only_observed_matching_detail_not_a_requery(adapter):
    adapter.base.observe.return_value = xml([node(desc="公交详情页面"), node("3"), node("号线"), node("体育西路"), node("海傍方向"), node("广州塔")])
    route = {"segments": [{"mode": "subway", "line": "3号线", "board": "体育西路", "direction": "海傍", "alight": "广州塔"}]}
    seed_view_context(adapter, route)
    adapter.show_details(route, lambda: None)
    assert adapter.base.shell.call_count >= 3
    assert any(c.args == ("am", "task", "focus", "71") for c in adapter.base.shell.call_args_list)
    assert not any(c.args[:2] == ("am", "start") for c in adapter.base.shell.call_args_list)


def test_extractor_receives_only_the_clicked_candidate_summary_not_other_options(adapter, tmp_path):
    # A tiny byte fixture is merely passed through to a mocked model; it is not
    # a generated screenshot or evidence of a successful real-device route.
    image = tmp_path / "offline-image.bin";image.write_bytes(b"offline-model-fixture")
    start, end = "广州塔地铁站", "体育西路地铁站"
    first = node(desc="8分钟3号线体育西路进站", rect="[0,300][1200,600]")
    other = node(desc="35分钟5号线公交换乘方案", rect="[0,700][1200,1000]")
    start_node = node(start, **{"resource-id": ID + "route_edit_summary_start"})
    end_node = node(end, **{"resource-id": ID + "route_edit_summary_end"})
    list_evidence = {"evidence_id": "all-candidates", "query_id": "query-current", "route_id": None,
                     "page_kind": "route_list", "text": "8分钟3号线体育西路进站\n35分钟5号线公交换乘方案"}
    list_shot = {"nodes": [first, other, start_node, end_node], "epoch": 0, "evidence": list_evidence}
    detail_nodes = [node(desc="公交详情页面"), node(desc="体育西路", rect="[0,900][1000,1100]")]
    adapter.check_ready = Mock();adapter._tap_label = Mock();adapter._set_origin = Mock();adapter._tap = Mock();adapter._scroll_detail = Mock()
    def resolve(role, value, _choices, _checkpoint):
        return {"kind": "place", "place": {"name": value, "city": "广州", "address": None, "source": "explicit", "evidence_refs": {}}}
    adapter._resolve_place = Mock(side_effect=resolve)
    adapter._wait = Mock(side_effect=[(list_shot, "input"), (list_shot, first), (list_shot, first), (list_shot, [first, other]), (list_shot, first)])
    counter = 0
    def observe(_checkpoint, page="screen", **kwargs):
        nonlocal counter
        counter += 1
        if page == "route_input":
            adapter.observations.append(list_evidence)
            return {"nodes": [], "epoch": 0, "evidence": {"text": ""}}
        evidence = {"evidence_id": f"detail-{counter}", "query_id": "query-current", "route_id": adapter.route_id,
                    "page_kind": "route_detail", "text": "体育西路\n3号线\n公交详情页面", "screenshot_path": str(image)}
        adapter.observations.append(evidence)
        return {"nodes": detail_nodes, "epoch": 0, "evidence": evidence}
    adapter._observe = Mock(side_effect=observe)
    adapter.models.extract_transit.return_value = {}
    result = adapter.query({"origin": start, "destination": end, "city": "广州", "preference": "fewer_transfers"}, "task-current", "query-current", lambda: None)
    supplied = adapter.models.extract_transit.call_args.args[0]
    summaries = [e for e in supplied if e["page_kind"] == "route_list"]
    assert len(summaries) == 1 and "3号线" in summaries[0]["text"]
    assert "5号线" not in summaries[0]["text"]
    assert summaries[0]["route_id"] == result["result"]["route_id"]
    assert result["collection_complete"]
    assert all(e["evidence_id"] != "all-candidates" for e in supplied)


def test_city_normalization_does_not_strip_the_city_token_inside_a_poi_name():
    assert place_key("广州塔(地铁站)", "广州") == place_key("广州塔(地铁站)")
    assert place_key("广州塔(地铁站)", "广州市") == place_key("广州塔(地铁站)")
    assert place_key("广州市广州塔(地铁站)", "广州") == place_key("广州塔(地铁站)")


@pytest.mark.parametrize("mismatch", ["board", "direction"])
def test_show_details_rejects_another_trip_on_the_same_line_to_the_same_destination(adapter, mismatch):
    result = {"segments": [{"mode": "subway", "line": "3号线", "board": "广州塔", "alight": "体育西路", "direction": "天河客运站"}]}
    ns = [node(desc="公交详情页面"), node("3号线"), node("体育西路"),
          node("客村" if mismatch == "board" else "广州塔"),
          node("海傍方向" if mismatch == "direction" else "天河客运站方向")]
    adapter.base.observe.return_value = xml(ns)
    seed_view_context(adapter, result)
    with pytest.raises(WorkflowError): adapter.show_details(result, lambda: None)


def test_sdk_uiautomator_xml_error_uses_a_new_file_each_time_and_cleans_it(adapter, monkeypatch):
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport()
    transport._device = Mock()
    transport._device.ui.side_effect = ValueError("uiautomator output did not contain XML")
    transport._device.screenshot.return_value = "sdk-image"
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-fixture")
    monkeypatch.setenv("ANDROID_SERIAL", "offline-test-device")
    calls = []
    expected = xml([node("公交详情页面")]).encode()
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, expected if "cat" in command else b"", b"")
    monkeypatch.setattr(subprocess, "run", run)
    assert transport.xml().encode() == expected
    assert transport.xml().encode() == expected
    dumps = [c for c in calls if "uiautomator" in c]
    reads = [c for c in calls if "cat" in c]
    removals = [c for c in calls if "rm" in c]
    assert len(dumps) == len(reads) == len(removals) == 2
    assert dumps[0][-1] != dumps[1][-1]
    assert [c[-1] for c in dumps] == [c[-1] for c in reads] == [c[-1] for c in removals]
    assert all(c[:3] == ["adb-fixture", "-s", "offline-test-device"] for c in calls)
    assert transport.last_ui_source == "adb-file-fallback"
    transport.tap(12, 34);transport._device.tap.assert_called_once_with(12, 34)
    assert transport.screenshot() == "sdk-image"


@pytest.mark.parametrize("failure", ["malformed_xml", "dump_error", "read_error"])
def test_file_fallback_never_returns_stale_or_invalid_xml_and_attempts_cleanup(monkeypatch, failure):
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport._device = Mock()
    transport._device.ui.side_effect = ValueError("uiautomator output did not contain XML")
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-fixture")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if failure == "dump_error" and "uiautomator" in command:
            raise subprocess.CalledProcessError(1, command)
        if failure == "read_error" and "cat" in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, b"not XML", b"")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises((ET.ParseError, subprocess.CalledProcessError)): transport.xml()
    assert "rm" in calls[-1]
    assert transport.last_ui_source == "mobilerun"
    if failure == "dump_error": assert not any("cat" in c for c in calls)


@pytest.mark.parametrize("problem", [ValueError("unrelated parser problem"), RuntimeError("device disconnected")])
def test_unrelated_sdk_errors_do_not_silently_switch_transport(monkeypatch, problem):
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport._device = Mock()
    transport._device.ui.side_effect = problem
    run = Mock();monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(type(problem), match=str(problem)): transport.xml()
    run.assert_not_called()


def test_normal_sdk_tree_still_uses_sdk_and_never_runs_adb_file_fallback(monkeypatch):
    from device.mobilerun import MobilerunTransport
    class State(dict): dead_tree = False
    transport = MobilerunTransport();transport._device = Mock()
    transport._device.ui.return_value = State(a11y_tree={"text": "起点", "packageName": PACKAGE,
        "className": "android.widget.EditText", "boundsInScreen": {"left": 10, "top": 20, "right": 100, "bottom": 80},
        "enabled": True, "children": []})
    run = Mock();monkeypatch.setattr(subprocess, "run", run)
    parsed = ET.fromstring(transport.xml());n = next(parsed.iter("node"))
    assert n.get("text") == "起点" and n.get("bounds") == "[10,20][100,80]"
    assert transport.last_ui_source == "mobilerun"
    transport._device.ui.assert_called_once_with(filter=False);run.assert_not_called()


def test_sdk_multiwindow_list_tree_keeps_map_and_keyboard_roots_with_their_children(monkeypatch):
    from device.mobilerun import MobilerunTransport
    class State(dict): dead_tree = False
    transport = MobilerunTransport();transport._device = Mock()
    transport._device.ui.return_value = State(a11y_tree=[
        {"packageName": PACKAGE, "className": "android.view.ViewGroup", "text": "高德路线",
         "bounds": [0, 0, 1200, 1800], "children": [
             {"packageName": PACKAGE, "className": "android.widget.EditText", "text": "广州塔",
              "bounds": [20, 100, 1100, 220], "enabled": True}]},
        {"packageName": "com.example.testkeyboard", "className": "android.inputmethodservice.KeyboardView",
         "text": "测试键盘", "bounds": [0, 1800, 1200, 2640], "children": []},
    ])
    run = Mock();monkeypatch.setattr(subprocess, "run", run)
    parsed = ET.fromstring(transport.xml());roots = list(parsed)
    assert len(roots) == 2
    assert roots[0].get("package") == PACKAGE and roots[1].get("package") == "com.example.testkeyboard"
    assert next(roots[0].iter("node")).get("text") == "高德路线"
    assert roots[0][0].get("text") == "广州塔" and roots[0][0].get("bounds") == "[20,100][1100,220]"
    assert roots[1].get("bounds") == "[0,1800][1200,2640]"
    assert transport.last_ui_source == "mobilerun" and not transport.file_xml_mode
    run.assert_not_called()


def test_protected_map_snapshot_binds_the_fresh_nonce_and_preserves_sdk_actions(monkeypatch):
    import base64
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport._device = Mock()
    transport._device.screenshot.return_value = "sdk-png"
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    monkeypatch.setenv("ANDROID_SERIAL", "offline-bridge-device")
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        nonce = command[-1]
        body = ET.Element("hierarchy", nonce=nonce)
        body.append(node("路线详情"))
        encoded = base64.urlsafe_b64encode(ET.tostring(body, encoding="utf-8")).decode()
        return subprocess.CompletedProcess(command, 0, f'Result: Bundle[{{code=1, result=1, data={encoded}, reason=ok, nonce={command[-1]}}}]', "")
    monkeypatch.setattr(subprocess, "run", run)
    body = transport._protected_transit_xml()
    root = ET.fromstring(body)
    assert root.get("nonce") == commands[0][-1] and len(root.get("nonce")) == 32
    assert next(root.iter("node")).get("package") == PACKAGE
    assert commands[0][:3] == ["adb-offline", "-s", "offline-bridge-device"]
    assert "dump_transit_ui" in commands[0]
    assert transport.last_ui_source == "protected-a11y-fallback"
    transport.tap(20, 30);transport._device.tap.assert_called_once_with(20, 30)
    assert transport.screenshot() == "sdk-png"


@pytest.mark.parametrize("failure", ["stale_nonce", "foreign_package", "missing_data", "bad_xml", "bad_base64"])
def test_successful_but_invalid_protected_snapshot_fails_closed_without_file_fallback(monkeypatch, failure):
    import base64
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport.prefer_transit_bridge = True
    transport._fresh_file_xml = Mock(return_value="must-not-be-used")
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    def run(command, **kwargs):
        nonce = "expired-response" if failure == "stale_nonce" else command[-1]
        root = ET.Element("hierarchy", nonce=nonce)
        child = node("路线详情")
        if failure == "foreign_package": child.set("package", "com.example.otherapp")
        root.append(child)
        payload = ET.tostring(root, encoding="utf-8") if failure != "bad_xml" else b"not an XML document"
        encoded = "abc" if failure == "bad_base64" else base64.urlsafe_b64encode(payload).decode()
        data = "" if failure == "missing_data" else f", data={encoded}"
        return subprocess.CompletedProcess(command, 0, f"Result: Bundle[{{code=1, result=1, nonce={command[-1]}, reason=ok{data}}}]", "")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError): transport.xml()
    transport._fresh_file_xml.assert_not_called()
    assert transport.last_ui_source == "mobilerun"


def test_non_transit_compatibility_keeps_the_fresh_file_path(monkeypatch):
    # This is the unchanged non-transit (mail/browser) SDK compatibility path;
    # a map query no longer has any old-broadcast or UiAutomation fallback.
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport.file_xml_mode = True
    expected = xml([node("新鲜文件路径由单独测试覆盖")])
    transport._fresh_file_xml = Mock(return_value=expected)
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    run = Mock();monkeypatch.setattr(subprocess, "run", run)
    assert transport.xml() == expected
    transport._fresh_file_xml.assert_called_once_with();run.assert_not_called()


@pytest.mark.parametrize("reason", ["service_unavailable", "root_unavailable", "tree_changed"])
def test_current_bridge_service_loss_retries_boundedly_without_starting_uiautomation(monkeypatch, reason):
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport._device = Mock()
    transport.prefer_transit_bridge = True;transport.file_xml_mode = True
    transport._fresh_file_xml = Mock(return_value="must-not-start-uiautomator")
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    run = Mock(side_effect=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, f'Result: Bundle[{{code=0, result=0, data={reason}, reason={reason}, nonce={command[-1]}}}]', ""))
    sleep = Mock();monkeypatch.setattr(subprocess, "run", run);monkeypatch.setattr("time.sleep", sleep)
    with pytest.raises(RuntimeError, match="无障碍窗口暂不可用"): transport.xml()
    assert run.call_count == (4 if reason == "tree_changed" else 2)
    assert sleep.call_count == (3 if reason == "tree_changed" else 1)
    assert transport.last_bridge_reason == reason
    transport._device.ui.assert_not_called();transport._fresh_file_xml.assert_not_called()
    assert all("dump_transit_ui" in call.args[0] and "content" in call.args[0] and "broadcast" not in call.args[0] and "uiautomator" not in call.args[0] for call in run.call_args_list)


def test_current_bridge_recovers_after_transient_tree_change_without_legacy_reads(monkeypatch):
    import base64
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport._device = Mock();transport.prefer_transit_bridge = True
    transport._fresh_file_xml = Mock()
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) < 2:
            return subprocess.CompletedProcess(command, 0, f'Result: Bundle[{{code=0, result=0, data=tree_changed, reason=tree_changed, nonce={command[-1]}}}]', "")
        root = ET.Element("hierarchy", nonce=command[-1]);root.append(node("已恢复的高德窗口"))
        encoded = base64.urlsafe_b64encode(ET.tostring(root, encoding="utf-8")).decode()
        return subprocess.CompletedProcess(command, 0, f'Result: Bundle[{{code=1, result=1, data={encoded}, reason=ok, nonce={command[-1]}}}]', "")
    sleep = Mock();monkeypatch.setattr(subprocess, "run", run);monkeypatch.setattr("time.sleep", sleep)
    assert "已恢复的高德窗口" in transport.xml()
    assert len(calls) == 2 and len({c[-1] for c in calls}) == 2 and sleep.call_count == 1
    assert transport.last_bridge_reason is None and transport.last_ui_source == "protected-a11y-fallback"
    transport._device.ui.assert_not_called();transport._fresh_file_xml.assert_not_called()


def test_changing_map_tree_recovers_after_multiple_fresh_reads(monkeypatch):
    import base64
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport(); transport._device = Mock(); transport.prefer_transit_bridge = True
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) <= 2:
            return subprocess.CompletedProcess(command, 0, f'Result: Bundle[{{code=0, result=0, data=tree_changed, reason=tree_changed, nonce={command[-1]}}}]', "")
        root = ET.Element("hierarchy", nonce=command[-1]); root.append(node("稳定页面"))
        encoded = base64.urlsafe_b64encode(ET.tostring(root, encoding="utf-8")).decode()
        return subprocess.CompletedProcess(command, 0, f'Result: Bundle[{{code=1, result=1, data={encoded}, reason=ok, nonce={command[-1]}}}]', "")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("time.sleep", lambda _: None)
    assert "稳定页面" in transport.xml()
    assert len(calls) == 3 and len({call[-1] for call in calls}) == 3
    transport._device.ui.assert_not_called()


def test_changing_map_tree_stops_on_cancel_before_retry(monkeypatch):
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport(); transport.prefer_transit_bridge = True
    def changing_tree():
        transport.last_bridge_reason = "tree_changed"
        return None
    transport._protected_transit_xml = Mock(side_effect=changing_tree)
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = 0
    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise Cancelled("cancelled")
    with pytest.raises(Cancelled): transport.xml(checkpoint)
    transport._protected_transit_xml.assert_called_once()


def test_transit_bridge_exception_does_not_reuse_previous_tree_change_reason(monkeypatch):
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport(); transport.prefer_transit_bridge = True
    def changing_then_broken():
        if transport._protected_transit_xml.call_count == 1:
            transport.last_bridge_reason = "tree_changed"
            return None
        raise RuntimeError("malformed bridge response")
    transport._protected_transit_xml = Mock(side_effect=changing_then_broken)
    monkeypatch.setattr("time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="malformed bridge response"):
        transport.xml()
    assert transport._protected_transit_xml.call_count == 2
    assert transport.last_bridge_reason is None


def test_missing_transit_provider_fails_closed_instead_of_using_uiautomation(monkeypatch):
    # Old APKs must be updated for maps; they must never make a map read start
    # UiAutomation.
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport._device = Mock()
    transport.prefer_transit_bridge = True;transport.file_xml_mode = True
    transport._fresh_file_xml = Mock(return_value="must-not-be-used")
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    run = Mock(side_effect=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "No content provider found", ""))
    sleep = Mock();monkeypatch.setattr(subprocess, "run", run);monkeypatch.setattr("time.sleep", sleep)
    with pytest.raises(RuntimeError): transport.xml()
    assert run.call_count == 2 and sleep.call_count == 1
    transport._fresh_file_xml.assert_not_called();transport._device.ui.assert_not_called()


@pytest.mark.parametrize("previous,fail", [(False, False), (False, True), (True, False), (True, True)])
def test_query_bridge_preference_is_restored_after_success_or_cancel_and_does_not_pollute_mail(adapter, previous, fail):
    from device.mobilerun import MobilerunTransport
    class State(dict): dead_tree = False
    transport = MobilerunTransport();transport._device = Mock();transport.prefer_transit_bridge = previous
    adapter.base.transport = transport
    def query(*args, **kwargs):
        assert transport.prefer_transit_bridge
        if fail: raise Cancelled("cancelled in map query")
        return {"kind": "result", "query_id": "offline-query"}
    adapter._query = Mock(side_effect=query)
    if fail:
        with pytest.raises(Cancelled): adapter.query({}, "offline-task", "offline-query", lambda: None)
    else:
        assert adapter.query({}, "offline-task", "offline-query", lambda: None)["kind"] == "result"
    assert transport.prefer_transit_bridge is previous
    if not previous:
        transport._protected_transit_xml = Mock()
        transport._device.ui.return_value = State(a11y_tree={"packageName": "com.huawei.email", "text": "邮件写信页", "bounds": [0, 0, 1200, 2486]})
        assert "com.huawei.email" in transport.xml()
        transport._device.ui.assert_called_once_with(filter=False);transport._protected_transit_xml.assert_not_called()


@pytest.mark.parametrize("previous,fail", [(False, False), (False, True), (True, False), (True, True)])
def test_viewing_saved_route_also_uses_the_bridge_and_restores_preference(adapter, previous, fail):
    # Viewing a finished route must not accidentally return to UiAutomation
    # after query() restored the shared transport's preference for mail.
    transport = SimpleNamespace(prefer_transit_bridge=previous)
    adapter.base.transport = transport
    route = {"segments": [{"mode": "subway", "line": "3号线", "board": "体育西路", "direction": "海傍", "alight": "广州塔"}]}
    seed_view_context(adapter, route)
    def observe(checkpoint):
        assert transport.prefer_transit_bridge
        if fail: raise Cancelled("cancelled while viewing")
        return xml([node(desc="公交详情页面"), node("3号线"), node("体育西路"), node("海傍方向"), node("广州塔")])
    adapter.base.observe.side_effect = observe
    if fail:
        with pytest.raises(Cancelled): adapter.show_details(route, lambda: None)
    else:
        adapter.show_details(route, lambda: None)
    assert transport.prefer_transit_bridge is previous


@pytest.mark.parametrize("active,worker_alive,may_start", [(False, True, False), (True, False, False), (False, False, True)])
def test_legacy_ai_entry_waits_for_the_engine_worker_to_finish_cleanup(monkeypatch, active, worker_alive, may_start):
    from fastapi.testclient import TestClient
    from api.application import create_app
    import workflow
    import recognize
    engine = SimpleNamespace(active=Mock(return_value=active), worker=SimpleNamespace(is_alive=Mock(return_value=worker_alive)))
    monkeypatch.setattr(workflow, "get_engine", lambda: engine)
    start = Mock(return_value={"ok": True, "message": "offline start stub"})
    monkeypatch.setattr(recognize, "start", start)
    app = create_app()
    response = TestClient(app).post("/recognize/start", json={"prompt": "打开设置"})
    assert response.status_code == 200 and response.json()["ok"] is may_start
    assert bool(start.call_count) is may_start
    if worker_alive and not active:
        assert "操作正在结束" in response.json()["message"]


def route_frame(card_text=None, *, epoch=0, text=None, rect="[0,300][1200,600]"):
    nodes = [node(desc=card_text, rect=rect)] if card_text else []
    return {"nodes": nodes, "epoch": epoch, "evidence": {"text": text if text is not None else card_text or "正在加载"}}


def test_filter_refresh_discards_the_old_card_and_loading_frame_then_returns_the_second_stable_new_frame(adapter, monkeypatch):
    frames = [route_frame("25分钟3号线"), route_frame(), route_frame("30分钟夜10路"), route_frame("30分钟夜10路")]
    adapter._observe = Mock(side_effect=frames);sleep = Mock();monkeypatch.setattr("time.sleep", sleep)
    shot, cards = adapter._wait_route_cards(lambda: None, attempts=4)
    assert shot is frames[-1] and cards[0] is frames[-1]["nodes"][0]
    assert label(cards[0]) == "30分钟夜10路" and sleep.call_count == 3
    adapter.base.tap.assert_not_called()


def test_route_card_stability_returns_the_latest_observed_object_not_a_cached_node(adapter, monkeypatch):
    frames = [route_frame("8分钟3号线"), route_frame("8分钟3号线")]
    adapter._observe = Mock(side_effect=frames);monkeypatch.setattr("time.sleep", Mock())
    shot, cards = adapter._wait_route_cards(lambda: None, attempts=2)
    assert shot is frames[1] and cards[0] is frames[1]["nodes"][0] and cards[0] is not frames[0]["nodes"][0]


def test_empty_frame_resets_stability_even_if_the_old_label_reappears(adapter, monkeypatch):
    adapter._observe = Mock(side_effect=[route_frame("8分钟3号线"), route_frame(), route_frame("8分钟3号线")])
    monkeypatch.setattr("time.sleep", Mock())
    with pytest.raises(WorkflowError): adapter._wait_route_cards(lambda: None, attempts=3)


def test_stability_cannot_span_a_pause_resume_epoch_change(adapter, monkeypatch):
    frames = [route_frame("8分钟3号线", epoch=0), route_frame("8分钟3号线", epoch=1), route_frame("8分钟3号线", epoch=1)]
    adapter._observe = Mock(side_effect=frames);monkeypatch.setattr("time.sleep", Mock())
    shot, cards = adapter._wait_route_cards(lambda: None, attempts=3)
    assert shot is frames[-1] and shot["epoch"] == 1


def test_no_route_requires_current_explicit_absence_in_two_consecutive_frames(adapter, monkeypatch):
    frames = [route_frame(text="暂无公交路线"), route_frame(text="暂无公交路线")]
    adapter._observe = Mock(side_effect=frames);monkeypatch.setattr("time.sleep", Mock())
    shot, value = adapter._wait_route_cards(lambda: None, attempts=2)
    assert value == "no_route" and shot is frames[1]


def test_persistent_empty_results_are_a_bounded_failure_not_a_no_route_claim(adapter, monkeypatch):
    adapter._observe = Mock(side_effect=[route_frame(), route_frame(), route_frame()])
    monkeypatch.setattr("time.sleep", Mock())
    with pytest.raises(WorkflowError): adapter._wait_route_cards(lambda: None, attempts=3)
    assert adapter._observe.call_count == 3


def test_walking_calorie_cards_are_not_public_transport_but_night_buses_and_named_lines_are(adapter):
    rejected = ["步行59分钟undefined消耗217大卡", "59分钟undefined消耗217大卡", "59分钟步行路线消耗217大卡"]
    accepted = ["45分钟夜12路预计进站", "20分钟APM线3站", "35分钟机场快线", "12分钟地铁出行"]
    nodes = [node(desc=text, rect=f"[0,{100+i*200}][1200,{250+i*200}]") for i, text in enumerate(rejected + accepted)]
    assert [label(n) for n in adapter._route_cards({"nodes": nodes})] == accepted


def test_mixed_stale_absence_and_visible_routes_do_not_become_a_current_result(adapter, monkeypatch):
    adapter._observe = Mock(side_effect=[route_frame("8分钟3号线", text="暂无公交路线\n8分钟3号线") for _ in range(3)])
    monkeypatch.setattr("time.sleep", Mock())
    with pytest.raises(WorkflowError): adapter._wait_route_cards(lambda: None, attempts=3)


def test_waiting_for_details_keeps_old_list_and_loading_frames_out_of_detail_evidence(adapter, monkeypatch):
    old_list = xml([node("起点", **{"resource-id": ID + "route_edit_summary_start"}),
                    node(desc="59分钟步行路线消耗217大卡"), node(desc="25分钟5号线另一方案")])
    loading = xml([node("路线加载中")])
    detail = xml([node(desc="公交详情页面"), node("3号线"), node("体育西路"), node("海傍方向"), node("广州塔")])
    adapter.base.observe.side_effect = [old_list, loading, detail]
    adapter.route_id = "selected-route"
    monkeypatch.setattr("time.sleep", Mock())
    shot, found = adapter._wait(lambda s: next((n for n in s["nodes"] if label(n) == "公交详情页面" and bounds(n)
                                                and s["evidence"]["page_kind"] == "route_detail"), None), lambda: None, "route_detail", attempts=3)
    assert found is not None and shot["evidence"]["page_kind"] == "route_detail"
    assert [e["page_kind"] for e in adapter.observations] == ["route_list", "screen", "route_detail"]
    selected = [e for e in adapter.observations if e["page_kind"] == "route_detail"]
    assert len(selected) == 1 and "3号线" in selected[0]["text"]
    assert "5号线" not in selected[0]["text"] and "步行" not in selected[0]["text"]
    assert all(e["route_id"] == "selected-route" for e in adapter.observations)


@pytest.mark.parametrize("hidden,mixed_fields", [(True, False), (False, True)])
def test_invisible_or_mixed_transition_detail_marker_cannot_certify_a_detail_page(adapter, hidden, mixed_fields):
    nodes = [node(desc="公交详情页面", rect="[0,0][0,0]" if hidden else "[0,0][1200,2486]")]
    if mixed_fields: nodes.append(node("起点", **{"resource-id": ID + "route_edit_summary_start"}))
    adapter.base.observe.return_value = xml(nodes)
    shot = adapter._observe(lambda: None, "route_detail")
    assert shot["evidence"]["page_kind"] != "route_detail"
    assert not [e for e in adapter.observations if e["page_kind"] == "route_detail"]


def test_a_successfully_seen_new_bridge_can_never_downgrade_silent_failures_to_legacy(monkeypatch):
    import base64
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport._device = Mock();transport.prefer_transit_bridge = True
    transport._fresh_file_xml = Mock(return_value="must-not-start-legacy-uiautomation")
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    count = 0
    def run(command, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            root = ET.Element("hierarchy", nonce=command[-1]);root.append(node("高德当前窗口"))
            encoded = base64.urlsafe_b64encode(ET.tostring(root, encoding="utf-8")).decode()
            return subprocess.CompletedProcess(command, 0, f'Result: Bundle[{{code=1, result=1, data={encoded}, reason=ok, nonce={command[-1]}}}]', "")
        return subprocess.CompletedProcess(command, 0, "Broadcast completed: result=0", "")
    monkeypatch.setattr(subprocess, "run", run);monkeypatch.setattr("time.sleep", Mock())
    assert "高德当前窗口" in transport.xml() and transport.protocol_seen
    with pytest.raises(RuntimeError): transport.xml()
    assert count == 3 and transport.last_bridge_reason == "bridge_no_response"
    transport._device.ui.assert_not_called();transport._fresh_file_xml.assert_not_called()


@pytest.mark.parametrize("reason", ["service_unavailable", "bridge_busy", "bridge_timeout", "tree_changed"])
def test_explicit_new_protocol_reason_prevents_later_silent_legacy_downgrade(monkeypatch, reason):
    import gmail_demo
    from device.mobilerun import MobilerunTransport
    transport = MobilerunTransport();transport._device = Mock();transport.prefer_transit_bridge = True
    transport.file_xml_mode = True;transport._fresh_file_xml = Mock()
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    count = 0
    def run(command, **kwargs):
        nonlocal count
        count += 1
        output = f"Result: Bundle[{{code=0, result=0, nonce={command[-1]}, reason={reason}, data={reason}}}]" if count == 1 else "No content provider response"
        return subprocess.CompletedProcess(command, 0, output, "")
    monkeypatch.setattr(subprocess, "run", run);monkeypatch.setattr("time.sleep", Mock())
    assert transport._protected_transit_xml() is None and transport.protocol_seen
    with pytest.raises(RuntimeError): transport.xml()
    assert count == 3 and transport.last_bridge_reason == "bridge_no_response"
    transport._device.ui.assert_not_called();transport._fresh_file_xml.assert_not_called()


def test_query_records_the_actual_resumed_task_identity_and_can_reload_it_from_private_evidence(adapter):
    result = {"task_id": "offline-task", "query_id": "offline-query", "route_id": "offline-route"}
    adapter.base.shell.return_value = map_activity_snapshot(307, "com.autonavi.minimap/.ActualObservedActivity")
    context = adapter._remember_detail_context(result, lambda: None)
    assert context["android_task_id"] == 307 and context["activity"].endswith("/.ActualObservedActivity")
    path = adapter._context_path(adapter._context_key(result));assert path.is_file()
    adapter._detail_contexts.clear()
    assert adapter._detail_context(result) == context
    assert all(c.args[:2] != ("am", "start") for c in adapter.base.shell.call_args_list)


@pytest.mark.parametrize("problem", ["task_gone", "activity_changed", "query_changed"])
def test_show_refuses_missing_or_changed_android_task_identity_without_guessing_a_launcher(adapter, problem):
    result = {"segments": [{"mode": "subway", "line": "3号线", "board": "体育西路", "direction": "海傍", "alight": "广州塔"}]}
    context = seed_view_context(adapter, result)
    if problem == "task_gone": adapter.base.shell.return_value = map_activity_snapshot(task_id=72)
    if problem == "activity_changed": adapter.base.shell.return_value = map_activity_snapshot(activity="com.autonavi.minimap/.DifferentActivity")
    if problem == "query_changed": context["query_id"] = "old-query"
    with pytest.raises(WorkflowError): adapter.show_details(result, lambda: None)
    adapter.base.observe.assert_not_called()
    assert not any(c.args[:2] == ("am", "start") or c.args[:3] == ("am", "task", "focus") for c in adapter.base.shell.call_args_list)


def test_query_does_not_invent_a_map_identity_if_the_current_resumed_activity_is_another_app(adapter):
    result = {"task_id": "offline-task", "query_id": "offline-query", "route_id": "offline-route"}
    adapter.base.shell.return_value = "mResumedActivity: ActivityRecord{offline u0 com.example.demo/.MainActivity t99}"
    assert adapter._remember_detail_context(result, lambda: None) is None
    assert not adapter._detail_contexts


def location_card(address="甲省乙市丙区和平路12号", *, marker=True, distance=True, eid="real-location"):
    ns = [node(desc=address, rect="[53,1402][1147,1486]")]
    if marker:
        ns.append(node("您当前位置是在市民中心附近", cls="android.view.View", rect="[53,1597][618,1643]"))
    if distance:
        ns.append(node("直线<100米", cls="android.view.View", rect="[53,1533][265,1584]"))
    return {"nodes": ns, "epoch": 0, "evidence": {"evidence_id": eid, "page_kind": "location"}}


def test_official_location_reads_address_city_from_real_card_without_using_nearby_poi(adapter):
    shot = location_card(address="广东省广州市天河区测试路12号")
    shot["nodes"].append(node("广州市另一个学校", rect="[53,1800][1147,1900]"))
    place = adapter._location_place(shot)
    assert place["name"] == place["address"] == "广东省广州市天河区测试路12号"
    assert place["city"] == "广州市" and place["source"] == "current_location"
    assert set(place["evidence_refs"]) == {"name", "address", "city"}


@pytest.mark.parametrize("kwargs", [{"marker": False}, {"distance": False}, {"address": "我的位置"}, {"address": "广州市"}])
def test_location_placeholder_or_unverified_nearby_card_never_becomes_a_fix(adapter, kwargs):
    assert adapter._location_place(location_card(**kwargs)) is None


def test_current_location_health_failure_requests_origin_instead_of_reusing_old_location(adapter, monkeypatch):
    adapter._progress = Mock(); adapter.base.shell = Mock()
    shot = location_card(address="广东省广州市天河区测试路12号")
    adapter._wait = Mock(return_value=(shot, adapter._location_place(shot)))
    monkeypatch.setattr("device.location_status.read_location_status", lambda *a: {"verified": False, "reason": "stale_fix"})
    result = adapter._resolve_current_location(lambda: None)
    assert result["kind"] == "clarification" and result["fields"] == ["origin"]
    assert "myLocation" in adapter.base.shell.call_args.args[6]


def test_only_same_primary_poi_municipal_designation_can_prove_city(adapter):
    shot = museum_search_shot(query="广州广东省博物馆", name="广东省博物馆")
    primary = next(n for n in shot["nodes"] if n.get("content-desc") == "广东省博物馆" and n.get("clickable") == "true")
    primary.append(node("广州市科普基地", cls="android.view.View", rect="[403,1739][856,1801]", clickable="false"))
    candidate = adapter._place_candidates(shot, "广东省博物馆")[0][0]
    assert candidate["city"] == "广州市"
    detail = {"nodes": [node("天河区珠江东路2号", rect="[53,751][833,797]")], "evidence": {"evidence_id": "detail"}}
    place = adapter._read_place(detail, candidate["name"], candidate=candidate)
    assert place["city"] == "广州市" and place["address"] == "天河区珠江东路2号"
    assert place["evidence_refs"]["city"] == [candidate["evidence_id"]]
    primary.remove(primary[-1])
    shot["nodes"].append(node("广州市科普基地", rect="[403,1739][856,1801]"))
    assert adapter._place_candidates(shot, "广东省博物馆")[0][0]["city"] is None


@pytest.mark.parametrize("error", [Cancelled("用户取消"), ScreenContextChanged("暂停恢复")])
def test_location_wait_preserves_cancellation_and_expired_context(adapter, error):
    adapter._progress = Mock();adapter._wait = Mock(side_effect=error)
    with pytest.raises(type(error)):
        adapter._resolve_current_location(lambda: None)


def test_selecting_current_origin_uses_the_observed_location_row_not_the_text_editor(adapter):
    summary = node("旧的明确起点", **{"resource-id": ID+"route_edit_summary_start"})
    editor = node("我的位置", cls="android.widget.EditText", rect="[251,194][785,292]")
    row = node("我的位置", cls="android.view.View", rect="[39,617][200,672]", clickable="false")
    first = {"nodes": [summary], "epoch": 0};second = {"nodes": [editor,row], "epoch": 0}
    frames = iter([first,second])
    def wait(predicate, *args, **kwargs):
        shot = next(frames);value = predicate(shot)
        if value is None: value = predicate(shot)
        assert value is not None
        return shot,value
    adapter._wait=wait;adapter._tap=Mock()
    adapter._set_current_origin(lambda:None)
    assert adapter._tap.call_args_list[1].args[1] is row


def test_location_health_check_cannot_attach_new_fix_to_a_pre_pause_address(adapter, monkeypatch):
    adapter._progress=Mock()
    shot=location_card(address="广东省广州市天河区测试路12号")
    adapter._wait=Mock(return_value=(shot,adapter._location_place(shot)))
    def health(*args):
        adapter.base.context_epoch += 1
        return {"verified":True}
    monkeypatch.setattr("device.location_status.read_location_status",health)
    with pytest.raises(ScreenContextChanged):
        adapter._resolve_current_location(lambda:None)


def test_current_location_entry_animation_requires_two_same_position_observations(adapter, monkeypatch):
    monkeypatch.setattr("device.amap.time.sleep",lambda _:None)
    summary=node("我的位置", **{"resource-id":ID+"route_edit_summary_start"})
    moving=node("我的位置",cls="android.view.View",rect="[39,625][200,680]")
    settled=node("我的位置",cls="android.view.View",rect="[39,617][200,672]")
    shots=[{"nodes":[n],"epoch":0} for n in [summary,moving,settled,settled]]
    adapter._observe=Mock(side_effect=shots);adapter._tap=Mock()
    adapter._set_current_origin(lambda:None)
    assert adapter._observe.call_count==4
    assert adapter._tap.call_args.args[1] is settled



def long_shown_route():
    return {"task_id": "long-task", "query_id": "long-query", "route_id": "long-route", "route_verified": True,
            "origin": {"name": "甲地铁站"}, "destination": {"name": "戊地铁站"},
            "segments": [{"mode": "subway", "line": "1号线", "board": "甲", "direction": "乙", "alight": "丙"},
                         {"mode": "subway", "line": "2号线", "board": "丙", "direction": "丁", "alight": "戊"}]}


def shown_detail_frame(part, *, summary="30分钟1号线2号线步行200米", hidden_first=False):
    ns = [node(desc="公交详情页面", rect="[0,154][1200,2486]"),
          node(desc=summary, rect="[40,350][1160,600]")]
    rows = {0: ["甲", "1号线", "乙方向", "丙"], 1: ["丙", "2号线", "丁方向", "戊"]}[part]
    for index, text in enumerate(rows): ns.append(node(text, rect=f"[160,{800+index*100}][1100,{880+index*100}]"))
    if hidden_first:
        hidden = node(rect="[100,1300][1100,2100]", **{"visible-to-user": "false"})
        for text in ["甲", "1号线", "乙方向", "丙"]: hidden.append(node(text, rect="[160,1400][1100,1500]"))
        ns.append(hidden)
    return xml(ns)


def prepare_shown_frames(adapter, route, frames, *, trusted=True):
    context = seed_view_context(adapter, route)
    if trusted:
        nodes, viewport = adapter._visible_detail_nodes(frames[0])
        strip = adapter._detail_strip(nodes, viewport, route)
        context["detail_strip_fingerprint"] = strip["fingerprint"]
    position = [len(frames) - 1]
    adapter.base.observe.side_effect = lambda checkpoint: frames[position[0]]
    def scroll(shot, checkpoint, reverse=False):
        checkpoint()
        position[0] = max(0, position[0]-1) if reverse else min(len(frames)-1, position[0]+1)
    adapter._scroll_detail = Mock(side_effect=scroll)
    return context


def test_show_long_route_checks_visible_rides_across_bounded_frames_and_preserves_original_context(adapter):
    import time
    route = long_shown_route();frames=[shown_detail_frame(0), shown_detail_frame(1)]
    prepare_shown_frames(adapter, route, frames)
    old_folder, old_started, old_query = adapter.folder, time.monotonic()-7200, adapter.query_id
    adapter.started = old_started
    adapter.show_details(route, lambda: None)
    assert adapter._scroll_detail.call_count == 3  # tail -> top -> stable top -> tail
    assert adapter.folder == old_folder and adapter.started == old_started and adapter.query_id == old_query
    assert any((adapter._context_path(adapter._context_key(route)).parent).glob("show-*/*.xml"))
    assert not any(call.args[:2] == ("am", "start") for call in adapter.base.shell.call_args_list)
    adapter.models.assert_not_called()


def test_show_backfills_legacy_context_only_from_current_query_trusted_xml(adapter):
    route=long_shown_route();frames=[shown_detail_frame(0),shown_detail_frame(1)]
    context=prepare_shown_frames(adapter,route,frames,trusted=False)
    folder=adapter._context_path(adapter._context_key(route)).parent;folder.mkdir(parents=True,exist_ok=True)
    source=folder/"actual-detail.xml";source.write_text(frames[0])
    route["evidence"]=[{"query_id":route["query_id"],"route_id":route["route_id"],"page_kind":"route_detail","xml_path":str(source)}]
    adapter.show_details(route,lambda:None)
    assert context["detail_strip_fingerprint"]


@pytest.mark.parametrize("problem", ["wrong_query", "outside_directory", "list_summary_only"])
def test_show_does_not_backfill_model_text_or_other_query_files(adapter,tmp_path,problem):
    route=long_shown_route();frames=[shown_detail_frame(0),shown_detail_frame(1)]
    prepare_shown_frames(adapter,route,frames,trusted=False)
    folder=adapter._context_path(adapter._context_key(route)).parent;folder.mkdir(parents=True,exist_ok=True)
    source=(tmp_path if problem=="outside_directory" else folder)/"untrusted.xml";source.write_text(frames[0])
    route["evidence"]=[{"query_id":"another-query" if problem=="wrong_query" else route["query_id"],
        "route_id":route["route_id"],"page_kind":"route_list" if problem=="list_summary_only" else "route_detail",
        "xml_path":str(source),"text":"30分钟1号线2号线步行200米"}]
    with pytest.raises(WorkflowError): adapter.show_details(route,lambda:None)
    adapter._scroll_detail.assert_not_called()


@pytest.mark.parametrize("hidden", [False,True])
def test_show_never_accepts_only_tail_or_hidden_first_ride(adapter,hidden):
    route=long_shown_route();frame=shown_detail_frame(1,hidden_first=hidden)
    prepare_shown_frames(adapter,route,[frame])
    with pytest.raises(WorkflowError):adapter.show_details(route,lambda:None)
    assert adapter._scroll_detail.call_count<=2


def test_show_rejects_changed_selected_plan_during_scroll(adapter):
    route=long_shown_route();frames=[shown_detail_frame(0),shown_detail_frame(1)]
    prepare_shown_frames(adapter,route,frames)
    adapter._scroll_detail.side_effect=lambda *args,**kwargs:setattr(adapter.base.observe,"side_effect",lambda checkpoint:shown_detail_frame(0,summary="40分钟1号线2号线步行500米"))
    with pytest.raises(WorkflowError,match="方案"):adapter.show_details(route,lambda:None)
    assert adapter._scroll_detail.call_count==1


@pytest.mark.parametrize("change", ["cancel", "epoch"])
def test_show_cancel_or_epoch_change_stops_remaining_multiscreen_actions(adapter,change):
    route=long_shown_route();prepare_shown_frames(adapter,route,[shown_detail_frame(0),shown_detail_frame(1)])
    def scroll(*args,**kwargs):
        if change=="cancel":raise Cancelled("cancelled during read-only viewing")
        adapter.base.context_epoch+=1
    adapter._scroll_detail.side_effect=scroll
    with pytest.raises(Cancelled if change=="cancel" else ScreenContextChanged):adapter.show_details(route,lambda:None)
    assert adapter._scroll_detail.call_count==1



def test_show_partial_multiscreen_missing_board_preserves_partial_without_completing_it(adapter):
    from copy import deepcopy
    route=long_shown_route();route.update(classification="PARTIAL",route_verified=False,missing_fields=["segments.first.board"])
    route["segments"][0]["board"]=None
    prepare_shown_frames(adapter,route,[shown_detail_frame(0),shown_detail_frame(1)])
    original=deepcopy(route)
    adapter.show_details(route,lambda:None)
    assert route==original and route["classification"]=="PARTIAL" and not route["route_verified"]
    assert adapter._scroll_detail.call_count==3


def test_show_complete_still_requires_all_four_critical_fields_per_ride(adapter):
    route=long_shown_route();route["classification"]="COMPLETE";route["segments"][0]["board"]=None
    prepare_shown_frames(adapter,route,[shown_detail_frame(0),shown_detail_frame(1)])
    with pytest.raises(WorkflowError):adapter.show_details(route,lambda:None)
    adapter._scroll_detail.assert_not_called()


@pytest.mark.parametrize("problem",["no_summary","no_endpoints","no_line","no_board_or_alight"])
def test_show_partial_requires_trusted_summary_endpoints_and_minimum_ride_facts(adapter,problem):
    route=long_shown_route();route.update(classification="PARTIAL",route_verified=False)
    if problem=="no_endpoints":route["origin"]["name"]=""
    if problem=="no_line":route["segments"][0]["line"]=None
    if problem=="no_board_or_alight":route["segments"][0].update(board=None,alight=None)
    prepare_shown_frames(adapter,route,[shown_detail_frame(0),shown_detail_frame(1)],trusted=problem!="no_summary")
    with pytest.raises(WorkflowError):adapter.show_details(route,lambda:None)
    adapter._scroll_detail.assert_not_called()


def campus_with_gate_card():
    card=node(desc="示例大学(国际校区)",rect="[0,1430][1200,2134]")
    title=node(desc="示例大学(国际校区)",cls="android.view.View",rect="[403,1463][1007,1538]",clickable="false")
    address=node("广州市测试路777号",cls="android.view.View",rect="[403,1544][834,1597]",clickable="false")
    gate=node(rect="[541,1769][1037,1888]")
    gate.append(node("西南门",cls="android.view.View",rect="[647,1769][768,1862]",clickable="false"))
    card.extend([title,address,gate])
    root=ET.fromstring(xml([card]));ns=list(root.iter('node'))
    return {'nodes':ns,'epoch':0,'evidence':{'evidence_id':'campus-list','observation_id':'campus-list','page_kind':'place_list'}},ns[0],ns[1]


def test_campus_card_click_targets_exact_title_instead_of_embedded_gate(adapter):
    shot,card,title=campus_with_gate_card()
    r=bounds(card);point=((r[0]+r[2])//2,(r[1]+r[3])//2)
    gate=next(n for n in card if bounds(n)==[541,1769,1037,1888])
    g=bounds(gate);assert g[0]<=point[0]<=g[2] and g[1]<=point[1]<=g[3]
    assert adapter._place_title_target(card) is title
    b=bounds(title);assert (b[1]+b[3])//2<g[1]


def test_place_title_click_rechecks_current_parent_address_before_acting(adapter):
    shot,card,title=campus_with_gate_card();candidate=adapter._place_candidates(shot,'示例大学国际校区')[0][0]
    fresh,fresh_card,fresh_title=campus_with_gate_card()
    fresh_card[1].set('text','广州市另一个地址999号')
    adapter._observe=Mock(return_value=fresh)
    binding={'query':'示例大学国际校区','candidate_id':candidate['candidate_id'],
             'row_signature':tuple(card.get(k) for k in ['resource-id','bounds','text','content-desc'])}
    with pytest.raises(WorkflowError,match='名称或地址已变化'):
        adapter._tap(shot,title,lambda:None,place_binding=binding)
    adapter.base.tap.assert_not_called()


def test_place_title_click_keeps_guarded_observation_and_real_title_geometry(adapter):
    shot,card,title=campus_with_gate_card();candidate=adapter._place_candidates(shot,'示例大学国际校区')[0][0]
    adapter._observe=Mock(return_value=shot)
    binding={'query':'示例大学国际校区','candidate_id':candidate['candidate_id'],
             'row_signature':tuple(card.get(k) for k in ['resource-id','bounds','text','content-desc'])}
    adapter._tap(shot,title,lambda:None,place_binding=binding)
    adapter.base.tap.assert_called_once_with(title,__import__('unittest').mock.ANY)


def test_card_without_unique_main_title_never_falls_back_to_the_card_center(adapter):
    shot,card,title=campus_with_gate_card();card.remove(title)
    with pytest.raises(WorkflowError,match='主标题入口'):
        adapter._place_title_target(card)
    card.append(title);card.append(node(desc=label(title),rect='[403,1663][1007,1738]'))
    with pytest.raises(WorkflowError,match='主标题入口'):
        adapter._place_title_target(card)


def test_preview_identity_keeps_gate_distinct_from_the_whole_campus(adapter):
    title=node(desc='示例大学国际校区(西南门)',rect='[53,1388][1147,1472]')
    for i,glyph in enumerate('示例大学国际校区(西南门)'):
        title.append(node(glyph,cls='android.view.View',rect=f'[{53+i*45},1388][{98+i*45},1472]',clickable='false'))
    assert adapter._poi_title_names({'nodes':list(title.iter('node'))})=={'示例大学国际校区(西南门)'}


def test_compact_station_detail_reads_the_address_from_its_own_header(adapter, monkeypatch):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    name = "广州塔(地铁站)"
    adapter.request["city"] = "广州市"
    listing = museum_search_shot(query="广州市广州塔地铁站", name=name, address="广州市海珠区")
    title = node(desc=name, rect="[53,806][1147,882]")
    title.append(node(name, cls="android.view.View", rect="[53,806][620,882]"))
    header = node(rect="[0,767][1200,1219]")
    header.extend([title, node("广州市海珠区", cls="android.view.View", rect="[53,1150][270,1200]")])
    screen = node(rect="[0,0][1200,2640]")
    screen.extend([header, node("广州市海珠区其他路99号", rect="[53,1510][850,1580]"),
                   node("搜周边", rect="[106,2484][205,2535]"), node("分享", rect="[362,2484][435,2534]"),
                   node("导航", rect="[686,2440][787,2506]"), node("路线", rect="[942,2440][1043,2506]")])
    detail = {"nodes": list(screen.iter("node")), "epoch": 0, "evidence": {"evidence_id": "huawei-detail"}}
    adapter._observe = Mock(side_effect=[listing, listing, detail])
    adapter._tap = Mock()

    resolved = adapter._resolve_place("origin", "广州塔地铁站", {}, lambda: None)

    assert resolved["kind"] == "place"
    assert resolved["place"]["name"] == name
    assert resolved["place"]["address"] == "广州市海珠区"
    assert resolved["place"]["evidence_refs"]["address"] == ["huawei-detail"]


def test_huawei_collapsed_station_is_a_scroll_target_only_with_a_matching_title(adapter):
    name = "广州塔(地铁站)"
    title = node(desc=name, rect="[53,2048][1147,2124]")
    leaf = node(name, cls="android.view.View", rect="[53,2048][430,2124]")
    title.append(leaf)
    preview = node(cls="androidx.recyclerview.widget.RecyclerView", rect="[0,2007][1200,2640]",
                   scrollable="true")
    preview.append(title)
    screen = node(rect="[0,0][1200,2640]")
    screen.extend([preview, node("搜周边"), node("分享"), node("导航"), node("路线")])
    shot = {"nodes": list(screen.iter("node"))}

    assert adapter._collapsed_station_card(shot, name) == ([53, 2048, 1147, 2124], [0, 2007, 1200, 2640])
    assert adapter._collapsed_station_card(shot, "客村(地铁站)") is None
    leaf.set("visible-to-user", "false")
    assert adapter._collapsed_station_card(shot, name) is None


def test_wrong_gate_preview_is_reported_as_identity_mismatch_not_a_network_timeout(adapter,monkeypatch):
    monkeypatch.setattr('device.amap.time.sleep',lambda _:None)
    shot,card,title=campus_with_gate_card()
    shot['nodes'].append(node(desc='搜索框，广州示例大学国际校区'))
    wrong=node(desc='示例大学国际校区(西南门)',rect='[53,1388][1147,1472]')
    for i,g in enumerate('示例大学国际校区(西南门)'):
        wrong.append(node(g,cls='android.view.View',rect=f'[{53+i*45},1388][{98+i*45},1472]',clickable='false'))
    preview={'nodes':list(wrong.iter('node'))+[node('路线'),node('导航')],'epoch':0,'evidence':{'evidence_id':'wrong-gate'}}
    adapter._observe=Mock(side_effect=[shot,shot,preview]);adapter._tap=Mock()
    with pytest.raises(WorkflowError,match='选择不一致'):
        adapter._resolve_place('origin','示例大学国际校区',{},lambda:None)
    assert adapter._tap.call_args.args[1] is title


def test_location_clarification_reports_actual_expired_fix_reason(adapter,monkeypatch):
    shot=location_card(address='广东省广州市天河区测试路12号')
    adapter._progress=Mock();adapter._wait=Mock(return_value=(shot,adapter._location_place(shot)))
    reason='系统最近定位已过期，请刷新高德定位或填写明确起点'
    monkeypatch.setattr('device.location_status.read_location_status',lambda *a:{'verified':False,'reason':reason})
    assert adapter._resolve_current_location(lambda:None)['message']==reason


def poi_overview(text='示例大学国际校区，位于广州市番禺区，是一所大学。'):
    block=node(rect='[0,1269][1200,1500]')
    intro=node(text,rect='[53,1269][1146,1390]')
    block.extend([intro,node('概述',rect='[53,1416][154,1477]'),node('查看全部',rect='[938,1428][1099,1469]')])
    return block,intro


def test_own_poi_overview_can_prove_city_without_changing_the_header_address(adapter):
    block,intro=poi_overview()
    detail={'nodes':[node('番禺区测试路777号',rect='[53,751][833,797]'),*block.iter('node'),
                     node('相似服务推荐',rect='[53,1517][900,1590]')],
            'evidence':{'evidence_id':'campus-detail'}}
    result=adapter._read_place(detail,'示例大学(国际校区)')
    assert result['city']=='广州市' and result['address']=='番禺区测试路777号'
    assert result['evidence_refs']['city']==['campus-detail']
    intro.set('text','示例大学其他校区，位于广州市番禺区。')
    assert adapter._read_place(detail,'示例大学(国际校区)')['city'] is None
    intro.set('text','示例大学国际校区，位于广州市番禺区。')
    intro.set('bounds','[53,1669][1146,1790]')
    assert adapter._read_place(detail,'示例大学(国际校区)')['city'] is None


def test_extraction_uses_inspected_canonical_names_but_keeps_real_location_mode():
    request={'origin':'示例大学国际校区','destination':'目的地','city':'广州市','use_current_location':False}
    places={'origin':{'name':'示例大学(国际校区)'},'destination':{'name':'目的地'}}
    result=AmapDevice._extraction_request(request,places)
    assert result['origin']=='示例大学(国际校区)' and request['origin']=='示例大学国际校区'
    current={**request,'origin':'','use_current_location':True}
    assert AmapDevice._extraction_request(current,places)['origin']==''
    assert AmapDevice._extraction_request(current,places)['use_current_location'] is True


@pytest.mark.parametrize('hidden',['title','title_parent','card','card_parent'])
def test_poi_title_must_still_be_visible_through_its_current_ancestor_chain(adapter,hidden):
    shot,card,title=campus_with_gate_card()
    candidate=adapter._place_candidates(shot,'示例大学国际校区')[0][0]
    binding={'query':'示例大学国际校区','candidate_id':candidate['candidate_id'],
             'row_signature':tuple(card.get(k) for k in ['resource-id','bounds','text','content-desc'])}
    fresh,current_row,current_title=campus_with_gate_card()
    if hidden=='title': current_title.set('visible-to-user','false')
    elif hidden=='card': current_row.set('visible-to-user','false')
    elif hidden=='title_parent':
        current_row.remove(current_title)
        parent=node(rect='[403,1463][1007,1538]',**{'visible-to-user':'false'})
        parent.append(current_title);current_row.insert(0,parent);fresh['nodes'].append(parent)
    else:
        parent=node(rect='[0,1430][1200,2134]',**{'visible-to-user':'false'})
        parent.append(current_row);fresh['nodes'].append(parent)
    with pytest.raises(WorkflowError):
        adapter._place_title_target(current_row,nodes=fresh['nodes'])
    adapter._observe=Mock(return_value=fresh)
    with pytest.raises(WorkflowError):
        adapter._tap(shot,title,lambda:None,place_binding=binding)
    adapter.base.tap.assert_not_called()


@pytest.mark.parametrize('hidden_parent',[False,True])
def test_hidden_poi_overview_cannot_prove_the_administrative_city(adapter,hidden_parent):
    parent,overview=poi_overview()
    (parent if hidden_parent else overview).set('visible-to-user','false')
    detail={'nodes':[node('番禺区测试路777号',rect='[53,751][833,797]'),*parent.iter('node')],
            'evidence':{'evidence_id':'campus-detail'}}
    assert adapter._read_place(detail,'示例大学(国际校区)')['city'] is None


@pytest.mark.parametrize('section',['用户评价(128)','用户评论（9）','相关推荐：更多'])
def test_review_or_recommendation_card_cannot_certify_a_poi_city(adapter,section):
    block,intro=poi_overview()
    # Even copied overview labels inside an explicitly different section
    # cannot make its user-supplied sentence authoritative.
    block.set('content-desc',section)
    detail={'nodes':[node('番禺区测试路777号',rect='[53,751][833,797]'),*block.iter('node')],
            'evidence':{'evidence_id':'review-page'}}
    assert adapter._read_place(detail,'示例大学(国际校区)')['city'] is None


def test_unbound_same_name_location_sentence_is_not_a_poi_overview(adapter):
    detail={'nodes':[node('番禺区测试路777号',rect='[53,751][833,797]'),
                     node('示例大学国际校区，位于广州市番禺区。',rect='[53,1269][1146,1390]')],
            'evidence':{'evidence_id':'unbound-text'}}
    assert adapter._read_place(detail,'示例大学(国际校区)')['city'] is None


_NAVIGATION_FOOTER = "[769,2446][1124,2605]"


def _navigation_detail(*, state="start", with_control=True, footer=_NAVIGATION_FOOTER,
                       line="3号线海傍方向", destination="客村"):
    # The native Huawei bridge exposed a clickable ViewGroup with the
    # accessible action and a visible text descendant in the same bounds.
    marker = node(desc="公交详情页面", rect="[0,0][1200,2640]")
    marker.extend([
        node("广州塔", rect="[178,1632][359,1707]"),
        node(line, rect="[178,1745][1133,1815]"),
        node(destination, rect="[178,2537][299,2612]"),
    ])
    if with_control:
        title = "结束导航" if state == "active" else "开始导航"
        description = "结束导航" if state == "active" else "开始导航，开启下车提醒"
        control = node(desc=description, rect=footer)
        control.append(node(title if state != "wrong_child" else "下车提醒", rect="[845,2490][1045,2558]",
                            cls="android.view.View"))
        marker.append(control)
    return xml([marker])


def _verified_navigation_route(adapter):
    route = {"classification": "COMPLETE", "route_verified": True,
             "origin": {"name": "广州塔"}, "destination": {"name": "客村"},
             "segments": [{"mode": "subway", "line": "3号线", "board": "广州塔", "direction": "海傍", "alight": "客村"}]}
    seed_view_context(adapter, route)
    return route


def test_native_navigation_uses_one_observed_footer_and_leaves_active_amap(adapter):
    route = _verified_navigation_route(adapter)
    start = _navigation_detail()
    adapter.base.observe.side_effect = [start, start, _navigation_detail(state="active")]
    before_tap = Mock()
    adapter.base.tap.side_effect = lambda control, checkpoint: (checkpoint(), before_tap.assert_called_once())

    proof = adapter.start_navigation(route, lambda: None, before_tap)

    assert proof["observation_id"] and proof["android_task_id"] == 71
    assert bounds(adapter.base.tap.call_args.args[0]) == [769, 2446, 1124, 2605]
    adapter.base.tap.assert_called_once()
    before_tap.assert_called_once()
    assert all(call.args[:2] != ("am", "start") for call in adapter.base.shell.call_args_list)


@pytest.mark.parametrize("second_read_changes", [False, True])
def test_native_navigation_retries_only_one_read_of_the_same_task(adapter, monkeypatch, second_read_changes):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    route = _verified_navigation_route(adapter)
    start = _navigation_detail()
    adapter.base.observe.side_effect = [start, start, _navigation_detail(state="active")]
    original_observe = adapter._observe
    read_count = 0

    def read(*args, **kwargs):
        nonlocal read_count
        read_count += 1
        if read_count == 1 or (read_count == 3 and second_read_changes):
            raise TransitTreeChanging("tree replaced during read")
        return original_observe(*args, **kwargs)

    monkeypatch.setattr(adapter, "_observe", read)
    before_tap = Mock()
    if second_read_changes:
        with pytest.raises(TransitTreeChanging):
            adapter.start_navigation(route, lambda: None, before_tap)
        assert read_count == 3
        before_tap.assert_not_called()
        adapter.base.tap.assert_not_called()
    else:
        assert adapter.start_navigation(route, lambda: None, before_tap)["observation_id"]
        assert read_count == 4
        before_tap.assert_called_once()
        adapter.base.tap.assert_called_once()


@pytest.mark.parametrize("bad_frame", ["wrong_route", "changed_footer", "wrong_child", "already_active"])
def test_native_navigation_does_not_dispatch_from_unverified_or_changed_footer(adapter, bad_frame):
    route = _verified_navigation_route(adapter)
    before_tap = Mock()
    adapter._scroll_detail = Mock()
    start = _navigation_detail()
    if bad_frame == "wrong_route":
        first = second = _navigation_detail(destination="体育西路")
    elif bad_frame == "changed_footer":
        first, second = start, _navigation_detail(footer="[769,2441][1124,2605]")
    elif bad_frame == "wrong_child":
        first = second = _navigation_detail(state="wrong_child")
    else:
        first = second = _navigation_detail(state="active")
    adapter.base.observe.side_effect = [first, second]

    with pytest.raises(WorkflowError):
        adapter.start_navigation(route, lambda: None, before_tap)

    before_tap.assert_not_called()
    adapter.base.tap.assert_not_called()


def test_native_navigation_scrolls_to_reveal_footer_without_replanning(adapter):
    route = _verified_navigation_route(adapter)
    adapter._scroll_detail = Mock()
    adapter.base.observe.side_effect = [_navigation_detail(with_control=False), _navigation_detail(),
                                        _navigation_detail(), _navigation_detail(state="active")]

    assert adapter.start_navigation(route, lambda: None, Mock())["observation_id"]

    adapter._scroll_detail.assert_called_once()
    adapter.base.tap.assert_called_once()


@pytest.mark.parametrize("variant", ["native", "hidden_panel", "unrelated_title", "unbound_button"])
def test_navigation_deviation_reports_a_verified_amap_prompt_without_tapping_it(adapter, monkeypatch, variant):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    route = _verified_navigation_route(adapter)
    start = _navigation_detail()
    tree = ET.fromstring(start)
    panel = node(rect="[153,1145][1047,1495]", cls="android.widget.LinearLayout",
                 **{"resource-id": ID + "parentPanel", "visible-to-user": "true"})
    top = node(rect="[153,1211][1047,1348]", cls="android.widget.LinearLayout",
               **{"resource-id": ID + "topPanel"})
    top.append(node("您已偏离路线，请重新规划" if variant != "unrelated_title" else "开启定位权限",
                    rect="[282,1211][918,1282]", cls="android.widget.TextView",
                    **{"resource-id": ID + "alertTitle", "clickable": "false"}))
    bottom = node(rect="[153,1349][1047,1495]", cls="android.widget.LinearLayout",
                  **{"resource-id": ID + "buttonPanel"})
    bottom.append(node("否", rect="[153,1349][599,1495]", cls="android.widget.Button",
                       **{"resource-id": ID + "button1"}))
    yes = node("是", rect="[600,1349][1047,1495]", cls="android.widget.Button",
               **{"resource-id": ID + "button2"})
    (tree if variant == "unbound_button" else bottom).append(yes)
    if variant == "hidden_panel":
        panel.set("visible-to-user", "false")
    panel.extend([top, bottom])
    tree.append(panel)
    after = ET.tostring(tree, encoding="unicode")
    adapter.base.observe.side_effect = [start, start, *([after] * 4)]
    before_tap = Mock()

    with pytest.raises(WorkflowError, match="已偏离路线" if variant == "native" else "实时导航界面"):
        adapter.start_navigation(route, lambda: None, before_tap)

    before_tap.assert_called_once()
    adapter.base.tap.assert_called_once()


@pytest.mark.parametrize("postcondition", ["unchanged", "different_footer", "outside_amap", "hidden_end", "different_ride"])
def test_navigation_never_retries_a_command_after_unconfirmed_result(adapter, monkeypatch, postcondition):
    monkeypatch.setattr("device.amap.time.sleep", lambda _: None)
    route = _verified_navigation_route(adapter)
    before_tap = Mock()
    if postcondition == "unchanged":
        after = _navigation_detail()
    elif postcondition == "hidden_end":
        after = _navigation_detail(state="active").replace('content-desc="结束导航"', 'content-desc="结束导航" visible-to-user="false"')
    elif postcondition == "different_ride":
        after = _navigation_detail(state="active", line="8号线万胜围方向")
    else:
        after = _navigation_detail(state="active", footer="[769,2440][1124,2605]")
    start = _navigation_detail()
    adapter.base.observe.side_effect = [start, start, *([after] * 4)]
    if postcondition == "outside_amap":
        adapter.base.tap.side_effect = lambda _, checkpoint: setattr(adapter.base.shell, "return_value", "")

    with pytest.raises(WorkflowError, match="实时导航界面"):
        adapter.start_navigation(route, lambda: None, before_tap)

    adapter.base.tap.assert_called_once()
    before_tap.assert_called_once()
