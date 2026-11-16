"""Independently ground AutoGLM proposals in the current Amap observation.

No role, application, coordinate or text claimed by a model grants authority.
This module is pure: it neither observes nor operates a device. The caller must
discard a proposal across cancellation/context changes and re-ground it against
a fresh, unchanged screenshot/tree immediately before dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from domain import WorkflowError
from device.amap import AmapDevice, PACKAGE, ID, bounds, label, place_key
from perception.search_text import task_search_text_matches


class GroundingError(WorkflowError):
    """Proposal cannot be proven to belong to this query; no action is issued."""
    code = "grounding_rejected"


class UnrecognizedTarget(GroundingError):
    """No independently known control at the proposed point; never dispatch."""
    code = "unrecognized_target"


class InputValueMismatch(GroundingError):
    """A real permitted field is known, but this proposed value is unauthorized."""
    code = "input_value_mismatch"


class LoadingOverlayActive(GroundingError):
    """A verified loading modal permits only wait, back or its native cancel."""
    code = "loading_overlay_active"


class RoutePrerequisiteMissing(GroundingError):
    """A proven route card is blocked by current endpoint/filter prerequisites."""
    code = "route_prerequisite_missing"


@dataclass(frozen=True)
class GroundedAction:
    kind: str
    node: Any = None
    point: tuple[int, int] | None = None
    role: str = ""
    page: str = "unknown"
    text: str = ""
    start: tuple[int, int] | None = None
    end: tuple[int, int] | None = None
    duration: float = 0.0
    binding: dict | None = None


@dataclass(frozen=True)
class _Candidate:
    node: Any
    role: str
    page: str
    binding: dict | None = None


_FORBIDDEN = re.compile(r"开始导航|开启导航|开始出发|开始步行|下车提醒|打车|网约车|顺风车|支付|付款|购票|购买|订单|登录|登陆|验证码|密码|授权|同意|允许|删除|收藏|分享|发送|上传|录音|语音搜索")
_SENSITIVE_PAGE = re.compile(r"^(?:隐私政策|个人信息保护|权限申请|申请权限|登录验证|身份验证|输入验证码|输入密码|确认支付|支付订单)")
_TRANSIT = {"公交", "公交地铁", "公交/地铁", "公共交通"}
_PREFERENCES = {
    "recommended": {"高德推荐", "推荐"},
    "fewer_transfers": {"换乘少", "少换乘"},
    "less_walking": {"步行少", "少步行"},
}
_ALL_PREFERENCES = set().union(*_PREFERENCES.values(), {"时间短", "地铁优先", "不坐地铁"})
_DETAILS = {"路线详情", "展开详情", "查看详情", "全部详情", "展开"}
_SEARCH_LABELS = {"搜索", "搜索地点", "搜索地点、公交、地铁", "搜索地点、公交、路线", "搜索目的地", "搜索起点"}
_BACK = {"返回", "返回上一页", "后退"}
_ENTRY_FIELDS = {
    ID + "route_edit_start": ("origin", "起点输入栏", "owner"),
    ID + "route_edit_start_text": ("origin", "起点输入栏", "edit"),
    ID + "route_edit_end": ("destination", "终点输入栏", "owner"),
    ID + "route_edit_end_text": ("destination", "终点输入栏", "edit"),
}


def _geometry(shot):
    w, h = shot.get("width"), shot.get("height")
    sw, sh = shot.get("screen_width"), shot.get("screen_height")
    rotation = shot.get("rotation")
    if (any(type(n) is not int or not 1 <= n <= 16384 for n in (w, h, sw, sh))
            or (w, h) != (sw, sh) or type(rotation) is not int or rotation not in range(4)):
        raise GroundingError("AutoGLM 截图尺寸、屏幕方向或设备坐标不能一致核对，未执行")
    # Rotation identifies an orientation, not a request to guess a transform.
    # Capture and device dimensions must already be in that current orientation.
    return w, h


def normalized_point(value, shot):
    """Official AutoGLM 0..999 integer coordinates, scaled by value / 1000."""
    w, h = _geometry(shot)
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(type(n) is not int or not 0 <= n < 1000 for n in value)):
        raise GroundingError("AutoGLM 坐标无效，未执行")
    return int(value[0] * w / 1000), int(value[1] * h / 1000)


def _inside(point, rect):
    return rect is not None and rect[0] <= point[0] < rect[2] and rect[1] <= point[1] < rect[3]


def _contained(inner, outer):
    return (inner is not None and outer is not None and outer[0] <= inner[0] < inner[2] <= outer[2]
            and outer[1] <= inner[1] < inner[3] <= outer[3])


def _visible(shot):
    w, h = _geometry(shot)
    nodes = list(shot.get("nodes", []))
    parents = {child: parent for parent in nodes for child in parent}
    def allowed(node):
        if node.get("package") != PACKAGE or not _contained(bounds(node), [0, 0, w, h]):
            return False
        current = node
        while current is not None:
            if current.get("visible-to-user") == "false" or current.get("enabled") == "false":
                return False
            if current.get("package", PACKAGE) != PACKAGE:
                return False
            current = parents.get(current)
        return True
    return [node for node in nodes if allowed(node)]


def _compact_poi(shot, visible=None):
    """Validate the compact header without losing its zero-bounds footer label.

    Only the literal native route label may supplement current positive-bounds
    nodes, beneath an independently visible native parent. It supplies page
    semantics, never a hit area; OCR still supplies and revalidates that area.
    """
    ns = list(_visible(shot) if visible is None else visible)
    positive = set(ns)
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    for n in shot["nodes"]:
        if (label(n) == "路线" and n.get("bounds") == "[0,0][0,0]"
                and n.get("package") == PACKAGE and n.get("visible-to-user") != "false"
                and n.get("enabled") != "false" and parents.get(n) in positive):
            ns.append(n)
    return AmapDevice._compact_poi_header({**shot, "nodes": ns})


def page_kind(shot):
    """Classify from actual native controls, never the model's page or role."""
    ns = _visible(shot)
    if any(_SENSITIVE_PAGE.search(label(n)) for n in ns):
        return "sensitive"
    fields = {n.get("resource-id") for n in ns}
    if any(label(n) == "公交详情页面" for n in ns) and not fields.intersection({ID + "route_edit_summary_start", ID + "route_edit_summary_end"}):
        return "route_detail"
    if fields.intersection({ID + "route_edit_summary_start", ID + "route_edit_summary_end"}):
        return "route_list" if any(label(n) in _TRANSIT | _ALL_PREFERENCES for n in ns) else "route_input"
    if any(_native_route_field_role(n, shot) for n in ns):
        return "route_input"
    if _anonymous_route_header(shot) is not None:
        return "route_input"
    if any("附近搜索" in label(n) for n in ns):
        return "place_detail"
    if _compact_poi(shot, ns) is not None:
        return "place_detail"
    if any(re.fullmatch(r"您当前位置是在.+附近", label(n)) for n in ns):
        return "location"
    if any("EditText" in n.get("class", "") or label(n).startswith("搜索框") for n in ns):
        return "search"
    return "unknown"


def _role(node, state):
    rid, text = node.get("resource-id", ""), label(node)
    if rid == ID + "route_edit_summary_start":
        return "origin"
    if rid == ID + "route_edit_summary_end":
        return "destination"
    if re.fullmatch(r"(?:请输入|搜索)?(?:起点|出发地|从哪出发|从哪里出发)", text):
        return "origin"
    if re.fullmatch(r"(?:请输入|搜索)?(?:终点|目的地|到哪去|到哪里去)", text):
        return "destination"
    remembered = state.get("input_role")
    return remembered if remembered in {"origin", "destination", "query"} else "query"


def _native_route_field_role(node, shot):
    """The distinct native start/end editors on Amap's route-entry screen."""
    identity = _ENTRY_FIELDS.get(node.get("resource-id"))
    if identity is None:
        return None
    role, caption, kind = identity
    if node.get("class") != ("android.widget.EditText" if kind == "edit" else "android.widget.RelativeLayout"):
        return None
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    ancestors = set();current = parents.get(node)
    while current is not None:
        ancestors.add(current.get("resource-id"));current = parents.get(current)
    if not {ID + "route_input_view_container", ID + "route_edit_frame"} <= ancestors:
        return None
    visible = set(_visible(shot))
    if node not in visible:
        return None
    stem = "start" if role == "origin" else "end"
    if kind == "edit":
        owner = parents.get(node)
        if (node.get("content-desc") != caption or owner is None
                or owner.get("resource-id") != ID + "route_edit_" + stem
                or not _contained(bounds(node), bounds(owner))):
            return None
    else:
        editors = [n for n in node if n in visible and n.get("class") == "android.widget.EditText"
                   and n.get("resource-id") == ID + "route_edit_" + stem + "_text"
                   and n.get("content-desc") == caption and _contained(bounds(n), bounds(node))]
        if len(editors) != 1:
            return None
    return role


def _anonymous_route_header(shot):
    """The observed route search editor has two stacked anonymous input slots.

    Its native structure includes exactly one focused editable slot, the other
    enabled slot, a back control and a switch-start/end control in one compact
    header. Position within this independently identified pair determines role;
    the model's role and the previously focused field do not determine it.
    """
    visible = _visible(shot)
    editors = [n for n in visible if n.get("class") == "android.widget.EditText" and not n.get("resource-id")
               and bounds(n)[1] < 450 and 30 <= bounds(n)[3] - bounds(n)[1] <= 200]
    if len(editors) != 2 or sum(n.get("focused") == "true" for n in editors) != 1:
        return None
    editors.sort(key=lambda n: bounds(n)[1])
    top, bottom = (bounds(n) for n in editors)
    if (abs(top[0]-bottom[0]) > 2 or abs(top[2]-bottom[2]) > 2
            or not top[1] < bottom[1] or not -2 <= bottom[1]-top[3] <= 80):
        return None
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    panel = parents.get(editors[0])
    visible_set = set(visible)
    while panel is not None:
        rect = bounds(panel)
        members = set(panel.iter("node")) & visible_set
        if (editors[1] in members and rect and rect[1] < 450 and rect[3] <= shot["height"] * .3
                and rect[2]-rect[0] >= shot["width"] * .8
                and sum(label(n) == "返回" for n in members) == 1
                and sum(label(n) == "切换起终点" for n in members) == 1):
            return panel, {editors[0]: "origin", editors[1]: "destination"}
        panel = parents.get(panel)
    return None


def _anonymous_field_role(node, shot):
    if node.get("class") != "android.widget.EditText" or node.get("resource-id"):
        return None
    header = _anonymous_route_header(shot)
    return header[1].get(node) if header is not None else None


def _query_backdrop(owner, node, shot):
    """Recognize only the live editor's empty, earlier two-part backdrop.

    Amap exposes this background as clickable sibling nodes. The exact empty
    vertical partition and its earlier native branch distinguish it from an
    unknown overlaid control; this exception grants no action to the backdrop.
    """
    header = _anonymous_route_header(shot)
    if header is None or owner not in header[1]:
        return False
    panel = header[0]
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    def branch_under(child, parent):
        while child is not None and parents.get(child) is not parent:
            child = parents.get(child)
        return child
    common = parents.get(panel)
    while common is not None:
        backdrop, foreground = branch_under(node, common), branch_under(panel, common)
        if backdrop is not None and foreground is not None and backdrop is not foreground:
            children = list(common)
            if children.index(backdrop) >= children.index(foreground):
                return False
            rows = list(backdrop)
            if bounds(backdrop) != [0, 0, shot["width"], shot["height"]] or len(rows) != 2:
                return False
            a, b = (bounds(row) for row in rows)
            parts = [backdrop, *rows]
            if (a is None or b is None or a != [0,0,shot["width"],a[3]]
                    or b != [0,a[3],shot["width"],shot["height"]]
                    or not bounds(panel)[3] <= a[3] <= bounds(panel)[3] + 64
                    or node not in parts or any(list(row) for row in rows)):
                return False
            return all(n.get("class") == "android.view.ViewGroup" and n.get("clickable") == "true"
                       and not label(n) and not n.get("resource-id") and n in _visible(shot) for n in parts)
        common = parents.get(common)
    return False


