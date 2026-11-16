"""Only the current native transit preference strip permits horizontal Swipe."""
from copy import deepcopy

import pytest

from autoglm.protocol import Action
from autoglm.safety import GroundingError, preference_scroll_context, preference_swipe_geometry, resolve_action
from autoglm.hints import _normalized_bounds
from device.amap import ID
from test_autoglm_safety import node, shot


def preference_bar_frame():
    req = {"origin": "体育西路地铁站", "destination": "广州塔地铁站", "city": "广州市",
           "preference": "fewer_transfers", "use_current_location": False}
    origin = node("体育西路(地铁站)", desc="起点输入栏", rect="[256,213][828,275]", cls="android.widget.EditText",
                  **{"resource-id": ID+"route_edit_summary_start"})
    destination = node("广州塔(地铁站)", desc="终点输入栏", rect="[256,312][828,374]", cls="android.widget.EditText",
                       **{"resource-id": ID+"route_edit_summary_end"})
    public = node("公共交通", rect="[586,441][770,503]", cls="android.widget.TextView", clickable="false",
                  selected="true", **{"resource-id": ID+"route_input_conbine_text"})
    mode_bg = node(rect="[458,426][803,519]", cls="android.widget.LinearLayout", clickable="false",
                   selected="true", **{"resource-id": ID+"route_input_conbine_bg"});mode_bg.append(public)
    mode = node(rect="[431,426][830,545]", cls="android.widget.FrameLayout", clickable="false",
                selected="true", **{"resource-id": ID+"rl_tab_container"});mode.append(mode_bg)
    rect = "[366,544][1200,716]"
    bar = node(rect=rect, cls="com.autonavi.minimap.ajx3.widget.view.SpringHorizontalScrollView",
               clickable="false", scrollable="true")
    wrap = node(rect=rect, clickable="false");items = node(rect=rect);wrap.append(items);bar.append(wrap)
    owners = []
    for name, bounds_, title_rect in [
        ("高德推荐", "[382,544][655,716]", "[418,596][618,664]"),
        ("地铁优先", "[655,544][900,716]", "[691,600][863,660]"),
        ("步行少", "[900,544][1102,716]", "[936,600][1065,660]"),
        ("换乘少", "[1103,544][1200,716]", "[1139,600][1200,660]"),
    ]:
        owner = node(desc=name, rect=bounds_)
        owner.append(node(name, rect=title_rect, cls="android.view.View", clickable="false"))
        owners.append(owner);items.append(owner)
    underline = node(rect="[468,669][567,679]");owners[0].append(underline)
    for name, bounds_ in [("时间短", "[1305,544][1200,716]"), ("不坐地铁", "[1507,544][1200,716]")]:
        items.append(node(desc=name, rect=bounds_, **{"visible-to-user": "false"}))
    bar_owner = node(rect=rect);bar_owner.append(bar)
    depart = node(rect="[0,544][364,716]")
    depart.append(node("现在出发", desc="现在出发", rect="[74,600][246,660]", cls="android.view.View", clickable="false"))
    depart.append(node(rect="[263,616][290,643]", cls="android.widget.ImageView", clickable="false"))
    horizontal = node(rect="[0,567][1200,569]");vertical = node(rect="[364,590][366,670]")
    row = node(rect="[0,544][1200,716]");row.extend([depart, horizontal, vertical, bar_owner])
    shell = node(rect="[0,544][1200,716]");shell.append(row)
    outer = node(rect="[0,544][1200,716]")
    background = node(rect="[0,544][1200,716]", cls="android.widget.ImageView", clickable="false")
    outer.extend([background, shell])
    frame = shot(origin, destination, mode, outer, height=2640, screen_height=2640)
    return frame, req, {"bar": bar, "wrap": wrap, "items": items, "owners": owners,
                        "row": row, "outer": outer, "background": background, "underline": underline,
                        "origin": origin, "destination": destination, "public": public, "mode": mode,
                        "mode_bg": mode_bg, "bar_owner": bar_owner, "horizontal": horizontal, "vertical": vertical}


def swipe(start=(915, 240), end=(415, 240)):
    return Action("Swipe", {"start": list(start), "end": list(end)})


