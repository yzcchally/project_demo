"""Conservative extraction of observed Amap 15.13 native transit columns.

This is a fast path for readable native rail/bus details. It reads no route
service and contains no station/line database. Unrecognized walking, incomplete
riding columns or disconnected transfers return None for the existing grounded
model path; a route summary alone never creates an itinerary.
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
import xml.etree.ElementTree as ET

PACKAGE = "com.autonavi.minimap"


def _clean(value):
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()


def _label(n):
    return _clean(n.get("content-desc") or n.get("text", ""))


def _visible(n):
    b = [int(x) for x in re.findall(r"-?\d+", n.get("bounds", ""))]
    return (n.get("package") == PACKAGE and n.get("visible-to-user") != "false"
            and n.get("enabled") != "false"
            and len(b) == 4 and min(b) >= 0 and b[0] < b[2] and b[1] < b[3])


def _key(value):
    return re.sub(r"\s+", "", _clean(value)).replace("（地铁站）", "").replace("(地铁站)", "").removesuffix("地铁站")


def _line(direction):
    # Amap renders e.g. '3' and '号线' as adjacent leaves inside the direction
    # group. Read those leaves in tree order, excluding its direction label.
    leaves = [_label(n) for n in direction.iter("node") if not list(n) and _visible(n)
              and _label(n) and not _label(n).endswith("方向") and _label(n) != "到站时刻表"]
    text = "".join(leaves)
    if re.fullmatch(r"(?:地铁)?(?:\d+[A-Za-z]?号线(?:支线|内环|外环)?|APM线)", text):
        return "subway", text
    if re.fullmatch(r"(?:公交)?(?:夜|高峰快线|旅游)?[A-Za-z0-9]+路(?:[A-Za-z0-9支快短线]*)", text):
        return "bus", text
    return None


def _direction(node, line):
    text = _label(node)
    if text.startswith(line):
        remainder = text[len(line):]
        if not any(not list(n) and _visible(n) and _label(n) == remainder for n in node.iter("node")):
            return None
        text = remainder
    return text.removesuffix("方向").removeprefix("开往").removeprefix("往")


def _station_fields(n):
    text = _label(n)
    if (not _visible(n) or n.get("class") != "android.view.ViewGroup" or not text or len(text) > 100
            or text.endswith("方向") or re.search(r"\d+(?:站|分钟|小时|米|公里)|[¥￥]|导航|更多|保存|收藏|公交详情|步行|换乘|刷新", text)):
        return None
    # A station group itself labels the stop and contains its name leaf; labels
    # on a broad ancestor or an arbitrary text elsewhere are insufficient.
    role = None
    for prefix, value in (("上车站", "board"), ("下车站", "alight")):
        if text.startswith(prefix):
            text, role = text[len(prefix):], value
            break
    leaves = [_label(c) for c in n.iter("node") if c is not n and not list(c) and _visible(c) and _label(c)]
    if not text:
        return None
    if text in leaves or _key(text) in {_key(x) for x in leaves}:
        return {"name": text, "portal": None, "role": role}
    # Amap explicitly groups an exit with its station, e.g. desc 珠江新城C口
    # and exactly two leaves 珠江新城 / C口. Never infer an exit from an
    # unrelated label elsewhere on the page or from a station-name database.
    if (len(leaves) == 2 and "".join(leaves) == text
            and re.fullmatch(r"(?:[A-Za-z]\d*|[东南西北](?:[一二三四五六七八九十\d]+号?)?)口", leaves[1])):
        return {"name": leaves[0], "portal": leaves[1], "role": role}
    return None


def _station(n):
    value = _station_fields(n)
    return value["name"] if value else None


def destination_station(n):
    fields = _station_fields(n)
    return fields["name"] if fields and fields["role"] == "alight" else ""


def _column(direction, parents, *, with_container=False):
    candidate = direction
    while candidate in parents:
        parent = parents[candidate]
        children = list(parent)
        if candidate not in children:
            return None
        index = children.index(candidate)
        before = children[:index]
        after = children[index + 1:]
        # Each recognized column has one direction and explicitly labeled stops
        # on either side of its direction branch, within the same parent.
        if len([n for n in parent.iter("node") if _visible(n) and _label(n).endswith("方向") and _line(n)]) == 1:
            boards = [station for n in before if (station := _station_fields(n))]
            alights = [station for n in after if (station := _station_fields(n))]
            if len(boards) == len(alights) == 1:
                roles = (boards[0]["role"], alights[0]["role"])
                if roles not in {(None, None), ("board", "alight")}:
                    return None
                board, alight = boards[0]["name"], alights[0]["name"]
                if _key(board) == _key(alight):
                    return None
                metrics = [_label(n) for child in after for n in child.iter("node") if _visible(n)]
                stop_values = {int(x) for s in metrics for x in re.findall(r"(\d+)站", s)}
                minutes = {float(x) for s in metrics for x in re.findall(r"(\d+(?:\.\d+)?)分钟", s)}
                values = {"board": board, "alight": alight,
                          "stops": next(iter(stop_values)) if len(stop_values) == 1 else None,
                          "duration_minutes": next(iter(minutes)) if len(minutes) == 1 else None}
                entrance, exit_ = boards[0]["portal"], alights[0]["portal"]
                if entrance: values["entrance"] = entrance
                if exit_: values["exit"] = exit_
                return (values, parent) if with_container else values
        candidate = parent
    return None


def _transfer_metrics(text):
    match = re.fullmatch(r"站内换乘\s*(\d+(?:\.\d+)?)(米|公里)\s*(?:[·•]\s*)?(\d+(?:\.\d+)?)分钟", text)
    if not match:
        return None
    return float(match.group(1)) * (1000 if match.group(2) == "公里" else 1), float(match.group(3))


def _bound_station_transfer(row, rides, parents):
    branch = row
    while branch in parents:
        owner = parents[branch]
        children = list(owner)
        index = children.index(branch)
        before = [ride for ride in rides if any(ride["container"] in c.iter() for c in children[:index])]
        after = [ride for ride in rides if any(ride["container"] in c.iter() for c in children[index + 1:])]
        if len(before) == len(after) == 1:
            a, b = before[0], after[0]
            if _key(a["segment"]["alight"]) == _key(b["segment"]["board"]):
                return a, b
        branch = owner
    return None


def _tail_walk_label(text):
    # This is the displayed walking slot, not a claim that the route's total
    # meters/minutes belong entirely to the station-to-POI leg.
    return bool(re.fullmatch(r"站内外共步行\s*\d+(?:\.\d+)?(?:公里|米)\s*(?:[·•]\s*)?\d+(?:\.\d+)?分钟", text))


def _leading_walk_metrics(text):
    match = re.fullmatch(r"步行\s*(\d+(?:\.\d+)?)(公里|米)\s*(?:[·•]\s*)?(\d+(?:\.\d+)?)分钟", text)
    if not match:
        return None
    return float(match[1]) * (1000 if match[2] == "公里" else 1), float(match[3])


def _bound_leading_walk(row, rides, parents):
    branch = row
    while branch in parents:
        owner = parents[branch]
        children = list(owner)
        if len(children) >= 3 and children[1] is branch and all(_visible(c) for c in children[:3]):
            origin, _, after = children[:3]
            walk_rows = [n for n in branch.iter("node") if _visible(n) and n.get("class") == "android.view.ViewGroup"
                         and (_leading_walk_metrics(_label(n)) is not None or _tail_walk_label(_label(n)))]
            if walk_rows != [row] or any(_line(n) for n in branch.iter("node") if _visible(n) and _label(n).endswith("方向")):
                return None
            source = _station(origin)
            following = [ride for ride in rides if ride["container"] in after.iter()]
            if source is None or len(following) != 1:
                return None
            directions = [n for n in after.iter("node") if _visible(n) and _label(n).endswith("方向") and _line(n)]
            if len(directions) != 1:
                return None
            ride = following[0]
            segment = ride["segment"]
            portals = {_label(n) for n in ride["container"].iter("node")
                       if (fields := _station_fields(n)) and fields["name"] == segment["board"]
                       and fields["portal"] == segment.get("entrance")}
            if len(portals) != 1:
                return None
            return source, ride, next(iter(portals))
        branch = owner
    return None


def _bound_tail_walk(row, rides, parents):
    branch = row
    while branch in parents:
        owner = parents[branch]
        children = list(owner)
        # Actual Amap itinerary topology: one complete riding block, the
        # walking slot, then a POI terminal. A broad ancestor with arbitrary
        # earlier/later labels cannot establish this relation.
        index = children.index(branch)
        if len(children) >= 3 and index == len(children)-2 and all(_visible(c) for c in children[-3:]):
            before, _, after = children[-3:]
            walk_rows = [n for n in branch.iter("node") if _visible(n) and n.get("class") == "android.view.ViewGroup"
                         and (_leading_walk_metrics(_label(n)) is not None or _tail_walk_label(_label(n)))]
            if walk_rows != [row] or any(_line(n) for n in branch.iter("node") if _visible(n) and _label(n).endswith("方向")):
                return None
            previous = [ride for ride in rides if ride["container"] in before.iter()]
            if len(previous) != 1 or any(ride["container"] in after.iter() for ride in rides):
                return None
            directions = [n for n in before.iter("node") if _visible(n) and _label(n).endswith("方向") and _line(n)]
            if len(directions) != 1:
                return None
            terminals = {_station(n) for n in after.iter("node") if _station(n)}
            if len(terminals) != 1 or any(_label(n).endswith("方向") for n in after.iter("node") if _visible(n)):
                return None
            ride = previous[0]
            segment = ride["segment"]
            portals = {_label(n) for n in ride["container"].iter("node")
                       if (fields := _station_fields(n)) and fields["name"] == segment["alight"]
                       and fields["portal"] == segment.get("exit")}
            if len(portals) != 1:
                return None
            return ride, next(iter(portals)), next(iter(terminals))
        branch = owner
    return None


def _bound_known_transfer(row, segments, parents):
    """Bind a visible interchange to rides already fully read in this query.

    Long itineraries do not fit two whole columns in one frame. A frame may
    still show the previous alighting wrapper, the explicit transfer row and
    the next boarding wrapper in adjacent native branches. This never creates
    a ride from two partial columns; both complete rides must already exist.
    """
    branch = row
    while branch in parents:
        owner = parents[branch]
        children = list(owner)
        index = children.index(branch)
        if 0 < index < len(children)-1:
            before, middle, after = children[index-1:index+2]
            if all(_visible(c) for c in [before,middle,after]):
                stops_before = [fields for n in before.iter("node") if (fields := _station_fields(n))]
                stops_after = [fields for n in after.iter("node") if (fields := _station_fields(n))]
                if stops_before and stops_after:
                    alight, board = stops_before[-1], stops_after[0]
                    prior = [s for s in segments if _key(s["alight"]) == _key(alight["name"])]
                    following = [s for s in segments if _key(s["board"]) == _key(board["name"])]
                    if (len(prior) == len(following) == 1 and prior[0]["segment_id"] != following[0]["segment_id"]
                            and _key(alight["name"]) == _key(board["name"])):
                        matches = True
                        for block, segment in [(before,prior[0]),(after,following[0])]:
                            for direction in block.iter("node"):
                                if (not _visible(direction) or direction.get("class") != "android.view.ViewGroup"
                                        or not _label(direction).endswith("方向")):
                                    continue
                                line = _line(direction)
                                identity = (*line, _direction(direction, line[1])) if line else None
                                if identity != tuple(segment[k] for k in ["mode","line","direction"]):
                                    matches = False
                        if matches:
                            return {"segment":prior[0]}, {"segment":following[0]}
        branch = owner
    return None


def _request_endpoint_matches(requested, actual, city):
    expected = _key(requested)
    prefix = city.removesuffix("市") + "市" if city else ""
    if prefix and expected.startswith(prefix):
        expected = expected[len(prefix):]
    return expected == _key(actual)


def _duration(text):
    m = re.match(r"(?:(\d+(?:\.\d+)?)小时)?(?:(\d+(?:\.\d+)?)分(?:钟)?)?", text)
    if not m or not any(m.groups()):
        return None
    return float(m.group(1) or 0) * 60 + float(m.group(2) or 0)


def _numbered_summary(observations, query_id, route_id, segments):
    for record in observations:
        if (record.get("query_id") != query_id or record.get("route_id") != route_id
                or record.get("page_kind") != "route_list"):
            continue
        header, *body = record.get("text", "").splitlines() or [""]
        match = re.fullmatch(
            r"方案[1-9]\d*，(\d+(?:\.\d+)?(?:小时(?:\d+(?:\.\d+)?分钟)?|分钟))，"
            r"(?:[^，]+，)*?(\d+(?:\.\d+)?)元，[^，]+", header,
        )
        lines = {part for part in body if not part.startswith(("起点：", "终点：", "本轮已点击筛选："))}
        if match and lines == {segment["line"] for segment in segments}:
            return record["evidence_id"], _duration(match[1]), float(match[2])
    return None


def extract_native(observations, request, *, task_id, query_id, route_id):
    """Return a candidate fact structure, or None. Verification remains separate.

    Only direct, same-frame native stop→line/direction→stop relationships count.
    Repeated frames deduplicate a ride. Every emitted fact references the actual
    observation which supplied it; COMPLETE is still independently gated by the
    adapter's full collection signal and the normal validation function.
    """
    request = request.model_dump() if hasattr(request, "model_dump") else request
    details = [e for e in observations if e.get("query_id") == query_id and e.get("route_id") == route_id
               and e.get("page_kind") == "route_detail"]
    if not details:
        return None
    segments, seen, described_lines = [], {}, set()
    summaries = []
    transfer_bindings, observed_transfers = {}, set()
    tail_bindings, observed_tail_rows = {}, set()
    leading_bindings, observed_leading_rows = {}, set()
    walking_seen = False
    for e in details:
        path = e.get("xml_path")
        if not path:
            return None
        try:
            root = ET.parse(Path(path)).getroot()
        except (OSError, ET.ParseError):
            return None
        ns = [n for n in root.iter("node") if _visible(n)]
        if not any(_label(n) == "公交详情页面" for n in ns):
            return None
        parents = {c: p for p in root.iter() for c in p}
        aggregate_groups = [n for n in ns if n.get("class") == "android.view.ViewGroup" and _duration(_label(n)) is not None
                            and ("号线" in _label(n) or "路" in _label(n)) and ("站" in _label(n) or "¥" in _label(n) or "￥" in _label(n))]
        transfer_rows = [n for n in ns if n.get("class") == "android.view.ViewGroup" and _transfer_metrics(_label(n)) is not None]
        tail_rows = [n for n in ns if n.get("class") == "android.view.ViewGroup" and _tail_walk_label(_label(n))]
        leading_rows = [n for n in ns if n.get("class") == "android.view.ViewGroup" and _leading_walk_metrics(_label(n)) is not None]
        if len(transfer_rows) > 1:
            return None  # Only one explicit same-station interchange is supported.
        if len(tail_rows) > 1 or len(leading_rows) > 2:
            return None
        # Sets below deduplicate repeated screenshots; they must not collapse
        # two indistinguishable walking rows in the same frame.
        if len({_label(n) for n in leading_rows}) != len(leading_rows):
            return None
        allowed_walk_nodes = {c for n in aggregate_groups + transfer_rows + tail_rows + leading_rows for c in n.iter()}
        walking_labels = [n for n in ns if ("步行" in _label(n) or "站内换乘" in _label(n)) and _label(n) not in {"步行少", "少步行"}]
        if any(n not in allowed_walk_nodes for n in walking_labels):
            return None
        walking_seen = walking_seen or bool(walking_labels)
        observed_transfers.update(_label(n) for n in transfer_rows)
        observed_tail_rows.update(_label(n) for n in tail_rows)
        observed_leading_rows.update(_label(n) for n in leading_rows)
        frame_records = []
        directions = [n for n in ns if n.get("class") == "android.view.ViewGroup" and _label(n).endswith("方向")]
        frame_rides = set()
        for n in directions:
            parsed_line = _line(n)
            if parsed_line is None:
                return None
            mode, line = parsed_line
            direction = _direction(n, line)
            if not direction:
                return None
            described_lines.add((mode, line, direction))
            matched_column = _column(n, parents, with_container=True)
            if matched_column is None:
                continue  # A later frame must expose this exact ride completely.
            column, container = matched_column
            identity = (mode, line, direction, column["board"], column["alight"])
            if identity in frame_rides:
                return None  # Cannot distinguish a loop/repeated leg from duplicate UI.
            frame_rides.add(identity)
            refs = {field: [e["evidence_id"]] for field, value in {"line": line, "direction": direction, **column}.items() if value is not None}
            segment_id = hashlib.sha256(repr(identity).encode()).hexdigest()[:20]
            segment = {"segment_id": segment_id, "mode": mode, "line": line, "direction": direction,
                       **column, "evidence_refs": refs}
            frame_records.append({"container": container, "segment": segment})
            if identity not in seen:
                segments.append(segment)
                seen[identity] = True
        for row in transfer_rows:
            bound = _bound_station_transfer(row, frame_records, parents) or _bound_known_transfer(row, segments, parents)
            if bound is None:
                continue  # Another full frame must bind this same literal row.
            a, b = bound
            pair = (a["segment"]["segment_id"], b["segment"]["segment_id"])
            quote = _label(row)
            meters, minutes = _transfer_metrics(quote)
            station = a["segment"]["alight"]
            transfer_bindings[pair] = {"segment_id": hashlib.sha256((repr(pair) + quote).encode()).hexdigest()[:20],
                "mode": "walk", "instructions": quote, "board": station, "alight": station,
                "walking_meters": meters, "duration_minutes": minutes,
                "evidence_refs": {field: [e["evidence_id"]] for field in ["instructions", "board", "alight", "walking_meters", "duration_minutes"]}}
        for row in leading_rows:
            bound = _bound_leading_walk(row, frame_records, parents)
            if bound is None:
                continue
            source, ride, terminal = bound
            if source in {"我的位置","当前位置","起点"}:
                if request.get("origin") or not request.get("use_current_location"):
                    continue
            elif not _request_endpoint_matches(request.get("origin", ""), source, request.get("city", "")):
                continue
            identity = (ride["segment"]["segment_id"], source, terminal)
            quote = _label(row)
            meters, minutes = _leading_walk_metrics(quote)
            leading_bindings[identity] = {
                "segment_id": hashlib.sha256(repr(identity).encode()).hexdigest()[:20],
                "mode": "walk", "instructions": quote, "board": source, "alight": terminal,
                "walking_meters": meters, "duration_minutes": minutes,
                "evidence_refs": {field: [e["evidence_id"]] for field in ["instructions","board","alight","walking_meters","duration_minutes"]},
            }
        # Some itineraries show a separate measured walking leg at both
        # ends, rather than the aggregate “站内外共步行” tail slot. Bind
        # each row by its own adjacent itinerary branches, never by position
        # in the flattened page text.
        for row in tail_rows + leading_rows:
            bound = _bound_tail_walk(row, frame_records, parents)
            if bound is None:
                continue
            ride, board, terminal = bound
            if not _request_endpoint_matches(request.get("destination", ""), terminal, request.get("city", "")):
                continue
            identity = (ride["segment"]["segment_id"], board, terminal)
            quote = _label(row)
            metrics = _leading_walk_metrics(quote)
            tail_bindings[identity] = {
                "segment_id": hashlib.sha256(repr(identity).encode()).hexdigest()[:20],
                "mode": "walk", "instructions": quote if metrics else "站内外共步行", "board": board, "alight": terminal,
                "walking_meters": metrics[0] if metrics else None, "duration_minutes": metrics[1] if metrics else None,
                "evidence_refs": {field: [e["evidence_id"]] for field in (["instructions", "board", "alight", "walking_meters", "duration_minutes"] if metrics else ["instructions", "board", "alight"])},
            }
        # Duration/fare must come from the native aggregate summary, not a
        # boarding countdown or one segment's minutes.
        for n in ns:
            text = _label(n)
            if (n.get("class") == "android.view.ViewGroup" and _duration(text) is not None
                    and ("号线" in text or "路" in text) and ("站" in text or "¥" in text or "￥" in text)):
                summaries.append((e["evidence_id"], text))
    if not segments or described_lines != {(s["mode"], s["line"], s["direction"]) for s in segments}:
        return None
    # Only explicitly same-stop transfers are supported by this fast path.
    # Any different-station interchange needs a walking segment/full parser.
    if any(_key(a["alight"]) != _key(b["board"]) for a, b in zip(segments, segments[1:])):
        return None
    # The first/last riding stops must agree with the explicitly selected station
    # endpoints; a general POI route might need unparsed walking at either end.
    leading = next(iter(leading_bindings.values())) if len(leading_bindings) == 1 else None
    if leading_bindings and (leading is None or next(iter(leading_bindings))[0] != segments[0]["segment_id"]):
        return None
    if (request.get("origin") and leading is None and _key(request["origin"]) not in _key(segments[0]["board"])):
        city_prefix = request.get("city", "").removesuffix("市") + "市"
        expected = _key(request["origin"])
        if not (request.get("city") and expected.startswith(city_prefix) and expected[len(city_prefix):] in _key(segments[0]["board"])):
            return None
    tail = next(iter(tail_bindings.values())) if len(tail_bindings) == 1 else None
    if tail_bindings and (tail is None or next(iter(tail_bindings))[0] != segments[-1]["segment_id"]):
        return None
    if request.get("destination") and tail is None and _key(request["destination"]) not in _key(segments[-1]["alight"]):
        city_prefix = request.get("city", "").removesuffix("市") + "市"
        expected = _key(request["destination"])
        if not (request.get("city") and expected.startswith(city_prefix) and expected[len(city_prefix):] in _key(segments[-1]["alight"])):
            return None
    eid, summary = summaries[-1] if summaries else (None, "")
    fare = re.search(r"[¥￥](\d+(?:\.\d+)?)", summary)
    duration = _duration(summary)
    fare = float(fare.group(1)) if fare else None
    if not summary and (numbered := _numbered_summary(observations, query_id, route_id, segments)):
        eid, duration, fare = numbered
    walking = None
    if walking_seen or observed_transfers or observed_tail_rows or observed_leading_rows:
        walk = None
        if observed_transfers:
            if len(segments) != 2:
                return None
            pair = (segments[0]["segment_id"], segments[1]["segment_id"])
            walk = transfer_bindings.get(pair)
            if walk is None or len(transfer_bindings) != 1 or observed_transfers != {walk["instructions"]}:
                return None
        if observed_tail_rows and (tail is None or len(observed_tail_rows) != 1):
            return None
        bound_normal = {v["instructions"] for v in (leading, tail) if v and _leading_walk_metrics(v["instructions"])}
        if observed_leading_rows != bound_normal:
            return None
        if walk is None and tail is None and leading is None:
            return None
        distance = re.search(r"步行(?:约)?\s*(\d+(?:\.\d+)?)(公里|米)", summary)
        if distance is None:
            return None  # No guessing that an unreported endpoint walk is absent.
        walking = float(distance.group(1)) * (1000 if distance.group(2) == "公里" else 1)
        if walk is not None and tail is None and leading is None and abs(walking - walk["walking_meters"]) > .001:
            return None
        if walk is not None:
            segments.insert(1, walk)
        if tail is not None:
            segments.append(tail)
        if leading is not None:
            segments.insert(0, leading)
    preference = request.get("preference", "recommended")
    filter_label = {"fewer_transfers": "换乘少", "less_walking": "步行少", "recommended": "高德推荐"}[preference]
    filtered = next((e for e in observations if e.get("query_id") == query_id and e.get("route_id") == route_id
                     and e.get("page_kind") == "route_list" and f"本轮已点击筛选：{filter_label}" in e.get("text", "")), None)
    result = {"task_id": task_id, "query_id": query_id, "route_id": route_id, "source_app": "高德地图",
              "origin": {"name": ""}, "destination": {"name": ""}, "preference": preference,
              "applied_filter": filter_label if filtered else None,
              "duration_minutes": duration, "fare_yuan": fare,
              "walking_meters": walking, "transfers": len([segment for segment in segments if segment["mode"] in {"bus", "subway"}]) - 1, "segments": segments,
              "evidence": observations, "evidence_refs": {}, "classification": "COMPLETE", "route_verified": False}
    if duration is not None: result["evidence_refs"]["duration_minutes"] = [eid]
    if fare is not None: result["evidence_refs"]["fare_yuan"] = [eid]
    if walking is not None: result["evidence_refs"]["walking_meters"] = [eid]
    if filtered: result["evidence_refs"]["applied_filter"] = [filtered["evidence_id"]]
    return result