def _result_query_backdrop(candidate, node, shot, request, state):
    """A proven result may share its own query editor's earlier background.

    This grants nothing to an uninspected route suggestion, another role's
    result, a gate, or an unknown overlay. The original background predicates
    still establish the exact native shape and its earlier sibling ordering.
    """
    binding = candidate.binding or {}
    role = binding.get("place_role")
    if candidate.role != "place" or role not in {"origin", "destination"}:
        return False
    visible = set(_visible(shot))
    if binding.get("quick_place_entry"):
        context = _single_query_header(shot)
        return bool(context is not None and context["field"].get("text", "") == ""
                    and candidate.node is binding.get("row")
                    and candidate.node in set(context["body"].iter("node"))
                    and _single_query_backdrop(context["field"], node, shot, state))
    if binding.get("place_main_region"):
        context = _single_query_header(shot)
        return bool(context is not None and binding.get("row") in set(context["body"].iter("node"))
                    and any(task_search_text_matches(context["field"].get("text"), value, request.get("city", ""))
                            for value in allowed_inputs(role, request, state))
                    and _single_query_backdrop(context["field"], node, shot, state))
    if not binding.get("inspected_suggestion"):
        return False
    inspected = state.get("places", {}).get(role) or {}
    refs = inspected.get("evidence_refs", {})
    if (inspected.get("source") != "explicit" or not refs.get("name")
            or not inspected.get("name") or candidate.node not in visible
            or place_key(label(candidate.node)) != place_key(inspected["name"])
            or re.search(r"(?:[A-Z]口|出入口|入口|出口|(?:东|南|西|北)门)$", label(candidate.node))
            or request.get("city") and (inspected.get("city") != request["city"] or not refs.get("city"))):
        return False
    selected = state.get("choices", {}).get(role) or {}
    if (selected.get("address") and selected["address"] != inspected.get("address")
            or selected.get("name") and not _same_place_name(selected["name"], inspected["name"], request.get("city", ""))):
        return False
    header = _anonymous_route_header(shot)
    if header is None:
        return False
    focused = [field for field, actual_role in header[1].items()
               if actual_role == role and field.get("focused") == "true"]
    if len(focused) != 1 or not any(task_search_text_matches(focused[0].get("text"), value, request.get("city", ""))
                                    for value in allowed_inputs(role, request, state)):
        return False
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    listing = parents.get(candidate.node)
    while listing is not None and "RecyclerView" not in listing.get("class", ""):
        listing = parents.get(listing)
    if listing not in visible:
        return False
    common = parents.get(header[0])
    while common is not None and listing not in set(common.iter("node")):
        common = parents.get(common)
    if common is None:
        return False
    def branch(child):
        while child is not None and parents.get(child) is not common:
            child = parents.get(child)
        return child
    source, front, backdrop = branch(listing), branch(header[0]), branch(node)
    if source is None or front is None or backdrop is None or len({source, front, backdrop}) != 3:
        return False
    order = list(common)
    if (order.index(backdrop) >= order.index(front) or order.index(front) >= order.index(source)
            or source not in visible or source.get("class") != "android.view.ViewGroup" or label(source)
            or bounds(source)[0] != 0 or bounds(source)[2:] != [shot["width"], shot["height"]]
            or not -2 <= bounds(source)[1]-bounds(header[0])[3] <= 64):
        return False
    return _query_backdrop(focused[0], node, shot)


def _plain_query_history(body, shot, visible):
    """Amap's initial history body sometimes exposes a plain native group.

    Literal History/Clear/Manage controls and both known horizontal category
    rows distinguish it from an arbitrary ViewGroup. This returns a container,
    never a place, address, city or action target.
    """
    members = set(body.iter("node")) & visible
    captions = [n for n in members if label(n) == "历史记录" and not list(n)
                and n.get("class") == "android.view.View" and n.get("clickable") == "false"]
    rollers = [n for n in members if n.get("class") == "com.autonavi.minimap.ajx3.widget.view.SpringHorizontalScrollView"
               and n.get("scrollable") == "true" and _contained(bounds(n), bounds(body))]
    if len(captions) != 1 or len(rollers) != 2:
        return None
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    row = parents.get(captions[0])
    while row is not None and row is not body:
        rect = bounds(row)
        leaves = [n for n in row.iter("node") if n in visible and not list(n) and label(n)]
        if (row in visible and row.get("class") == "android.view.ViewGroup" and not label(row)
                and rect[2]-rect[0] >= shot["width"] * .85 and rect[3]-rect[1] <= 160
                and len(leaves) == 3 and {label(n) for n in leaves} == {"历史记录", "清空", "管理"}
                and all(n.get("class") == "android.view.View" and n.get("clickable") ==
                        ("false" if label(n) == "历史记录" else "true") for n in leaves)):
            listing = parents.get(row)
            if (listing in visible and listing.get("class") == "android.view.ViewGroup"
                    and listing.get("clickable") == "false" and not label(listing)
                    and _contained(bounds(listing), bounds(body))
                    and bounds(listing)[2]-bounds(listing)[0] >= shot["width"] * .85
                    and bounds(listing)[3]-bounds(listing)[1] >= shot["height"] * .3
                    and any(n in set(listing.iter("node")) for n in rollers)):
                return listing
        row = parents.get(row)
    return None


def _single_query_header(shot):
    """Independently identify the current one-field native POI search editor.

    Its unique actual editable, input owner, bounded Return/Search header and
    later native list prove query role without remembering a previous Tap.
    Disabled placeholder editors are not current editable fields.
    """
    if shot.get("current_package") != PACKAGE:
        return None
    visible = set(_visible(shot))
    if any(_SENSITIVE_PAGE.search(label(n)) for n in visible):
        return None
    fields = [n for n in visible if n.get("class") == "android.widget.EditText"]
    if len(fields) != 1:
        return None
    field = fields[0]
    rect = bounds(field)
    if (field.get("resource-id") or list(field)
            or field.get("password") == "true" or _FORBIDDEN.search(label(field))
            or any(token in field.get("input-type", "").lower() for token in ("password", "phone"))
            or rect[1] >= 450
            or not 30 <= rect[3] - rect[1] <= 200
            or not shot["width"] * .4 <= rect[2] - rect[0] <= shot["width"] * .8):
        return None
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    ancestor = field
    while ancestor is not None:
        if re.search(r"评论|评价|反馈|登录|验证码|密码|手机号", label(ancestor)):
            return None
        ancestor = parents.get(ancestor)
    input_owner = parents.get(field)
    if (input_owner not in visible or input_owner.get("class") != "android.view.ViewGroup"
            or label(input_owner) or input_owner.get("resource-id") or bounds(input_owner) != rect):
        return None
    header = None
    current = parents.get(input_owner)
    while current is not None:
        box = bounds(current)
        if current not in visible or not box or box[3] - box[1] > 250 or box[1] >= 450:
            break
        members = set(current.iter("node")) & visible
        if (sum(label(n) == "返回" for n in members) == 1
                and sum(label(n) == "搜索" for n in members) == 1
                and all(not label(n) or n is field or label(n) in {"返回", "清空", "搜索"} for n in members)):
            header = current
        current = parents.get(current)
    if header is None:
        return None
    search = [n for n in header.iter("node") if n in visible and label(n) == "搜索"]
    panel = parents.get(header)
    if panel not in visible or panel.get("class") != "android.view.ViewGroup" or label(panel):
        return None
    panel_rect, header_rect = bounds(panel), bounds(header)
    if (panel_rect[:3] != header_rect[:3] or panel_rect[3] - panel_rect[1] < shot["height"] * .3
            or panel_rect[2] - panel_rect[0] < shot["width"] * .85):
        return None
    children = list(panel)
    bodies = []
    for child in children[children.index(header)+1:]:
        if child not in visible or bounds(child)[1] != header_rect[3] or not _contained(bounds(child), panel_rect):
            continue
        recycler = any(n in visible and "RecyclerView" in n.get("class", "")
                       and bounds(n)[1] >= header_rect[3] for n in child.iter("node"))
        history = _plain_query_history(child, shot, visible)
        placeholder = any(n is not field and n.get("package") == PACKAGE and n.get("class") == "android.widget.EditText"
                          and bounds(n) == rect and n.get("text") and len(n.get("text")) <= 200
                          and (n.get("enabled") == "false" or n.get("visible-to-user") == "false")
                          for n in input_owner)
        if recycler or history is not None and field.get("text", "") == "" and placeholder:
            bodies.append((child, history))
    if len(bodies) != 1:
        return None
    return {"field": field, "header": header, "panel": panel, "search": search,
            "body": bodies[0][0], "history_list": bodies[0][1]}


def _single_query_backdrop(owner, node, shot, state):
    """Only the proven POI editor's earlier empty native sheet background."""
    context = _single_query_header(shot)
    if context is None or owner is not context["field"] and owner not in context["search"]:
        return False
    visible = set(_visible(shot))
    header, panel = context["header"], context["panel"]
    panel_rect = bounds(panel)
    children = list(panel)
    if node not in children or children.index(node) >= children.index(header):
        return False
    backdrops = [n for n in children[:children.index(header)] if n in visible
                 and n.get("class") == "android.view.ViewGroup" and n.get("clickable") == "true"
                 and not label(n) and not n.get("resource-id") and not list(n) and bounds(n) == panel_rect]
    if backdrops != [node]:
        return False
    return True


def _submitted_query_shadow(owner, node, shot):
    """Only the earlier inset shadow leaf of the native four-control toolbar.

    This does not authorize the shadow, voice or Close. Its query caption is
    already an independent target; the exact sibling structure proves why
    this one empty background node may overlap that target.
    """
    visible = set(_visible(shot))
    if (owner not in visible or owner.get('class') != 'android.view.ViewGroup'
            or owner.get('clickable') != 'true' or owner.get('resource-id')
            or not owner.get('content-desc','').startswith('搜索框，') or len(owner) != 1):
        return False
    title = owner[0]
    query = owner.get('content-desc').removeprefix('搜索框，')
    if (title not in visible or title.get('class') != 'android.view.View' or list(title)
            or title.get('clickable') != 'false' or title.get('resource-id') or title.get('content-desc')
            or not query or label(title) != query or not _contained(bounds(title), bounds(owner))):
        return False
    parents = {child: parent for parent in shot['nodes'] for child in parent}
    search_voice = parents.get(owner);front = parents.get(search_voice)
    if search_voice not in visible or front not in visible:
        return False
    box = bounds(front)
    if (front.get('class') != 'android.view.ViewGroup' or label(front) or front.get('resource-id')
            or front.get('clickable') != 'true' or box[1] >= 450 or not 80 <= box[3]-box[1] <= 240
            or box[2]-box[0] < shot['width']*.85):
        return False
    members = [n for n in front.iter('node') if n in visible]
    back = [n for n in members if n.get('class') == 'android.view.ViewGroup' and label(n) == '返回'
            and n.get('clickable') == 'true' and len(n) == 1 and n[0].get('class') == 'android.widget.ImageView'
            and n[0] in visible and not label(n[0]) and n[0].get('clickable') == 'false']
    voice = [n for n in members if n.get('class') == 'android.widget.ImageView' and not list(n)
             and n.get('resource-id') == ID+'llm_img' and label(n) == '语音输入按钮'
             and n.get('clickable') == 'false']
    close = [n for n in members if n.get('class') == 'android.widget.ImageView' and not list(n)
             and label(n) == '关闭' and not n.get('resource-id') and n.get('clickable') == 'true']
    if not (len(back) == len(voice) == len(close) == 1):
        return False
    if (set(n for n in members if not list(n)) != {back[0][0], title, voice[0], close[0]}
            or any(label(n) not in {'', '返回', '搜索框，'+query, query, '语音', '语音输入按钮', '关闭'} for n in members)
            or any(not _contained(bounds(n), box) for n in members)
            or not bounds(back[0])[2] <= bounds(owner)[0] < bounds(owner)[2] <= bounds(voice[0])[0]
                   < bounds(voice[0])[2] <= bounds(close[0])[0]):
        return False
    voice_container = parents.get(voice[0])
    if (voice_container not in visible or voice_container.get('class') != 'android.widget.FrameLayout'
            or voice_container.get('resource-id') != ID+'llm_img_container'):
        return False
    common = parents.get(front)
    if (common not in visible or common.get('class') != 'android.view.ViewGroup'
            or label(common) or common.get('resource-id') or bounds(common) != box):
        return False
    children = [n for n in common if n in visible]
    if len(children) != 2 or children[1] is not front:
        return False
    shadow = children[0]
    if (shadow.get('class') != 'android.view.ViewGroup' or shadow.get('clickable') != 'false'
            or label(shadow) or shadow.get('resource-id') or bounds(shadow) != box or list(shadow) != [node]
            or node not in visible or node.get('class') != 'android.view.ViewGroup' or list(node)
            or node.get('clickable') != 'true' or label(node) or node.get('resource-id')):
        return False
    inset = bounds(node)
    return (all(1 <= value <= max(8,int(shot['width']*.01)) for value in
                (inset[0]-box[0],inset[1]-box[1],box[2]-inset[2],box[3]-inset[3])))