def test_actual_preference_strip_is_a_fact_and_preserves_the_proposed_gesture():
    frame, req, nodes = preference_bar_frame()
    context = preference_scroll_context(frame, req, {})
    assert context == {"bounds": [366, 544, 1200, 716], "gesture_bounds": [0, 544, 1200, 716], "axis": "horizontal",
                       "role": "preference_scroll", "page": "route_list",
                       "labels": ["高德推荐", "地铁优先", "步行少", "换乘少"]}
    action = resolve_action(swipe(), frame, req, {})
    assert action.node is nodes["bar"]
    assert (action.start, action.end) == ((1098, 633), (498, 633))
    assert action.role == "preference_scroll" and action.page == "route_list"
    assert action.binding == {"preference_bar": True, "bounds": context["bounds"],
                              "gesture_bounds": context["gesture_bounds"]}
    reverse = resolve_action(swipe((415, 240), (915, 240)), frame, req, {})
    assert (reverse.start, reverse.end) == (action.end, action.start)
    assert not {"start", "end", "center", "action"}.intersection(context)


def test_verified_drag_can_end_left_of_the_child_scroller_inside_its_same_native_row():
    frame, req, ns = preference_bar_frame()
    action = resolve_action(swipe((780, 236), (195, 236)), frame, req, {})
    assert action.node is ns["bar"] and action.role == "preference_scroll"
    assert (action.start, action.end) == ((936, 623), (234, 623))
    assert action.binding["bounds"] == [366, 544, 1200, 716]
    assert action.binding["gesture_bounds"] == [0, 544, 1200, 716]


def test_model_interior_ranges_are_the_same_geometry_as_actual_swipe_grounding():
    frame, req, _ = preference_bar_frame()
    context = preference_scroll_context(frame, req, {})
    start, end, min_distance = preference_swipe_geometry(context['bounds'], context['gesture_bounds'])
    a = _normalized_bounds(start, 1200, 2640)
    b = _normalized_bounds(end, 1200, 2640)
    assert a == [332,216,973,262] and b == [27,216,973,262]
    assert min_distance == pytest.approx(125.1)
    for y in (a[1],a[3]):
        # Leftward drag from either vertical boundary stays within the same
        # grounded native row and comfortably exceeds minimum distance.
        assert resolve_action(swipe((a[2],y),(b[0],y)),frame,req,{}).role == 'preference_scroll'
    for start_point,end_point in [((a[0]-1,240),(b[0],240)),((a[2]+1,240),(b[0],240)),
                                  ((a[2],a[1]-1),(b[0],a[1]-1)),((a[2],a[3]+1),(b[0],a[3]+1))]:
        with pytest.raises(GroundingError): resolve_action(swipe(start_point,end_point),frame,req,{})


@pytest.mark.parametrize("mutation", ["generic_scroll", "not_scrollable", "clickable_bar", "hidden_bar", "foreign_bar",
    "wrong_origin", "wrong_destination", "false_public", "public_unknown_owner", "duplicate_public", "wrong_caption",
    "no_mode", "unknown_chip", "reordered_chip", "active_title", "new_child", "hidden_owner", "unknown_underline",
    "generic_outer", "missing_depart", "wrong_depart", "unknown_separator", "duplicate_bar", "malformed_input"])
