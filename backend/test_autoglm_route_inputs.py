"""Offline native route-list grounding; fixtures never constitute live proof."""
import xml.etree.ElementTree as ET

import pytest

from autoglm.executor import AutoGLMTransitDevice
from autoglm.protocol import Action
from autoglm.safety import GroundingError, _canonical_route_cards, _query_matches, resolve_action
from device.amap import ID, PACKAGE
from domain import WorkflowError


def native(text="", rect="[0,0][1200,2640]", *, desc="", cls="android.view.ViewGroup", clickable="true", **attrs):
    return ET.Element("node", {"text": text, "content-desc": desc, "class": cls, "bounds": rect,
                               "package": PACKAGE, "clickable": clickable, "enabled": "true",
                               "visible-to-user": "true", **attrs})


def itinerary(top=742, minutes=8, stations=2):
    short = f"{minutes}分钟3号线无堵车风险{stations}站¥2体育西路进站"
    tail = "3号线(海傍方向)即将进站"
    owner = native(desc=short + tail, rect=f"[0,{top}][1200,{top+556}]")
    wrapper = native(rect=f"[26,{top}][1173,{top+529}]")
    summary = native(desc=short, rect=f"[79,{top+53}][1120,{top+330}]")
    for text, y1, y2 in [(f"{minutes}分钟", top+53, top+147), ("3号线", top+163, top+236),
                         (f"无堵车风险{stations}站¥2体育西路进站", top+263, top+329)]:
        group = native(rect=f"[79,{y1}][1120,{y2}]")
        group.append(native(text, f"[79,{y1}][1120,{y2}]", cls="android.view.View", clickable="false"))
        summary.append(group)
    tail_group = native(rect=f"[79,{top+329}][1120,{top+476}]")
    tail_group.extend([native("", f"[79,{top+368}][1120,{top+369}]"),
                       native("3号线(海傍方向)", f"[79,{top+409}][450,{top+475}]", cls="android.view.View", clickable="false"),
                       native("图标", f"[884,{top+420}][927,{top+463}]", cls="android.widget.ImageView", clickable="false"),
                       native("即将进站", f"[933,{top+409}][1119,{top+475}]", cls="android.view.View", clickable="false")])
    wrapper.extend([summary, tail_group]); owner.append(wrapper)
    return owner, summary


def fixture(*, second=False):
    root = native(clickable="false")
    start = native("体育西路(地铁站)", "[256,213][828,275]", desc="起点输入栏", cls="android.widget.EditText",
                   **{"resource-id": ID + "route_edit_summary_start"})
    end = native("广州塔(地铁站)", "[256,312][828,374]", desc="终点输入栏", cls="android.widget.EditText",
                 **{"resource-id": ID + "route_edit_summary_end"})
    preference = native("高德推荐", "[382,544][655,716]")
    preference.append(native("高德推荐", "[418,596][618,664]", cls="android.view.View", clickable="false"))
    card, inner = itinerary()
    root.extend([start, end, native("公共交通", "[200,400][400,500]"), preference, card])
    other = None
    if second:
        other, _ = itinerary(top=1397, minutes=11, stations=4); root.append(other)
    shot = {"nodes": list(root.iter("node")), "epoch": 0, "width": 1200, "height": 2640,
            "screen_width": 1200, "screen_height": 2640, "rotation": 0,
            "current_app": PACKAGE, "current_package": PACKAGE,
            "evidence": {"evidence_id": "offline-list", "query_id": "offline-query", "page_kind": "route_list"}}
    adapter = AutoGLMTransitDevice.__new__(AutoGLMTransitDevice)
    adapter.query_id, adapter.route_id = "offline-query", None
    adapter.request = {"origin": start.get("text"), "destination": end.get("text"), "city": "广州市", "preference": "recommended"}
    adapter.state = {"adapter": adapter, "choices": {}, "filter": "高德推荐", "places": {
        role: {"name": adapter.request[role], "city": "广州市", "evidence_refs": {
            "name": ["offline-prechecked-" + role], "city": ["offline-prechecked-" + role]}}
        for role in ("origin", "destination")}}
    return shot, adapter, root, card, inner, other


def tap(shot, adapter, point):
    return resolve_action(Action("Tap", {"element": point}), shot, adapter.request, adapter.state)


def test_actual_endpoint_text_overrides_role_caption_and_natural_centers_keep_outer_route_owner():
    shot, adapter, _, card, inner, _ = fixture()
    assert all(_query_matches(shot, role, adapter.request, adapter.state) for role in ("origin", "destination"))
    assert set(adapter._route_cards(shot)) == {card, inner}
    assert _canonical_route_cards(shot, adapter) == {card}
    assert tap(shot, adapter, [432, 239]).role == "preference"
    for point in ([500, 386], [500, 354], [500, 440]):
        grounded = tap(shot, adapter, point)
        assert grounded.node is card and grounded.point == (int(point[0]*1.2), int(point[1]*2.64))
        source = adapter._selected_route(shot, grounded)
        assert source["query_id"] == "offline-query" and source["route_id"] == adapter.route_id
        assert source["page_kind"] == "route_list"
    adapter.state["filter"] = ""
    with pytest.raises(WorkflowError, match="路线偏好"):
        adapter._selected_route(shot, tap(shot, adapter, [500, 386]))


def test_correct_content_description_cannot_hide_wrong_actual_endpoint_text():
    shot, adapter, root, _, _, _ = fixture()
    root[0].set("content-desc", adapter.request["origin"])
    root[0].set("text", "另一出发地")
    assert not _query_matches(shot, "origin", adapter.request, adapter.state)
    with pytest.raises(GroundingError): tap(shot, adapter, [500, 386])


def test_adjacent_distinct_itineraries_remain_independent_owners():
    shot, adapter, _, card, _, other = fixture(second=True)
    assert _canonical_route_cards(shot, adapter) == {card, other}
    assert tap(shot, adapter, [500, 386]).node is card
    assert tap(shot, adapter, [500, 634]).node is other


@pytest.mark.parametrize("mutation", ["nested_distinct_scheme", "unknown_nested_action", "later_overlay", "false_aggregate_caption"])
def test_distinct_schemes_unknown_actions_overlays_and_false_captions_cannot_become_summary_aliases(mutation):
    shot, adapter, root, card, _, _ = fixture()
    point = [500, 386]
    if mutation == "nested_distinct_scheme":
        distinct = native(desc="11分钟4号线3站", rect="[79,900][1120,1100]")
        distinct.append(native("11分钟4号线3站", "[79,900][1120,1100]", cls="android.view.View", clickable="false"))
        card[0].append(distinct)
    elif mutation == "unknown_nested_action":
        card.append(native("查看街景", "[400,1120][800,1210]")); point = [500, 440]
    elif mutation == "later_overlay": root.append(native("", "[400,900][800,1100]", cls="android.widget.ImageView"))
    else: card.set("content-desc", card.get("content-desc") + "未在叶节点出现的文字")
    shot["nodes"] = list(root.iter("node"))
    with pytest.raises(GroundingError): tap(shot, adapter, point)