def _input_eligible(node, shot, state):
    """A generic EditText is not by itself evidence of a place-search field."""
    rid, text = node.get("resource-id", ""), label(node)
    if (node.get("password") == "true" or re.search(r"password|account|phone|comment|review|feedback|message|login", rid, re.I)):
        return False
    parents = {c: p for p in shot["nodes"] for c in p}
    current = node
    while current is not None:
        if re.search(r"评论|评价|反馈|登录|验证码|密码|手机号", label(current)):
            return False
        current = parents.get(current)
    if rid in {ID + "route_edit_summary_start", ID + "route_edit_summary_end"}:
        return True
    if _native_route_field_role(node, shot):
        return True
    if rid in _ENTRY_FIELDS:
        return False  # A malformed entry field cannot downgrade to generic search.
    if _anonymous_field_role(node, shot):
        return True
    query_header = _single_query_header(shot)
    if query_header is not None and node is query_header["field"]:
        return True
    if rid.startswith(ID) and re.search(r"search.*(?:input|edit|keyword)|(?:input|edit).*search", rid, re.I):
        return True
    if text in _SEARCH_LABELS or text.startswith("搜索框"):
        return True
    return False  # A remembered Tap alone cannot authorize an arbitrary editor.


def _place_names(role, request, state):
    values = set()
    if request.get(role):
        values.add(request[role])
    for source in (state.get("places", {}), state.get("choices", {})):
        place = source.get(role) or {}
        if isinstance(place, dict) and isinstance(place.get("name"), str) and place["name"]:
            values.add(place["name"])
    return values


def _same_place_name(actual, expected, city):
    return (place_key(actual) == place_key(expected)
            or place_key(actual, city) == place_key(expected, city))


def allowed_inputs(role, request, state):
    roles = ("origin", "destination") if role == "query" else (role,)
    values = {name for r in roles for name in _place_names(r, request, state)}
    city = request.get("city", "")
    if city:
        values |= {city + value for value in list(values) if not value.startswith(city)}
    if (role == "origin" and not request.get("origin") and request.get("use_current_location")
            and state.get("location_verified")):
        values.add("我的位置")
    return values


def _query_matches(shot, role, request, state):
    suffix = "start" if role == "origin" else "end"
    found = [n for n in _visible(shot) if n.get("resource-id") == ID + "route_edit_summary_" + suffix]
    if len(found) != 1:
        return False
    # These native fields expose their ROLE in content-desc. The current
    # endpoint value is the literal text, never that accessibility caption.
    actual = found[0].get("text", "")
    if role == "origin" and not request.get("origin"):
        return actual == "我的位置" and request.get("use_current_location") and state.get("location_verified")
    names = _place_names(role, request, state)
    return any(_same_place_name(actual, name, request.get("city", "")) for name in names)


def _route_card_aliases(owner, shot, raw_cards, *, prerequisite_probe=False):
    """Prove native summary/line wrappers belong to one itinerary owner.

    The renderer repeats a card's summary on an inner group. Only one nested
    chain, exact ordered inert leaf text and ordinary blank ViewGroup wrappers
    can be aliases. Separate schemes, unknown buttons and overlays remain
    independent controls and never borrow the card's authorization.
    """
    visible = set(_visible(shot))
    rect = bounds(owner)
    if owner not in visible or rect is None:
        return set()
    members = [n for n in owner.iter("node") if n is not owner and n in visible]
    if (prerequisite_probe and not members and owner.get("class") == "android.view.ViewGroup"
            and owner.get("clickable") == "true" and not owner.get("resource-id")
            and label(owner) and not _FORBIDDEN.search(label(owner))):
        return {owner}  # A native aggregate-only card can justify rejection, never a weaker click guard.
    nested = [n for n in members if n in raw_cards]
    for i, first in enumerate(nested):
        if not label(owner).startswith(label(first)):
            return set()
        for second in nested[i + 1:]:
            if second not in set(first.iter("node")) and first not in set(second.iter("node")):
                return set()  # More than one native itinerary branch.
    leaves = []
    aliases = set()
    for node in members:
        box, text = bounds(node), label(node)
        if (not _contained(box, rect) or node.get("resource-id")
                or node.get("class") not in {"android.view.ViewGroup", "android.view.View", "android.widget.ImageView"}
                or _FORBIDDEN.search(text)):
            return set()
        children = [child for child in node if child in visible]
        if children:
            if (node.get("class") != "android.view.ViewGroup"
                    or text and (node not in raw_cards or not label(owner).startswith(text))):
                return set()
        elif node.get("clickable") == "true":
            # The observed renderer exposes tiny bullets/dividers as blank
            # clickable leaves. No regular blank or labelled button qualifies.
            if text or not (box[2] - box[0] <= 10 and box[3] - box[1] <= 10
                            or box[3] - box[1] <= 2):
                return set()
        elif text:
            if node.get("class") == "android.widget.ImageView" and text == "图标":
                continue
            if node.get("class") != "android.view.View":
                return set()
            leaves.append(text)
        aliases.add(node)
    if not leaves or not prerequisite_probe and "".join(leaves) != label(owner):
        return set()  # An aggregate caption cannot invent or hide leaf text.
    return aliases


def _canonical_route_cards(shot, adapter):
    visible = set(_visible(shot))
    raw = {n for n in adapter._route_cards(shot) if n in visible}
    aliases = {owner: _route_card_aliases(owner, shot, raw) for owner in raw}
    return {node for node in raw if not any(node in proven for owner, proven in aliases.items() if owner is not node)}


def _route_prerequisite_card(point, shot, request, state):
    """Recognize a blocked native card for correction, never authorization.

    Only real endpoint editors and one independently canonical itinerary at
    this actual point qualify. The normal forbidden/overlap checks must still
    pass before its caller emits the narrowly retryable rejection.
    """
    adapter = state.get("adapter")
    if adapter is None or page_kind(shot) != "route_list":
        return None
    visible = set(_visible(shot))
    for suffix, caption in (("start", "起点输入栏"), ("end", "终点输入栏")):
        fields = [n for n in visible if n.get("resource-id") == ID+"route_edit_summary_"+suffix]
        if (len(fields) != 1 or fields[0].get("class") != "android.widget.EditText"
                or fields[0].get("content-desc") != caption or not fields[0].get("text", "").strip()
                or fields[0].get("password") == "true"
                or any(token in fields[0].get("input-type", "").lower() for token in ("password", "phone"))):
            return None
    preference = {"recommended": "高德推荐", "fewer_transfers": "换乘少", "less_walking": "步行少"}.get(
        request.get("preference", "recommended"))
    if preference is None:
        return None
    if (all(_query_matches(shot, role, request, state) for role in ("origin", "destination"))
            and state.get("filter") == preference):
        return None
    raw = {n for n in adapter._route_cards(shot) if n in visible}
    # A rich accessibility caption can express icon/connectors absent from
    # literal leaves. Its native structure can justify correction feedback,
    # but never relax the strict caption guard used for an authorized click.
    probes = {n: _route_card_aliases(n, shot, raw, prerequisite_probe=True) for n in raw}
    cards = [n for n, aliases in probes.items() if aliases and _inside(point, bounds(n))
             and not any(n in other for owner, other in probes.items() if owner is not n)]
    return (cards[0], probes[cards[0]]) if len(cards) == 1 else None


def _search_values(shot):
    values = set()
    for n in _visible(shot):
        text = label(n)
        if "EditText" in n.get("class", ""):
            values.add(n.get("text", ""))
        elif text.startswith("搜索框，"):
            values.add(text.removeprefix("搜索框，"))
    return values


def _search_entry(node):
    rect = bounds(node)
    text = label(node)
    return (rect is not None and rect[1] < 450 and rect[3] - rect[1] <= 300
            and (text.startswith("搜索框") or text in _SEARCH_LABELS))


def _home_search_entry(node, shot, request, state):
    """The inert native query-content region, excluding QR/mic siblings.

    Amap may retain any previous ordinary query. Its value grants no place
    facts or input authority: this exact native control only opens the search
    editor. Its literal title/caption and native hierarchy must still agree.
    """
    if (node.get("resource-id") != ID + "layout_searchbar_content"
            or node.get("class") != "android.widget.LinearLayout"
            or node.get("clickable") != "false" or label(node) or len(node) != 1):
        return False
    title = node[0]
    text = title.get("text", "")
    prefix = "搜索框，输入文字"
    caption = title.get("content-desc", "")
    if (title.get("resource-id") != ID + "txt_hotword" or title.get("class") != "android.widget.TextView"
            or title.get("clickable") != "false" or list(title) or title not in _visible(shot)
            or not text or text != text.strip() or not caption.startswith(prefix)
            or caption[len(prefix):].lstrip() != text):
        return False
    if _FORBIDDEN.search(text) or text in {"路线", "导航"} | _TRANSIT:
        return False
    visible = set(_visible(shot))
    if (sum(n.get("resource-id") == ID+"layout_searchbar_content" for n in visible) != 1
            or sum(n.get("resource-id") == ID+"txt_hotword" for n in visible) != 1
            or sum(n.get("resource-id") == ID+"maphome_searchbar_bg" for n in visible) != 1):
        return False
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    current = node
    rect = bounds(node)
    if (rect is None or not 30 <= rect[3]-rect[1] <= 160
            or not shot["width"] * .3 <= rect[2]-rect[0] <= shot["width"] * .7
            or not _contained(bounds(title), rect)):
        return False
    for expected, cls, clickable in (("maphome_searchbar_bg", "android.widget.LinearLayout", "true"),
                                     ("maphome_searchbar_container", "android.widget.LinearLayout", "false"),
                                     ("slidecontainer_header", "android.widget.FrameLayout", "false")):
        parent = parents.get(current)
        if (parent is None or parent.get("resource-id") != ID + expected or parent.get("class") != cls
                or parent.get("clickable") != clickable or not _contained(bounds(current), bounds(parent))):
            return False
        current = parent
    return True


def _home_transit_entry(node, shot):
    """Amap 15.13's labelled public-transit tile in its native home service grid.

    This is a query entry, not a nearby-bus, taxi, ticket or navigation tile.
    Both its semantic owner and its literal title must agree, inside the actual
    Amap home_scroll_view/quickservice ancestry seen on the live device.
    """
    rect = bounds(node)
    if (node.get("class") != "android.view.ViewGroup" or node.get("clickable") != "true"
            or label(node) != "公交地铁" or rect is None
            or not 40 <= rect[2] - rect[0] <= shot["width"] * .25
            or not 40 <= rect[3] - rect[1] <= shot["height"] * .15):
        return False
    visible = set(_visible(shot))
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    ancestors = set()
    current = parents.get(node)
    while current is not None:
        ancestors.add(current.get("resource-id"));current = parents.get(current)
    if not {ID + "home_scroll_view", ID + "quickservice"} <= ancestors:
        return False
    descendants = [n for n in node.iter("node") if n is not node and n in visible]
    return (any(n.get("text") == "公交地铁" and not list(n) for n in descendants)
            and all(label(n) in {"", "公交地铁"} and _contained(bounds(n), rect) for n in descendants))


def _owned_home_icon(owner, node, shot):
    """The known tile has blank ViewGroup wrappers around one inert image.

    Only this independently identified owner and this single-child decorative
    shape share its action. Unknown child buttons, labels or sibling overlays
    remain unapproved even when their bounds overlap the tile.
    """
    if not _home_transit_entry(owner, shot) or node not in list(owner.iter("node"))[1:]:
        return False
    branch = list(node.iter("node"))
    return (bool(branch) and branch[-1].get("class") == "android.widget.ImageView"
            and branch[-1].get("clickable") == "false"
            and all(not label(n) and not n.get("resource-id") and _contained(bounds(n), bounds(owner))
                    and (n.get("class") == "android.view.ViewGroup" and len(n) == 1
                         or n.get("class") == "android.widget.ImageView" and len(n) == 0
                         and n.get("clickable") == "false") for n in branch))


