"""Independent query-bound verification of facts read from a map GUI.

The caller supplies trusted observations from the device. Model-returned evidence
is never accepted as a replacement for those observations in production.
"""
import html
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

from api.schemas import TransitResult, TransitRequest
from domain import WorkflowError


def normalized(value):
    return re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))))


def _number_matches(value, text, kind):
    text = normalized(text)
    if kind == "duration_minutes":
        values = []
        for hours, minutes, minutes_only in re.findall(r"(\d+(?:\.\d+)?)小时(?:(\d+(?:\.\d+)?)分钟?)?|(\d+(?:\.\d+)?)分钟?", text):
            values.append(float(hours) * 60 + float(minutes or 0) if hours else float(minutes_only))
    elif kind == "walking_meters":
        values = [float(x) * 1000 for x in re.findall(r"(\d+(?:\.\d+)?)(?:公里|千米|km)", text, re.I)]
        values += [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)(?:米|m)(?![A-Za-z])", text, re.I)]
    elif kind == "stops":
        values = [float(x) for x in re.findall(r"(\d+)站", text)]
    elif kind == "fare_yuan":
        values = [float(x) for x in re.findall(r"(?:[¥￥]\s*)?(\d+(?:\.\d+)?)元", text)]
        values += [float(x) for x in re.findall(r"[¥￥](\d+(?:\.\d+)?)", text)]
    else:
        values = [float(x) for x in re.findall(r"(?:换乘|转乘)(\d+)次", text)]
        if any(x in text for x in ["直达", "无需换乘", "不换乘"]):
            values.append(0)
    return any(abs(float(value) - x) < .001 for x in values)


def _walk_aggregate(text):
    text = normalized(text)
    if not any(word in text for word in ["步行", "走路", "行走"]):
        return False
    if re.search(r"(?:站内外(?:共)?|全程|总(?:计)?|合计|累计|共计|共)步行|步行(?:总计|合计|共计|总距离|总时长)", text):
        return True
    # Amap's persistent selected-route strip summarizes the whole itinerary.
    # Its descendants repeat the walk distance/time; those are not extra legs.
    ride = bool(re.search(r"\d+(?:号线|路)|APM|地铁|公交", text, re.I))
    return ride and (bool(re.match(r"\d+(?:\.\d+)?(?:小时|分钟?)", text))
                     or bool(re.search(r"[¥￥]\d|票价", text)))


def _location_healthy(record):
    """The device collector supplies this metadata, never model evidence."""
    status = record.get("location_status") or {}
    if (any(status.get(field) is not True for field in ["verified", "enabled", "permission_granted", "app_access_recent"])
            or status.get("provider") not in {"gps", "fused", "network"} or status.get("is_mock") is not False):
        return False
    age, accuracy = status.get("age_seconds"), status.get("accuracy_m")
    if (isinstance(age, bool) or not isinstance(age, (int, float)) or not 0 <= age <= 120
            or isinstance(accuracy, bool) or not isinstance(accuracy, (int, float)) or not 0 < accuracy <= 100):
        return False
    access_age = status.get("app_access_age_seconds")
    if ((status.get("appops_mode") is not None and status["appops_mode"] not in {"allow", "foreground"})
            or (access_age is not None and (isinstance(access_age, bool) or not isinstance(access_age, (int, float))
                                           or not 0 <= access_age <= 120))):
        return False
    try:
        times = [datetime.fromisoformat(status[field].replace("Z", "+00:00")) for field in ["checked_at", "fix_time"]]
    except (KeyError, AttributeError, TypeError, ValueError):
        return False
    if any(value.tzinfo is None or value.utcoffset() != timedelta(0) for value in times):
        return False
    delta = (times[0] - times[1]).total_seconds()
    # Android's checked_at may be rounded to a whole epoch second while the
    # actual fix retains milliseconds. Both timestamps use the device clock.
    return delta >= -1.1 and abs(delta - age) <= 10


def _xml_visible(node):
    box = [int(x) for x in re.findall(r"-?\d+", node.get("bounds", ""))]
    return (node.get("package") == "com.autonavi.minimap" and len(box) == 4
            and min(box) >= 0 and box[0] < box[2] and box[1] < box[3]
            and node.get("visible-to-user") != "false")


