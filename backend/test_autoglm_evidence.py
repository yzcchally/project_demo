"""Offline native POI evidence regressions; no phone, API or live claims."""
import xml.etree.ElementTree as ET

import pytest

from autoglm.executor import AutoGLMTransitDevice
from device.amap import AmapDevice, PACKAGE
from domain import WorkflowError


def native(text="", rect="[0,0][1200,2640]", *, desc="", cls="android.view.ViewGroup", **attrs):
    return ET.Element("node", {"text": text, "content-desc": desc, "bounds": rect, "class": cls,
                               "package": PACKAGE, "visible-to-user": "true", "enabled": "true",
                               "clickable": "false", **attrs})


def compact_poi_shot(*, address="广州市天河区", footer=True, history=False):
    """Minimal public native structure from the run12 compact main POI card."""
    root = native()
    body = native(rect="[0,1443][1200,2640]", cls="androidx.recyclerview.widget.RecyclerView")
    header = native(rect="[0,1445][1200,1826]")
    title = native(desc="体育西路(地铁站)", rect="[53,1494][1147,1578]", clickable="true")
    for i, char in enumerate("体育西路(地铁站)"):
        title.append(native(char, f"[{53+i*40},1494][{93+i*40},1578]", cls="android.view.View"))
    title.extend([native("地铁站", "[477,1507][613,1563]", cls="android.view.View"),
                  native("最近浏览", "[626,1507][790,1563]", cls="android.view.View")])
    header.append(title)
    if address:
        header.append(native(address, "[53,1766][400,1812]", cls="android.view.View"))
    body.extend([header, native("B口", "[156,1864][222,1919]", cls="android.view.View"),
                 native("相似服务推荐", "[58,2034][335,2097]", cls="android.view.View"),
                 native("广州市海珠区", "[409,2212][900,2265]", cls="android.view.View")])
    root.append(body)
    if footer:
        bar = native(rect="[0,2453][1200,2640]")
        for i, text in enumerate(("周边", "分享", "导航", "路线")):
            bar.append(native(text, f"[{50+i*280},2509][{180+i*280},2580]", cls="android.view.View"))
        root.append(bar)
    if history:
        root.append(native("搜索历史", "[50,400][350,480]", cls="android.widget.TextView"))
    return {"nodes": list(root.iter("node")), "xml": ET.tostring(root, encoding="unicode"),
            "width": 1200, "height": 2640, "screen_width": 1200, "screen_height": 2640,
            "epoch": 0, "rotation": 0, "current_app": PACKAGE, "current_package": PACKAGE,
            "evidence": {"evidence_id": "current-compact-frame", "observation_id": "current-compact-frame",
                         "query_id": "current-query", "page_kind": "screen"}}


def collector():
    # _facts and native extraction are pure. Bypass device/config construction
    # so this evidence regression cannot touch a phone, environment or model.
    adapter = AutoGLMTransitDevice.__new__(AutoGLMTransitDevice)
    adapter.request = {"origin": "体育西路地铁站", "destination": "广州塔地铁站", "city": "广州市"}
    adapter.state = {"places": {}, "choices": {}}
    return adapter


@pytest.mark.parametrize("recent_badge", ["最近浏览", "浏览过", "刚刚浏览"])
def test_compact_main_header_reads_original_native_name_city_address_and_refs(recent_badge):
    shot = compact_poi_shot()
    next(n for n in shot["nodes"] if n.get("text") == "最近浏览").set("text", recent_badge)
    compact = AmapDevice._compact_poi_header(shot)
    assert compact["name"] == "体育西路(地铁站)" and compact["bounds"] == [0, 1445, 1200, 1826]
    assert compact["title_node"].get("bounds") == "[53,1494][1147,1578]"
    assert compact["name"] in AmapDevice._poi_title_names(shot)
    adapter = collector()
    assert adapter._facts(shot, lambda: None) is None
    place = adapter.state["places"]["origin"]
    assert place["name"] == compact["name"] and place["city"] == "广州市"
    assert place["address"] == "广州市天河区"
    assert place["evidence_refs"] == {field: ["current-compact-frame"] for field in ("name", "address", "city")}
    assert shot["evidence"]["page_kind"] == "place_detail"