def _quick_place_candidates(shot, request, state):
    """Whole-task-place navigation chips in the native two-row quick grid.

    Unlike the history below, these are one-title chip controls. They only
    open a fresh place/search view; their text supplies no location facts.
    """
    context = _single_query_header(shot)
    if context is None or context["field"].get("text", "") != "":
        return []
    visible = set(_visible(shot))
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    entries = []
    for title in visible:
        name = label(title)
        if (title.get("class") != "android.view.View" or title.get("clickable") != "false"
                or title.get("resource-id") or title.get("content-desc") or list(title) or not name
                or _FORBIDDEN.search(name) or name in _TRANSIT | _DETAILS | _BACK | {"路线", "导航", "家", "公司"}):
            continue
        owner = parents.get(title)
        if (owner not in visible or owner.get("class") != "android.view.ViewGroup" or label(owner)
                or owner.get("resource-id") or owner.get("clickable") != "true" or list(owner) != [title]
                or not _contained(bounds(title), bounds(owner))
                or not 50 <= bounds(owner)[2]-bounds(owner)[0] <= shot["width"] * .5
                or not 50 <= bounds(owner)[3]-bounds(owner)[1] <= 160):
            continue
        row = parents.get(owner);grid = parents.get(row)
        if row not in visible or grid not in visible:
            continue
        rows = [n for n in grid if n in visible]
        if (len(rows) != 2 or row not in rows or bounds(grid)[2]-bounds(grid)[0] < shot["width"] * .8
                or any(n.get("class") != "android.view.ViewGroup" or label(n) or n.get("resource-id")
                       or n.get("clickable") != "true" or not _contained(bounds(n), bounds(grid))
                       or bounds(n)[0] != bounds(grid)[0] or bounds(n)[2] != bounds(grid)[2] for n in rows)):
            continue
        rows.sort(key=lambda n: bounds(n)[1])
        if (not 0 <= bounds(rows[1])[1]-bounds(rows[0])[3] <= 64
                or abs((bounds(rows[0])[3]-bounds(rows[0])[1])-(bounds(rows[1])[3]-bounds(rows[1])[1])) > 2
                or not _contained(bounds(owner), bounds(row))):
            continue
        wrapper = parents.get(grid);viewport = parents.get(wrapper)
        if (grid.get("class") != "android.view.ViewGroup" or grid.get("clickable") != "true" or label(grid)
                or grid.get("resource-id") or wrapper not in visible or viewport not in visible
                or wrapper.get("class") != "android.view.ViewGroup" or wrapper.get("clickable") != "false"
                or label(wrapper) or wrapper.get("resource-id") or list(wrapper) != [grid]
                or bounds(wrapper) != bounds(grid) or bounds(viewport) != bounds(grid)
                or viewport.get("class") != "com.autonavi.minimap.ajx3.widget.view.SpringHorizontalScrollView"
                or viewport.get("clickable") != "false" or viewport.get("scrollable") != "true"
                or viewport not in set(context["body"].iter("node"))):
            continue
        entries.append((owner, name))
    candidates = []
    for role in ("origin", "destination"):
        selected = state.get("choices", {}).get(role) or state.get("places", {}).get(role) or {}
        if selected.get("address"):
            continue
        names = _place_names(role, request, state)
        matching = [(owner, name) for owner, name in entries if any(
            _same_place_name(name, expected, request.get("city", "")) for expected in names)]
        if len(matching) > 1:
            raise GroundingError("高德快捷地点出现同名入口，请重新搜索核对，未执行")
        if matching:
            owner, name = matching[0]
            candidates.append(_Candidate(owner, "place", "place_list", {
                "quick_place_entry": True, "row": owner, "place_role": role, "name": name,
            }))
    return candidates


def _history_entries(shot):
    """Current POI-history navigation controls, never current POI evidence.

    A history POI has a named left control and separate taxi/route controls.
    A textual query history item does not have that structure. Only the named
    left control and its inert title/district/line labels share this action.
    """
    visible = set(_visible(shot))
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    context = _single_query_header(shot)
    if context is None or context["field"].get("text", "") != "":
        return []
    field, header, sheet = context["field"], context["header"], context["panel"]
    captions = [n for n in visible if label(n) == "历史记录" and not list(n)
                and n.get("class") == "android.view.View" and n.get("clickable") == "false"]
    if len(captions) != 1:
        return []
    caption = captions[0]
    listing = parents.get(caption)
    while listing is not None and "RecyclerView" not in listing.get("class", ""):
        listing = parents.get(listing)
    if listing is None:
        listing = context["history_list"]
    if listing not in visible:
        return []
    if listing not in set(context["body"].iter("node")) or caption not in set(listing.iter("node")):
        return []
    entries = []
    for owner in visible:
        rect, name = bounds(owner), label(owner)
        if (owner.get("class") != "android.view.ViewGroup" or owner.get("clickable") != "true"
                or not name or _FORBIDDEN.search(name) or owner.get("resource-id")
                or not shot["width"] * .4 <= rect[2] - rect[0] <= shot["width"] * .8
                or not 60 <= rect[3] - rect[1] <= 250 or rect[1] < bounds(caption)[3]):
            continue
        foreground = parents.get(owner)
        if foreground is None or foreground not in visible or not _contained(rect, bounds(foreground)):
            continue
        siblings = [n for n in foreground if n is not owner and n in visible]
        if len(siblings) != 1:
            continue
        actions = siblings[0]
        buttons = [n for n in actions if n in visible]
        if (bounds(actions) is None or bounds(actions)[0] != rect[2]
                or bounds(actions)[1:4:2] != rect[1:4:2]
                or len(buttons) != 2 or {label(n) for n in buttons} != {"打车", "路线"}
                or any(n.get("class") != "android.view.ViewGroup" or n.get("clickable") != "true"
                       or not _contained(bounds(n), bounds(actions)) for n in buttons)):
            continue
        members = [n for n in owner.iter("node") if n in visible]
        titles = [n for n in members if n is not owner and not list(n) and label(n) == name
                  and n.get("class") == "android.view.View" and n.get("clickable") == "false"]
        if len(titles) != 1:
            continue
        def inert_member(n):
            text = label(n)
            if not _contained(bounds(n), rect) or n.get("resource-id"):
                return False
            if text and text != name and not re.fullmatch(r"[\u4e00-\u9fff]{1,12}(?:区|县)|(?:\d+号线|APM线)", text):
                return False
            return (n.get("class") == "android.view.ViewGroup" and (not text or n is owner)
                    or not list(n) and n.get("class") in {"android.view.View", "android.widget.ImageView"}
                    and n.get("clickable") == "false")
        if not all(inert_member(n) for n in members):
            continue
        row = foreground
        while parents.get(row) is not listing and parents.get(row) is not None:
            row = parents[row]
        if parents.get(row) is not listing or not _contained(bounds(row), bounds(listing)):
            continue
        # Known backing swipe controls have separate hit areas on the right.
        aliases = set(members)
        branch = owner
        while branch is not row:
            parent = parents.get(branch)
            for sibling in parent:
                if sibling is branch or sibling not in visible or sibling is actions:
                    continue
                children = [n for n in sibling if n in visible]
                if (not label(sibling) and not sibling.get("resource-id")
                        and sibling.get("class") == "android.view.ViewGroup"
                        and bounds(sibling) == bounds(row) and children
                        and {label(n) for n in children} in ({"收藏", "删除"}, {"删除"})
                        and all(not list(n) and n.get("class") == "android.view.View"
                                and n.get("clickable") == "true" and not n.get("resource-id")
                                and not _contained(bounds(titles[0]), bounds(n))
                                for n in children)):
                    aliases.add(sibling)
            branch = parent
        # The observed sheet has one empty backdrop before its content. Later
        # overlays, nonempty nodes and extra descendants are never aliases.
        content = listing
        while parents.get(content) is not sheet:
            content = parents[content]
        children = list(sheet)
        backdrops = [n for n in children[:children.index(content)] if n in visible
                     and n.get("class") == "android.view.ViewGroup" and not label(n)
                     and not n.get("resource-id") and n.get("clickable") == "true"
                     and not list(n) and bounds(n) == bounds(sheet)]
        if len(backdrops) == 1:
            aliases.add(backdrops[0])
        entries.append((owner, row, aliases))
    return entries


def _history_place_candidates(shot, request, state):
    entries = _history_entries(shot)
    candidates = []
    for role in ("origin", "destination"):
        # A history title cannot establish a previously selected full address.
        selected = state.get("choices", {}).get(role) or state.get("places", {}).get(role) or {}
        if selected.get("address"):
            continue
        names = _place_names(role, request, state)
        matching = [(owner, row) for owner, row, _ in entries
                    if any(_same_place_name(label(owner), name, request.get("city", "")) for name in names)]
        if len(matching) > 1:
            raise GroundingError("高德历史记录存在同名地点，需重新搜索核对，未执行")
        if matching:
            owner, _ = matching[0]
            candidates.append(_Candidate(owner, "place", "place_list", {
                "history_entry": True, "place_role": role, "name": label(owner),
            }))
    return candidates


def _in_history_section(node, shot):
    """Old history must not become a fresh search result while input changes."""
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    current = parents.get(node)
    visible = set(_visible(shot))
    while current is not None:
        if "RecyclerView" in current.get("class", ""):
            return any(n in visible and label(n) == "历史记录" and not list(n)
                       and bounds(n)[3] <= bounds(node)[1] for n in current.iter("node"))
        if current in visible and current.get("class") == "android.view.ViewGroup":
            listing = _plain_query_history(current, shot, visible)
            if listing is not None and node in set(listing.iter("node")):
                return any(n in visible and label(n) == "历史记录" and not list(n)
                           and bounds(n)[3] <= bounds(node)[1] for n in listing.iter("node"))
        current = parents.get(current)
    return False


def _station_heading_placeholder(row, title, slot, shot, visible, parents):
    """The observed empty badge slot in one native station heading component.

    This is only used after the complete primary result and query scope are
    proven. A generic blank leaf, another sibling or a similarly sized overlay
    never inherits this component's place-opening meaning.
    """
    def group(n, rect=None):
        return (n in visible and n.get("class") == "android.view.ViewGroup"
                and not label(n) and not n.get("resource-id") and n.get("clickable") == "true"
                and (rect is None or bounds(n) == rect))
    if not group(slot) or list(slot):
        return False
    line = parents.get(title)
    if not group(line) or list(line) != [title, slot]:
        return False
    line_box, title_box, slot_box, row_box = bounds(line), bounds(title), bounds(slot), bounds(row)
    if (line_box[0] != title_box[0] or line_box[1::2] != title_box[1::2]
            or line_box[2] != row_box[2]-1 or slot_box[2] != line_box[2]):
        return False
    wrapper = parents.get(line)
    if not group(wrapper, line_box) or list(wrapper) != [line]:
        return False
    heading = parents.get(wrapper)
    if (not group(heading, [row_box[0], title_box[1], row_box[2], title_box[3]])
            or parents.get(heading) is not row or len(heading) != 2 or list(heading)[1] is not wrapper):
        return False
    icon_owner = list(heading)[0];icon_box = bounds(icon_owner)
    if (not group(icon_owner) or len(icon_owner) != 1 or icon_box[0] != row_box[0]
            or icon_box[1::2] != title_box[1::2]
            or not 24 <= icon_box[2]-icon_box[0] == icon_box[3]-icon_box[1] <= 100):
        return False
    icon_wrap = list(icon_owner)[0]
    if not group(icon_wrap, icon_box) or len(icon_wrap) != 1:
        return False
    icon = list(icon_wrap)[0]
    if (icon not in visible or icon.get("class") != "android.widget.ImageView" or list(icon)
            or icon.get("clickable") != "false" or label(icon) or icon.get("resource-id") or bounds(icon) != icon_box):
        return False
    height = title_box[3]-title_box[1]
    gap = title_box[0]-icon_box[2]
    inset = slot_box[1]-title_box[1]
    return (0 < gap <= min(32, height*.3) and slot_box[0]-title_box[2] == gap
            and 0 < inset == title_box[3]-slot_box[3] <= height*.2
            and slot_box[2]-slot_box[0] >= height*2)