def _xml_label(node):
    return html.unescape(re.sub(r"<[^>]+>", "", node.get("content-desc") or node.get("text", ""))).strip()


def _xml_station(node):
    """A literal name wrapper, including an explicitly grouped portal."""
    title = _xml_label(node)
    leaves = [child for child in node.iter("node") if child is not node and not list(child) and _xml_label(child)]
    if (not _xml_visible(node) or node.get("class") != "android.view.ViewGroup" or not title or not leaves
            or any(not _xml_visible(child) for child in leaves)
            or re.search(r"\d+(?:站|分钟|小时|米|公里)|[¥￥]|导航|步行|换乘|方向$", title)):
        return None
    names = [_xml_label(child) for child in leaves]
    if len(names) == 1 and normalized(title) == normalized(names[0]):
        return {"name": title, "portal": None, "title": title}
    if (len(names) == 2 and normalized("".join(names)) == normalized(title)
            and re.fullmatch(r"(?:[A-Za-z]\d*|[东南西北](?:[一二三四五六七八九十\d]+号?)?)口", names[1])):
        return {"name": names[0], "portal": names[1], "title": title}
    return None


def _xml_direction(node):
    if node.get("class") != "android.view.ViewGroup" or not _xml_label(node).endswith("方向"):
        return None
    leaves = [child for child in node.iter("node") if not list(child) and _xml_label(child)]
    if not _xml_visible(node) or any(not _xml_visible(child) for child in leaves):
        return None
    line = "".join(_xml_label(child) for child in leaves if not _xml_label(child).endswith("方向"))
    if re.fullmatch(r"(?:地铁)?(?:\d+[A-Za-z]?号线(?:支线|内环|外环)?|APM线)", line):
        mode = "subway"
    elif re.fullmatch(r"(?:公交)?(?:夜|高峰快线|旅游)?[A-Za-z0-9]+路(?:[A-Za-z0-9支快短线]*)", line):
        mode = "bus"
    else:
        return None
    return mode, line, _xml_label(node).removesuffix("方向").removeprefix("开往").removeprefix("往")