@pytest.mark.parametrize('badge',['刚刚浏览 推荐','刚刚浏览广州市','为您推荐','浏览过的广告'])
def test_compact_recent_badge_is_exact_and_cannot_supply_title_or_city_facts(badge):
    shot=compact_poi_shot()
    next(n for n in shot['nodes'] if n.get('text')=='最近浏览').set('text',badge)
    assert '体育西路(地铁站)' not in AmapDevice._poi_title_names(shot)
    assert AmapDevice._compact_poi_header(shot) is None
    adapter=collector()
    assert adapter._facts(shot,lambda:None) is None and adapter.state['places']=={}


@pytest.mark.parametrize('branch',['hidden_field','hidden_field_ancestor','foreign_field_ancestor','disabled_field'])
def test_detail_classification_and_evidence_ignore_noncurrent_summary_fields(tmp_path,branch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from device.amap import ID
    root=native()
    marker=native(desc='公交详情页面',rect='[0,600][1200,2640]')
    marker.append(native('当前路线正文','[100,700][900,850]',cls='android.view.View'))
    field=native('不可采纳的旧起点','[256,213][828,275]',cls='android.widget.EditText',
                 **{'resource-id':ID+'route_edit_summary_start'})
    wrapper=native()
    if branch=='hidden_field':field.set('visible-to-user','false')
    elif branch=='hidden_field_ancestor':wrapper.set('visible-to-user','false')
    elif branch=='foreign_field_ancestor':wrapper.set('package','other.app')
    else:field.set('enabled','false')
    wrapper.append(field);root.extend([marker,wrapper])
    xml=ET.tostring(root,encoding='unicode')
    adapter=AmapDevice.__new__(AmapDevice)
    adapter.base=SimpleNamespace(context_epoch=0,observe=Mock(return_value=xml))
    adapter._check=Mock();adapter.folder=tmp_path;adapter.query_id='current-query';adapter.route_id='selected-route';adapter.observations=[]
    shot=adapter._observe(lambda:None,'route_detail')
    assert shot['evidence']['page_kind']=='route_detail'
    assert '当前路线正文' in shot['evidence']['text'] and '不可采纳的旧起点' not in shot['evidence']['text']
    assert '不可采纳的旧起点' in shot['xml']


@pytest.mark.parametrize('branch',['hidden','hidden_ancestor','foreign_ancestor','foreign_marker','disabled'])
def test_noncurrent_detail_marker_cannot_create_route_detail_evidence(tmp_path,branch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    root=native();wrapper=native();marker=native(desc='公交详情页面',rect='[0,600][1200,2640]')
    if branch=='hidden':marker.set('visible-to-user','false')
    elif branch=='hidden_ancestor':wrapper.set('visible-to-user','false')
    elif branch=='foreign_ancestor':wrapper.set('package','other.app')
    elif branch=='foreign_marker':marker.set('package','other.app')
    else:marker.set('enabled','false')
    wrapper.append(marker);root.append(wrapper)
    adapter=AmapDevice.__new__(AmapDevice)
    adapter.base=SimpleNamespace(context_epoch=0,observe=Mock(return_value=ET.tostring(root,encoding='unicode')))
    adapter._check=Mock();adapter.folder=tmp_path;adapter.query_id='current-query';adapter.route_id='selected-route';adapter.observations=[]
    shot=adapter._observe(lambda:None,'route_detail')
    assert shot['evidence']['page_kind']=='screen' and '公交详情页面' not in shot['evidence']['text']


def test_compact_read_does_not_take_address_or_city_from_recommendations():
    shot = compact_poi_shot(address="体育西路1号")
    compact = AmapDevice._compact_poi_header(shot)
    place = collector()._read_place(shot, compact["name"], header_bounds=compact["bounds"])
    assert place["address"] == "体育西路1号" and place["city"] is None
    assert place["evidence_refs"]["city"] == []
    # A positive recommendation address cannot replace a missing main address.
    assert AmapDevice._compact_poi_header(compact_poi_shot(address=None)) is None


@pytest.mark.parametrize("mutation", ["history", "search_results", "hidden_header", "foreign_header", "gate_in_header", "two_titles", "bad_title_badge"])
def test_search_history_recommendations_and_unproved_headers_are_not_compact_details(mutation):
    shot = compact_poi_shot(history=mutation == "history", footer=mutation != "search_results")
    header = next(n for n in shot["nodes"] if n.get("bounds") == "[0,1445][1200,1826]")
    title = header[0]
    if mutation == "hidden_header": header.set("visible-to-user", "false")
    elif mutation == "foreign_header":
        for node in header.iter("node"): node.set("package", "other.app")
    elif mutation == "gate_in_header": header.append(native("B口", "[500,1750][600,1810]", cls="android.view.View"))
    elif mutation == "two_titles":
        second = native(desc="其他地点", rect="[53,1600][1147,1684]")
        second.append(native("其他地点", "[53,1600][500,1684]", cls="android.view.View"))
        header.append(second)
    elif mutation == "bad_title_badge": title[-1].set("text", "另一个地点")
    shot["nodes"] = list(shot["nodes"][0].iter("node"))
    assert AmapDevice._compact_poi_header(shot) is None
    adapter = collector()
    assert adapter._facts(shot, lambda: None) is None and not adapter.state["places"]


def test_compact_read_rejects_arbitrary_shifted_bounds_and_selected_address_mismatch():
    shot = compact_poi_shot()
    adapter = collector()
    with pytest.raises(WorkflowError, match="主卡片无法独立核对"):
        adapter._read_place(shot, "体育西路(地铁站)", header_bounds=[0, 0, 1200, 2640])
    adapter.state["choices"] = {"origin": {"name": "体育西路(地铁站)", "address": "另一个已选地址"}}
    with pytest.raises(WorkflowError, match="用户确认地点不同"):
        adapter._facts(shot, lambda: None)


def autocomplete_shot(names, *, focused=True):
    """Native one-field search editor; rows are suggestions before Search."""
    root = native()
    panel = native(rect="[26,154][1173,1742]")
    header = native(rect="[26,154][1173,313]")
    owner = native(rect="[185,154][886,313]")
    field = native("体育西路地铁站", "[185,154][886,313]", cls="android.widget.EditText",
                   clickable="true", focused=str(focused).lower())
    owner.append(field)
    header.extend([native(desc="返回", rect="[39,167][172,300]", clickable="true"), owner,
                   native(desc="清空", rect="[899,203][959,263]", clickable="true"),
                   native("搜索", "[993,154][1155,313]", desc="搜索", cls="android.view.View", clickable="true")])
    body = native(rect="[26,313][1173,1742]")
    listing = native(rect="[52,339][1146,1742]", cls="android.support.v7.widget.RecyclerView")
    for i, name in enumerate(names):
        top = 367 + 180 * i
        row = native(desc=name, rect=f"[91,{top}][854,{top+133}]", clickable="true")
        row.extend([native(name, f"[163,{top}][800,{top+66}]", cls="android.view.View"),
                    native("广州市天河区", f"[163,{top+69}][800,{top+125}]", cls="android.view.View")])
        listing.append(row)
    body.append(listing); panel.extend([header, body]); root.append(panel)
    return {"nodes": list(root.iter("node")), "xml": ET.tostring(root, encoding="unicode"),
            "width": 1200, "height": 2640, "screen_width": 1200, "screen_height": 2640,
            "rotation": 0, "current_app": PACKAGE, "current_package": PACKAGE,
            "evidence": {"evidence_id": "current-editor", "query_id": "current-query", "page_kind": "screen"}}, panel


@pytest.mark.parametrize("names", [["体育西路地铁站G口"], ["体育西路地铁站"],
                                   ["体育西路地铁站G口", "体育西路地铁站A口"]])
@pytest.mark.parametrize("focused", [True, False])
def test_editor_suggestions_never_become_places_candidates_or_human_choices(names, focused):
    shot, _ = autocomplete_shot(names, focused=focused)
    adapter = collector()
    # These rows genuinely match the generic row reader; merely filtering a
    # gate or requiring an address would miss the actual editor-stage bug.
    assert adapter._place_candidates(shot, adapter.request["origin"])
    assert adapter._facts(shot, lambda: None) is None
    assert adapter.state == {"places": {}, "choices": {}}
    assert shot["evidence"]["page_kind"] == "screen"


def test_submitted_result_list_retains_actual_ambiguous_place_choice():
    shot, panel = autocomplete_shot(["体育西路地铁站G口", "体育西路地铁站A口"])
    panel.remove(panel[0])  # The editable autocomplete header is no longer present.
    panel.insert(0, native(desc="搜索框，体育西路地铁站", rect="[185,154][886,313]", clickable="true"))
    shot["nodes"] = list(shot["nodes"][0].iter("node"))
    shot["xml"] = ET.tostring(shot["nodes"][0], encoding="unicode")
    adapter = collector()
    result = adapter._facts(shot, lambda: None)
    assert result["kind"] == "choice" and result["role"] == "origin"
    assert {candidate["name"] for candidate in result["candidates"]} == {"体育西路地铁站G口", "体育西路地铁站A口"}
    assert not adapter.state["places"]


def screen_info_collector():
    adapter = collector()
    adapter.query_id = "current-query"
    adapter.route_id = None
    adapter.observations = []
    return adapter


def test_current_place_and_noncoordinate_back_are_current_frame_native_facts():
    shot = compact_poi_shot()
    adapter = screen_info_collector()
    adapter._facts(shot, lambda: None)
    info = adapter._model_screen_info(shot)
    assert info["page_kind"] == "place_detail"
    assert info["current_place"] == {"role": "origin", "name": "体育西路(地铁站)", "city": "广州市"}
    assert info["available_navigation_actions"] == ["Back"]
    # Collection does not dispatch or manufacture an executed action.
    assert not hasattr(adapter, "base") and not hasattr(adapter, "history")


@pytest.mark.parametrize("mutation", ["old_name", "old_city", "missing_city", "wrong_city", "foreign",
                                      "other_query", "no_query", "search_page", "ambiguous_roles"])
def test_current_place_never_uses_cached_incomplete_foreign_or_other_query_facts(mutation):
    shot = compact_poi_shot()
    adapter = screen_info_collector()
    adapter._facts(shot, lambda: None)
    place = adapter.state["places"]["origin"]
    if mutation == "old_name": place["evidence_refs"]["name"] = ["old-frame"]
    elif mutation == "old_city": place["evidence_refs"]["city"] = ["old-frame"]
    elif mutation == "missing_city": place["city"] = None
    elif mutation == "wrong_city": place["city"] = "北京市"
    elif mutation == "foreign": shot["current_app"] = "other.app"
    elif mutation == "other_query": shot["evidence"]["query_id"] = "old-query"
    elif mutation == "no_query": shot["evidence"].pop("query_id")
    elif mutation == "search_page": shot = autocomplete_shot(["体育西路地铁站"])[0]
    elif mutation == "ambiguous_roles": adapter.state["places"]["destination"] = dict(place)
    info = adapter._model_screen_info(shot)
    assert info["current_place"] is None
    assert info["available_navigation_actions"] == []