def _station_result_main_region(row, shot):
    """One exact station result's native text region, excluding shortcuts.

    The literal main title, inert line badges and visit count share this
    region. Right-side distance/route controls and lower entrance links do not.
    Blank leaves retain the overlap check, except the exact native heading's
    reserved badge-slot component, proven separately with its full ancestry.
    """
    if not re.fullmatch(r".+[（(]地铁站[）)]", label(row)):
        return None
    visible = set(_visible(shot))
    try:
        title = AmapDevice._place_title_target(row, nodes=shot["nodes"])
    except WorkflowError:
        return None
    if title not in visible or title.get("clickable") != "false" or title.get("class") != "android.view.View":
        return None
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    context = _single_query_header(shot)
    parent = parents.get(row)
    siblings = [n for n in parent if n is not row and n in visible] if parent is not None else []
    rect = bounds(row)
    if (context is not None and row in set(context["body"].iter("node"))
            and row in visible and row.get("clickable") == "true"
            and shot["width"] * .4 <= rect[2]-rect[0] <= shot["width"] * .8
            and 60 <= rect[3]-rect[1] <= 250 and len(siblings) == 1):
        side = siblings[0];side_rect = bounds(side)
        side_members = set(side.iter("node")) & visible
        taxis = [n for n in side_members if not list(n) and label(n) == "打车" and n.get("clickable") == "false"]
        routes = [n for n in side_members if n.get("class") == "android.view.ViewGroup"
                  and label(n) == "路线" and n.get("clickable") == "true"]
        if (side_rect and side.get("class") == "android.view.ViewGroup" and side.get("clickable") == "true"
                and side_rect[0] == rect[2] and side_rect[1] == rect[1] and side_rect[3] == rect[3]
                and len(taxis) == len(routes) == 1
                and any(n in visible and not list(n) and n.get("clickable") == "false"
                        and re.fullmatch(r"\d+(?:\.\d+)?(?:公里|米)", label(n)) for n in routes[0].iter("node"))):
            members = [n for n in row.iter("node") if n in visible]
            def inert_compact(n):
                if not _contained(bounds(n), rect) or n.get("resource-id"):
                    return False
                if n.get("class") == "android.view.ViewGroup":
                    return n is row or not label(n)
                return (not list(n) and n.get("clickable") == "false"
                        and (n.get("class") == "android.widget.ImageView" and not label(n)
                             or n.get("class") == "android.view.View" and (n is title or
                                 re.fullmatch(r"(?:浏览过|刚刚浏览|\d+号线|APM线)", label(n)))))
            if all(inert_compact(n) for n in members):
                unknown_targets = {n for n in members if not list(n) and n.get("clickable") == "true"}
                # An otherwise empty sibling does not become a structural
                # alias merely by acquiring an inert image descendant.
                unknown_targets |= {n for n in parents.get(title, []) if n is not title and n in visible
                                    and n.get("clickable") == "true" and not any(label(c) for c in n.iter("node"))}
                if unknown_targets:
                    if (len(unknown_targets) == 1
                            and _station_heading_placeholder(row, title, next(iter(unknown_targets)), shot, visible, parents)):
                        return row, {n for n in members if n.get("class") == "android.view.ViewGroup" and list(n)} | unknown_targets
                    # An empty inline target has no proven meaning. Keep it
                    # unauthorized, and expose only the literal station name
                    # instead of the parent's broad padding hit area.
                    title_box = bounds(title)
                    if any(max(bounds(n)[0], title_box[0]) < min(bounds(n)[2], title_box[2])
                           and max(bounds(n)[1], title_box[1]) < min(bounds(n)[3], title_box[3])
                           for n in unknown_targets):
                        raise GroundingError("高德地点名称存在未授权的内嵌覆盖控件，未执行")
                    return title, set()
                return row, {n for n in members if n.get("class") == "android.view.ViewGroup" and list(n)}
    region = parents.get(title)
    while region is not None and region is not row:
        rect, parent = bounds(region), parents.get(region)
        siblings = [n for n in parent if n is not region and n in visible] if parent is not None else []
        if (region in visible and region.get("class") == "android.view.ViewGroup"
                and region.get("clickable") == "true" and not label(region) and not region.get("resource-id")
                and _contained(rect, bounds(row)) and rect[1] > 400
                and shot["width"] * .5 <= rect[2]-rect[0] <= shot["width"] * .85
                and 80 <= rect[3]-rect[1] <= 300 and len(siblings) == 1):
            shortcut = siblings[0];side = bounds(shortcut)
            distance = [n for n in shortcut.iter("node") if n in visible and not list(n)
                        and n.get("class") == "android.view.View" and n.get("clickable") == "false"
                        and re.fullmatch(r"\d+(?:\.\d+)?(?:公里|米)", label(n))]
            if (side and shortcut.get("class") == "android.view.ViewGroup" and shortcut.get("clickable") == "true"
                    and _contained(side, bounds(row)) and side[1] == rect[1] and side[3] == rect[3]
                    and -1 <= side[0]-rect[2] <= 2 and side[2]-side[0] <= shot["width"] * .2
                    and len(distance) == 1 and bounds(title)[1] == rect[1] and bounds(title)[0] == rect[0]):
                members = [n for n in region.iter("node") if n in visible]
                def inert(n):
                    if not _contained(bounds(n), rect) or n.get("resource-id"):
                        return False
                    if n.get("class") == "android.view.ViewGroup":
                        return not label(n)
                    text = label(n)
                    return (not list(n) and n.get("class") == "android.view.View" and n.get("clickable") == "false"
                            and (n is title or not text or re.fullmatch(r"(?:\d+号线|APM线|\d+\+?人去过)", text)))
                if all(inert(n) for n in members):
                    aliases = {n for n in members if n.get("class") == "android.view.ViewGroup" and list(n)}
                    return region, aliases
        region = parents.get(region)
    return None


def _place_candidates(shot, request, state):
    adapter = state.get("adapter")
    if adapter is None:
        return []
    candidates = _history_place_candidates(shot, request, state) + _quick_place_candidates(shot, request, state)
    visible = set(_visible(shot))
    queries = _search_values(shot)
    remembered = state.get("input_role")
    for role in ("origin", "destination"):
        if remembered in {"origin", "destination"} and role != remembered:
            continue
        expected = _place_names(role, request, state)
        if not expected or not any(task_search_text_matches(actual, value, request.get("city", ""))
                                   for actual in queries for value in allowed_inputs(role, request, state)):
            continue
        query = request.get(role) or next(iter(expected))
        values = [(c, n) for c, n in adapter._place_candidates(shot, query, include_related=True)
                  if n in visible and not _in_history_section(n, shot)
                  and any(_same_place_name(c["name"], name, request.get("city", "")) for name in expected)]
        chosen = state.get("choices", {}).get(role)
        if chosen:
            values = [(c, n) for c, n in values if c["candidate_id"] == chosen.get("candidate_id")
                      and c["name"] == chosen.get("name") and c.get("address") == chosen.get("address")]
            if len(values) != 1:
                raise GroundingError("高德地点与本轮已确认的名称或地址不一致，需重新选择，未执行")
        if len(values) > 1:
            raise GroundingError("高德存在同名地点候选，需用户选择，未执行模型点击")
        if values:
            candidate, row = values[0]
            try:
                title = adapter._place_title_target(row, nodes=shot["nodes"])
            except WorkflowError:
                continue
            if title in visible:
                region = _station_result_main_region(row, shot)
                candidates.append(_Candidate(region[0] if region else title, "place", "place_list", {
                    "candidate": candidate, "query": query, "row": row, "place_role": role,
                    **({"place_main_region": True} if region else {}),
                }))
            continue
        # The route start/destination editor can show a simple native suggestion
        # list rather than POI cards. It is usable only after that exact POI has
        # independently been inspected, with an unambiguous literal title.
        inspected = state.get("places", {}).get(role)
        if inspected and inspected.get("name"):
            suggestions = [n for n in visible if n.get("class") == "android.view.View"
                           and bounds(n)[1] > 400 and not _in_history_section(n, shot)
                           and place_key(label(n)) == place_key(inspected["name"])]
            if len(suggestions) > 1:
                raise GroundingError("高德起终点建议存在同名候选，需用户确认，未执行")
            if len(suggestions) == 1:
                candidates.append(_Candidate(suggestions[0], "place", "place_list", {
                    "query": query, "candidate": inspected, "row": suggestions[0], "place_role": role,
                    "inspected_suggestion": True,
                }))
    return candidates


def _compact_query_close(shot, request, state):
    """The exact top search-header X of this independently read task POI."""
    visible = set(_visible(shot))
    compact = _compact_poi(shot, visible)
    adapter = state.get("adapter")
    if compact is None or adapter is None:
        return None
    matched = False
    for role in ("origin", "destination"):
        if not any(_same_place_name(compact["name"], name, request.get("city", ""))
                   for name in _place_names(role, request, state)):
            continue
        selected = state.get("choices", {}).get(role) or state.get("places", {}).get(role) or {}
        actual = adapter._read_place(shot, compact["name"], selected=selected, header_bounds=compact["bounds"])
        if (request.get("city") and actual.get("city") != request["city"]
                or selected.get("address") and actual.get("address") != selected["address"]
                or selected.get("name") and not _same_place_name(compact["name"], selected["name"], request.get("city", ""))):
            continue
        matched = True
    if not matched:
        return None
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    matches = []
    for close in visible:
        rect = bounds(close)
        if (close.get("class") != "android.widget.ImageView" or label(close) != "关闭"
                or close.get("clickable") != "false" or close.get("resource-id") or list(close)
                or rect[1] >= 450 or rect[0] < shot["width"] * .8
                or not 30 <= rect[2]-rect[0] <= 120 or abs((rect[2]-rect[0])-(rect[3]-rect[1])) > 4):
            continue
        owner = parents.get(close)
        if (owner not in visible or owner.get("class") != "android.view.ViewGroup" or label(owner)
                or owner.get("resource-id") or owner.get("clickable") != "true" or list(owner) != [close]):
            continue
        header = parents.get(owner)
        while header is not None:
            box = bounds(header)
            if header not in visible or box[1] >= 450 or box[3]-box[1] > 250:
                break
            leaves = [n for n in header.iter("node") if n in visible and not list(n)]
            back = [n for n in leaves if n.get("class") == "android.widget.ImageView"
                    and label(n) == "返回" and n.get("clickable") == "true"]
            query = [n for n in leaves if n.get("class") == "android.view.View" and n.get("clickable") == "true"
                     and n.get("text") == compact["name"] and n.get("content-desc") == "搜索框，"+compact["name"]]
            voice = [n for n in leaves if n.get("class") == "android.widget.ImageView"
                     and n.get("resource-id") == ID+"llm_img" and label(n) == "语音输入按钮"
                     and n.get("clickable") == "false"]
            if (not label(header) and header.get("class") == "android.view.ViewGroup"
                    and box[2]-box[0] >= shot["width"] * .85 and len(back) == len(query) == len(voice) == 1
                    and set(leaves) == {back[0], query[0], voice[0], close}
                    and all(_contained(bounds(n), box) for n in leaves)
                    and bounds(back[0])[2] <= bounds(query[0])[0] < bounds(query[0])[2]
                    <= bounds(voice[0])[0] < bounds(voice[0])[2] <= rect[0]):
                matches.append(close);break
            header = parents.get(header)
    if len(matches) != 1:
        return None
    close_rect = bounds(matches[0])
    # A foreign covering surface cannot inherit this native header's meaning.
    for n in shot["nodes"]:
        rect = bounds(n)
        if (n.get("package", PACKAGE) != PACKAGE and n.get("visible-to-user") != "false"
                and n.get("enabled") != "false" and rect
                and max(rect[0], close_rect[0]) < min(rect[2], close_rect[2])
                and max(rect[1], close_rect[1]) < min(rect[3], close_rect[3])):
            return None
    return matches[0]