def _edge_walk_binding(record, ride, walk, *, leading=False, current_origin_name=None):
    """Bind one visible edge triplet in an ordered native itinerary owner.

    Other legs can be offscreen. Only directly adjacent first/last branches
    count, never arbitrary page-wide names before and after a walking label.
    """
    if not record.get("xml_path") or not ride:
        return None
    try:
        root = ET.parse(Path(record["xml_path"])).getroot()
    except (OSError, ET.ParseError):
        return None
    parents = {child: parent for parent in root.iter() for child in parent}

    def path_visible(node, stop):
        while node is not stop:
            if not _xml_visible(node):
                return False
            node = parents.get(node)
            if node is None:
                return False
        return _xml_visible(stop)

    def named_leaves_visible(node, marker):
        return all(path_visible(child, marker) for child in node.iter("node") if not list(child) and _xml_label(child))

    bindings = []
    for marker in root.iter("node"):
        if (_xml_label(marker) != "公交详情页面" or marker.get("class") != "android.view.ViewGroup"
                or not _xml_visible(marker)):
            continue
        for owner in marker.iter("node"):
            if len(owner) < 3 or not path_visible(owner, marker):
                continue
            branches = list(owner)
            if leading:
                endpoint, walk_branch, ride_branch = branches[:3]
            else:
                ride_branch, walk_branch, endpoint = branches[-3:]
            if not all(path_visible(node, marker) for node in [ride_branch, walk_branch, endpoint]):
                continue
            terminal = _xml_station(endpoint)
            if not terminal or terminal["portal"] or not named_leaves_visible(endpoint, marker):
                continue
            literal_endpoint = normalized(walk.get("board" if leading else "alight"))
            endpoint_matches = literal_endpoint == normalized(terminal["title"])
            if (leading and normalized(terminal["title"]) in {"我的位置", "当前位置", "这里"}
                    and current_origin_name and literal_endpoint == normalized(current_origin_name)):
                endpoint_matches = True
            if not endpoint_matches:
                continue
            row_pattern = (r"步行\s*(\d+(?:\.\d+)?)(米|公里)\s*(?:[·•]\s*)?(\d+(?:\.\d+)?)分钟" if leading else
                           r"站内外共步行\s*(\d+(?:\.\d+)?)(米|公里)\s*(?:[·•]\s*)?(\d+(?:\.\d+)?)分钟")
            rows = [node for node in walk_branch.iter("node") if node.get("class") == "android.view.ViewGroup"
                    and re.fullmatch(row_pattern, _xml_label(node))]
            if len(rows) != 1 or not path_visible(rows[0], marker):
                continue
            total_leaves = [node for node in rows[0].iter("node") if not list(node) and re.fullmatch(row_pattern, _xml_label(node))]
            if len(total_leaves) != 1 or not path_visible(total_leaves[0], marker):
                continue
            row_label = _xml_label(rows[0])
            if normalized(re.sub(r"[·•]", "", _xml_label(total_leaves[0]))) != normalized(re.sub(r"[·•]", "", row_label)):
                continue
            allowed_quotes = {normalized(row_label)} if leading else {"站内外共步行", normalized(row_label)}
            if normalized(walk.get("instructions")) not in allowed_quotes:
                continue
            directions = [node for node in ride_branch.iter("node")
                          if node.get("class") == "android.view.ViewGroup" and _xml_label(node).endswith("方向")]
            if len(directions) != 1 or not path_visible(directions[0], marker):
                continue
            direction = directions[0]
            parsed = _xml_direction(direction)
            if (not parsed or not named_leaves_visible(direction, marker)
                    or tuple(ride.get(field) for field in ["mode", "line", "direction"]) != parsed):
                continue
            branch = direction
            columns = []
            while branch is not ride_branch:
                parent = parents[branch]
                children = list(parent)
                index = children.index(branch)
                before = [(node, _xml_station(node)) for node in children[:index]]
                after = [(node, _xml_station(node)) for node in children[index + 1:]]
                boards = [(node, station) for node, station in before if station]
                alights = [(node, station) for node, station in after if station]
                if len(boards) == len(alights) == 1:
                    board_node, board = boards[0]
                    alight_node, alight = alights[0]
                    if (path_visible(board_node, marker) and path_visible(alight_node, marker)
                            and named_leaves_visible(board_node, marker) and named_leaves_visible(alight_node, marker)
                            and _same_station(ride.get("board"), board["name"])
                            and _same_station(ride.get("alight"), alight["name"])
                            and (not ride.get("entrance") or normalized(ride["entrance"]) == normalized(board["portal"]))
                            and (not ride.get("exit") or normalized(ride["exit"]) == normalized(alight["portal"]))
                            and normalized(walk.get("alight" if leading else "board")) == normalized((board if leading else alight)["title"])):
                        metric = re.fullmatch(row_pattern, row_label)
                        columns.append({"start": terminal["title"] if leading else alight["name"],
                            "end": board["name"] if leading else terminal["title"], "instructions": row_label,
                            "walking_meters": float(metric.group(1)) * (1000 if metric.group(2) == "公里" else 1),
                            "duration_minutes": float(metric.group(3))})
                branch = parent
            if len(columns) == 1:
                bindings.append(columns[0])
    return bindings[0] if len(bindings) == 1 else None


def _trailing_walk_binding(record, previous, walk):
    binding = _edge_walk_binding(record, previous, walk)
    return (binding["start"], binding["end"]) if binding else None


def _leading_walk_binding(record, following, walk, *, current_origin_name=None):
    return _edge_walk_binding(record, following, walk, leading=True, current_origin_name=current_origin_name)


