"""Offline, device-free grounding tests; model-supplied roles grant no rights."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from autoglm.protocol import Action
from autoglm.safety import (GroundingError, allowed_inputs, normalized_point,
                            resolve_action, page_kind, focused_input_context)
from device.amap import AmapDevice, PACKAGE, ID


def node(text="", *, desc="", rect="[100,100][1100,250]", cls="android.view.ViewGroup", **attrs):
    return ET.Element("node", {"package": PACKAGE, "class": cls, "text": text, "content-desc": desc,
                               "bounds": rect, "visible-to-user": "true", "enabled": "true",
                               "clickable": "true", **attrs})


def shot(*nodes, **extras):
    root = ET.Element("hierarchy");root.extend(nodes)
    return {"xml": ET.tostring(root, encoding="unicode"), "nodes": list(root.iter("node")),
            "epoch": 0, "width": 1200, "height": 2400, "screen_width": 1200, "screen_height": 2400,
            "rotation": 0, "current_package": PACKAGE,
            "evidence": {"evidence_id": "current-observation", "observation_id": "current-observation"}, **extras}


@pytest.fixture
def req():
    return {"origin": "广州塔地铁站", "destination": "体育西路地铁站", "city": "广州市",
            "preference": "fewer_transfers", "use_current_location": False}


@pytest.fixture
def state(req):
    base = SimpleNamespace(context_epoch=0, shell=Mock(), tap=Mock(), transport=None)
    adapter = AmapDevice(base, Mock());adapter.request = dict(req)
    return {"adapter": adapter, "places": {}, "choices": {}}


def at(kind, x, y):
    return Action(kind, {"element": [x, y]})


@pytest.mark.parametrize("width,height,rotation,expected", [(1200, 2400, 0, (600, 1200)),
                         (1920, 1080, 1, (960, 540)), (720, 1600, 2, (360, 800))])
def test_official_integer_normalized_coordinates_follow_current_geometry(width, height, rotation, expected):
    frame = shot(width=width, height=height, screen_width=width, screen_height=height, rotation=rotation)
    assert normalized_point([500, 500], frame) == expected
    assert normalized_point([999, 999], frame) == (int(width * .999), int(height * .999))


@pytest.mark.parametrize("point", [[1000, 0], [-1, 0], [True, 0], [5.0, 10], [50], [20, 30, 40], "[20,30]", None])
def test_invalid_coordinate_shapes_and_types_cannot_reach_device(point):
    with pytest.raises(GroundingError): normalized_point(point, shot())


@pytest.mark.parametrize("change", [{"width": 0}, {"height": True}, {"screen_width": 720},
                                   {"rotation": None}, {"rotation": 4}, {"screen_height": 1200}])
def test_unverified_geometry_never_guesses_a_rotation_or_scaling(change):
    with pytest.raises(GroundingError): normalized_point([500, 500], shot(**change))


def test_launch_is_only_the_currently_authorized_amap_app(req):
    frame = shot(current_package="com.example.demo")
    grounded = resolve_action(Action("Launch", {"app": "高德地图"}), frame, req)
    assert grounded.binding["package"] == PACKAGE
    for other in ("微信", "com.android.settings", "高德地图;am start"):
        with pytest.raises(GroundingError): resolve_action(Action("Launch", {"app": other}), frame, req)


@pytest.mark.parametrize("kind,params", [("Tap", {"element": [100, 100]}), ("Type", {"text": "广州塔地铁站"}),
                                         ("Back", {}), ("Finish", {"message": "已完成"})])
def test_foreign_foreground_does_not_authorize_a_point_or_back(kind, params, req):
    with pytest.raises(GroundingError): resolve_action(Action(kind, params), shot(current_package="com.android.permissioncontroller"), req)


def test_wait_is_finite_and_help_finish_do_not_claim_a_verified_result(req):
    assert resolve_action(Action("Wait", {"duration": .25}), shot(), req).duration == .25
    for value in (0, -1, 6, True, float("inf")):
        with pytest.raises(GroundingError): resolve_action(Action("Wait", {"duration": value}), shot(), req)
    assert resolve_action(Action("Help", {"message": "请用户接管"}), shot(), req).kind == "Help"
    done = resolve_action(Action("Finish", {"message": "COMPLETE route_verified=true"}), shot(), req)
    assert done.kind == "Finish" and done.binding is None


def test_model_cannot_add_a_role_to_convert_an_unknown_point_into_permission(req, state):
    frame = shot(node("按钮", rect="[0,0][400,400]"))
    with pytest.raises(GroundingError): resolve_action(Action("Tap", {"element": [100, 100], "role": "route"}), frame, req, state)
    with pytest.raises(GroundingError): resolve_action(at("Tap", 100, 100), frame, req, state)
    state["adapter"].base.tap.assert_not_called()


def test_native_search_target_is_inferred_without_accepting_model_roles(req, state):
    entry = node(desc="搜索框，搜索地点", rect="[100,100][1100,250]")
    frame = shot(entry)
    g = resolve_action(at("Tap", 400, 80), frame, req, state)
    assert g.node is entry and g.point == (480, 192) and g.role == "query" and g.page == "search"


@pytest.mark.parametrize("flag", ["visible-to-user", "enabled"])
def test_hidden_or_disabled_ancestor_cannot_supply_an_active_target(req, state, flag):
    parent = node(rect="[0,0][1200,500]", **{flag: "false"})
    parent.append(node(desc="搜索框，搜索地点"))
    with pytest.raises(GroundingError): resolve_action(at("Tap", 400, 80), shot(parent), req, state)


def test_overlapping_distinct_controls_are_not_resolved_by_area_or_arbitrary_index(req, state):
    frame = shot(node(desc="搜索框，搜索地点"), node("返回"))
    with pytest.raises(GroundingError, match="重叠"): resolve_action(at("Tap", 400, 80), frame, req, state)


def test_parent_child_aliases_may_represent_one_physical_search_control(req, state):
    parent = node(desc="搜索框，搜索地点")
    child = node(desc="搜索框，搜索地点", rect="[200,120][900,220]", clickable="false")
    parent.append(child)
    g = resolve_action(at("Tap", 400, 80), shot(parent), req, state)
    assert g.node is child


def test_actual_search_caption_and_its_literal_editor_child_are_one_control(req, state):
    parent = node(desc="搜索框，搜索地点")
    child = node("搜索地点",rect="[200,120][900,220]",cls="android.widget.EditText")
    parent.append(child)
    assert resolve_action(at("Tap",400,80),shot(parent),req,state).node is child


def test_large_unrelated_container_is_not_an_actionable_search_bar(req, state):
    broad = node(desc="搜索框，搜索地点",rect="[0,100][1200,2100]")
    with pytest.raises(GroundingError): resolve_action(at("Tap",500,500),shot(broad),req,state)


def test_unknown_nested_clickable_entry_is_not_authorized_by_a_large_parent(req, state):
    parent = node(desc="搜索框，搜索地点", rect="[100,100][1100,250]")
    parent.append(node("麦克风", rect="[900,100][1100,250]", cls="android.widget.ImageView"))
    with pytest.raises(GroundingError, match="内嵌"): resolve_action(at("Tap", 850, 80), shot(parent), req, state)


def test_unknown_sibling_overlay_blocks_an_underlying_safe_control(req, state):
    search = node(desc="搜索框，搜索地点", rect="[100,100][1100,250]")
    overlay = node("", rect="[400,100][800,250]", cls="android.widget.ImageView")
    with pytest.raises(GroundingError, match="重叠"): resolve_action(at("Tap",500,80),shot(search,overlay),req,state)


def route_frame(*extra):
    return shot(node("广州塔(地铁站)", rect="[100,100][600,200]", **{"resource-id": ID + "route_edit_summary_start"}),
                node("体育西路(地铁站)", rect="[600,100][1100,200]", **{"resource-id": ID + "route_edit_summary_end"}),
                node("公共交通", rect="[200,250][400,350]"), *extra)


def test_preference_tap_is_limited_to_the_current_request(req, state):
    frame = route_frame(node("换乘少", rect="[100,400][300,500]"), node("步行少", rect="[400,400][600,500]"))
    g = resolve_action(at("Tap", 150, 180), frame, req, state)
    assert g.role == "preference" and g.binding["filter"] == "换乘少"
    with pytest.raises(GroundingError): resolve_action(at("Tap", 420, 180), frame, req, state)


def test_model_can_select_a_real_transit_card_only_with_matching_current_endpoints(req, state):
    card = node(desc="9分钟3号线2站¥2", rect="[0,600][1200,950]")
    frame = route_frame(card)
    g = resolve_action(at("Tap", 400, 300), frame, req, state)
    assert g.node is card and g.binding["route_summary"] == "9分钟3号线2站¥2"
    frame["nodes"][0].set("text", "我的位置")
    with pytest.raises(GroundingError): resolve_action(at("Tap", 400, 300), frame, req, state)


def test_visible_explicit_city_prefix_does_not_change_the_whole_place_identity(req, state):
    card=node(desc="9分钟3号线2站¥2",rect="[0,600][1200,950]")
    frame=route_frame(card);frame['nodes'][0].set('text','广州市广州塔(地铁站)')
    assert resolve_action(at('Tap',400,300),frame,req,state).node is card
    frame['nodes'][0].set('text','广州市广州塔(地铁站)(西门)')
    with pytest.raises(GroundingError):resolve_action(at('Tap',400,300),frame,req,state)


@pytest.mark.parametrize("text", ["开始导航", "付款", "登录", "收藏", "下车提醒"])
def test_sensitive_nested_control_cannot_hide_under_an_allowed_card(req, state, text):
    card = node(desc="9分钟3号线2站¥2", rect="[0,600][1200,950]")
    card.append(node(text, rect="[400,650][800,850]"))
    with pytest.raises(GroundingError): resolve_action(at("Tap", 500, 300), route_frame(card), req, state)


def place_list(req, *, second=False, gate=False):
    name = "广州塔(地铁站)" + ("(西门)" if gate else "")
    row = node(desc=name, rect="[0,600][1200,1250]")
    title = node(desc=name, rect="[300,640][1000,740]", cls="android.view.View", clickable="false")
    row.extend([title, node("广州市海珠区测试路1号", rect="[300,750][1100,820]", cls="android.view.View", clickable="false"),
                node("西南门", rect="[550,950][1000,1100]")])
    listing = node(rect="[0,500][1200,2000]", cls="androidx.recyclerview.widget.RecyclerView", scrollable="true", clickable="false")
    listing.append(row)
    if second:
        twin = deepcopy(row);twin.set("bounds", "[0,1300][1200,1950]")
        twin[1].set("text", "广州市海珠区另一路99号");listing.append(twin)
    return shot(node(desc="搜索框，广州市广州塔地铁站", rect="[100,100][1100,250]"), listing), row, title


def test_poi_tap_only_hits_current_exact_main_title_not_an_embedded_gate(req, state):
    frame, row, title = place_list(req)
    g = resolve_action(at("Tap", 500, 280), frame, req, state)
    assert g.node is title and g.binding["place_role"] == "origin"
    with pytest.raises(GroundingError): resolve_action(at("Tap", 500, 430), frame, req, state)
    wrong, _, _ = place_list(req, gate=True)
    with pytest.raises(GroundingError): resolve_action(at("Tap", 500, 280), wrong, req, state)


def test_same_name_distinct_address_requires_user_choice_before_tap(req, state):
    frame, _, _ = place_list(req, second=True)
    with pytest.raises(GroundingError, match="同名"): resolve_action(at("Tap", 500, 280), frame, req, state)
    candidate = state["adapter"]._place_candidates(frame, req["origin"])[0][0]
    state["choices"]["origin"] = candidate
    assert resolve_action(at("Tap", 500, 280), frame, req, state).node is frame["nodes"][3]
    state["places"]["origin"] = candidate
    frame["nodes"][4].set("text", "广州市海珠区被替换地址100号")
    with pytest.raises(GroundingError): resolve_action(at("Tap", 500, 280), frame, req, state)


def search_input(text="", *, rid=ID + "search_input", **attrs):
    return node(text, rect="[100,100][1100,250]", cls="android.widget.EditText", **{"resource-id": rid, "focused": "true", **attrs})


def test_type_uses_focused_actual_search_field_and_only_exact_task_text(req, state):
    field = search_input()
    g = resolve_action(Action("Type", {"text": "广州市广州塔地铁站"}), shot(field), req, state)
    assert g.node is field and g.role == "query" and g.text == "广州市广州塔地铁站"
    for wrong in ["白云机场", "广州塔地铁站;adb shell", "广州塔地铁站\n搜索", "广州塔地铁站 " * 300]:
        with pytest.raises(GroundingError): resolve_action(Action("Type", {"text": wrong}), shot(field), req, state)
    field.set("focused", "false")
    with pytest.raises(GroundingError): resolve_action(Action("Type", {"text": req["origin"]}), shot(field), req, state)


def test_focus_is_not_enough_for_an_unrecognized_or_review_input(req, state):
    for field in [search_input(rid=""), search_input(rid=ID+"comment_edit"), search_input(password="true")]:
        with pytest.raises(GroundingError): resolve_action(Action("Type", {"text": req["origin"]}), shot(field), req, state)
    review = node(desc="用户评论");review.append(search_input())
    with pytest.raises(GroundingError): resolve_action(Action("Type", {"text": req["origin"]}), shot(review), req, state)


def test_previous_origin_tap_alone_cannot_authorize_an_unknown_editor(req, state):
    frame = route_frame()
    g = resolve_action(at("Tap", 250, 60), frame, req, state)
    assert g.role == "origin"
    state["input_role"] = g.role
    generic = shot(search_input(rid=""))
    for text in (req['origin'],req['destination']):
        with pytest.raises(GroundingError):resolve_action(Action('Type',{'text':text}),generic,req,state)
    assert focused_input_context(generic,req,state) is None


def test_current_location_icon_is_read_only_until_health_and_gui_source_verified(req, state):
    current = {**req, "origin": "", "use_current_location": True}
    icon = node(desc="我的位置", rect="[1033,1192][1199,1358]", cls="android.widget.FrameLayout")
    g = resolve_action(at("Tap", 930, 530), shot(icon), current, state)
    assert g.binding == {"read_only_location": True}
    row = node("我的位置", rect="[39,617][200,672]", cls="android.view.View", clickable="false")
    field = search_input("我的位置")
    frame = shot(field, row)
    with pytest.raises(GroundingError): resolve_action(at("Tap", 100, 265), frame, current, state)
    state["location_verified"] = True
    assert resolve_action(at("Tap", 100, 265), frame, current, state).binding["current_location"]
    with pytest.raises(GroundingError): resolve_action(at("Tap", 930, 530), shot(icon), req, state)


def test_swipe_is_limited_to_an_observed_safe_container_not_any_screen_point(req):
    root = node(desc="公交详情页面", rect="[0,200][1200,2100]", clickable="false")
    g = resolve_action(Action("Swipe", {"start": [800, 750], "end": [800, 300]}), shot(root), req)
    assert g.node is root and g.start == (960, 1800) and g.end == (960, 720) and g.duration == .42
    for params in [{"start": [800, 750], "end": [800, 950]}, {"start": [100, 500], "end": [900, 500]}]:
        with pytest.raises(GroundingError): resolve_action(Action("Swipe", params), shot(root), req)
    with pytest.raises(GroundingError): resolve_action(Action("Swipe", {"start": [800, 750], "end": [800, 300]}), shot(), req)


def test_back_requires_a_known_query_page_and_sensitive_pages_stop(req):
    assert resolve_action(Action("Back", {}), shot(search_input()), req).kind == "Back"
    with pytest.raises(GroundingError): resolve_action(Action("Back", {}), shot(), req)
    with pytest.raises(GroundingError): resolve_action(Action("Back", {}), shot(node("输入验证码")), req)


def ocr_poi_frame():
    title = node(desc="广州塔(地铁站)", rect="[53,339][1147,423]")
    title.append(node("广州塔(地铁站)", rect="[53,339][1147,423]", cls="android.view.View", clickable="false"))
    return shot(title, node("在广州塔附近搜索", rect="[100,500][1100,600]"), node("路线", rect="[0,0][0,0]"),
                ocr_targets=[{"text": "路线", "confidence": .95, "bounds": [950, 2250, 1100, 2330]}])


def test_zero_bounds_poi_footer_can_use_only_current_independent_ocr(req, state):
    frame = ocr_poi_frame()
    g = resolve_action(at("Tap", 850, 950), frame, req, state)
    assert g.node is None and g.point == (1020, 2280) and g.role == "route"
    assert g.binding["ocr_label"] == "路线" and g.binding["poi_name"] == "广州塔(地铁站)"


@pytest.mark.parametrize("change", ["unrecognized_poi", "uncertain", "two_routes", "outside_footer", "wrong_point", "no_ocr"])
def test_ocr_does_not_turn_arbitrary_model_coordinates_into_authority(req, state, change):
    frame = ocr_poi_frame();point = [850, 950]
    if change == "unrecognized_poi":
        for n in frame["nodes"][:2]:
            n.set("text", "另一个地点");n.set("content-desc", "另一个地点")
    elif change == "uncertain": frame["ocr_targets"][0]["confidence"] = .79
    elif change == "two_routes": frame["ocr_targets"].append({"text": "路线", "confidence": .99, "bounds": [700,2250,900,2330]})
    elif change == "outside_footer": frame["ocr_targets"][0]["bounds"] = [950,1250,1100,1330]
    elif change == "wrong_point": point = [700,950]
    else: frame["ocr_targets"] = []
    with pytest.raises(GroundingError): resolve_action(at("Tap", *point), frame, req, state)


def test_ocr_route_entry_preserves_selected_address_binding(req, state):
    frame = ocr_poi_frame()
    state["choices"]["origin"] = {"name": "广州塔(地铁站)", "address": "广州市海珠区正确路1号"}
    frame["nodes"].append(node("广州市海珠区另一路9号", rect="[100,650][1100,750]"))
    with pytest.raises(GroundingError): resolve_action(at("Tap",850,950), frame, req, state)


def test_ocr_label_does_not_authorize_an_unknown_native_overlay(req, state):
    frame = ocr_poi_frame()
    frame["nodes"].append(node("",rect="[950,2250][1100,2330]",cls="android.widget.ImageView"))
    with pytest.raises(GroundingError): resolve_action(at("Tap",850,950),frame,req,state)


def home_transit_tile(*, caption="公交地铁"):
    # Public, minimal hierarchy reconstructed from the actual rejected live
    # screen. No map position, private labels or screenshot is committed.
    services = node(rect="[0,0][1200,2640]", clickable="false", **{"resource-id": ID+"quickservice"})
    scroll = node(rect="[0,1679][1200,2640]", cls="android.widget.ScrollView", clickable="false",
                  **{"resource-id": ID+"home_scroll_view"})
    owner = node(desc=caption,rect="[39,1705][262,1944]")
    icon = node(rect="[47,1705][252,1903]")
    wrapper = node(rect="[50,1705][249,1903]")
    wrapper.append(node(rect="[50,1705][249,1903]",cls="android.widget.ImageView",clickable="false"))
    icon.append(wrapper)
    title = node(caption,rect="[70,1882][231,1943]",cls="android.view.View",clickable="false")
    owner.extend([icon,title]);scroll.append(owner);services.append(scroll)
    return shot(services,height=2640,screen_height=2640),owner,icon


def test_real_home_public_transit_tile_executes_the_model_point_not_a_fixed_route(req,state):
    frame,owner,icon=home_transit_tile()
    grounded=resolve_action(at('Tap',120,691),frame,req,state)
    assert grounded.node is owner and grounded.point==(144,1824)
    assert grounded.role=='transit' and grounded.binding=={'home_transit_entry':True}
    # Another model point on the literal title grounds the same real tile.
    assert resolve_action(at('Tap',120,725),frame,req,state).point==(144,1914)
    assert page_kind(frame)=='unknown'  # Home entry independently recognized.


@pytest.mark.parametrize('mutation',['no_home_ancestry','different_literal_title','nearby_bus','taxi','hidden_owner','unknown_child','sibling_overlay'])
def test_home_entry_support_does_not_authorize_other_services_or_unknown_hits(req,state,mutation):
    frame,owner,icon=home_transit_tile()
    if mutation=='no_home_ancestry':frame['nodes'][0].set('resource-id',ID+'another_container')
    elif mutation=='different_literal_title':owner[-1].set('text','实时公交')
    elif mutation in {'nearby_bus','taxi'}:
        text='实时公交' if mutation=='nearby_bus' else '打车'
        owner.set('content-desc',text);owner[-1].set('text',text)
    elif mutation=='hidden_owner':owner.set('visible-to-user','false')
    elif mutation=='unknown_child':icon[0][0].set('clickable','true')
    else:frame['nodes'].append(node('',rect='[100,1750][200,1900]',cls='android.widget.ImageView'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',120,691),frame,req,state)


def native_route_entry():
    root=node(rect='[0,0][1200,595]',cls='android.widget.LinearLayout',clickable='false',
              **{'resource-id':ID+'route_input_view_container'})
    frame=node(rect='[183,194][1146,393]',**{'resource-id':ID+'route_edit_frame'})
    start=node(desc='我的位置',rect='[183,194][868,294]',cls='android.widget.RelativeLayout',
               **{'resource-id':ID+'route_edit_start'})
    origin=node('我的位置',desc='起点输入栏',rect='[256,213][828,275]',cls='android.widget.EditText',
                focused='false',**{'resource-id':ID+'route_edit_start_text'})
    end=node(rect='[183,294][868,393]',cls='android.widget.RelativeLayout',**{'resource-id':ID+'route_edit_end'})
    destination=node('输入终点（支持跨城路线）',desc='终点输入栏',rect='[256,312][828,374]',cls='android.widget.EditText',
                     focused='false',**{'resource-id':ID+'route_edit_end_text'})
    start.append(origin);end.append(destination);frame.extend([start,end]);root.append(frame)
    root.append(node('公共交通',rect='[586,441][770,503]',cls='android.widget.TextView',clickable='false'))
    return shot(root,height=2640,screen_height=2640),origin,destination


def test_live_route_entry_start_and_end_fields_are_independent_roles(req,state):
    frame,origin,destination=native_route_entry()
    first=resolve_action(at('Tap',286,91),frame,req,state)
    assert first.node is origin and first.role=='origin' and first.point==(343,240)
    second=resolve_action(at('Tap',286,130),frame,req,state)
    assert second.node is destination and second.role=='destination'
    assert resolve_action(at('Tap',550,180),frame,req,state).role=='transit'
    origin.set('focused','true')
    assert resolve_action(Action('Type',{'text':req['origin']}),frame,req,state).role=='origin'
    with pytest.raises(GroundingError):resolve_action(Action('Type',{'text':req['destination']}),frame,req,state)


@pytest.mark.parametrize('mutation',['wrong_caption','no_container','hidden_parent','extra_editor','sibling_overlay'])
def test_native_entry_ids_do_not_bypass_identity_or_overlay_checks(req,state,mutation):
    frame,origin,destination=native_route_entry()
    if mutation=='wrong_caption':origin.set('content-desc','密码输入栏')
    elif mutation=='no_container':frame['nodes'][0].set('resource-id',ID+'different_screen')
    elif mutation=='hidden_parent':frame['nodes'][1].set('visible-to-user','false')
    elif mutation=='extra_editor':
        twin=deepcopy(origin);frame['nodes'][2].append(twin);frame['nodes'].append(twin)
    else:frame['nodes'].append(node('',rect='[300,200][500,300]',cls='android.widget.ImageView'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',286,91),frame,req,state)


def anonymous_route_editor():
    root=node(rect='[0,0][1200,2640]')
    backdrop=node(rect='[0,0][1200,2640]')
    backdrop.extend([node(rect='[0,0][1200,485]'),node(rect='[0,485][1200,2640]')])
    header=node(rect='[0,154][1200,432]')
    header.append(node(desc='返回',rect='[0,160][179,319]'))
    fields=node(rect='[178,154][1199,432]')
    origin=node('体育西路地铁站',rect='[251,194][785,292]',cls='android.widget.EditText',focused='true')
    destination=node('',rect='[251,291][785,389]',cls='android.widget.EditText',focused='false')
    a=node(rect='[251,194][785,292]');a.append(origin)
    b=node(rect='[251,291][785,389]');b.append(destination)
    fields.extend([a,b,node(desc='切换起终点',rect='[1071,259][1137,325]')]);header.append(fields)
    root.extend([backdrop,header])
    return shot(root,height=2640,screen_height=2640),origin,destination,backdrop,header


def test_native_anonymous_query_pair_uses_actual_slot_not_previous_role(req,state):
    frame,origin,destination,backdrop,header=anonymous_route_editor()
    state['input_role']='origin'
    g=resolve_action(at('Tap',499,129),frame,req,state)
    assert g.node is destination and g.role=='destination' and g.point==(598,340)
    assert resolve_action(at('Tap',286,90),frame,req,state).node is origin
    origin.set('focused','false');destination.set('focused','true')
    assert resolve_action(Action('Type',{'text':req['destination']}),frame,req,state).role=='destination'
    with pytest.raises(GroundingError):resolve_action(Action('Type',{'text':req['origin']}),frame,req,state)


@pytest.mark.parametrize('mutation',['late_backdrop','nonempty_backdrop','active_backdrop_child','wrong_partition','missing_switch','three_fields','unknown_overlay'])
def test_anonymous_header_exception_does_not_authorize_unknown_overlays(req,state,mutation):
    frame,origin,destination,backdrop,header=anonymous_route_editor();state['input_role']='origin'
    if mutation=='late_backdrop':frame['nodes'][0].remove(backdrop);frame['nodes'][0].append(backdrop)
    elif mutation=='nonempty_backdrop':backdrop[0].set('text','另一个入口')
    elif mutation=='active_backdrop_child':backdrop[0].append(node('',rect='[300,200][500,300]'))
    elif mutation=='wrong_partition':backdrop[0].set('bounds','[0,0][1200,460]')
    elif mutation=='missing_switch':header[-1][-1].set('content-desc','其他按钮')
    elif mutation=='three_fields':
        third=node('',rect='[251,390][785,420]',cls='android.widget.EditText');header.append(third);frame['nodes'].append(third)
    else:frame['nodes'].append(node('',rect='[300,200][700,400]',cls='android.widget.ImageView'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',499,129),frame,req,state)


def home_poi_search():
    header=node(rect='[0,1491][1200,1733]',cls='android.widget.FrameLayout',clickable='false',**{'resource-id':ID+'slidecontainer_header'})
    outer=node(rect='[0,1491][1200,1733]',cls='android.widget.LinearLayout',clickable='false',**{'resource-id':ID+'maphome_searchbar_container'})
    bar=node(rect='[27,1522][1173,1701]',cls='android.widget.LinearLayout',**{'resource-id':ID+'maphome_searchbar_bg'})
    content=node(rect='[174,1572][908,1654]',cls='android.widget.LinearLayout',clickable='false',**{'resource-id':ID+'layout_searchbar_content'})
    title=node('查找地点、公交、地铁',desc='搜索框，输入文字 查找地点、公交、地铁',rect='[174,1572][774,1654]',
               cls='android.widget.TextView',clickable='false',**{'resource-id':ID+'txt_hotword'})
    content.append(title);bar.extend([content,node(desc='扫一扫',rect='[921,1573][1001,1653]',cls='android.widget.ImageView'),
                                   node('',rect='[1034,1553][1153,1672]',cls='android.widget.RelativeLayout')])
    outer.append(bar);header.append(outer)
    return shot(header,height=2640,screen_height=2640),title


def test_home_native_poi_search_authorizes_content_region_excluding_qr_and_mic(req,state):
    frame,title=home_poi_search()
    g=resolve_action(at('Tap',400,610),frame,req,state)
    assert g.node.get('resource-id')==ID+'layout_searchbar_content'
    assert title in list(g.node) and g.role=='query' and g.binding=={'home_search_entry':True}
    for point in [[800,610],[900,610]]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


@pytest.mark.parametrize('mutation',['wrong_caption','wrong_ancestry','overlay'])
def test_home_search_text_does_not_authorize_unknown_controls(req,state,mutation):
    frame,title=home_poi_search()
    if mutation=='wrong_caption':title.set('content-desc','评论输入框')
    elif mutation=='wrong_ancestry':frame['nodes'][0].set('resource-id',ID+'other_header')
    else:frame['nodes'].append(node('',rect='[400,1572][700,1654]',cls='android.widget.ImageView'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',400,610),frame,req,state)


@pytest.mark.parametrize('text',['查找地点、公交、地铁','体育西路(地铁站)','广州市体育西路地铁站','广州塔(地铁站)',
                               '广东省博物馆','北京市体育西路地铁站','体育西路地铁站D口','今日热门景点'])
def test_home_search_cached_query_opens_only_the_real_search_editor_region(req,state,text):
    frame,title=home_poi_search()
    title.set('text',text);title.set('content-desc','搜索框，输入文字 '+text)
    title.set('bounds','[174,1572][636,1654]')
    grounded=resolve_action(at('Tap',680,610),frame,req,state)
    assert grounded.node.get('resource-id')==ID+'layout_searchbar_content'
    assert grounded.role=='query' and grounded.point==(816,1610)
    assert state['places']=={} and state['choices']=={}


def test_home_search_accepts_current_verified_poi_name_without_reusing_it_as_fact(req,state):
    frame,title=home_poi_search();name='体育中心南站'
    state['places']['destination']={'name':name,'source':'explicit','evidence_refs':{'name':['current-poi']}}
    title.set('text',name);title.set('content-desc','搜索框，输入文字 '+name)
    assert resolve_action(at('Tap',680,610),frame,req,state).role=='query'
    assert state['places']['destination']['evidence_refs']['name']==['current-poi']


@pytest.mark.parametrize('mutation',['caption_disagrees',
                                     'active_title','active_child','inert_ad_child','wrong_owner_class'])
def test_retained_home_search_never_grants_ads_mismatched_captions_or_interactive_children(req,state,mutation):
    frame,title=home_poi_search();text='体育西路(地铁站)'
    title.set('text',text);title.set('content-desc','搜索框，输入文字 '+text)
    content=next(n for n in frame['nodes'] if n.get('resource-id')==ID+'layout_searchbar_content')
    if mutation=='caption_disagrees':title.set('content-desc','搜索框，输入文字 广州塔地铁站')
    elif mutation=='active_title':title.set('clickable','true')
    elif mutation in {'active_child','inert_ad_child'}:
        content.append(node('广告' if mutation=='inert_ad_child' else '',rect='[700,1572][850,1654]',
                            clickable='false' if mutation=='inert_ad_child' else 'true'))
        frame['nodes']=list(frame['nodes'][0].iter('node'))
    elif mutation=='wrong_owner_class':content.set('class','android.view.ViewGroup')
    with pytest.raises(GroundingError):resolve_action(at('Tap',680,610),frame,req,state)


@pytest.mark.parametrize('mutation',['duplicate_bar','duplicate_content','foreign_cover','hidden_parent','wrong_bounds',
                                     'taxi_caption','route_caption','voice_caption'])
def test_generic_cached_search_editor_still_rejects_unproved_or_sensitive_controls(req,state,mutation):
    frame,title=home_poi_search();title.set('text','广东省博物馆');title.set('content-desc','搜索框，输入文字 广东省博物馆')
    content=next(n for n in frame['nodes'] if n.get('resource-id')==ID+'layout_searchbar_content')
    bar=next(n for n in frame['nodes'] if n.get('resource-id')==ID+'maphome_searchbar_bg')
    if mutation=='duplicate_bar':frame['nodes'].append(deepcopy(bar))
    elif mutation=='duplicate_content':frame['nodes'].append(deepcopy(content))
    elif mutation=='foreign_cover':frame['nodes'].append(node('',rect='[400,1572][700,1654]',package='other.app'))
    elif mutation=='hidden_parent':bar.set('visible-to-user','false')
    elif mutation=='wrong_bounds':content.set('bounds','[174,1572][400,1654]')
    else:
        value={'taxi_caption':'打车','route_caption':'路线','voice_caption':'语音搜索'}[mutation]
        title.set('text',value);title.set('content-desc','搜索框，输入文字 '+value)
    with pytest.raises(GroundingError):resolve_action(at('Tap',499,610),frame,req,state)


def test_opening_cached_search_entry_does_not_authorize_the_cached_input_value(req,state):
    frame,title=home_poi_search();title.set('text','广东省博物馆');title.set('content-desc','搜索框，输入文字 广东省博物馆')
    assert resolve_action(at('Tap',499,610),frame,req,state).role=='query'
    assert '广东省博物馆' not in allowed_inputs('query',req,state)
    with pytest.raises(GroundingError) as rejected:
        resolve_action(Action('Type',{'text':'广东省博物馆'}),shot(search_input()),req,state)
    assert rejected.value.code=='input_value_mismatch' and state['places']=={}


def test_focused_context_uses_same_independent_actual_role_as_type(req,state):
    frame,origin,destination,_,_=anonymous_route_editor();state['input_role']='origin'
    origin.set('focused','false');destination.set('focused','true')
    context=focused_input_context(frame,req,state)
    assert context['role']=='destination' and context['current_text']==''
    assert req['destination'] in context['allowed_texts'] and req['origin'] not in context['allowed_texts']
    assert context['already_matches'] is False
    assert context['target']=={'resource_id':'','bounds':[251,291,785,389],
                               'observation_id':'current-observation','context_epoch':0}
    destination.set('text',req['destination'])
    assert focused_input_context(frame,req,state)['already_matches'] is True
    for value in context['allowed_texts']:
        assert resolve_action(Action('Type',{'text':value}),frame,req,state).role=='destination'


@pytest.mark.parametrize('mutation',['foreign','hidden','multiple','no_focus','review','unknown'])
def test_factual_context_does_not_reveal_or_authorize_unrecognized_fields(req,state,mutation):
    field=search_input();frame=shot(field)
    if mutation=='foreign':frame['current_package']='com.example.other'
    elif mutation=='hidden':field.set('visible-to-user','false')
    elif mutation=='multiple':frame['nodes'].append(deepcopy(field))
    elif mutation=='no_focus':field.set('focused','false')
    elif mutation=='review':field.set('resource-id',ID+'review_text')
    else:field.set('resource-id','')
    assert focused_input_context(frame,req,state) is None


def test_only_a_valid_field_value_mismatch_gets_the_input_mismatch_code(req,state):
    frame=shot(search_input())
    with pytest.raises(GroundingError) as caught:
        resolve_action(Action('Type',{'text':'另一个地点'}),frame,req,state)
    assert caught.value.code=='input_value_mismatch'
    for action,screen in [(Action('Type',{'text':'错误\n命令'}),frame),
                          (Action('Type',{'text':req['origin']}),shot(search_input(focused='false'))),
                          (at('Tap',500,500),frame)]:
        with pytest.raises(GroundingError) as rejected:resolve_action(action,screen,req,state)
        assert rejected.value.code==('unrecognized_target' if action.kind=='Tap' else 'grounding_rejected')


def history_poi_search():
    """The recorded home-search history shape, including separate shortcuts."""
    sheet=node(rect='[26,154][1173,1742]')
    backdrop=node(rect='[26,154][1173,1742]')
    header=node(rect='[26,154][1173,313]')
    input_owner=node(rect='[185,154][951,313]')
    placeholder=node('查找地点、公交、地铁',rect='[185,154][951,313]',cls='android.widget.EditText',enabled='false')
    field=node('',rect='[185,154][951,313]',cls='android.widget.EditText',focused='true')
    input_owner.extend([placeholder,field])
    header.extend([node(desc='返回',rect='[39,167][172,300]'),input_owner,
                   node('搜索',rect='[993,154][1155,313]',cls='android.view.View')])
    listing=node(rect='[26,611][1173,1742]',cls='android.support.v7.widget.RecyclerView',clickable='false',scrollable='true')
    caption=node('历史记录',rect='[65,955][251,1021]',cls='android.view.View',clickable='false')
    listing.append(caption)
    row=node(rect='[26,1212][1173,1354]',clickable='false')
    inner=node(rect='[26,1212][1173,1354]')
    foreground=node(rect='[26,1212][1173,1354]')
    owner=node(desc='体育西路(地铁站)',rect='[26,1212][878,1354]')
    group=node(rect='[138,1250][627,1316]')
    title=node('体育西路(地铁站)',rect='[138,1250][492,1316]',cls='android.view.View',clickable='false')
    district=node('天河区',rect='[505,1255][626,1310]',cls='android.view.View',clickable='false')
    group.extend([title,district]);owner.append(group)
    actions=node(rect='[878,1212][1173,1354]')
    taxi=node(desc='打车',rect='[878,1212][1027,1354]')
    taxi.append(node('打车',rect='[919,1294][985,1340]',cls='android.view.View',clickable='false'))
    route=node(desc='路线',rect='[1027,1212][1173,1354]')
    route.append(node('路线',rect='[1057,1294][1123,1340]',cls='android.view.View',clickable='false'))
    actions.extend([taxi,route]);foreground.extend([owner,actions])
    swipe=node(rect='[26,1212][1173,1354]')
    swipe.extend([node('收藏',rect='[729,1212][951,1354]',cls='android.view.View'),
                  node('删除',rect='[951,1212][1173,1354]',cls='android.view.View')])
    inner.extend([foreground,swipe]);row.append(inner);listing.append(row)
    body=node(rect='[26,313][1173,1742]');body.append(listing)
    sheet.extend([backdrop,header,body])
    return shot(sheet,height=2640,screen_height=2640),locals()


def refresh_nodes(frame):
    frame['nodes']=[n for root in frame['nodes'][:1] for n in root.iter('node')]


def test_current_history_poi_owner_opens_detail_without_granting_history_facts(req,state):
    frame,items=history_poi_search();state['input_role']='query'
    grounded=resolve_action(at('Tap',499,481),frame,req,state)
    assert grounded.node is items['owner'] and grounded.point==(598,1269)
    assert grounded.binding=={'history_entry':True,'place_role':'destination','name':'体育西路(地铁站)'}
    assert state['places']=={} and state['choices']=={}
    state['adapter'].base.tap.assert_not_called()


def test_typed_query_cannot_promote_old_history_into_fresh_poi_results(req,state):
    frame,items=history_poi_search();state['input_role']='query'
    items['field'].set('text',req['destination'])
    with pytest.raises(GroundingError):resolve_action(at('Tap',250,481),frame,req,state)
    state['places']['destination']={'name':'体育西路地铁站'}
    with pytest.raises(GroundingError):resolve_action(at('Tap',250,481),frame,req,state)


@pytest.mark.parametrize('related',[True,False])
def test_history_route_shortcut_has_no_generic_search_route_permission(req,state,related):
    frame,items=history_poi_search();state['input_role']='query'
    # Eliminate overlapping wrappers so rejection proves that the shortcut
    # itself has no grant, rather than depending on incidental overlap.
    items['sheet'].remove(items['backdrop']);items['inner'].remove(items['swipe'])
    if not related:
        items['owner'].set('content-desc','无关地点');items['title'].set('text','无关地点')
    refresh_nodes(frame)
    with pytest.raises(GroundingError):resolve_action(at('Tap',915,495),frame,req,state)


@pytest.mark.parametrize('point',[(790,481),(915,495),(850,370)])
def test_history_permission_never_grants_taxi_route_delete_or_management(req,state,point):
    frame,_=history_poi_search();state['input_role']='query'
    with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


@pytest.mark.parametrize('mutation',['no_caption','query_history','gate','wrong_title','active_child','foreign',
                                     'hidden','duplicate','unknown_overlay','late_backdrop','nonempty_backdrop',
                                     'extra_backdrop_child','unknown_swipe_child','selected_address'])
def test_history_title_permission_stays_bound_to_the_observed_unique_control(req,state,mutation):
    frame,items=history_poi_search();state['input_role']='query'
    if mutation=='no_caption':items['caption'].set('text','附近推荐')
    elif mutation=='query_history':items['actions'].clear()
    elif mutation=='gate':
        items['owner'].set('content-desc','体育西路地铁站D口');items['title'].set('text','体育西路地铁站D口')
    elif mutation=='wrong_title':items['title'].set('text','另一个地铁站')
    elif mutation=='active_child':items['district'].set('clickable','true')
    elif mutation=='foreign':items['owner'].set('package','com.example.overlay')
    elif mutation=='hidden':items['title'].set('visible-to-user','false')
    elif mutation=='duplicate':items['listing'].append(deepcopy(items['row']))
    elif mutation=='unknown_overlay':frame['nodes'][0].append(node('',rect='[100,1212][800,1354]',cls='android.widget.ImageView'))
    elif mutation=='late_backdrop':
        items['sheet'].remove(items['backdrop']);items['sheet'].append(items['backdrop'])
    elif mutation=='nonempty_backdrop':items['backdrop'].set('content-desc','其他入口')
    elif mutation=='extra_backdrop_child':items['backdrop'].append(node('',rect='[100,1212][800,1354]'))
    elif mutation=='unknown_swipe_child':items['swipe'][0].set('text','其他操作')
    else:state['choices']['destination']={'name':'体育西路地铁站','address':'已确认的完整地址'}
    refresh_nodes(frame)
    with pytest.raises(GroundingError):resolve_action(at('Tap',499,481),frame,req,state)


def compact_poi_frame(*, zero_route=False):
    card=node(rect='[0,1445][1200,1826]',clickable='false')
    title=node(desc='体育西路(地铁站)',rect='[53,1494][1147,1578]')
    for i,char in enumerate('体育西路(地铁站)'):
        title.append(node(char,rect=f'[{53+i*40},1494][{93+i*40},1578]',cls='android.view.View',clickable='false'))
    title.extend([node('地铁站',rect='[477,1507][613,1563]',cls='android.view.View',clickable='false'),
                  node('最近浏览',rect='[626,1507][790,1563]',cls='android.view.View',clickable='false')])
    address=node('广州市天河区',rect='[53,1766][270,1812]',cls='android.view.View',clickable='false')
    card.extend([title,node('直线15.6公里',rect='[53,1702][292,1753]',cls='android.view.View',clickable='false'),address])
    recommendation=node(rect='[0,1990][1200,2440]',clickable='false')
    recommendation.extend([node('相似服务推荐',rect='[53,2010][400,2080]',cls='android.view.View',clickable='false'),
                           node('另一个地点',rect='[53,2130][500,2210]',cls='android.view.View',clickable='false')])
    listing=node(rect='[0,1443][1200,2640]',cls='androidx.recyclerview.widget.RecyclerView',clickable='false')
    listing.extend([card,recommendation])
    footer=node(rect='[0,2453][1200,2639]',clickable='false')
    for caption,x in [('周边',26),('分享',309),('导航',632)]:
        button=node(rect=f'[{x},2479][{x+238},2612]')
        button.append(node(caption,rect=f'[{x+20},2509][{x+160},2580]',cls='android.view.View',clickable='false'))
        footer.append(button)
    route_owner=node(rect='[897,2479][1133,2612]')
    route=node('路线',rect='[0,0][0,0]' if zero_route else '[962,2509][1068,2580]',cls='android.view.View',clickable='false')
    route_owner.append(route);footer.append(route_owner)
    frame=shot(listing,footer,node(desc='返回',rect='[65,193][145,273]',cls='android.widget.ImageView'),
               node(desc='搜索框，体育西路(地铁站)',rect='[194,154][894,313]',cls='android.view.View'),
               height=2640,screen_height=2640)
    if zero_route:frame['ocr_targets']=[{'text':'路线','confidence':.99,'bounds':[962,2509,1068,2580]}]
    return frame,card,title,address,route


def test_compact_main_poi_is_detail_for_back_and_actual_native_route(req,state):
    frame,_,_,_,route=compact_poi_frame()
    assert page_kind(frame)=='place_detail'
    assert resolve_action(Action('Back',{}),frame,req,state).page=='place_detail'
    grounded=resolve_action(at('Tap',850,965),frame,req,state)
    assert grounded.node is route and grounded.role=='route' and grounded.point==(1020,2547)
    for point in [(100,675),(250,585)]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


def test_compact_main_poi_can_ground_zero_bounds_footer_ocr(req,state):
    frame,_,_,_,_=compact_poi_frame(zero_route=True)
    grounded=resolve_action(at('Tap',850,965),frame,req,state)
    assert grounded.node is None and grounded.binding['poi_name']=='体育西路(地铁站)'
    assert grounded.page=='place_detail' and grounded.point==(1020,2547)


def test_compact_ocr_revalidates_selected_address_with_original_main_header_bounds(req,state):
    frame,_,_,_,_=compact_poi_frame(zero_route=True)
    state['choices']['destination']={'name':'体育西路(地铁站)','address':'广州市天河区'}
    assert resolve_action(at('Tap',850,965),frame,req,state).role=='route'
    state['choices']['destination']['address']='广州市海珠区另一路9号'
    with pytest.raises(GroundingError):resolve_action(at('Tap',850,965),frame,req,state)


@pytest.mark.parametrize('mutation',['no_address','hidden_address','foreign_address','recommendation_address',
                                     'unknown_overlay','unrequested_poi','sensitive'])
def test_compact_ocr_keeps_independent_header_identity_and_overlay_guards(req,state,mutation):
    frame,card,title,address,_=compact_poi_frame(zero_route=True)
    if mutation=='no_address':address.set('text','2327评价')
    elif mutation=='hidden_address':address.set('visible-to-user','false')
    elif mutation=='foreign_address':address.set('package','com.example.overlay')
    elif mutation=='recommendation_address':card.remove(address);frame['nodes'][0][-1].append(address)
    elif mutation=='unknown_overlay':frame['nodes'].append(node('',rect='[950,2500][1100,2590]',cls='android.widget.ImageView'))
    elif mutation=='unrequested_poi':
        title.set('content-desc','另一座车站')
        for child in list(title):title.remove(child)
        title.append(node('另一座车站',rect='[53,1494][500,1578]',cls='android.view.View',clickable='false'))
    else:frame['nodes'].append(node('输入验证码',rect='[53,1000][1100,1200]'))
    if mutation in {'recommendation_address','unrequested_poi'}:
        top=[n for n in frame['nodes'] if n.get('bounds') in {'[0,1443][1200,2640]','[0,2453][1200,2639]',
                                                          '[65,193][145,273]','[194,154][894,313]'}]
        frame['nodes']=[n for root in top for n in root.iter('node')]
    with pytest.raises(GroundingError):resolve_action(at('Tap',850,965),frame,req,state)


@pytest.mark.parametrize('variant,expected_ocr',[('zero_route',True),('native_bounds',False),('no_header_address',False)])
def test_observation_requests_ocr_only_for_a_proven_compact_missing_footer(tmp_path,monkeypatch,req,variant,expected_ocr):
    import struct
    from autoglm.executor import AutoGLMTransitDevice
    frame,_,_,address,_=compact_poi_frame(zero_route=variant!='native_bounds')
    if variant=='no_header_address':address.set('text','2327评价')
    monkeypatch.setattr(AmapDevice,'_observe',lambda *_args,**_kwargs:frame)
    device=AutoGLMTransitDevice.__new__(AutoGLMTransitDevice)
    device.base=SimpleNamespace(context_epoch=0,shell=Mock(return_value=''))
    device.folder=tmp_path;device.state={};device.request=req
    device._check=Mock();device._foreground=Mock(return_value=PACKAGE)
    device._geometry=Mock(return_value=(1200,2640,0))
    device._png=Mock(return_value=b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',1200,2640))
    device._ocr=Mock(return_value={'items':[{'text':'路线','confidence':.99,'bounds':[962,2509,1068,2580]}]})
    observed=AutoGLMTransitDevice._agent_observe(device,lambda:None)
    assert device._ocr.called is expected_ocr
    if expected_ocr:assert observed['ocr_targets'][0]['text']=='路线'


def fixed_footer_frame(*,zero_route=False):
    frame,card,title,address,route=compact_poi_frame(zero_route=zero_route)
    listing,footer,back,search=frame['nodes'][0],None,None,None
    roots=[n for n in frame['nodes'] if n.get('bounds') in {'[0,1443][1200,2640]','[0,2453][1200,2639]',
                                                          '[65,193][145,273]','[194,154][894,313]'}]
    listing,footer,back,search=roots
    for caption,x in [('收藏',168),('打车',451)]:
        button=node(rect=f'[{x},2479][{x+142},2612]')
        button.append(node(caption,rect=f'[{x+20},2509][{x+110},2580]',cls='android.view.View',clickable='false'))
        footer.append(button)
    recommendation=listing[-1];recommendation.set('bounds','[0,1990][1200,2640]')
    covered=node(rect='[900,2500][1100,2600]')
    # A real accessible label behind the fixed footer must not be mistaken
    # for the control actually tapped in front of it.
    covered_label=node('开始导航',rect='[900,2500][1100,2600]',cls='android.view.View',clickable='false')
    covered.append(covered_label);recommendation.append(covered)
    root=node(rect='[0,0][1200,2640]',clickable='false');root.extend(roots)
    frame['nodes']=list(root.iter('node'))
    return frame,locals()


def full_poi_footer_frame():
    frame,items=fixed_footer_frame()
    # Full-page heading, as opposed to the compact preview card. The list
    # deliberately extends behind the later fixed bottom bar.
    import re
    for n in items['card'].iter('node'):
        box=[int(v) for v in re.findall(r'\d+',n.get('bounds'))]
        n.set('bounds',f'[{box[0]},{box[1]-1100}][{box[2]},{box[3]-1100}]')
    items['listing'].set('bounds','[0,0][1200,2640]')
    items['search'].set('content-desc','在体育西路(地铁站)附近搜索')
    frame['nodes']=list(items['root'].iter('node'))
    return frame,items


def test_full_poi_fixed_footer_overlaps_only_earlier_recommendations(req,state):
    frame,items=full_poi_footer_frame()
    grounded=resolve_action(at('Tap',842,961),frame,req,state)
    assert grounded.point==(1010,2537) and grounded.role=='route'
    assert grounded.binding=={'fixed_route_footer':True}


@pytest.mark.parametrize('mutation',['wrong_city','wrong_title','missing_header_search',
                                     'later_list','foreign_overlay','extra_footer_control'])
def test_full_poi_footer_never_hides_unproven_or_foreign_controls(req,state,mutation):
    frame,items=full_poi_footer_frame()
    if mutation=='wrong_city':items['address'].set('text','北京市朝阳区')
    elif mutation=='wrong_title':items['title'].set('content-desc','另一座车站')
    elif mutation=='missing_header_search':items['search'].set('content-desc','搜索')
    elif mutation=='later_list':
        items['root'].remove(items['listing']);items['root'].append(items['listing'])
    elif mutation=='foreign_overlay':items['covered'].set('package','com.example.overlay')
    else:items['footer'].append(node('',rect='[950,2500][1100,2590]'))
    frame['nodes']=list(items['root'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',842,961),frame,req,state)


@pytest.mark.parametrize('zero_route',[False,True])
def test_known_fixed_route_footer_occludes_only_its_earlier_main_poi_list(req,state,zero_route):
    frame,items=fixed_footer_frame(zero_route=zero_route)
    grounded=resolve_action(at('Tap',850,965),frame,req,state)
    assert grounded.role=='route' and grounded.point==(1020,2547)
    expected=None if zero_route else next(n for n in frame['nodes'] if list(n)==[items['route']])
    assert grounded.node is expected
    for point in [(620,965),(420,965),(180,965),(310,965)]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


@pytest.mark.parametrize('mutation',['later_overlay','unknown_same_bbox','wrong_list_source','later_list',
                                     'foreign_overlay','footer_child_overlay','header_wrong','city_wrong',
                                     'selected_address_wrong','bottom_not_fixed','missing_footer_caption'])
def test_fixed_footer_exception_rejects_every_unproven_source_or_overlay(req,state,mutation):
    frame,items=fixed_footer_frame();point=(850,965)
    if mutation=='later_overlay':items['root'].append(node('',rect='[950,2500][1100,2590]',cls='android.widget.ImageView'))
    elif mutation=='unknown_same_bbox':items['root'].insert(1,node('',rect='[0,2453][1200,2639]'))
    elif mutation=='wrong_list_source':
        items['recommendation'].remove(items['covered']);items['root'].insert(1,items['covered'])
    elif mutation=='later_list':
        items['root'].remove(items['listing']);items['root'].append(items['listing'])
    elif mutation=='foreign_overlay':
        items['covered'].set('package','com.example.overlay')
    elif mutation=='footer_child_overlay':items['footer'].append(node('',rect='[950,2500][1100,2590]'))
    elif mutation=='header_wrong':
        items['title'].set('content-desc','另一座车站')
        for child in list(items['title']):items['title'].remove(child)
        items['title'].append(node('另一座车站',rect='[53,1494][500,1578]',cls='android.view.View',clickable='false'))
    elif mutation=='city_wrong':items['address'].set('text','北京市朝阳区')
    elif mutation=='selected_address_wrong':state['choices']['destination']={'name':'体育西路(地铁站)','address':'广州市天河区另一路9号'}
    elif mutation=='bottom_not_fixed':
        import re
        for n in items['footer'].iter('node'):
            a,b,c,d=[int(v) for v in re.findall(r'\d+',n.get('bounds'))]
            n.set('bounds',f'[{a},{b-300}][{c},{d-300}]')
        items['covered'].set('bounds','[900,2200][1100,2300]');items['covered_label'].set('bounds','[900,2200][1100,2300]')
        point=(850,850)
    else:items['footer'][-1][0].set('text','其他入口')
    frame['nodes']=list(items['root'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


def single_query_editor():
    panel=node(rect='[26,154][1173,1742]')
    backdrop=node(rect='[26,154][1173,1742]')
    header=node(rect='[26,154][1173,313]')
    input_owner=node(rect='[185,154][886,313]')
    field=node('体育西路(地铁站)',rect='[185,154][886,313]',cls='android.widget.EditText',focused='true')
    input_owner.append(field)
    back=node(desc='返回',rect='[39,167][172,300]')
    search=node('搜索',desc='搜索',rect='[993,154][1155,313]',cls='android.view.View')
    header.extend([back,input_owner,node(desc='清空',rect='[899,203][959,263]'),search])
    body=node(rect='[26,313][1173,1742]')
    listing=node(rect='[26,352][1173,1728]',cls='android.support.v7.widget.RecyclerView',clickable='false')
    body.append(listing);panel.extend([backdrop,header,body])
    return shot(panel,height=2640,screen_height=2640),locals()


def test_one_field_native_editor_can_refocus_and_use_its_actual_search_control(req,state):
    frame,items=single_query_editor();state['input_role']='query'
    assert resolve_action(at('Tap',329,85),frame,req,state).node is items['field']
    assert resolve_action(at('Tap',880,85),frame,req,state).node is items['search']
    assert resolve_action(Action('Type',{'text':req['origin']}),frame,req,state).role=='query'
    state['adapter'].base.tap.assert_not_called()


@pytest.mark.parametrize('mutation',['later_backdrop','labelled_backdrop','nested_backdrop','wrong_bounds',
                                     'missing_return','missing_search','missing_list','second_field','unknown_overlay'])
def test_single_query_backdrop_alias_cannot_hide_later_or_unknown_controls(req,state,mutation):
    frame,items=single_query_editor();state['input_role']='query'
    if mutation=='later_backdrop':items['panel'].remove(items['backdrop']);items['panel'].append(items['backdrop'])
    elif mutation=='labelled_backdrop':items['backdrop'].set('content-desc','其他入口')
    elif mutation=='nested_backdrop':items['backdrop'].append(node('',rect='[300,180][600,280]'))
    elif mutation=='wrong_bounds':items['backdrop'].set('bounds','[26,154][1173,1700]')
    elif mutation=='missing_return':items['back'].set('content-desc','其他功能')
    elif mutation=='missing_search':items['search'].set('text','查找');items['search'].set('content-desc','查找')
    elif mutation=='missing_list':items['listing'].set('class','android.view.ViewGroup')
    elif mutation=='second_field':items['header'].append(node('',rect='[100,160][170,300]',cls='android.widget.EditText'))
    else:items['panel'].append(node('',rect='[300,180][600,280]',cls='android.widget.ImageView'))
    frame['nodes']=list(items['panel'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',329,85),frame,req,state)


@pytest.mark.parametrize('remembered',[None,'origin','destination'])
@pytest.mark.parametrize('placeholder',['none','disabled','hidden'])
def test_single_query_header_proves_current_query_role_without_any_previous_tap(req,state,remembered,placeholder):
    frame,items=single_query_editor();items['field'].set('text','')
    if remembered is not None:state['input_role']=remembered
    if placeholder!='none':
        items['input_owner'].insert(0,node('体育西路(地铁站)',rect=items['field'].get('bounds'),
                                         cls='android.widget.EditText',enabled='false' if placeholder=='disabled' else 'true',
                                         **{'visible-to-user':'false' if placeholder=='hidden' else 'true'}))
        frame['nodes']=list(items['panel'].iter('node'))
    context=focused_input_context(frame,req,state)
    assert context['role']=='query' and context['current_text']==''
    assert req['origin'] in context['allowed_texts'] and req['destination'] in context['allowed_texts']
    assert resolve_action(at('Tap',329,85),frame,req,state).role=='query'
    for text in (req['origin'],req['destination']):
        assert resolve_action(Action('Type',{'text':text}),frame,req,state).role=='query'


@pytest.mark.parametrize('mutation',['missing_header_return','missing_header_search','missing_body_list',
                                     'generic_without_sheet','password','password_type','sensitive_page',
                                     'foreign_field','foreign_ancestor','hidden_field','hidden_ancestor','second_editor'])
def test_independent_single_query_proof_never_authorizes_unrecognized_or_sensitive_input(req,state,mutation):
    frame,items=single_query_editor()
    if mutation=='missing_header_return':items['back'].set('content-desc','其他功能')
    elif mutation=='missing_header_search':items['search'].set('text','其他功能');items['search'].set('content-desc','其他功能')
    elif mutation=='missing_body_list':items['listing'].set('class','android.view.ViewGroup')
    elif mutation=='generic_without_sheet':items['panel'].remove(items['body'])
    elif mutation=='password':items['field'].set('password','true')
    elif mutation=='password_type':items['field'].set('input-type','textPassword')
    elif mutation=='sensitive_page':items['panel'].append(node('输入验证码',rect='[100,800][1100,1000]'))
    elif mutation=='foreign_field':items['field'].set('package','com.example.other')
    elif mutation=='foreign_ancestor':items['input_owner'].set('package','com.example.other')
    elif mutation=='hidden_field':items['field'].set('visible-to-user','false')
    elif mutation=='hidden_ancestor':items['input_owner'].set('visible-to-user','false')
    else:items['header'].append(node('',rect='[100,160][170,300]',cls='android.widget.EditText'))
    frame['nodes']=list(items['panel'].iter('node'))
    assert focused_input_context(frame,req,state) is None
    with pytest.raises(GroundingError):resolve_action(Action('Type',{'text':req['destination']}),frame,req,state)


def plain_history_editor():
    frame,items=history_poi_search()
    items['listing'].set('class','android.view.ViewGroup')
    items['placeholder'].set('text','体育西路(地铁站)')
    items['listing'].remove(items['caption'])
    history_header=node(rect='[26,916][1173,1035]',clickable='false')
    clear=node('清空',rect='[877,955][1024,1008]',cls='android.view.View')
    manage=node('管理',rect='[1025,955][1172,1008]',cls='android.view.View')
    history_header.extend([items['caption'],clear,manage]);items['listing'].insert(0,history_header)
    roller1=node(rect='[39,329][1159,561]',cls='com.autonavi.minimap.ajx3.widget.view.SpringHorizontalScrollView',
                 clickable='false',scrollable='true')
    roller2=node(rect='[79,637][1173,876]',cls='com.autonavi.minimap.ajx3.widget.view.SpringHorizontalScrollView',
                 clickable='false',scrollable='true')
    items['body'].insert(0,roller1);items['listing'].insert(0,roller2)
    frame['nodes']=list(items['sheet'].iter('node'))
    items.update(history_header=history_header,clear=clear,manage=manage,roller1=roller1,roller2=roller2)
    return frame,items


@pytest.mark.parametrize('placeholder',['disabled','hidden'])
def test_actual_plain_history_body_proves_query_and_readonly_poi_navigation(req,state,placeholder):
    frame,items=plain_history_editor()
    if placeholder=='hidden':items['placeholder'].set('enabled','true');items['placeholder'].set('visible-to-user','false')
    assert focused_input_context(frame,req,state)['role']=='query'
    assert resolve_action(at('Tap',329,85),frame,req,state).role=='query'
    assert resolve_action(Action('Type',{'text':req['origin']}),frame,req,state).role=='query'
    grounded=resolve_action(at('Tap',499,481),frame,req,state)
    assert grounded.node is items['owner'] and grounded.binding['history_entry'] is True
    assert state['places']=={} and state['choices']=={}


@pytest.mark.parametrize('mutation',['no_manage','no_clear','no_history','one_roller','generic_scroll_group',
                                     'not_scrollable','foreign_roller','no_placeholder','nonempty_field'])
def test_plain_group_alone_never_proves_query_or_place_authority(req,state,mutation):
    frame,items=plain_history_editor()
    if mutation=='no_manage':items['manage'].set('text','其他功能')
    elif mutation=='no_clear':items['clear'].set('text','其他功能')
    elif mutation=='no_history':items['caption'].set('text','热门推荐')
    elif mutation=='one_roller':items['body'].remove(items['roller1'])
    elif mutation=='generic_scroll_group':items['roller1'].set('class','android.view.ViewGroup')
    elif mutation=='not_scrollable':items['roller1'].set('scrollable','false')
    elif mutation=='foreign_roller':items['roller1'].set('package','com.example.other')
    elif mutation=='no_placeholder':items['input_owner'].remove(items['placeholder'])
    else:items['field'].set('text',req['destination'])
    frame['nodes']=list(items['sheet'].iter('node'))
    assert focused_input_context(frame,req,state) is None
    with pytest.raises(GroundingError):resolve_action(Action('Type',{'text':req['origin']}),frame,req,state)
    with pytest.raises(GroundingError):resolve_action(at('Tap',499,481),frame,req,state)


def test_plain_history_cannot_become_a_query_result_or_a_route_shortcut(req,state):
    frame,items=plain_history_editor()
    items['sheet'].remove(items['backdrop']);items['inner'].remove(items['swipe'])
    frame['nodes']=list(items['sheet'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',915,495),frame,req,state)
    items['field'].set('text',req['destination'])
    state['places']['destination']={'name':'体育西路地铁站'}
    with pytest.raises(GroundingError):resolve_action(at('Tap',250,481),frame,req,state)


def compact_query_header():
    frame,card,title,address,_=compact_poi_frame()
    listing=frame['nodes'][0]
    footer=next(n for n in frame['nodes'] if n.get('bounds')=='[0,2453][1200,2639]')
    header=node(rect='[26,154][1173,313]')
    back=node(desc='返回',rect='[65,193][145,273]',cls='android.widget.ImageView')
    query=node('体育西路(地铁站)',desc='搜索框，体育西路(地铁站)',rect='[194,154][894,313]',cls='android.view.View')
    trailing=node(rect='[921,154][1173,313]')
    voice_owner=node(rect='[921,154][1027,313]')
    voice=node(desc='语音输入按钮',rect='[921,180][1027,286]',cls='android.widget.ImageView',clickable='false',
               **{'resource-id':ID+'llm_img'})
    voice_owner.append(voice)
    close_owner=node(rect='[1027,154][1173,313]')
    close=node(desc='关闭',rect='[1053,193][1133,273]',cls='android.widget.ImageView',clickable='false')
    close_owner.append(close);trailing.extend([voice_owner,close_owner]);header.extend([back,query,trailing])
    root=node(rect='[0,0][1200,2640]',clickable='false');root.extend([listing,footer,header])
    frame['nodes']=list(root.iter('node'))
    return frame,locals()


def test_compact_task_poi_top_close_is_native_back_and_caption_remains_query(req,state):
    frame,items=compact_query_header()
    grounded=resolve_action(at('Tap',911,85),frame,req,state)
    assert grounded.node is items['close'] and grounded.point==(1093,224) and grounded.role=='back'
    assert grounded.binding=={'compact_poi_close':True}
    assert resolve_action(at('Tap',329,85),frame,req,state).role=='query'
    for point in [(812,85),(860,85)]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


@pytest.mark.parametrize('mutation',['not_a_task_poi','caption_mismatch','missing_back','wrong_voice_id',
                                     'extra_active_child','not_top_header','hidden_close','foreign_close',
                                     'foreign_overlay','unknown_overlay','sensitive_overlay','wrong_address','wrong_city'])
def test_top_close_permission_cannot_apply_to_generic_close_icons_or_wrong_pois(req,state,mutation):
    frame,items=compact_query_header()
    if mutation=='not_a_task_poi':
        items['title'].set('content-desc','另一座车站')
        for child in list(items['title']):items['title'].remove(child)
        items['title'].append(node('另一座车站',rect='[53,1494][500,1578]',cls='android.view.View',clickable='false'))
    elif mutation=='caption_mismatch':items['query'].set('content-desc','搜索框，广州塔(地铁站)')
    elif mutation=='missing_back':items['back'].set('content-desc','其他功能')
    elif mutation=='wrong_voice_id':items['voice'].set('resource-id',ID+'other_icon')
    elif mutation=='extra_active_child':items['header'].append(node('',rect='[1053,193][1133,273]'))
    elif mutation=='not_top_header':items['close'].set('bounds','[1053,700][1133,780]')
    elif mutation=='hidden_close':items['close'].set('visible-to-user','false')
    elif mutation=='foreign_close':items['close'].set('package','com.example.other')
    elif mutation in {'foreign_overlay','unknown_overlay'}:
        cover=node('',rect='[1053,193][1133,273]')
        if mutation=='foreign_overlay':cover.set('package','com.example.other')
        items['root'].append(cover)
    elif mutation=='sensitive_overlay':items['root'].append(node('输入验证码',rect='[100,700][1100,900]'))
    elif mutation=='wrong_address':state['choices']['destination']={'name':'体育西路(地铁站)','address':'广州市天河区另一路9号'}
    else:items['address'].set('text','北京市朝阳区')
    frame['nodes']=list(items['root'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',911,85),frame,req,state)


def station_search_result():
    listing=node(rect='[0,1443][1200,2640]',cls='androidx.recyclerview.widget.RecyclerView',clickable='false')
    row=node(desc='广州塔(地铁站)',rect='[0,1487][1200,1881]')
    content=node(rect='[59,1520][1153,1729]')
    main=node(rect='[59,1520][1021,1729]')
    heading=node(rect='[59,1520][1021,1595]')
    title=node('<font><b>广州塔(地铁站)</b></font>',rect='[59,1520][413,1595]',cls='android.view.View',clickable='false')
    heading.extend([title,node('',rect='[413,1520][426,1595]')])
    lines=node(rect='[59,1601][1021,1656]')
    badges=node(rect='[59,1601][316,1656]')
    badges.extend([node('3号线',rect='[59,1604][165,1650]',cls='android.view.View',clickable='false'),
                   node('APM线',rect='[178,1604][302,1650]',cls='android.view.View',clickable='false')])
    lines.append(badges)
    visits=node(rect='[59,1669][1021,1722]')
    visits.append(node('5000+人去过',rect='[59,1672][294,1722]',cls='android.view.View',clickable='false'))
    main.extend([heading,lines,visits])
    shortcut=node(rect='[1020,1520][1153,1729]')
    distance=node('13.2公里',rect='[1020,1606][1153,1652]',cls='android.view.View',clickable='false')
    shortcut.append(distance);content.extend([main,shortcut]);row.append(content)
    gates=node(rect='[46,1754][1153,1847]')
    for text,left,right in [('A口',46,371),('B口',371,696),('G口',696,1021)]:
        gate=node(rect=f'[{left},1754][{right},1847]')
        gate.append(node(text,rect=f'[{left+80},1760][{left+160},1840]',cls='android.view.View',clickable='false'))
        gates.append(gate)
    row.append(gates);listing.append(row)
    search=node(desc='搜索框，广州塔地铁站',rect='[194,163][907,322]')
    frame=shot(search,listing,height=2640,screen_height=2640)
    return frame,locals()


def test_real_station_search_main_text_region_allows_title_and_inert_blank_hit(req,state):
    frame,items=station_search_result();state['input_role']='query'
    for point,actual in [((499,608),(598,1605)),((200,590),(240,1557))]:
        grounded=resolve_action(at('Tap',*point),frame,req,state)
        assert grounded.node is items['main'] and grounded.point==actual
        assert grounded.binding['candidate']['name']=='广州塔(地铁站)' and grounded.binding['place_main_region'] is True
    for point in [(900,608),(165,682),(440,682),(720,682),(350,590)]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


@pytest.mark.parametrize('mutation',['child_link','blank_child_control','inert_ad_label','wrong_shortcut',
                                     'wrong_query','duplicate_station','gate_result','selected_address','unknown_overlay'])
def test_station_result_region_keeps_query_choice_and_child_control_boundaries(req,state,mutation):
    frame,items=station_search_result();state['input_role']='query'
    if mutation=='child_link':items['main'].append(node('其他入口',rect='[500,1596][700,1640]'))
    elif mutation=='blank_child_control':items['main'].append(node('',rect='[500,1596][700,1640]'))
    elif mutation=='inert_ad_label':items['main'].append(node('广告推荐',rect='[500,1596][700,1640]',cls='android.view.View',clickable='false'))
    elif mutation=='wrong_shortcut':items['distance'].set('text','其他操作')
    elif mutation=='wrong_query':items['search'].set('content-desc','搜索框，北京市广州塔地铁站')
    elif mutation=='duplicate_station':items['listing'].append(deepcopy(items['row']))
    elif mutation=='gate_result':
        items['row'].set('content-desc','广州塔地铁站G口');items['title'].set('text','广州塔地铁站G口')
    elif mutation=='selected_address':state['choices']['origin']={'name':'广州塔(地铁站)','address':'广州市海珠区已确认的地址'}
    else:items['listing'].append(node('',rect='[500,1596][700,1640]',cls='android.widget.ImageView'))
    frame['nodes']=[items['search'],*items['listing'].iter('node')]
    with pytest.raises(GroundingError):resolve_action(at('Tap',499,608),frame,req,state)


def autocomplete_station():
    frame,items=single_query_editor();items['field'].set('text','广州塔地铁站')
    outer=node(rect='[26,352][1173,652]',clickable='false')
    content=node(rect='[52,352][1146,533]')
    owner=node(desc='广州塔(地铁站)',rect='[52,352][881,533]')
    heading=node(rect='[52,378][881,444]')
    title=node('<font>广州塔</font>(地铁站)',rect='[131,378][439,444]',cls='android.view.View',clickable='false')
    browse=node(rect='[452,386][594,436]')
    browse_label=node('浏览过',rect='[452,386][581,436]',cls='android.view.View',clickable='false')
    browse.append(browse_label);heading.extend([title,browse])
    lines=node(rect='[52,451][881,499]')
    lines.extend([node('3号线',rect='[131,451][239,497]',cls='android.view.View',clickable='false'),
                  node('APM线',rect='[251,451][377,497]',cls='android.view.View',clickable='false')])
    owner.extend([heading,lines])
    side=node(rect='[881,352][1146,533]')
    taxi=node(rect='[881,380][987,519]');taxi.append(node('打车',rect='[900,466][966,512]',cls='android.view.View',clickable='false'))
    route=node(desc='路线',rect='[987,380][1146,519]')
    route.append(node('13.2公里',rect='[1000,466][1133,512]',cls='android.view.View',clickable='false'))
    side.extend([taxi,route]);content.extend([owner,side]);outer.append(content)
    gates=node(rect='[132,532][1146,651]')
    gates.extend([node('A口',rect='[132,532][447,625]'),node('B口',rect='[466,532][781,625]')]);outer.append(gates)
    items['listing'].append(outer)
    frame['nodes']=list(items['panel'].iter('node'))
    items.update(outer=outer,owner=owner,title=title,browse_label=browse_label,route=route)
    return frame,items


@pytest.mark.parametrize('badge',['浏览过','刚刚浏览'])
def test_compact_autocomplete_main_owner_uses_only_its_own_verified_query_background(req,state,badge):
    frame,items=autocomplete_station();state['input_role']='query'
    items['browse_label'].set('text',badge)
    for point in [(499,154),(230,154)]:
        grounded=resolve_action(at('Tap',*point),frame,req,state)
        assert grounded.node is items['owner'] and grounded.binding['place_main_region'] is True
        assert grounded.binding['candidate']['name']=='广州塔(地铁站)'
    for point in [(880,154),(780,154),(230,218),(500,218)]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


def autocomplete_with_blank_inline_target():
    frame, items = autocomplete_station()
    parents = {c:n for n in frame['nodes'] for c in n}
    inline = parents[items['browse_label']]
    inline.remove(items['browse_label']);inline.set('bounds','[452,386][880,436]')
    frame['nodes'] = list(items['panel'].iter('node'))
    items['inline'] = inline
    return frame, items


def test_unknown_inline_target_narrows_station_candidate_to_its_full_literal_title(req,state):
    from autoglm.hints import native_control_hints
    frame, items = autocomplete_with_blank_inline_target();state['input_role'] = 'query'
    action = resolve_action(at('Tap',230,156),frame,req,state)
    assert action.node is items['title'] and action.point == (276,411)
    assert action.binding['candidate']['name'] == '广州塔(地铁站)'
    assert action.binding['place_main_region'] is True
    with pytest.raises(GroundingError) as rejected:
        resolve_action(at('Tap',499,157),frame,req,state)
    assert rejected.value.code == 'unrecognized_target'
    hints = [h for h in native_control_hints(frame,req,state) if h['role'] == 'place']
    assert hints == [{'text':'广州塔(地铁站)','role':'place','bounds':[131,378,439,444],
                      'normalized_bounds':[110,144,365,168]}]
    assert not state['places'] and not state['choices']


@pytest.mark.parametrize('mutation',['inline_covers_title','unknown_cover','foreign_cover','duplicate','wrong_address',
                                     'wrong_query','late_background'])
def test_title_only_fallback_keeps_all_identity_address_and_coverage_guards(req,state,mutation):
    from autoglm.hints import native_control_hints
    frame, items = autocomplete_with_blank_inline_target();state['input_role'] = 'query'
    if mutation == 'inline_covers_title':items['inline'].set('bounds','[131,378][439,444]')
    elif mutation in {'unknown_cover','foreign_cover'}:
        cover = node('',rect='[131,378][439,444]')
        if mutation == 'foreign_cover':cover.set('package','other.app');cover.set('clickable','false')
        items['panel'].append(cover)
    elif mutation == 'duplicate':items['listing'].append(deepcopy(items['outer']))
    elif mutation == 'wrong_address':
        state['choices']['origin'] = {'candidate_id':'unconfirmed','name':'广州塔(地铁站)','address':'本人确认的另一路1号'}
    elif mutation == 'wrong_query':items['field'].set('text',req['destination'])
    elif mutation == 'late_background':items['panel'].remove(items['backdrop']);items['panel'].append(items['backdrop'])
    frame['nodes'] = list(items['panel'].iter('node'))
    with pytest.raises(GroundingError) as rejected:
        resolve_action(at('Tap',230,156),frame,req,state)
    if mutation in {'inline_covers_title','unknown_cover','foreign_cover','duplicate','wrong_address','late_background'}:
        assert rejected.value.code == 'grounding_rejected'
    assert not [h for h in native_control_hints(frame,req,state) if h['role'] == 'place']


def autocomplete_with_reserved_heading_slot():
    frame, items = autocomplete_with_blank_inline_target()
    parents = {c:n for n in frame['nodes'] for c in n}
    heading = parents[items['title']]
    heading.remove(items['title']);heading.remove(items['inline'])
    line = node(rect='[131,378][880,444]');line.extend([items['title'],items['inline']])
    wrapper = node(rect='[131,378][880,444]');wrapper.append(line)
    icon_owner = node(rect='[52,378][118,444]');icon_wrap = node(rect='[52,378][118,444]')
    icon = node(rect='[52,378][118,444]',cls='android.widget.ImageView',clickable='false')
    icon_wrap.append(icon);icon_owner.append(icon_wrap);heading.extend([icon_owner,wrapper])
    items.update(heading=heading,line=line,wrapper=wrapper,icon_owner=icon_owner,icon_wrap=icon_wrap,icon=icon)
    frame['nodes'] = list(items['panel'].iter('node'))
    return frame, items


def test_only_exact_native_heading_slot_shares_the_current_primary_poi_control(req,state):
    frame, items = autocomplete_with_reserved_heading_slot();state['input_role']='query'
    for point, expected in [((499,160),(598,422)),((499,157),(598,414)),((230,156),(276,411))]:
        action = resolve_action(at('Tap',*point),frame,req,state)
        assert action.node is items['owner'] and action.point == expected
        assert action.binding['candidate']['name'] == '广州塔(地铁站)' and action.binding['place_main_region'] is True
    for point in [(880,154),(780,154),(230,218),(500,218)]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)
    assert state['places'] == {} and state['choices'] == {}


@pytest.mark.parametrize('kind', ['foreign_visible', 'foreign_hidden', 'same_package'])
def test_hit_descendant_cannot_hide_foreign_ancestor_outside_its_bounds(req,state,kind):
    frame, items = autocomplete_with_reserved_heading_slot();state['input_role']='query'
    # A descendant can protrude beyond a parent's bounds. Its own package may
    # appear to be Amap even though its enclosing surface belongs to another app.
    ancestor=node('',rect='[0,0][1,1]',clickable='false')
    if kind != 'same_package':ancestor.set('package','other.app')
    if kind == 'foreign_hidden':ancestor.set('visible-to-user','false')
    child=node('',rect='[590,380][700,440]',clickable='false')
    ancestor.append(child);items['panel'].append(ancestor)
    frame['nodes']=list(items['panel'].iter('node'))
    if kind == 'foreign_visible':
        with pytest.raises(GroundingError,match='其他应用'):
            resolve_action(at('Tap',499,160),frame,req,state)
    else:
        action=resolve_action(at('Tap',499,160),frame,req,state)
        assert action.node is items['owner'] and action.point == (598,422)


@pytest.mark.parametrize('mutation',['no_wrapper','extra_wrapper','no_icon_wrapper','wrong_icon','icon_not_square',
    'slot_child','slot_id','slot_label','slot_crosses_title','slot_unbalanced','slot_not_remainder','unequal_gap',
    'extra_title_sibling','new_overlay','foreign_overlay','duplicate','wrong_query','wrong_address'])
def test_reserved_slot_template_does_not_authorize_other_blank_controls_or_overlays(req,state,mutation):
    frame, items = autocomplete_with_reserved_heading_slot();state['input_role']='query'
    if mutation == 'no_wrapper':items['heading'].remove(items['wrapper']);items['heading'].append(items['line'])
    elif mutation == 'extra_wrapper':
        extra=node(rect='[131,378][880,444]');items['wrapper'].remove(items['line']);extra.append(items['line']);items['wrapper'].append(extra)
    elif mutation == 'no_icon_wrapper':items['icon_owner'].remove(items['icon_wrap']);items['icon_owner'].append(items['icon'])
    elif mutation == 'wrong_icon':items['icon'].set('content-desc','其他入口')
    elif mutation == 'icon_not_square':items['icon_owner'].set('bounds','[52,378][117,444]')
    elif mutation == 'slot_child':items['inline'].append(node('',rect='[452,386][880,436]',cls='android.widget.ImageView',clickable='false'))
    elif mutation == 'slot_id':items['inline'].set('resource-id',ID+'other_action')
    elif mutation == 'slot_label':items['inline'].set('content-desc','其他入口')
    elif mutation == 'slot_crosses_title':items['inline'].set('bounds','[430,386][880,436]')
    elif mutation == 'slot_unbalanced':items['inline'].set('bounds','[452,384][880,436]')
    elif mutation == 'slot_not_remainder':items['inline'].set('bounds','[452,386][800,436]')
    elif mutation == 'unequal_gap':items['inline'].set('bounds','[460,386][880,436]')
    elif mutation == 'extra_title_sibling':items['line'].append(node('',rect='[600,390][650,430]'))
    elif mutation in {'new_overlay','foreign_overlay'}:
        cover=node('',rect='[590,380][700,440]')
        if mutation == 'foreign_overlay':cover.set('package','other.app')
        items['panel'].append(cover)
    elif mutation == 'duplicate':items['listing'].append(deepcopy(items['outer']))
    elif mutation == 'wrong_query':items['field'].set('text',req['destination'])
    elif mutation == 'wrong_address':state['choices']['origin']={'candidate_id':'other','name':'广州塔(地铁站)','address':'本人已选另一路1号'}
    frame['nodes'] = list(items['panel'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',499,160),frame,req,state)


@pytest.mark.parametrize('badge',['为您推荐','广州市','刚刚浏览 推荐','浏览过的广告','刚刚浏览后可领取'])
def test_autocomplete_badge_whitelist_does_not_swallow_arbitrary_inert_text(req,state,badge):
    frame,items=autocomplete_station();items['browse_label'].set('text',badge)
    with pytest.raises(GroundingError):resolve_action(at('Tap',499,154),frame,req,state)
    assert state['places']=={} and state['choices']=={}


def test_just_viewed_autocomplete_badge_is_not_a_place_or_city_evidence_source(req):
    from autoglm.executor import AutoGLMTransitDevice
    frame,items=autocomplete_station();frame['current_app']=PACKAGE
    items['browse_label'].set('text','刚刚浏览')
    adapter=AutoGLMTransitDevice.__new__(AutoGLMTransitDevice)
    adapter.request=req;adapter.state={'places':{},'choices':{}}
    assert adapter._facts(frame,lambda:None) is None
    assert adapter.state['places']=={} and not adapter.state.get('candidates')


@pytest.mark.parametrize('mutation',['wrong_query','interactive_badge','unknown_child','unknown_overlay',
                                     'foreign_overlay','late_background','gate_name','duplicate','wrong_body'])
def test_autocomplete_region_never_grants_other_queries_children_or_backgrounds(req,state,mutation):
    frame,items=autocomplete_station();state['input_role']='query'
    if mutation=='wrong_query':items['field'].set('text',req['destination'])
    elif mutation=='interactive_badge':items['browse_label'].set('clickable','true')
    elif mutation=='unknown_child':items['owner'].append(node('',rect='[590,378][700,444]'))
    elif mutation in {'unknown_overlay','foreign_overlay'}:
        cover=node('',rect='[590,378][700,444]')
        if mutation=='foreign_overlay':cover.set('package','other.app')
        items['panel'].append(cover)
    elif mutation=='late_background':items['panel'].remove(items['backdrop']);items['panel'].append(items['backdrop'])
    elif mutation=='gate_name':items['owner'].set('content-desc','广州塔地铁站G口');items['title'].set('text','广州塔地铁站G口')
    elif mutation=='duplicate':items['listing'].append(deepcopy(items['outer']))
    else:items['listing'].set('class','android.view.ViewGroup')
    frame['nodes']=list(items['panel'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',499,154),frame,req,state)


def inspected_route_suggestion():
    frame,origin,destination,backdrop,header=anonymous_route_editor()
    origin.set('text','广州市广州塔(地铁站)')
    body=node(rect='[0,431][1200,2640]')
    listing=node(rect='[0,484][1200,2640]',cls='android.support.v7.widget.RecyclerView',clickable='false')
    row=node(rect='[0,714][1200,1247]',clickable='false')
    owner=node(rect='[39,748][971,799]')
    title=node('广州塔(地铁站)',desc='广州塔(地铁站)',rect='[105,748][459,799]',cls='android.view.View',clickable='false')
    owner.append(title);row.append(owner);listing.append(row);body.append(listing)
    root=frame['nodes'][0];root.append(body);frame['nodes']=list(root.iter('node'))
    inspected={'name':'广州塔(地铁站)','city':'广州市','source':'explicit',
               'evidence_refs':{'name':['independent-poi-name'],'city':['independent-poi-city']}}
    return frame,locals()


def test_inspected_route_suggestion_shares_only_the_current_role_header_background(req,state):
    frame,items=inspected_route_suggestion();state['input_role']='origin';state['places']['origin']=items['inspected']
    grounded=resolve_action(at('Tap',235,293),frame,req,state)
    assert grounded.node is items['title'] and grounded.binding['inspected_suggestion'] is True


@pytest.mark.parametrize('mutation',['uninspected','history_source','missing_name_ref','missing_city_ref','wrong_city',
                                     'wrong_focused_role','gate','late_background','labelled_background',
                                     'nested_background','wrong_body','unknown_overlay'])
def test_route_suggestion_background_never_accepts_uninspected_wrong_role_or_gate(req,state,mutation):
    frame,items=inspected_route_suggestion();state['input_role']='origin';state['places']['origin']=items['inspected']
    if mutation=='uninspected':state['places'].clear()
    elif mutation=='history_source':items['inspected']['source']='history'
    elif mutation=='missing_name_ref':items['inspected']['evidence_refs']['name']=[]
    elif mutation=='missing_city_ref':items['inspected']['evidence_refs']['city']=[]
    elif mutation=='wrong_city':items['inspected']['city']='北京市'
    elif mutation=='wrong_focused_role':items['origin'].set('focused','false');items['destination'].set('focused','true')
    elif mutation=='gate':
        items['inspected']['name']='广州塔地铁站G口';items['title'].set('text','广州塔地铁站G口')
        items['title'].set('content-desc','广州塔地铁站G口');items['origin'].set('text','广州市广州塔地铁站G口')
    elif mutation=='late_background':items['root'].remove(items['backdrop']);items['root'].append(items['backdrop'])
    elif mutation=='labelled_background':items['backdrop'].set('text','其他入口')
    elif mutation=='nested_background':items['backdrop'][1].append(node('',rect='[200,748][450,799]'))
    elif mutation=='wrong_body':items['body'].set('bounds','[0,600][1200,2640]')
    else:items['root'].append(node('',rect='[200,748][450,799]',cls='android.widget.ImageView'))
    frame['nodes']=list(items['root'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',235,293),frame,req,state)


def test_foreign_cover_at_actual_tap_point_blocks_but_outside_keyboard_does_not(req,state):
    field=search_input();cover=node('',rect='[100,100][1100,250]',package='other.app')
    with pytest.raises(GroundingError):resolve_action(at('Tap',500,80),shot(field,cover),req,state)
    cover.set('bounds','[0,1400][1200,2400]')
    assert resolve_action(at('Tap',500,80),shot(field,cover),req,state).node is field
    cover.set('bounds','[100,100][1100,250]')
    parent=node(rect='[0,0][1200,2400]',**{'visible-to-user':'false'});parent.append(cover)
    assert resolve_action(at('Tap',500,80),shot(field,parent),req,state).node is field


def test_fixed_footer_route_owner_is_one_native_target_for_label_and_blue_padding(req,state):
    from autoglm.safety import _candidates
    frame,items=fixed_footer_frame()
    owner=next(n for n in frame['nodes'] if list(n)==[items['route']])
    for point,actual in [((904,961),(1084,2537)),((850,965),(1020,2547)),((760,965),(912,2547))]:
        grounded=resolve_action(at('Tap',*point),frame,req,state)
        assert grounded.node is owner and grounded.point==actual
        assert grounded.binding=={'fixed_route_footer':True} and grounded.role=='route'
    native=[c for c in _candidates(frame,req,state) if c.binding and c.binding.get('fixed_route_footer')]
    assert len(native)==1 and native[0].node is owner
    for point in [(960,965),(620,965),(420,965),(310,965),(180,965)]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


@pytest.mark.parametrize('mutation',['unknown_cover','foreign_cover','interactive_child','widened_owner',
                                     'wrong_city','wrong_address','missing_footer_proof'])
def test_blue_padding_permission_requires_the_same_complete_footer_proof(req,state,mutation):
    frame,items=fixed_footer_frame()
    owner=next(n for n in frame['nodes'] if list(n)==[items['route']])
    if mutation in {'unknown_cover','foreign_cover'}:
        cover=node('',rect='[1070,2500][1120,2590]')
        if mutation=='foreign_cover':cover.set('package','other.app')
        items['root'].append(cover)
    elif mutation=='interactive_child':owner.append(node('',rect='[1070,2500][1120,2590]'))
    elif mutation=='widened_owner':owner.set('bounds','[632,2479][1133,2612]')
    elif mutation=='wrong_city':items['address'].set('text','北京市朝阳区')
    elif mutation=='wrong_address':state['choices']['destination']={'name':'体育西路(地铁站)','address':'广州市天河区另一路9号'}
    else:items['footer'][-1][0].set('text','其他功能')
    frame['nodes']=list(items['root'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',904,961),frame,req,state)


def test_zero_bounds_label_does_not_grant_blue_padding_outside_independent_ocr(req,state):
    frame,_=fixed_footer_frame(zero_route=True)
    with pytest.raises(GroundingError):resolve_action(at('Tap',904,961),frame,req,state)


@pytest.mark.parametrize('mismatch',['origin','destination','filter_missing','filter_wrong'])
def test_real_route_card_prerequisites_produce_a_typed_rejection_without_authorization(mismatch):
    from test_autoglm_route_inputs import fixture
    frame,adapter,root,_,_,_=fixture()
    if mismatch=='origin':root[0].set('text','我的位置')
    elif mismatch=='destination':root[1].set('text','另一目的地')
    else:adapter.state['filter']='' if mismatch=='filter_missing' else '步行少'
    with pytest.raises(GroundingError) as rejected:
        resolve_action(at('Tap',500,386),frame,adapter.request,adapter.state)
    assert rejected.value.code=='route_prerequisite_missing'
    assert vars(rejected.value)=={} and adapter.request['origin'] not in str(rejected.value)
    assert adapter.route_id is None


def test_route_prerequisite_correction_keeps_matching_card_and_native_edit_actions_available():
    from test_autoglm_route_inputs import fixture
    frame,adapter,root,card,_,_=fixture()
    assert resolve_action(at('Tap',500,386),frame,adapter.request,adapter.state).node is card
    root[0].set('text','我的位置');adapter.state['filter']=''
    assert resolve_action(at('Tap',500,90),frame,adapter.request,adapter.state).role=='origin'
    assert resolve_action(at('Tap',432,239),frame,adapter.request,adapter.state).role=='preference'


@pytest.mark.parametrize('mutation',['foreign_cover','unknown_cover','sensitive_page','unknown_child',
                                     'invalid_endpoint','missing_endpoint','outside_card'])
def test_unknown_or_covered_route_is_not_relabeled_as_a_retryable_prerequisite(mutation):
    from test_autoglm_route_inputs import fixture
    frame,adapter,root,card,_,_=fixture();root[0].set('text','我的位置');point=(500,386)
    if mutation in {'foreign_cover','unknown_cover'}:
        cover=node('',rect='[400,900][800,1100]',cls='android.widget.ImageView')
        if mutation=='foreign_cover':cover.set('package','other.app')
        root.append(cover)
    elif mutation=='sensitive_page':root.append(node('输入验证码',rect='[100,400][1100,600]'))
    elif mutation=='unknown_child':card.append(node('查看街景',rect='[400,900][800,1100]'))
    elif mutation=='invalid_endpoint':root[0].set('class','android.widget.TextView')
    elif mutation=='missing_endpoint':root.remove(root[1])
    else:point=(500,200)
    frame['nodes']=list(root.iter('node'))
    with pytest.raises(GroundingError) as rejected:
        resolve_action(at('Tap',*point),frame,adapter.request,adapter.state)
    expected='unrecognized_target' if mutation in {'invalid_endpoint','missing_endpoint','outside_card'} else 'grounding_rejected'
    assert rejected.value.code==expected and adapter.route_id is None


def test_error_only_route_structure_probe_never_weakens_normal_caption_authorization():
    from test_autoglm_route_inputs import fixture
    frame,adapter,root,card,_,_=fixture()
    card.set('content-desc',card.get('content-desc')+'仅聚合标题可见的额外文字')
    root[0].set('text','我的位置')
    with pytest.raises(GroundingError) as blocked:
        resolve_action(at('Tap',500,386),frame,adapter.request,adapter.state)
    assert blocked.value.code=='route_prerequisite_missing'
    root[0].set('text',adapter.request['origin'])
    with pytest.raises(GroundingError) as still_unproven:
        resolve_action(at('Tap',500,386),frame,adapter.request,adapter.state)
    assert still_unproven.value.code=='grounding_rejected' and adapter.route_id is None


def task_quick_chip():
    frame,items=plain_history_editor()
    wrapper=node(rect='[79,637][1173,876]',clickable='false')
    grid=node(rect='[79,637][1173,876]')
    upper=node(rect='[79,637][1173,743]');lower=node(rect='[79,769][1173,875]')
    other=node(rect='[686,637][860,743]')
    other.append(node('天河城',rect='[712,664][833,715]',cls='android.view.View',clickable='false'))
    upper.append(other)
    chip=node(rect='[342,769][703,875]')
    chip_title=node('体育西路(地铁站)',rect='[368,796][676,847]',cls='android.view.View',clickable='false')
    chip.append(chip_title)
    favorite=node(rect='[79,769][316,875]')
    favorite.append(node('收藏夹',rect='[168,796][289,847]',cls='android.view.View',clickable='false'))
    unrelated=node(rect='[729,769][1103,875]')
    unrelated.append(node('正佳极地海洋世界',rect='[755,796][1076,847]',cls='android.view.View',clickable='false'))
    lower.extend([favorite,chip,unrelated]);grid.extend([upper,lower]);wrapper.append(grid)
    items['roller2'].append(wrapper)
    frame['nodes']=list(items['sheet'].iter('node'))
    items.update(grid=grid,lower=lower,chip=chip,chip_title=chip_title)
    return frame,items


def test_unique_task_quick_chip_opens_fresh_place_without_reusing_history_or_location_facts(req,state):
    frame,items=task_quick_chip()
    for point in [(429,309),(499,309)]:
        grounded=resolve_action(at('Tap',*point),frame,req,state)
        assert grounded.node is items['chip'] and grounded.role=='place'
        assert grounded.binding['quick_place_entry'] is True
        assert set(grounded.binding)=={'quick_place_entry','row','place_role','name'}
    assert state['places']=={} and state['choices']=={}
    for point in [(150,309),(640,258),(780,309)]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


@pytest.mark.parametrize('mutation',['not_task_whole_name','confirmed_address','known_address','duplicate_chip',
                                     'active_title','extra_child','generic_scroll','not_scrollable','third_row',
                                     'late_backdrop','unknown_overlay','foreign_overlay','nonempty_query'])
def test_quick_grid_navigation_never_grants_other_chips_children_or_unbound_sources(req,state,mutation):
    frame,items=task_quick_chip()
    if mutation=='not_task_whole_name':items['chip_title'].set('text','体育西路地铁站D口')
    elif mutation=='confirmed_address':state['choices']['destination']={'name':'体育西路地铁站','address':'已核对的完整街道地址'}
    elif mutation=='known_address':state['places']['destination']={'name':'体育西路地铁站','address':'已读取的完整街道地址'}
    elif mutation=='duplicate_chip':items['lower'].append(deepcopy(items['chip']))
    elif mutation=='active_title':items['chip_title'].set('clickable','true')
    elif mutation=='extra_child':items['chip'].append(node('',rect='[500,790][650,850]'))
    elif mutation=='generic_scroll':items['roller2'].set('class','android.widget.HorizontalScrollView')
    elif mutation=='not_scrollable':items['roller2'].set('scrollable','false')
    elif mutation=='third_row':items['grid'].append(node('',rect='[79,745][1173,760]'))
    elif mutation=='late_backdrop':items['sheet'].remove(items['backdrop']);items['sheet'].append(items['backdrop'])
    elif mutation in {'unknown_overlay','foreign_overlay'}:
        overlay=node('',rect='[500,790][650,850]')
        if mutation=='foreign_overlay':overlay.set('package','other.app')
        items['sheet'].append(overlay)
    else:items['field'].set('text',req['destination'])
    frame['nodes']=list(items['sheet'].iter('node'))
    with pytest.raises(GroundingError):resolve_action(at('Tap',429,309),frame,req,state)


@pytest.mark.parametrize('caption',['广州塔(地铁站)','广州塔（地铁站）','广州市广州塔(地铁站)'])
def test_canonical_station_search_caption_keeps_result_scoped_to_the_task(req,state,caption):
    frame,items=station_search_result();items['search'].set('content-desc','搜索框，'+caption)
    assert resolve_action(at('Tap',499,608),frame,req,state).node is items['main']


@pytest.mark.parametrize('caption',['广州塔','北京市广州塔(地铁站)','广州塔(地铁站)G口'])
def test_search_caption_canonicalization_does_not_accept_landmark_city_or_gate_aliases(req,state,caption):
    frame,items=station_search_result();items['search'].set('content-desc','搜索框，'+caption)
    with pytest.raises(GroundingError):resolve_action(at('Tap',499,608),frame,req,state)


def test_search_caption_matching_does_not_expand_type_authorization(req,state):
    from perception.search_text import task_search_text_matches
    assert task_search_text_matches('广州塔(地铁站)',req['origin'],req['city'])
    with pytest.raises(GroundingError) as rejected:
        resolve_action(Action('Type',{'text':'广州塔(地铁站)'}),shot(search_input()),req,state)
    assert rejected.value.code=='input_value_mismatch'


@pytest.mark.parametrize('caption,accepted',[('广州塔(地铁站)',True),('广州市广州塔（地铁站）',True),
                                           ('广州塔',False),('北京市广州塔(地铁站)',False)])
def test_factual_candidate_scope_uses_the_same_narrow_search_caption_match(req,caption,accepted):
    from autoglm.executor import AutoGLMTransitDevice
    frame,items=station_search_result();items['search'].set('content-desc','搜索框，'+caption)
    frame['current_app']=PACKAGE
    adapter=AutoGLMTransitDevice.__new__(AutoGLMTransitDevice)
    adapter.request=req;adapter.state={'places':{},'choices':{}}
    assert adapter._facts(frame,lambda:None) is None
    assert ('origin' in adapter.state.get('candidates',{})) is accepted
    assert adapter.state['places']=={}


def test_inert_result_title_or_disabled_old_caption_cannot_create_a_current_search_scope(req):
    from autoglm.executor import AutoGLMTransitDevice
    frame,items=station_search_result();items['search'].set('content-desc','搜索框，广州塔(地铁站)')
    frame['current_app']=PACKAGE
    items['search'].set('enabled','false')
    adapter=AutoGLMTransitDevice.__new__(AutoGLMTransitDevice)
    adapter.request=req;adapter.state={'places':{},'choices':{}}
    assert adapter._facts(frame,lambda:None) is None
    assert not adapter.state.get('candidates') and adapter.state['places']=={}


def submitted_search_toolbar():
    common=node(rect='[26,154][1173,333]')
    shadow_wrapper=node(rect='[26,154][1173,333]',clickable='false')
    shadow=node(rect='[32,160][1166,326]');shadow_wrapper.append(shadow)
    front=node(rect='[26,154][1173,333]')
    back=node(desc='返回',rect='[65,197][171,290]')
    back.append(node(rect='[91,203][171,283]',cls='android.widget.ImageView',clickable='false'))
    search_voice=node(rect='[194,163][1040,322]')
    caption=node(desc='搜索框，体育西路(地铁站)',rect='[194,163][907,322]')
    title=node('体育西路(地铁站)',rect='[220,163][907,322]',cls='android.view.View',clickable='false');caption.append(title)
    voice_owner=node(rect='[923,163][1022,322]')
    voice_linear=node(desc='语音',rect='[923,189][1022,295]',cls='android.widget.LinearLayout')
    voice_container=node(rect='[923,189][1022,295]',cls='android.widget.FrameLayout',clickable='false',
                         **{'resource-id':ID+'llm_img_container'})
    voice=node(desc='语音输入按钮',rect='[923,189][1022,295]',cls='android.widget.ImageView',clickable='false',
               **{'resource-id':ID+'llm_img'})
    voice_container.append(voice);voice_linear.append(voice_container);voice_owner.append(voice_linear)
    search_voice.extend([caption,voice_owner])
    close_owner=node(rect='[1040,163][1133,322]')
    close=node(desc='关闭',rect='[1053,202][1133,282]',cls='android.widget.ImageView');close_owner.append(close)
    front.extend([back,search_voice,close_owner]);common.extend([shadow_wrapper,front])
    root=node(rect='[0,0][1200,2640]',clickable='false');root.append(common)
    return shot(root,height=2640,screen_height=2640),locals()


def test_submitted_search_caption_can_overlap_only_its_exact_prior_native_shadow(req,state):
    frame,items=submitted_search_toolbar()
    grounded=resolve_action(at('Tap',341,90),frame,req,state)
    assert grounded.node is items['caption'] and grounded.role=='query' and grounded.point==(409,237)
    assert state['places']=={} and state['choices']=={}
    for point in [(810,90),(910,90),(100,90)]:
        with pytest.raises(GroundingError):resolve_action(at('Tap',*point),frame,req,state)


@pytest.mark.parametrize('mutation',['late_shadow','labelled_shadow','nested_shadow','no_inset','wrong_shadow_class',
                                     'active_shadow_wrapper','missing_back','caption_mismatch','wrong_voice_id',
                                     'wrong_voice_container','extra_function','new_sibling_overlay','new_later_overlay',
                                     'foreign_cover','hidden_ancestor','foreign_ancestor','unrelated_shadow_parent'])
def test_submitted_query_shadow_proof_does_not_generalize_to_other_containers_or_overlays(req,state,mutation):
    frame,items=submitted_search_toolbar()
    if mutation=='late_shadow':items['common'].remove(items['shadow_wrapper']);items['common'].append(items['shadow_wrapper'])
    elif mutation=='labelled_shadow':items['shadow'].set('text','其他入口')
    elif mutation=='nested_shadow':items['shadow'].append(node('',rect='[300,200][700,280]'))
    elif mutation=='no_inset':items['shadow'].set('bounds','[26,154][1173,333]')
    elif mutation=='wrong_shadow_class':items['shadow_wrapper'].set('class','android.widget.FrameLayout')
    elif mutation=='active_shadow_wrapper':items['shadow_wrapper'].set('clickable','true')
    elif mutation=='missing_back':items['back'].set('content-desc','其他功能')
    elif mutation=='caption_mismatch':items['title'].set('text','广州塔(地铁站)')
    elif mutation=='wrong_voice_id':items['voice'].set('resource-id',ID+'unknown_icon')
    elif mutation=='wrong_voice_container':items['voice_container'].set('resource-id',ID+'unknown_container')
    elif mutation=='extra_function':items['front'].append(node('其他功能',rect='[300,200][700,280]'))
    elif mutation=='new_sibling_overlay':items['common'].insert(0,node('',rect='[32,160][1166,326]'))
    elif mutation=='new_later_overlay':items['root'].append(node('',rect='[300,200][700,280]',cls='android.widget.ImageView'))
    elif mutation=='foreign_cover':items['root'].append(node('',rect='[300,200][700,280]',package='other.app'))
    elif mutation=='hidden_ancestor':items['common'].set('visible-to-user','false')
    elif mutation=='foreign_ancestor':items['common'].set('package','other.app')
    else:items['shadow_wrapper'].remove(items['shadow']);items['root'].append(items['shadow'])
    frame['nodes']=list(items['root'].iter('node'))
    with pytest.raises(GroundingError) as rejected:resolve_action(at('Tap',341,90),frame,req,state)
    if mutation in {'foreign_cover','new_sibling_overlay','new_later_overlay'}:
        assert rejected.value.code=='grounding_rejected'


@pytest.mark.parametrize('make_scene',[single_query_editor,plain_history_editor])
def test_proven_query_scene_can_regain_focus_without_authorizing_unfocused_type(req,state,make_scene):
    frame,items=make_scene();items['field'].set('focused','false')
    assert focused_input_context(frame,req,state) is None
    with pytest.raises(GroundingError):resolve_action(Action('Type',{'text':req['destination']}),frame,req,state)
    assert resolve_action(at('Tap',329,85),frame,req,state).role=='query'
    if make_scene is plain_history_editor:
        assert resolve_action(at('Tap',499,481),frame,req,state).binding['history_entry']