def _candidates(shot, request, state):
    from autoglm.loading import loading_scene
    loading = loading_scene(shot)
    if loading is not None:
        return [_Candidate(loading["cancel_node"], "loading_cancel", "loading", {"loading_cancel": True})]
    if _loading_markers(shot):
        return []
    ns = _visible(shot)
    page = page_kind(shot)
    values = [] if page == "place_detail" else _place_candidates(shot, request, state)
    adapter = state.get("adapter")
    query_header = _single_query_header(shot)
    compact_close = _compact_query_close(shot, request, state)
    footer_route = _fixed_footer_route_owner(shot, request, state) if page == "place_detail" else None
    cards = _canonical_route_cards(shot, adapter) if adapter is not None else set()
    for n in ns:
        text, rid = label(n), n.get("resource-id", "")
        if n in cards:
            if _query_matches(shot, "origin", request, state) and _query_matches(shot, "destination", request, state):
                values.append(_Candidate(n, "route", "route_list", {"route_summary": text}))
            continue
        native_role = _native_route_field_role(n, shot) or _anonymous_field_role(n, shot)
        if native_role:
            values.append(_Candidate(n, native_role, "route_input", {"native_route_field": native_role}))
        elif rid in {ID + "route_edit_summary_start", ID + "route_edit_summary_end"}:
            values.append(_Candidate(n, _role(n, state), "route_input"))
        elif "EditText" in n.get("class", "") and _input_eligible(n, shot, state):
            role = "query" if query_header is not None and n is query_header["field"] else _role(n, state)
            values.append(_Candidate(n, role, "search"))
        elif _search_entry(n):
            values.append(_Candidate(n, "query", "search"))
        elif _home_search_entry(n, shot, request, state):
            values.append(_Candidate(n, "query", "search", {"home_search_entry": True}))
        elif text in _TRANSIT and page in {"route_input", "route_list"}:
            values.append(_Candidate(n, "transit", "route_input"))
        elif _home_transit_entry(n, shot):
            values.append(_Candidate(n, "transit", "route_input", {"home_transit_entry": True}))
        elif text in _PREFERENCES.get(request.get("preference", "fewer_transfers"), set()) and page == "route_list":
            values.append(_Candidate(n, "preference", "route_list", {"filter": text}))
        elif n is footer_route:
            values.append(_Candidate(n, "route", "place_detail", {"fixed_route_footer": True}))
        elif (text == "路线" and page in {"place_detail", "search"} and not _in_history_section(n, shot)
              and (footer_route is None or n not in set(footer_route.iter("node")))):
            values.append(_Candidate(n, "route", "place_detail"))
        elif text in _DETAILS and page in {"route_list", "route_detail"}:
            values.append(_Candidate(n, "details", page))
        elif text in _BACK and page not in {"unknown", "sensitive"}:
            values.append(_Candidate(n, "back", page))
        elif n is compact_close:
            values.append(_Candidate(n, "back", "place_detail", {"compact_poi_close": True}))
        elif (text == "我的位置" and page in {"route_input", "search"} and not request.get("origin")
              and request.get("use_current_location") and state.get("location_verified")):
            values.append(_Candidate(n, "place", "route_input", {"place_role": "origin", "current_location": True}))
        elif (text in {"我的位置", "定位"} and not request.get("origin") and request.get("use_current_location")
              and page not in {"route_input", "route_list", "route_detail"}
              and n.get("class") == "android.widget.FrameLayout" and n.get("clickable") == "true"
              and bounds(n)[0] >= shot["width"] * .8
              and abs((bounds(n)[2] - bounds(n)[0]) - (bounds(n)[3] - bounds(n)[1])) <= 20
              and bounds(n)[3] - bounds(n)[1] <= 250):
            # The observed right-side map location icon only opens/recentres
            # location. It does not authorize selecting a route origin. Actual
            # location address and system-health checks remain mandatory.
            values.append(_Candidate(n, "location", "location", {"read_only_location": True}))
    return values


def _fixed_footer_occlusion(point, shot, request, state):
    """Only current compact-list content underneath a proven fixed Route button.

    Amap leaves recommendation controls accessible behind its later fixed
    footer. This identifies their native container and ordering, rather than
    relaxing overlap checks for blank controls or unrelated screen regions.
    """
    visible = set(_visible(shot))
    compact = _compact_poi(shot, visible)
    adapter = state.get("adapter")
    if adapter is None:
        return set()
    full_header = False
    if compact is None:
        # Full-height POI details can leave recommendation rows behind the
        # same fixed footer. Bind only a unique real title near the top of
        # this POI page, not a lower recommendation or a search result.
        names = AmapDevice._poi_title_names({**shot, "nodes": list(visible)})
        titles = [n for n in visible if n.get("class") == "android.view.ViewGroup"
                  and label(n) in names and bounds(n)[1] < 800]
        nearby = [n for n in visible if len(names) == 1
                  and label(n) == f"在{next(iter(names))}附近搜索"
                  and bounds(n)[1] < 800]
        if len(names) != 1 or len(titles) != 1 or len(nearby) != 1:
            return set()
        compact = {"name": next(iter(names)), "bounds": bounds(titles[0]), "title_node": titles[0]}
        full_header = True
    roles = [role for role in ("origin", "destination") if any(
        _same_place_name(compact["name"], name, request.get("city", ""))
        for name in _place_names(role, request, state))]
    if not roles:
        return set()
    # Names and selected addresses are re-read from this actual main header.
    validated = False
    for role in roles:
        selected = state.get("choices", {}).get(role) or state.get("places", {}).get(role) or {}
        if selected.get("name") and not _same_place_name(compact["name"], selected["name"], request.get("city", "")):
            continue
        kwargs = {} if full_header else {"header_bounds": compact["bounds"]}
        current = adapter._read_place(shot, compact["name"], selected=selected, **kwargs)
        if (request.get("city") and current.get("city") != request["city"]
                or selected.get("city") and current.get("city") != selected["city"]
                or selected.get("address") and current.get("address") != selected["address"]):
            continue
        validated = True
    if not validated:
        return set()
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    listing = parents.get(compact["title_node"])
    while listing is not None and "RecyclerView" not in listing.get("class", ""):
        listing = parents.get(listing)
    if (listing not in visible or not _contained(compact["bounds"], bounds(listing))
            or not _inside(point, bounds(listing))):
        return set()
    route_labels = [n for n in shot["nodes"] if n.get("class") == "android.view.View" and not list(n)
                    and n.get("text") == "路线" and n.get("package") == PACKAGE
                    and n.get("visible-to-user") != "false" and n.get("enabled") != "false"
                    and (n in visible or n.get("bounds") == "[0,0][0,0]" and parents.get(n) in visible)]
    found = []
    for route in route_labels:
        button = parents.get(route)
        if (button not in visible or button.get("class") != "android.view.ViewGroup"
                or button.get("clickable") != "true" or label(button) not in {"", "路线"}
                or list(button) != [route] or not _inside(point, bounds(button))
                or not shot["width"] * .1 <= bounds(button)[2] - bounds(button)[0] <= shot["width"] * .3
                or not 30 <= bounds(button)[3] - bounds(button)[1] <= min(300, shot["height"] * .12)
                or bounds(route) and not _contained(bounds(route), bounds(button))):
            continue
        footer = parents.get(button)
        while footer is not None:
            rect = bounds(footer)
            if rect and footer in visible:
                captions = [n for n in footer.iter("node") if n in visible and not list(n)
                            and n.get("text") in {"周边", "收藏", "分享", "打车", "导航", "路线"}]
                if route not in visible and route in set(footer.iter("node")):
                    captions.append(route)
                by_name = {name: [n for n in captions if n.get("text") == name]
                           for name in ("周边", "收藏", "分享", "打车", "导航", "路线")}
                if (footer.get("class") == "android.view.ViewGroup" and not label(footer)
                        and rect[0] == 0 and rect[2] == shot["width"] and rect[1] >= shot["height"] * .85
                        and rect[3] - rect[1] <= shot["height"] * .15
                        and 0 <= shot["height"] - rect[3] <= max(8, shot["height"] * .015)
                        and all(len(values) == 1 for values in by_name.values())
                        and by_name["路线"] == [route] and _contained(bounds(button), rect)):
                    controls = [parents.get(by_name[name][0]) for name in by_name]
                    if (all(control in visible and _contained(bounds(control), rect) for control in controls)
                            and (xs := [bounds(control)[0] for control in controls]) == sorted(set(xs))):
                        found.append((route, footer))
                    break
            footer = parents.get(footer)
    if len(found) != 1:
        return set()
    _, footer = found[0]
    if footer in set(listing.iter("node")):
        return set()  # A scrolling footer is not this fixed native bottom band.
    common = parents.get(footer)
    while common is not None and listing not in set(common.iter("node")):
        common = parents.get(common)
    if common is None:
        return set()
    list_branch, footer_branch = listing, footer
    while parents.get(list_branch) is not common:
        list_branch = parents[list_branch]
    while parents.get(footer_branch) is not common:
        footer_branch = parents[footer_branch]
    order = list(common)
    if list_branch is footer_branch or order.index(list_branch) >= order.index(footer_branch):
        return set()
    # A foreign visible surface never becomes part of the Amap list exception.
    for node in shot["nodes"]:
        if (node.get("package", PACKAGE) != PACKAGE and node.get("visible-to-user") != "false"
                and node.get("enabled") != "false" and _inside(point, bounds(node))):
            raise GroundingError("高德路线按钮上存在其他应用的覆盖控件，未执行")
    return {n for n in listing.iter("node") if n in visible and _contained(bounds(n), bounds(listing))}