def _independent_walk_sources(record):
    """Literal independent walk labels, excluding route-summary subtrees.

    Text snapshots flatten/deduplicate nodes, so a summary's child cannot be
    distinguished from an independent leg by substring matching. Prefer the
    trusted XML hierarchy. Missing fixture XML uses conservative standalone
    source lines; an unreadable existing XML never falls back to flat text.
    """
    marker = lambda s: any(word in s for word in ["步行", "走到", "走至", "走路", "行走", "站内换乘"])
    path = Path(record["xml_path"]) if record.get("xml_path") else None
    if path is not None and path.exists():
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            return []
        parents = {child: parent for parent in root.iter() for child in parent}
        def visible(node):
            box = [int(x) for x in re.findall(r"-?\d+", node.get("bounds", ""))]
            return (node.get("package") == "com.autonavi.minimap" and len(box) == 4
                    and min(box) >= 0 and box[0] < box[2] and box[1] < box[3]
                    and node.get("visible-to-user") != "false")
        def aggregate_ancestor(node):
            while node is not None:
                if any(_walk_aggregate(node.get(key, "")) for key in ["content-desc", "text"]):
                    return True
                node = parents.get(node)
            return False
        sources = []
        for node in root.iter("node"):
            if not visible(node) or aggregate_ancestor(node):
                continue
            for key in ["content-desc", "text"]:
                text = node.get(key, "").strip()
                if marker(text) and normalized(text) not in {"步行", "步行导航", "开始步行导航"}:
                    sources.append(text)
        return list(dict.fromkeys(sources))
    lines = record["text"].splitlines()
    aggregates = [line for line in lines if _walk_aggregate(line)]
    return [line for line in lines if marker(line) and not _walk_aggregate(line)
            and normalized(line) not in {"步行", "步行导航", "开始步行导航"}
            # A short metric-only duplicate could be a child of the aggregate.
            # With no hierarchy, do not claim it is a separate walking leg.
            and not (aggregates and re.fullmatch(r"步行[约共\d.公里千米km小时分钟·]+", normalized(line), re.I))]


def _same_place(expected, actual, city=""):
    def clean(s):
        value = normalized(s).replace("（地铁站）", "").replace("(地铁站)", "").replace("地铁站", "")
        # Parentheses delimit a displayed qualifier; removing their glyphs
        # must retain every character of the campus/gate/branch qualifier.
        return re.sub(r"[（）()]", "", value)
    a, b = clean(expected), clean(actual)
    if a and b and a == b:
        return True
    # Remove only an explicit municipality prefix, never a bare city token
    # inside a POI name (e.g. 广州塔 must not silently become 塔).
    city_prefix = normalized(city).removesuffix("市") + "市" if city else ""
    if city_prefix and a.startswith(city_prefix):
        a = a[len(city_prefix):]
    # A campus name is not interchangeable with a longer gate/parking/branch
    # name. Such changes require an explicit current POI choice, not substring
    # acceptance by the independent request verifier.
    return bool(a and b and a == b)


def _station_key(value):
    value = normalized(value)
    for suffix in ["（地铁站）", "(地铁站)", "（公交站）", "(公交站)", "地铁站", "公交车站", "公交站"]:
        value = value.removesuffix(suffix)
    return value.removesuffix("站")


def _same_station(a, b):
    return bool(a and b and _station_key(a) and _station_key(a) == _station_key(b))


def _endpoint_at_stop(endpoint, stop):
    if not endpoint or not stop:
        return False
    if normalized(endpoint) == normalized(stop):
        return True
    # An explicitly typed station may omit its type suffix in the itinerary.
    # An arbitrary POI/city is never made equivalent by substring matching.
    return bool(re.search(r"(?:站[）)]?|地铁站|公交站)$", normalized(endpoint)) and _same_station(endpoint, stop))


def _walk_quote_connects(instructions, start, end):
    text = normalized(instructions)
    if not text or not start or not end or not any(word in text for word in ["步行", "走到", "走至", "走路", "行走"]):
        return False
    starts = {normalized(start), _station_key(start)} - {""}
    ends = {normalized(end), _station_key(end)} - {""}
    # A single source quote must explicitly mention the ordered connection.
    # Separate name facts on the same large page do not prove a walking link.
    return any((position := text.find(a)) >= 0 and text.find(b, position + len(a)) >= 0
               for a in starts for b in ends)