def test_unproven_strip_cannot_become_an_arbitrary_horizontal_scroll_region(mutation):
    frame, req, ns = preference_bar_frame()
    if mutation == "generic_scroll":ns["bar"].set("class", "android.widget.HorizontalScrollView")
    elif mutation == "not_scrollable":ns["bar"].set("scrollable", "false")
    elif mutation == "clickable_bar":ns["bar"].set("clickable", "true")
    elif mutation == "hidden_bar":ns["bar"].set("visible-to-user", "false")
    elif mutation == "foreign_bar":ns["bar"].set("package", "other.app")
    elif mutation == "wrong_origin":ns["origin"].set("text", "我的位置")
    elif mutation == "wrong_destination":ns["destination"].set("text", "其他站")
    elif mutation == "false_public":ns["public"].set("selected", "false")
    elif mutation == "public_unknown_owner":ns["mode_bg"].set("resource-id", "")
    elif mutation == "duplicate_public":frame["nodes"].append(deepcopy(ns["public"]))
    elif mutation == "wrong_caption":ns["origin"].set("content-desc", "任意输入框")
    elif mutation == "no_mode":frame["nodes"].remove(ns["public"])
    elif mutation == "unknown_chip":ns["owners"][0].set("content-desc", "购买车票")
    elif mutation == "reordered_chip":ns["items"].remove(ns["owners"][0]);ns["items"].append(ns["owners"][0])
    elif mutation == "active_title":list(ns["owners"][0])[0].set("clickable", "true")
    elif mutation == "new_child":ns["owners"][1].append(node("其他操作", rect="[700,600][800,650]"))
    elif mutation == "hidden_owner":ns["bar_owner"].set("visible-to-user", "false")
    elif mutation == "unknown_underline":ns["underline"].set("bounds", "[400,600][630,700]")
    elif mutation == "generic_outer":ns["outer"].set("class", "android.widget.FrameLayout")
    elif mutation == "missing_depart":ns["row"].remove(list(ns["row"])[0])
    elif mutation == "wrong_depart":list(list(ns["row"])[0])[0].set("text", "预约出发");list(list(ns["row"])[0])[0].set("content-desc", "预约出发")
    elif mutation == "unknown_separator":ns["horizontal"].set("content-desc", "未知控件")
    elif mutation == "duplicate_bar":
        extra = deepcopy(ns["outer"]);frame["nodes"].extend(extra.iter("node"))
    elif mutation == "malformed_input":ns["origin"].set("class", "android.view.View")
    assert preference_scroll_context(frame, req, {}) is None
    with pytest.raises(GroundingError):resolve_action(swipe(), frame, req, {})


@pytest.mark.parametrize("start,end", [((990,240),(415,240)), ((915,210),(415,210)), ((915,270),(415,270)),
    ((195,236),(780,236)), ((780,236),(0,236)), ((780,236),(195,210)), ((780,236),(195,270)),
    ((915,240),(880,240)), ((915,235),(415,249)), ((915,240),(415,500)),
    ((915,500),(415,500)), ((915,240),(1000,240))])
def test_horizontal_swipe_stays_clear_of_bar_edges_and_other_native_regions(start, end):
    frame, req, _ = preference_bar_frame()
    with pytest.raises(GroundingError):resolve_action(swipe(start, end), frame, req, {})


@pytest.mark.parametrize("foreign,inert", [(False,False),(False,True),(True,False),(True,True)])
def test_a_cover_in_the_middle_of_the_path_is_rejected_even_when_endpoints_are_clear(foreign, inert):
    frame, req, _ = preference_bar_frame()
    cover = node(rect="[700,610][800,650]", clickable="false" if inert else "true")
    if foreign:cover.set("package", "other.app")
    frame["nodes"].append(cover)
    with pytest.raises(GroundingError):resolve_action(swipe(), frame, req, {})


def test_a_cover_outside_the_gesture_is_not_a_reason_to_move_the_proposed_path():
    frame, req, _ = preference_bar_frame()
    frame["nodes"].append(node(rect="[700,900][800,950]", package="other.app"))
    assert resolve_action(swipe(), frame, req, {}).role == "preference_scroll"


@pytest.mark.parametrize("foreign,inert", [(False,False),(False,True),(True,False),(True,True)])
def test_drag_leaving_child_bar_still_checks_its_entire_path_over_the_left_native_row(foreign, inert):
    frame, req, _ = preference_bar_frame()
    cover = node(rect="[280,610][330,650]", clickable="false" if inert else "true")
    if foreign:cover.set("package", "other.app")
    frame["nodes"].append(cover)
    with pytest.raises(GroundingError):resolve_action(swipe((780,236),(195,236)), frame, req, {})


@pytest.mark.parametrize("mutation", ["not_native_id", "foreign_ancestor"])
def test_known_transparent_layer_names_cannot_hide_unknown_or_foreign_covers(mutation):
    frame, req, _ = preference_bar_frame()
    cover = node(rect="[700,610][800,650]", cls="android.widget.FrameLayout", clickable="false",
                 **{"resource-id": "map_container" if mutation == "not_native_id" else ID+"map_container"})
    if mutation == "foreign_ancestor":
        parent = node(rect="[0,0][0,0]", package="other.app");parent.append(cover)
        frame["nodes"].append(parent)
    frame["nodes"].append(cover)
    with pytest.raises(GroundingError):resolve_action(swipe(), frame, req, {})