def _fixed_footer_route_owner(shot, request, state):
    """Canonical native Route button, including its real blue padding.

    Reuse the complete task-POI/fixed-footer proof at a positive native label.
    The label center is only a proof probe; no click is generated or moved.
    Zero-bounds labels retain the independently grounded OCR path.
    """
    visible = set(_visible(shot))
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    matches = []
    for route in visible:
        if (route.get("class") != "android.view.View" or route.get("text") != "路线"
                or list(route) or route.get("clickable") != "false"):
            continue
        owner = parents.get(route)
        if (owner not in visible or owner.get("class") != "android.view.ViewGroup"
                or owner.get("clickable") != "true" or list(owner) != [route]):
            continue
        box = bounds(route)
        probe = ((box[0]+box[2])//2, (box[1]+box[3])//2)
        occluded = _fixed_footer_occlusion(probe, shot, request, state)
        if occluded and route not in occluded:
            matches.append(owner)
    return matches[0] if len(matches) == 1 else None


def _resolve_tap(action, shot, request, state):
    point = normalized_point(action.params.get("element"), shot)
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    for node in shot["nodes"]:
        if not _inside(point, bounds(node)):
            continue
        current = node;shown = True;foreign = False
        while current is not None:
            if current.get("visible-to-user") == "false" or current.get("enabled") == "false":
                shown = False;break
            if current.get("package", PACKAGE) not in {"", PACKAGE}:
                foreign = True
            current = parents.get(current)
        if shown and foreign:
            raise GroundingError("点击位置存在其他应用的可见覆盖控件，未执行")
    visible = _visible(shot)
    occluded = _fixed_footer_occlusion(point, shot, request, state)
    if any(n not in occluded and _inside(point, bounds(n)) and _FORBIDDEN.search(label(n)) for n in visible if not list(n)):
        raise GroundingError("AutoGLM 点击命中导航、敏感或无关操作，已拒绝")
    values = [c for c in _candidates(shot, request, state) if _inside(point, bounds(c.node))]
    blocked_probe = _route_prerequisite_card(point, shot, request, state)
    blocked_card = blocked_probe[0] if blocked_probe is not None else None
    if blocked_probe is not None:
        values = [c for c in values if c.node is blocked_card or c.node not in blocked_probe[1]]
    if blocked_card is not None and not any(c.node is blocked_card for c in values):
        # This local probe exists only to run the same current-control and
        # overlap checks. It can never reach the returned GroundedAction.
        values.append(_Candidate(blocked_card, "route", "route_list", {"route_summary": label(blocked_card)}))
    if not values:
        ocr_route = _ocr_route_target(point, shot, request, state, occluded=occluded)
        if ocr_route is not None:
            return ocr_route
        raise UnrecognizedTarget("AutoGLM 坐标未命中可独立核对的高德查询控件，未执行")
    # A parent and its literal child can represent the same physical control.
    # Collapse only proven ancestor aliases, not arbitrary overlapping boxes.
    deepest = []
    for c in values:
        def control_name(value):
            return label(value).removeprefix("搜索框，") if c.role in {"query", "origin", "destination"} else label(value)
        aliases = [d for d in values if d is not c and d.node in list(c.node.iter("node"))[1:]
                   and c.role == d.role and (control_name(c.node) == control_name(d.node)
                       or c.binding and d.binding and c.binding.get("native_route_field") == d.binding.get("native_route_field")
                       and c.binding.get("native_route_field") in {"origin", "destination"})]
        if not aliases:
            deepest.append(c)
    if len(deepest) != 1:
        raise GroundingError("AutoGLM 坐标命中多个重叠候选，无法唯一核对，未执行")
    c = deepest[0]
    if _FORBIDDEN.search(label(c.node)):
        raise GroundingError("该高德控件不属于本次路线查询范围")
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    ancestors = set()
    current = c.node
    while current is not None:
        ancestors.add(current);current = parents.get(current)
    authorized = {d.node for d in values}
    if blocked_probe is not None and c.node is blocked_card:
        authorized |= blocked_probe[1]  # Correction-only probe: the final branch always raises.
    fixed_footer = bool(c.binding and c.binding.get("fixed_route_footer"))
    if fixed_footer:
        authorized |= set(c.node.iter("node"))  # Exactly the proven owner's one inert Route label.
    if c.binding and c.binding.get("place_main_region"):
        region = _station_result_main_region(c.binding["row"], shot)
        if region is not None and region[0] is c.node:
            authorized |= region[1]
    if c.role == "route" and c.binding and c.binding.get("route_summary") and state.get("adapter") is not None:
        authorized |= _route_card_aliases(c.node, shot, set(state["adapter"]._route_cards(shot)))
    if c.binding and c.binding.get("history_entry"):
        for owner, _, aliases in _history_entries(shot):
            if owner is c.node:
                authorized |= aliases
    if any(n not in ancestors and n not in authorized and n not in occluded and n.get("clickable") == "true"
           and _inside(point, bounds(n)) and not _owned_home_icon(c.node, n, shot)
           and not _query_backdrop(c.node, n, shot)
           and not _single_query_backdrop(c.node, n, shot, state)
           and not (c.role == 'query' and _submitted_query_shadow(c.node, n, shot))
           and not _result_query_backdrop(c, n, shot, request, state) for n in visible):
        raise GroundingError("AutoGLM 点击位置包含未授权的内嵌入口或重叠控件，未执行")
    if blocked_card is not None and c.node is blocked_card:
        raise RoutePrerequisiteMissing("高德当前路线起终点或路线偏好尚未与本轮需求核对，未执行")
    return GroundedAction("Tap", node=c.node, point=point, role=c.role, page=c.page, binding=c.binding)


def _ocr_route_target(point, shot, request, state, *, occluded=()):
    """Only a current, independently OCR-grounded POI footer route entry.

    The caller supplies Apple Vision items from this exact screenshot, never
    labels/boxes proposed by the model, then repeats OCR during fresh checking.
    This deliberately does not authorize arbitrary graphical controls.
    """
    if page_kind(shot) != "place_detail":
        return None
    ns = _visible(shot)
    compact = _compact_poi(shot, ns)
    names = {compact["name"]} if compact else AmapDevice._poi_title_names({"nodes": ns})
    if len(names) != 1:
        return None
    name = next(iter(names))
    roles = [role for role in ("origin", "destination")
             if any(_same_place_name(name, expected, request.get("city", ""))
                    for expected in _place_names(role, request, state))]
    if not roles:
        return None
    # An already selected address cannot be loosened to name-only matching.
    adapter = state.get("adapter")
    for role in roles:
        selected = (state.get("choices", {}).get(role) or state.get("places", {}).get(role) or {})
        if selected.get("address"):
            if adapter is None:
                return None
            current = adapter._read_place(shot, name, selected=selected,
                                          **({"header_bounds": compact["bounds"]} if compact else {}))
            if current.get("address") != selected["address"]:
                return None
    candidates = []
    for item in shot.get("ocr_targets", []):
        if not isinstance(item, dict) or item.get("text") != "路线":
            continue
        confidence, rect = item.get("confidence"), item.get("bounds")
        if (type(confidence) not in {float, int} or not math.isfinite(confidence) or confidence < .8
                or not isinstance(rect, (list, tuple)) or len(rect) != 4 or any(type(x) is not int for x in rect)
                or not _contained(rect, [0, 0, shot["width"], shot["height"]])):
            continue
        if (rect[1] >= shot["height"] * .75 and rect[2] - rect[0] <= shot["width"] * .4
                and rect[3] - rect[1] <= shot["height"] * .12):
            candidates.append(rect)
    if len(candidates) != 1 or not _inside(point, candidates[0]):
        return None
    for n in ns:
        if n in occluded:
            continue
        if n.get("clickable") != "true" or not _inside(point, bounds(n)):
            continue
        # A real native owner can have a zero-bounds route-label child; that
        # parent is independently attributable to this OCR label. An unknown
        # sibling overlay at the same location is not.
        if not any(label(child) == "路线" for child in n.iter("node")):
            return None
    return GroundedAction("Tap", point=point, role="route", page="place_detail",
                          binding={"ocr_label": "路线", "bounds": list(candidates[0]), "poi_name": name})


def _focused_input(shot, request, state):
    """One common authority check for factual context and actual Type actions."""
    _geometry(shot)
    if shot.get("current_package") != PACKAGE:
        raise GroundingError("高德不是当前前台应用，未执行 AutoGLM 输入")
    if page_kind(shot) == "sensitive":
        raise GroundingError("高德正在显示登录、权限或敏感页面，需要用户处理")
    focused = [n for n in _visible(shot) if "EditText" in n.get("class", "") and n.get("focused") == "true"]
    if len(focused) != 1:
        raise GroundingError("高德当前输入框未唯一聚焦，未执行 AutoGLM 输入")
    node = focused[0]
    if (not _input_eligible(node, shot, state) or _FORBIDDEN.search(label(node))
            or any(x in node.get("input-type", "").lower() for x in ("password", "phone"))):
        raise GroundingError("当前输入框不属于路线地点查询，未执行")
    query_header = _single_query_header(shot)
    role = (_native_route_field_role(node, shot) or _anonymous_field_role(node, shot)
            or ("query" if query_header is not None and node is query_header["field"] else _role(node, state)))
    permitted = sorted(text for text in allowed_inputs(role, request, state)
                       if text and len(text) <= 200 and not any(ord(c) < 32 or ord(c) == 127 for c in text))
    return node, role, permitted


def focused_input_context(shot, request, state=None):
    """JSON-safe observed field facts; it grants no action and never fixes focus.

    Missing, foreign, ambiguous, sensitive, hidden or unrecognized inputs return
    None rather than telling the model to use a weaker input path. The actual
    Type guard calls the identical helper, including anonymous two-slot roles.
    """
    try:
        node, role, permitted = _focused_input(shot, request, state or {})
    except GroundingError:
        return None
    if not permitted:
        return None
    current = node.get("text", "")
    evidence = shot.get("evidence", {})
    return {"role": role, "current_text": current, "allowed_texts": permitted,
            "already_matches": current in permitted,
            "target": {"resource_id": node.get("resource-id", ""), "bounds": list(bounds(node)),
                       "observation_id": evidence.get("observation_id", evidence.get("evidence_id")),
                       "context_epoch": shot.get("epoch")}}


def _resolve_type(action, shot, request, state):
    text = action.params.get("text")
    if not isinstance(text, str) or not text or len(text) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise GroundingError("AutoGLM 输入内容无效，未执行")
    node, role, permitted = _focused_input(shot, request, state)
    if text not in permitted:
        raise InputValueMismatch("AutoGLM 输入与本次明确需求或已确认地点不一致，未执行")
    return GroundedAction("Type", node=node, role=role, page="route_input", text=text)


def _preference_scroll_target(shot, request, state):
    """Prove the native six-option transit preference strip, not any scroller."""
    if (shot.get("current_package") != PACKAGE or page_kind(shot) != "route_list"
            or request.get("preference", "fewer_transfers") not in _PREFERENCES):
        return None
    visible = set(_visible(shot))
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    for role, suffix, caption in (("origin", "start", "起点输入栏"), ("destination", "end", "终点输入栏")):
        fields = [n for n in visible if n.get("resource-id") == ID+"route_edit_summary_"+suffix]
        if (len(fields) != 1 or fields[0].get("class") != "android.widget.EditText"
                or fields[0].get("content-desc") != caption or fields[0].get("password") == "true"
                or any(v in fields[0].get("input-type", "").lower() for v in ("password", "phone"))
                or not _query_matches(shot, role, request, state)):
            return None
    public = [n for n in visible if n.get("resource-id") == ID+"route_input_conbine_text"
              and n.get("class") == "android.widget.TextView" and n.get("text") == "公共交通"
              and n.get("selected") == "true"]
    if len(public) != 1:
        return None
    mode = public[0]
    for rid, cls in (("route_input_conbine_bg", "android.widget.LinearLayout"),
                     ("rl_tab_container", "android.widget.FrameLayout")):
        mode = parents.get(mode)
        if (mode not in visible or mode.get("resource-id") != ID+rid
                or mode.get("class") != cls or mode.get("selected") != "true"):
            return None

    def blank_group(n, rect=None, clickable="true"):
        return (n in visible and n.get("class") == "android.view.ViewGroup" and not label(n)
                and not n.get("resource-id") and n.get("clickable") == clickable
                and (rect is None or bounds(n) == rect))

    matches = []
    labels = ["高德推荐", "地铁优先", "步行少", "换乘少", "时间短", "不坐地铁"]
    for bar in visible:
        rect = bounds(bar)
        if (bar.get("class") != "com.autonavi.minimap.ajx3.widget.view.SpringHorizontalScrollView"
                or bar.get("scrollable") != "true" or bar.get("clickable") != "false"
                or label(bar) or bar.get("resource-id") or len(bar) != 1
                or rect[2] != shot["width"] or not shot["width"]*.5 <= rect[2]-rect[0] <= shot["width"]*.85
                or not 48 <= rect[3]-rect[1] <= 240 or rect[1] > shot["height"]*.35
                or bounds(public[0])[3] > rect[1]):
            continue
        owner, wrap = parents.get(bar), list(bar)[0]
        if (not blank_group(owner, rect) or list(owner) != [bar]
                or not blank_group(wrap, rect, "false") or len(wrap) != 1):
            continue
        items = list(wrap)[0]
        if (not blank_group(items, rect) or len(items) != len(labels)
                or [label(n) for n in items] != labels):
            continue
        valid, shown, underline_count, last_right = True, [], 0, rect[0]
        for item in items:
            if (item.get("package") != PACKAGE or item.get("class") != "android.view.ViewGroup"
                    or item.get("resource-id") or item.get("clickable") != "true" or item.get("enabled") == "false"):
                valid = False;break
            if item not in visible:
                if item.get("visible-to-user") != "false" or list(item):
                    valid = False;break
                continue
            box = bounds(item)
            children = list(item)
            if (not _contained(box, rect) or box[1::2] != rect[1::2] or box[0] < last_right
                    or len(children) not in {1, 2}):
                valid = False;break
            title = children[0]
            if (title not in visible or title.get("class") != "android.view.View" or list(title)
                    or title.get("clickable") != "false" or title.get("resource-id")
                    or title.get("text") != label(item) or title.get("content-desc") or not _contained(bounds(title), box)):
                valid = False;break
            if len(children) == 2:
                line = children[1];line_box = bounds(line)
                if (not blank_group(line) or list(line) or not _contained(line_box, box)
                        or not 1 <= line_box[3]-line_box[1] <= 12
                        or not (box[2]-box[0])*.2 <= line_box[2]-line_box[0] <= (box[2]-box[0])*.6
                        or line_box[1] < bounds(title)[3]):
                    valid = False;break
                underline_count += 1
            shown.append(label(item));last_right = box[2]
        if not valid or len(shown) < 2 or underline_count > 1:
            continue
        row = parents.get(owner)
        row_box = [0, rect[1], shot["width"], rect[3]]
        if not blank_group(row, row_box) or len(row) != 4 or list(row)[-1] is not owner:
            continue
        depart, horizontal, vertical, _ = list(row)
        depart_box = bounds(depart)
        if (not blank_group(depart) or len(depart) != 2 or depart_box != [0, rect[1], rect[0]-2, rect[3]]):
            continue
        caption, arrow = list(depart)
        if (caption not in visible or label(caption) != "现在出发" or list(caption)
                or caption.get("class") != "android.view.View" or caption.get("clickable") != "false"
                or arrow not in visible or arrow.get("class") != "android.widget.ImageView" or list(arrow)
                or label(arrow) or arrow.get("resource-id") or arrow.get("clickable") != "false"
                or not all(_contained(bounds(n), depart_box) for n in (caption, arrow))):
            continue
        hbox, vbox = bounds(horizontal), bounds(vertical)
        if (not blank_group(horizontal) or list(horizontal) or hbox[0] != 0 or hbox[2] != shot["width"]
                or not _contained(hbox, row_box) or not 1 <= hbox[3]-hbox[1] <= 3
                or not blank_group(vertical) or list(vertical) or vbox[0] != rect[0]-2 or vbox[2] != rect[0]
                or not _contained(vbox, row_box)):
            continue
        shell, outer = parents.get(row), parents.get(parents.get(row))
        if (not blank_group(shell, row_box) or list(shell) != [row]
                or not blank_group(outer, row_box) or len(outer) != 2 or list(outer)[1] is not shell):
            continue
        background = list(outer)[0]
        if (background not in visible or background.get("class") != "android.widget.ImageView"
                or background.get("clickable") != "false" or label(background) or background.get("resource-id")
                or list(background) or bounds(background) != row_box):
            continue
        matches.append((bar, {"bounds": list(rect), "gesture_bounds": row_box, "axis": "horizontal", "role": "preference_scroll",
                              "page": "route_list", "labels": shown}, set(outer.iter("node"))))
    if len(matches) != 1:
        return None
    return matches[0]


def preference_scroll_context(shot, request, state=None):
    """Current native horizontal preference region; no gesture or point chosen."""
    try:
        found = _preference_scroll_target(shot, request, state or {})
    except GroundingError:
        return None
    return found[1] if found is not None else None


def preference_swipe_geometry(rect, row):
    """Interior pixel rectangles shared by swipe grounding and model facts."""
    mx = min(32, max(8, int((rect[2]-rect[0])*.04)))
    my = min(24, max(8, int((rect[3]-rect[1])*.15)))
    return ([rect[0]+mx, rect[1]+my, rect[2]-mx, rect[3]-my],
            [row[0]+mx, row[1]+my, row[2]-mx, row[3]-my],
            max(60, (rect[2]-rect[0])*.15))


def _preference_swipe_clear(start, end, shot, bar, structural):
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    allowed = set(structural);current = bar
    while current is not None:
        allowed.add(current);current = parents.get(current)
    transparent = {
        "map_container": "android.widget.FrameLayout", "atmapsView": "android.widget.FrameLayout",
        "temporary_layer": "android.widget.FrameLayout", "notification_layer": "android.widget.FrameLayout",
        "floating_layer": "android.widget.RelativeLayout", "fl_splash_container": "android.widget.FrameLayout",
        "vui_layer": "android.widget.RelativeLayout", "route_view_shadow_bg": "android.view.View",
        "tab_container_id": "android.widget.FrameLayout", "widget_container": "android.widget.FrameLayout",
        "map_widget_container": "android.widget.RelativeLayout"}
    def overlaps_segment(rect):
        if rect is None:
            return False
        low, high = 0.0, 1.0
        for axis in (0, 1):
            delta = end[axis]-start[axis]
            if delta == 0:
                if not rect[axis] <= start[axis] < rect[axis+2]:
                    return False
            else:
                values = ((rect[axis]-start[axis])/delta, (rect[axis+2]-start[axis])/delta)
                low, high = max(low, min(values)), min(high, max(values))
                if low > high:
                    return False
        return low <= high
    for n in shot["nodes"]:
        if n in allowed or not overlaps_segment(bounds(n)):
            continue
        current, shown, foreign = n, True, False
        while current is not None:
            if current.get("visible-to-user") == "false" or current.get("enabled") == "false":
                shown = False;break
            foreign |= current.get("package", PACKAGE) != PACKAGE
            current = parents.get(current)
        if not shown:
            continue
        if foreign:
            return False
        inert = (not label(n) and n.get("clickable") == "false" and n.get("scrollable") != "true")
        rid = n.get("resource-id", "").removeprefix(ID)
        if inert and n.get("resource-id", "").startswith(ID) and n.get("class") == transparent.get(rid):
            continue
        parent = parents.get(n)
        if (inert and n.get("class") == "android.widget.RelativeLayout" and not n.get("resource-id")
                and parent is not None and parent.get("resource-id") == ID+"fl_content_view"
                and len(n) == 1 and list(n)[0].get("resource-id") == ID+"notification_layer"
                and bounds(list(n)[0]) == bounds(n) == bounds(parent)):
            continue  # The actual empty native notification-layer wrapper.
        native_map_leaf = {"atmapsView": "android.view.View", "temporary_layer": "android.widget.RelativeLayout",
                           "tab_container_id": "android.view.View"}
        if (inert and not list(n) and not n.get("resource-id") and parent is not None
                and list(parent) == [n] and parent.get("resource-id", "").startswith(ID)
                and n.get("class") == native_map_leaf.get(parent.get("resource-id").removeprefix(ID))
                and bounds(parent) == bounds(n)):
            continue
        return False
    return True


def _resolve_swipe(action, shot, request, state):
    start = normalized_point(action.params.get("start"), shot)
    end = normalized_point(action.params.get("end"), shot)
    if abs(end[0]-start[0]) > abs(end[1]-start[1]):
        found = _preference_scroll_target(shot, request, state)
        if found is None:
            raise GroundingError("AutoGLM 水平滑动不属于当前已核对的公交偏好栏，未执行")
        bar, context, structural = found;rect = context["bounds"]
        row = context["gesture_bounds"]
        interior, row_interior, min_dx = preference_swipe_geometry(rect, row)
        # Android keeps this drag's touch target after ACTION_DOWN. Its UP may
        # leave the child scroller to the left, but only within the same proven
        # native preference/time row. It cannot start on that time control.
        valid_end = (_inside(end, interior)
                     or end[0] < rect[0] and end[0] < start[0] and _inside(end, row_interior))
        dx, dy = abs(end[0]-start[0]), abs(end[1]-start[1])
        if (not _inside(start, interior) or not valid_end
                or dx < min_dx or dy > min(24, dx*.15)
                or not _preference_swipe_clear(start, end, shot, bar, structural)):
            raise GroundingError("AutoGLM 水平滑动必须完整位于唯一原生公交偏好栏内且无覆盖控件，未执行")
        return GroundedAction("Swipe", node=bar, role="preference_scroll", page="route_list", start=start,
                              end=end, duration=.42, binding={"preference_bar": True, "bounds": list(rect),
                                                            "gesture_bounds": list(row)})
    if math.dist(start, end) < 20 or abs(start[0] - end[0]) > abs(start[1] - end[1]) * .4:
        raise GroundingError("AutoGLM 仅允许查询容器内的明确纵向滚动")
    page = page_kind(shot)
    candidates = []
    for n in _visible(shot):
        if page == "route_detail":
            permitted = label(n) == "公交详情页面"
        else:
            permitted = page in {"search", "route_list"} and n.get("scrollable") == "true" and any(x in n.get("class", "") for x in ("RecyclerView", "ScrollView", "ListView"))
        r = bounds(n)
        if permitted and _inside(start, r) and _inside(end, r):
            # Keep away from the container's toolbar/footer edges.
            margin = min(48, int((r[3] - r[1]) * .08))
            if r[1] + margin <= min(start[1], end[1]) and max(start[1], end[1]) < r[3] - margin:
                candidates.append(n)
    if len(candidates) != 1:
        raise GroundingError("AutoGLM 滑动区域不属于唯一可核对的路线或搜索列表，未执行")
    return GroundedAction("Swipe", node=candidates[0], role="scroll", page=page, start=start, end=end, duration=.42)


def _loading_markers(shot):
    return any(n.get("resource-id") in {ID + "loading_view", ID + "loading_text"} for n in _visible(shot))


def _loading_underlay(node, modal, shot, parents):
    """Only a proven full-screen query page in an earlier native branch."""
    path = []
    current = node
    while current is not None:
        path.append(current);current = parents.get(current)
    modal_path = []
    current = modal
    while current is not None:
        modal_path.append(current);current = parents.get(current)
    common = next((n for n in path if n in modal_path), None)
    if common is None:
        branch, modal_branch = path[-1], modal_path[-1]
        order = [n for n in shot["nodes"] if n not in parents]
    else:
        if common is node or common is modal:
            return False
        branch = path[path.index(common)-1]
        modal_branch = modal_path[modal_path.index(common)-1]
        order = list(common)
    if (branch is modal_branch or order.index(branch) >= order.index(modal_branch)
            or bounds(branch) != [0, 0, shot["width"], shot["height"]]
            or any(n.get("package") != PACKAGE for n in branch.iter("node"))):
        return False
    context = {**shot, "nodes": list(branch.iter("node"))}
    return page_kind(context) not in {"unknown", "sensitive"}


def _resolve_loading_tap(action, shot, scene):
    point = normalized_point(action.params.get("element"), shot)
    if not _inside(point, scene["cancel_bounds"]):
        raise LoadingOverlayActive("高德仍在加载，只能等待、返回或点击当前加载框的取消按钮，未执行")
    cancel = scene["cancel_node"]
    parents = {child: parent for parent in shot["nodes"] for child in parent}
    ancestors = set();current = cancel
    while current is not None:
        ancestors.add(current);current = parents.get(current)
    # Include foreign/inert visible covers here: a model cannot authorize a
    # native cancel target through another surface merely because it has no
    # click listener. Only a proven earlier query page is behind this modal.
    for node in shot["nodes"]:
        if node in ancestors or not _inside(point, bounds(node)):
            continue
        current = node;shown = True
        while current is not None:
            if current.get("visible-to-user") == "false" or current.get("enabled") == "false":
                shown = False;break
            current = parents.get(current)
        if not shown:
            continue
        if node.get("package") != PACKAGE or not _loading_underlay(node, scene["view_node"], shot, parents):
            raise GroundingError("高德加载取消按钮存在无法核对的覆盖控件，未执行")
    return GroundedAction("Tap", node=cancel, point=point, role="loading_cancel", page="loading",
                          binding={"loading_cancel": True})


def resolve_action(action, shot, request, state=None):
    """Return one independently grounded proposal; never dispatch any action."""
    state = state or {}
    kind, params = getattr(action, "kind", None), getattr(action, "params", None)
    if not isinstance(params, dict):
        raise GroundingError("AutoGLM 动作对象无效，未执行")
    allowed_fields = {"Launch": {"app"}, "Tap": {"element", "message"}, "Type": {"text"},
                      "Swipe": {"start", "end"}, "Back": set(), "Wait": {"duration"},
                      "Finish": {"message"}, "Help": {"message"}}
    if kind not in allowed_fields or set(params) - allowed_fields[kind]:
        raise GroundingError("AutoGLM 动作参数超出允许范围，未执行")
    _geometry(shot)
    from autoglm.loading import loading_scene
    loading = loading_scene(shot)
    if loading is not None:
        if kind == "Back":
            return GroundedAction("Back", role="back", page="loading")
        if kind == "Tap":
            return _resolve_loading_tap(action, shot, loading)
        if kind not in {"Wait", "Help"}:
            raise LoadingOverlayActive("高德仍在加载，只能等待、返回或点击当前加载框的取消按钮，未执行")
    elif _loading_markers(shot) and kind not in {"Wait", "Help"}:
        raise GroundingError("高德加载弹窗未通过当前原生控件校验，未执行")
    if kind == "Launch":
        if params.get("app") not in {"高德地图", PACKAGE}:
            raise GroundingError("AutoGLM 只能启动本次授权的高德地图")
        return GroundedAction(kind, role="launch", binding={"package": PACKAGE})
    if kind == "Wait":
        duration = params.get("duration", 1)
        if type(duration) not in {int, float} or not math.isfinite(duration) or not 0 < duration <= 5:
            raise GroundingError("AutoGLM 等待超过允许时限")
        return GroundedAction(kind, duration=float(duration), page="loading" if loading is not None else "unknown")
    if kind == "Help":
        return GroundedAction(kind, text="AutoGLM 请求用户协助，请检查高德页面后重新查询")
    if shot.get("current_package") != PACKAGE:
        raise GroundingError("高德不是当前前台应用，未执行 AutoGLM 动作")
    page = page_kind(shot)
    if page == "sensitive":
        raise GroundingError("高德正在显示登录、权限或敏感页面，需要用户处理")
    if kind == "Tap":
        return _resolve_tap(action, shot, request, state)
    if kind == "Type":
        return _resolve_type(action, shot, request, state)
    if kind == "Swipe":
        return _resolve_swipe(action, shot, request, state)
    if kind == "Back":
        if page == "unknown":
            raise GroundingError("高德当前页面未在允许的查询上下文中，未执行返回")
        return GroundedAction(kind, role="back", page=page)
    if kind == "Finish":
        # This releases control to the independent collector, never certifies
        # route_verified or COMPLETE, regardless of the model's message.
        return GroundedAction(kind, page=page)
    raise GroundingError("AutoGLM 动作不在本次允许范围内，未执行")