def _walk_chain_connects(steps, start, end):
    if not steps:
        return False
    current = start
    for index, step in enumerate(steps):
        if step["mode"] != "walk" or not step.get("instructions"):
            return False
        if step.get("board") and not _same_station(step["board"], current):
            return False
        target = step.get("alight") or (end if index == len(steps) - 1 else None)
        if not target or not _walk_quote_connects(step["instructions"], current, target):
            return False
        current = target
    return normalized(current) == normalized(end) or _same_station(current, end)


def _check_connections(segments, origin, destination, walk_bindings=None, leading_bindings=None):
    ride_indices = [i for i, segment in enumerate(segments) if segment["mode"] in {"bus", "subway"}]
    if not ride_indices:
        return []
    missing = []
    first, last = ride_indices[0], ride_indices[-1]
    if not _endpoint_at_stop(origin, segments[first].get("board")):
        head = segments[:first]
        binding = (leading_bindings or {}).get(head[0]["segment_id"]) if len(head) == 1 else None
        native_connection = bool(binding and normalized(binding[0]) == normalized(origin)
                                 and _same_station(binding[1], segments[first].get("board")))
        if not native_connection and not _walk_chain_connects(head, origin, segments[first].get("board")):
            missing.append("origin.walk_connection_not_verified")
    for previous, following in zip(ride_indices, ride_indices[1:]):
        start, end = segments[previous].get("alight"), segments[following].get("board")
        if not _same_station(start, end) and not _walk_chain_connects(segments[previous + 1:following], start, end):
            missing.append(f"segments.{segments[following]['segment_id']}.transfer_connection_not_verified")
    if not _endpoint_at_stop(destination, segments[last].get("alight")):
        tail = segments[last + 1:]
        binding = (walk_bindings or {}).get(tail[0]["segment_id"]) if len(tail) == 1 else None
        native_connection = bool(binding and _same_station(binding[0], segments[last].get("alight"))
                                 and normalized(binding[1]) == normalized(destination))
        if not native_connection and not _walk_chain_connects(tail, segments[last].get("alight"), destination):
            missing.append("destination.walk_connection_not_verified")
    return missing


def validate_result(result, task_id, query_id, request=None, *, evidence=None, collection_complete=False, selected_places=None):
    """Return a sanitized result; missing/ungrounded facts become PARTIAL.

    `evidence` must be the actual observation list collected by the adapter. If
    omitted, supplied result evidence can validate offline fixtures only: a
    COMPLETE result is never certified without the trusted argument.
    """
    raw = result.model_dump() if hasattr(result, "model_dump") else deepcopy(result)
    if raw.get("task_id") != task_id or raw.get("query_id") != query_id:
        raise WorkflowError("路线结果已过期或不属于当前任务")
    trusted = evidence is not None
    if trusted:
        raw["evidence"] = [e.model_dump() if hasattr(e, "model_dump") else e for e in evidence]
    try:
        data = TransitResult.model_validate(raw).model_dump()
    except (ValueError, TypeError) as error:
        raise WorkflowError("高德路线结果结构无法核对，请重新查询") from error
    route_id = data["route_id"]
    records = {}
    for e in data["evidence"]:
        if e["query_id"] != query_id:
            raise WorkflowError("路线证据来自另一轮查询")
        if e["evidence_id"] in records:
            raise WorkflowError("路线证据编号重复，不能可靠核对")
        records[e["evidence_id"]] = e
    # A collector/extractor can know it missed a leg even when the last screen
    # contains the destination. Never upgrade its explicit incompleteness into
    # a verified whole itinerary solely because the surviving fields match.
    missing = list(data["missing_fields"])
    reported_partial = data["classification"] == "PARTIAL"
    if reported_partial and not missing:
        missing.append("extractor_reported_partial")
    data["derived"] = {}
    excerpts = {}
    walk_sources = {}

    def proof(value, refs, field, *, detail=False, route=False, location=False, walk_instruction=None):
        selected = [records[r] for r in refs if r in records]
        selected = [e for e in selected if e["route_id"] in {None, route_id}
                    and (not detail or e["page_kind"] == "route_detail" and e["route_id"] == route_id)
                    and (not location or e["page_kind"] == "location" and _location_healthy(e))
                    and (not route or e["route_id"] == route_id and e["page_kind"] in {"route_list", "route_detail"})]
        if value is None or value == "" or not selected:
            return False
        for e in selected:
            texts = [e["text"]]
            if walk_instruction is not None:
                if not walk_instruction or _walk_aggregate(walk_instruction):
                    continue
                if e["evidence_id"] not in walk_sources:
                    walk_sources[e["evidence_id"]] = _independent_walk_sources(e)
                sources = walk_sources[e["evidence_id"]]
                texts = [text for text in sources if normalized(walk_instruction) in normalized(text)]
            for text in texts:
                valid = _number_matches(value, text, field) if isinstance(value, (int, float)) else normalized(value) in normalized(text)
                if not valid:
                    continue
                # Keep only short literal source fragments in task/WebSocket
                # snapshots. Full XML/screenshots remain referenced on disk.
                lines = [line for line in text.splitlines() if normalized(value) in normalized(line)]
                if not lines and isinstance(value, (int, float)):
                    lines = [line for line in text.splitlines() if _number_matches(value, line, field)]
                excerpts.setdefault(e["evidence_id"], []).extend(lines or [text[:300]])
                if isinstance(value, (int, float)):
                    data["derived"][field] = f"原文：{(lines or [text[:300]])[0][:200]}；数值 {value}，小时×60为分钟，公里×1000为米，其他单位保持原值"
                return True
        return False

    for role in ["origin", "destination"]:
        place = data[role]
        for field in ["name", "address", "city"]:
            value = place[field]
            if value and not proof(value, place["evidence_refs"].get(field, []), field):
                place[field] = "" if field == "name" else None
                missing.append(f"{role}.{field}")
        if not place["name"]:
            missing.append(f"{role}.name")

    if request is not None:
        req = TransitRequest.model_validate(request).model_dump()
        data["preference"] = req["preference"]
        for role in ["origin", "destination"]:
            if req[role] and not _same_place(req[role], data[role]["name"], req["city"]):
                missing.append(f"{role}.does_not_match_request")
        if req["city"]:
            city = normalized(req["city"]).removesuffix("市")
            for role in ["origin", "destination"]:
                actual_city = normalized(data[role]["city"]).removesuffix("市")
                if actual_city != city:
                    missing.append(f"{role}.city_not_verified")
        if req["use_current_location"] and not req["origin"]:
            place = data["origin"]
            location_valid = (place["source"] == "current_location"
                              and normalized(place["name"]) not in {"我的位置", "当前位置", "这里", ""})
            for field in ["name", "address", "city"]:
                if not place[field] or not proof(place[field], place["evidence_refs"].get(field, []), field, location=True):
                    place[field] = "" if field == "name" else None
                    missing.append(f"origin.{field}.current_location_not_verified")
                    location_valid = False
            if not location_valid:
                missing.append("origin.current_location_not_verified")

    for role, chosen in (selected_places or {}).items():
        if role not in {"origin", "destination"}:
            continue
        for field in ["name", "address", "city"]:
            expected = chosen.get(field)
            actual = data[role].get(field)
            if expected and normalized(expected) != normalized(actual):
                missing.append(f"{role}.selected_{field}_does_not_match")
    if data["applied_filter"] and not proof(data["applied_filter"], data["evidence_refs"].get("applied_filter", []), "applied_filter", route=True):
        data["applied_filter"] = None
    data["selection_basis"] = (f"高德当前实际使用“{data['applied_filter']}”筛选；仅限本次读取方案，不表示全局最优"
                               if data["applied_filter"] else "采用本次高德实际打开的方案；未核实偏好筛选，不声称少换乘或步行最少")
    # A route's literal “我的位置” alias is resolved only by this query's
    # independently healthy location observations, including all three facts.
    current_origin_refs = {}
    origin = data["origin"]
    if trusted and origin["source"] == "current_location" and normalized(origin["name"]) not in {"我的位置", "当前位置", "这里", ""}:
        for field in ["name", "address", "city"]:
            if origin[field]:
                current_origin_refs[field] = [eid for eid in origin["evidence_refs"].get(field, [])
                    if eid in records and records[eid]["page_kind"] == "location"
                    and records[eid]["route_id"] in {None, route_id} and _location_healthy(records[eid])
                    and normalized(origin[field]) in normalized(records[eid]["text"])]
    current_origin_verified = all(current_origin_refs.get(field) for field in ["name", "address", "city"])
    valid_segments = []
    seen_rides = set()
    previous_walk = None
    walk_bindings = {}
    leading_bindings = {}
    for index, seg in enumerate(data["segments"]):
        prefix = f"segments.{seg['segment_id']}"
        walk_instruction = seg["instructions"] if seg["mode"] == "walk" else None
        aggregate_walk = bool(walk_instruction and normalized(walk_instruction).startswith("站内外共步行"))
        leading_walk = bool(index == 0 and walk_instruction and re.fullmatch(
            r"步行\d+(?:\.\d+)?(?:米|公里)(?:[·•])?\d+(?:\.\d+)?分钟", normalized(walk_instruction)))
        binding = None
        leading_binding = None
        leading_eid = None
        if leading_walk and trusted and len(data["segments"]) > 1 and data["segments"][1]["mode"] in {"bus", "subway"}:
            refs = [set(seg["evidence_refs"].get(field, [])) for field in ["instructions", "board", "alight"]]
            for eid in sorted(set.intersection(*refs)):
                record = records.get(eid)
                if not record or record["page_kind"] != "route_detail" or record["route_id"] != route_id:
                    continue
                candidate = _leading_walk_binding(record, data["segments"][1], seg,
                    current_origin_name=origin["name"] if current_origin_verified else None)
                if not candidate:
                    continue
                alias = normalized(candidate["start"]) in {"我的位置", "当前位置", "这里"}
                if (alias and not current_origin_verified) or (not alias and normalized(candidate["start"]) != normalized(origin["name"])):
                    continue
                leading_binding, leading_eid = candidate, eid
                for field in ["instructions", "board", "alight"]:
                    seg["evidence_refs"][field] = [eid]
                excerpts.setdefault(eid, []).extend([candidate["start"], candidate["instructions"], seg["alight"]])
                if alias:
                    seg["board"] = origin["name"]
                    seg["evidence_refs"]["board"] += current_origin_refs["name"]
                seg["instructions"] = candidate["instructions"]
                leading_bindings[seg["segment_id"]] = (origin["name"], candidate["end"])
                start_description = "本次已核实当前位置" if alias else seg["board"]
                data["derived"][f"{prefix}.walk_connection"] = (f"同一高德详情的相邻行程分支连接{start_description}与{seg['alight']}；"
                                                                "独立步行行保留原文，具体街道走法未显示")
                break
        if aggregate_walk and trusted and index == len(data["segments"]) - 1 and valid_segments and valid_segments[-1]["mode"] in {"bus", "subway"}:
            refs = [set(seg["evidence_refs"].get(field, [])) for field in ["instructions", "board", "alight"]]
            for eid in sorted(set.intersection(*refs)):
                record = records.get(eid)
                if record and record["page_kind"] == "route_detail" and record["route_id"] == route_id:
                    binding = _trailing_walk_binding(record, valid_segments[-1], seg)
                    if binding:
                        for field in ["instructions", "board", "alight"]:
                            seg["evidence_refs"][field] = [eid]
                        excerpts.setdefault(eid, []).extend([seg["board"], "站内外共步行", seg["alight"]])
                        break
        if binding:
            seg["instructions"] = "站内外共步行"
            walk_bindings[seg["segment_id"]] = binding
            data["derived"][f"{prefix}.walk_connection"] = (f"同一高德详情的相邻行程分支连接{seg['board']}与{seg['alight']}；"
                                                            "具体街道走法未显示；站内外总计未分配为此段距离或时间")
        for field in ["line", "direction", "board", "alight", "stops", "instructions", "entrance", "exit", "duration_minutes", "walking_meters"]:
            value = seg[field]
            walk_field = seg["mode"] == "walk" and field in {"instructions", "duration_minutes", "walking_meters"}
            if leading_walk and field in {"board", "alight", "instructions", "duration_minutes", "walking_meters"}:
                if leading_binding:
                    if field in {"board", "alight", "instructions"}:
                        continue
                    if value is not None and (leading_eid not in seg["evidence_refs"].get(field, [])
                            or abs(value - leading_binding[field]) > .001):
                        seg[field] = None
                        missing.append(f"{prefix}.{field}.ungrounded")
                    continue
                if field in {"board", "alight"} and value is not None and value != "":
                    seg[field] = None
                    missing.append(f"{prefix}.{field}.ungrounded")
                    continue
            if aggregate_walk and field in {"instructions", "board", "alight", "duration_minutes", "walking_meters"}:
                if binding and field in {"instructions", "board", "alight"}:
                    continue
                if value is not None and value != "":
                    seg[field] = None
                    missing.append(f"{prefix}.{field}.ungrounded")
                continue
            if value is not None and value != "" and not proof(value, seg["evidence_refs"].get(field, []), field, detail=True,
                    walk_instruction=(walk_instruction or "") if walk_field else None):
                seg[field] = None
                if field in {"line", "direction", "board", "alight", "instructions"} or walk_field:
                    missing.append(f"{prefix}.{field}.ungrounded")
        if seg["mode"] in {"bus", "subway"}:
            identity = tuple(seg[f] for f in ["mode", "line", "direction", "board", "alight"])
            if identity in seen_rides:
                continue
            seen_rides.add(identity)
            previous_walk = None
            for field in ["line", "direction", "board", "alight"]:
                if not seg[field]:
                    missing.append(f"{prefix}.{field}")
        else:
            identity = (seg["instructions"], seg["walking_meters"], seg["board"], seg["alight"])
            if identity == previous_walk:
                continue
            previous_walk = identity
            if not seg["instructions"]:
                missing.append(f"{prefix}.instructions")
        valid_segments.append(seg)
    data["segments"] = valid_segments
    rides = [s for s in valid_segments if s["mode"] in {"bus", "subway"}]
    missing.extend(_check_connections(valid_segments, data["origin"]["name"], data["destination"]["name"], walk_bindings, leading_bindings))
    for field in ["duration_minutes", "walking_meters", "fare_yuan"]:
        if data[field] is not None and not proof(data[field], data["evidence_refs"].get(field, []), field, route=True):
            data[field] = None
    if rides:
        # A transfer is derived from deduplicated ordered riding segments; do
        # not count walking sections or repeated frames as another transfer.
        data["transfers"] = max(0, len(rides) - 1) if collection_complete and not missing else None
        data["derived"]["transfers"] = (f"本次详情中去重后的乘车段数量 {len(rides)} - 1；步行段不计换乘"
                                           if data["transfers"] is not None else "路线或连接尚未核对完整，不能计算总换乘次数")
    else:
        data["transfers"] = None
        missing.append("transit_segments")
    detail = any(e["page_kind"] == "route_detail" and e["route_id"] == route_id for e in records.values())
    if not detail:
        missing.append("route_detail_not_observed")
    if not collection_complete:
        missing.append("detail_collection_incomplete")
    if not trusted:
        missing.append("trusted_device_evidence_not_supplied")
    # Claims of NO_ROUTE require an actual observed absence message, not an
    # empty list or a model's assertion.
    if data["classification"] == "NO_ROUTE":
        absent = any(re.search(r"暂无(?:公交|公共交通|可用)?(?:方案|路线)|未找到.*(?:公交|公共交通|路线)|无(?:公交|公共交通)(?:方案|路线)|没有.*公交(?:方案|路线)", e["text"]) for e in records.values())
        if not trusted or not absent:
            data["classification"] = "PARTIAL"
            missing.append("no_route_not_observed")
    elif data["classification"] != "FAILED":
        data["classification"] = "COMPLETE" if not missing else "PARTIAL"
    data["missing_fields"] = list(dict.fromkeys(missing))
    data["route_verified"] = data["classification"] == "COMPLETE" and trusted
    for e in data["evidence"]:
        e["text"] = "\n".join(dict.fromkeys(excerpts.get(e["evidence_id"], [e["text"][:300]])))[:2000]
    return data
