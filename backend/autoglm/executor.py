"""Query-scoped AutoGLM GUI loop under SilverGuide's existing device lease.

No PhoneAgent.run/step, route service, coordinate replay, or rules fallback.
Finish only requests extraction of real, independently collected GUI evidence.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import time
import uuid
import xml.etree.ElementTree as ET

from domain import WorkflowError, now
from device.amap import AmapDevice, PACKAGE, ID, bounds, label, place_key
from device.huawei import ScreenContextChanged
from perception.search_text import task_search_text_matches


def png_size(data):
    if not isinstance(data, bytes) or len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise WorkflowError("AutoGLM 未取得有效 PNG 截图")
    width, height = struct.unpack(">II", data[16:24])
    if not 100 <= width <= 16384 or not 100 <= height <= 16384:
        raise WorkflowError("AutoGLM 截图尺寸无效")
    return width, height


def screen_signature(shot):
    # Arrival times and overlays are not silently removed. A changed screen
    # causes a new model decision, never execution of a stale coordinate.
    try:
        root = ET.fromstring(shot.get("xml", ""))
        # Bridge nonces and capture timestamps change on every read; they
        # are transport metadata, not screen content. Window identity stays.
        content = json.dumps([sorted((key, value) for key, value in root.attrib.items() if key not in {"nonce", "captured-at"}), [(sorted(n.attrib.items()), len(n))
                             for n in root.iter("node")]], ensure_ascii=False)
    except ET.ParseError:
        content = shot.get("xml", "")
    return (shot["epoch"], shot["width"], shot["height"], shot["rotation"],
            shot["current_app"], hashlib.sha256(content.encode()).hexdigest(),
            hashlib.sha256(shot.get("screenshot_base64", "").encode()).hexdigest())


def proposal_is_current(action, before, after, *, request=None, state=None):
    first, fresh = screen_signature(before), screen_signature(after)
    if first[:5] != fresh[:5]:
        return False  # epoch, dimensions, rotation and foreground remain binding.
    if action.kind in {"Launch", "Wait", "Help"}:
        # These actions do not target screenshot content. Launch is separately
        # restricted to the authorized Amap package, Wait never touches GUI.
        # The host's own progress repaint must not deadlock app launching.
        return True
    if first[:6] != fresh[:6]:
        return False  # Every native attribute/window identity remains exact.
    if first == fresh:
        return True
    from autoglm.freshness import visual_decorations_only
    return visual_decorations_only(action, before, after, request=request, state=state)


class _NativeCycleGuard:
    """Bound repeated executed native transitions, independent of pixel animations.

    This guard only stops work; it never authorizes a stale action. Swipe is
    excluded because a native tree may not represent moving visual content.
    """
    def __init__(self):
        self.transitions = {}
        self.progress = set()

    def note_progress(self, tokens):
        new = set(tokens) - self.progress
        if new:
            self.transitions.clear()
            self.progress.update(new)

    def repeated(self, action, grounded, before, after):
        if action.kind not in {'Tap', 'Type', 'Back'}:
            return False
        a, b = screen_signature(before), screen_signature(after)
        if a[:5] != b[:5]:
            self.transitions.clear()
            return False
        key = (action.kind, getattr(grounded, 'role', ''), a[:6], b[:6])
        count = self.transitions.get(key, 0) + 1
        self.transitions[key] = count
        return count >= 3


class _PlaceEvidenceRequired(WorkflowError):
    """A known entry would bypass the existing POI evidence prerequisite."""


class AutoGLMTransitDevice(AmapDevice):
    def __init__(self, base_device, models, *, client=None, config=None):
        super().__init__(base_device, models)
        from autoglm.config import Config
        from autoglm.client import AutoGLMClient
        self.config = config or Config.from_env()
        self.config.validate()
        self.client = client or AutoGLMClient(self.config)
        self.total_seconds = self.config.total_seconds
        self.history = []
        self.steps = 0
        self.agent_progress_callback = None
        self.state = {}
        self.spent = 0.0

    def _record(self, **record):
        record.update(time=now(), task_id=self.task_id, query_id=self.query_id, executor="autoglm")
        with (self.folder / "autoglm-actions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.agent_progress_callback:
            self.agent_progress_callback(record)

    def _geometry(self):
        size = self.base.shell("wm", "size")
        sizes = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", size)
        orientation = self.base.shell("dumpsys", "input")
        rotations = set(re.findall(r"SurfaceOrientation:\s*([0-3])\b", orientation))
        if not sizes or len(rotations) != 1:
            raise WorkflowError("AutoGLM 无法核对真实屏幕尺寸或方向，请保持手机竖屏后重查")
        width, height = map(int, sizes[-1]); rotation = int(next(iter(rotations)))
        if rotation % 2: width, height = height, width
        return width, height, rotation

    def _foreground(self):
        text = self.base.shell("dumpsys", "activity", "activities")
        match = re.search(r"mResumedActivity:.*?\s([A-Za-z0-9_.]+)/[^\s}]+", text)
        if not match:
            raise WorkflowError("AutoGLM 无法核对当前前台应用")
        return match.group(1)

    def _agent_observe(self, checkpoint):
        self._check(checkpoint)
        epoch = self.base.context_epoch
        foreground = self._foreground()
        width, height, rotation = self._geometry()
        if foreground == PACKAGE:
            shot = super()._observe(checkpoint, "route_detail")
        else:
            oid = uuid.uuid4().hex
            evidence = {"evidence_id": oid, "query_id": self.query_id, "route_id": None,
                        "observation_id": oid, "page_kind": "screen", "text": "当前前台应用：" + foreground}
            shot = {"nodes": [], "xml": "", "epoch": epoch, "evidence": evidence}
            self.observations.append(evidence)
        png = self._png(); actual = png_size(png)
        self._check(checkpoint)
        if epoch != self.base.context_epoch or self._foreground() != foreground or self._geometry() != (width, height, rotation):
            raise ScreenContextChanged("AutoGLM 观察期间上下文变化，请重新观察")
        if actual != (width, height):
            raise WorkflowError("AutoGLM 截图与设备坐标尺寸不一致，已停止操作")
        path = self.folder / (shot["evidence"]["evidence_id"] + ".png"); path.write_bytes(png)
        shot["evidence"]["screenshot_path"] = str(path)
        shot.update(width=width, height=height, screen_width=width, screen_height=height,
                    rotation=rotation, current_app=foreground, current_package=foreground,
                    observation_id=shot["evidence"]["observation_id"], screenshot_base64=base64.b64encode(png).decode())
        # Independent OS insets and the existing native input guard prove
        # the only visual decorations eligible for freshness comparison. No
        # screenshot region/role supplied by a model is accepted here.
        from autoglm.freshness import status_bar_frame
        self._check(checkpoint)
        try:
            insets = self.base.shell("dumpsys", "window", "displays", timeout=8)
        except Exception as error:
            from domain import Cancelled
            if isinstance(error, (Cancelled, ScreenContextChanged)):
                raise
            insets = ""
        self._check(checkpoint)
        if self.base.context_epoch != epoch:
            raise ScreenContextChanged("AutoGLM 装饰元数据读取期间上下文变化，旧截图已废弃")
        shot["status_bar_frame"] = status_bar_frame(insets, width, height)
        if foreground == PACKAGE:
            from autoglm.safety import focused_input_context
            context = focused_input_context(shot, self.request, self.state)
            if context is not None:
                shot["caret_context"] = {"role": context["role"],
                    "resource_id": context["target"]["resource_id"], "bounds": context["target"]["bounds"]}
        (self.folder / (shot["observation_id"] + ".decorations.json")).write_text(json.dumps({
            "status_bar_frame": shot["status_bar_frame"], "caret_context": shot.get("caret_context"),
            "observation_id": shot["observation_id"], "epoch": epoch, "width": width, "height": height,
            "rotation": rotation, "current_app": foreground}, ensure_ascii=False), encoding="utf-8")
        if (foreground == PACKAGE
                and (any("附近搜索" in label(n) for n in shot["nodes"])
                     or AmapDevice._compact_poi_header(shot) is not None)
                and any(label(n) == "路线" and not bounds(n) for n in shot["nodes"])):
            # Only supplement an independently observed, known zero-bounds
            # POI footer. This is actual local OCR, never model-provided boxes.
            shot["ocr_targets"] = self._ocr(path).get("items", [])
            self._check(checkpoint)
            if self.base.context_epoch != epoch:
                raise ScreenContextChanged("AutoGLM OCR 期间上下文变化，旧截图已废弃")
        return shot

    def _reset_context(self):
        self.route_id = None
        self.history.clear()
        self.observations[:] = [e for e in self.observations if e["page_kind"] == "user_request"]
        self.state.update(places={}, selected_list=None, location_verified=False, input_role="", filter="", candidates={}, transit_applied=False)
        self.state.pop("input_correction", None)
        self.state.pop("evidence_correction", None)
        self.state.pop("route_correction", None)
        self.state.pop("target_correction", None)
        self.state.pop("model_correction", None)

    def _facts(self, shot, checkpoint):
        """Observe identities only; this function never clicks or types."""
        if shot["current_app"] != PACKAGE: return None
        from autoglm.loading import loading_scene
        if loading_scene(shot) is not None:
            return None  # The modal's background is not a current POI readback.
        from autoglm.safety import _single_query_header
        if _single_query_header(shot) is not None:
            # Native autocomplete rows belong to the active one-field editor,
            # not a submitted search. In particular a gate-only suggestion
            # must not preempt the model's Search action with a human choice.
            # This scene is proved from the actual editor/header/body, never
            # from a remembered Type action, candidate name or keyboard state.
            return None
        ns = self._visible_place_nodes(shot["nodes"])
        places = self.state["places"]
        compact = self._compact_poi_header(shot)
        if not self.request.get("origin") and self.request.get("use_current_location"):
            location = self._location_place(shot)
            if location:
                from device.location_status import read_location_status
                health = read_location_status(self.base, checkpoint)
                self._check(checkpoint)
                (self.folder / "location-status.json").write_text(json.dumps(health, ensure_ascii=False, indent=2))
                if not health.get("verified"):
                    return {"kind": "clarification", "fields": ["origin"],
                            "message": health.get("reason") or "当前位置健康核验未通过，请填写明确起点"}
                if self.request.get("city") and place_key(location["city"]) != place_key(self.request["city"]):
                    return {"kind": "clarification", "fields": ["origin", "city"], "message": "当前位置城市与请求不同，请核对地点"}
                from api.schemas import TransitLocationStatus
                public_health = {key: value for key, value in health.items() if key in TransitLocationStatus.model_fields}
                shot["evidence"].update(page_kind="location", location_status=public_health)
                places["origin"] = location; self.state["location_verified"] = True
                self.state["location_health"] = public_health
                self.state["location_checked_at"] = time.monotonic()
        for role in ("origin", "destination"):
            query = self.request.get(role)
            if not query: continue
            values = self._place_candidates(shot, query)
            # A search result belongs to a role only when the actual search
            # caption matches that role's query, with only the native station
            # parenthesis format and exact city prefix treated as equivalent.
            captions = {query, self.request.get("city", "") + query}
            searching = not compact and any(
                task_search_text_matches(label(n).removeprefix("搜索框，"), caption, self.request.get("city", ""))
                for n in ns if n.get("enabled") != "false" and
                (label(n).startswith("搜索框，") or n.get("class") == "android.widget.EditText")
                for caption in captions)
            if values and searching:
                exact = [(c, row) for c, row in values if place_key(c["name"]) == place_key(query, self.request.get("city", ""))]
                chosen = self.state["choices"].get(role)
                if chosen:
                    exact = [(c, row) for c, row in values if c["candidate_id"] == chosen.get("candidate_id")]
                    if len(exact) != 1:
                        raise WorkflowError("AutoGLM 当前地点与用户已选名称或地址不同，请重新选择")
                elif len(exact) != 1:
                    shot["evidence"]["page_kind"] = "place_list"
                    return {"kind": "choice", "role": role, "candidates": [c for c, row in values]}
                self.state.setdefault("candidates", {})[role] = exact[0][0]
                shot["evidence"]["page_kind"] = "place_list"
            nearby = any("附近搜索" in label(n) for n in ns)
            titles = [label(n) for n in ns if n.get("class") == "android.view.ViewGroup" and bounds(n)
                      and bounds(n)[1] < 800 and place_key(label(n)) == place_key(query, self.request.get("city", ""))]
            compact_match = compact and place_key(compact["name"]) == place_key(query, self.request.get("city", ""))
            if compact_match or nearby and len(set(titles)) == 1:
                shot["evidence"]["page_kind"] = "place_detail"
                extra = {"header_bounds": compact["bounds"]} if compact_match else {}
                place = self._read_place(shot, compact["name"] if compact_match else titles[0],
                                         selected=self.state["choices"].get(role),
                                         candidate=self.state.get("candidates", {}).get(role), **extra)
                selected = self.state["choices"].get(role)
                if selected and (place["name"] != selected["name"] or place["address"] != selected.get("address")):
                    raise WorkflowError("AutoGLM 地点详情与用户确认地点不同")
                places[role] = place
                self.state.pop("loading_recovery", None)
        return None

    def _location_fresh(self, checkpoint):
        if self.request.get("origin"): return True
        health = self.state.get("location_health", {})
        elapsed = time.monotonic() - self.state.get("location_checked_at", float("-inf"))
        age = health.get("age_seconds")
        if not self.state.get("location_verified") or not isinstance(age, (int, float)) or age + elapsed > 120:
            return False
        # Re-check system health without attaching a newer fix to an older GUI
        # address. The original query-bound evidence is kept unchanged.
        from device.location_status import read_location_status
        current = read_location_status(self.base, checkpoint)
        self._check(checkpoint)
        return current.get("verified") is True

    def _place_status(self):
        places = self.state.get("places", {})
        selected = self.state.get("choices", {})
        status = {}
        for role in ("origin", "destination"):
            place = places.get(role, {})
            required = {"name", "city"}
            if place.get("source") == "current_location" or selected.get(role, {}).get("address"):
                required.add("address")
            refs = place.get("evidence_refs", {})
            status[role] = {field: place.get(field) for field in ("name", "address", "city", "source")}
            status[role]["missing_fields"] = sorted(field for field in required if not place.get(field) or not refs.get(field))
            status[role]["missing_optional_fields"] = ["address"] if "address" not in required and not place.get("address") else []
        return status

    def _missing_place_fields(self):
        return {role: place["missing_fields"] for role, place in self._place_status().items() if place["missing_fields"]}

    def _place_evidence_retry_count(self):
        """Renew bounded retries only after strictly reducing missing proof.

        Moving around or rereading a page cannot renew this budget. Keeping
        the smallest recorded set also prevents oscillation or context loss
        from repeatedly granting retries for the same evidence.
        """
        missing = frozenset((role, field) for role, fields in self._missing_place_fields().items()
                            for field in fields)
        stored = self.state.get("place_evidence_missing_baseline")
        previous = frozenset(map(tuple, stored)) if stored is not None else None
        if previous is None or missing < previous:
            self.state["place_evidence_missing_baseline"] = sorted(missing)
            if previous is not None:
                self.state["place_evidence_recoveries"] = 0
        return self.state.get("place_evidence_recoveries", 0)

    def _model_screen_info(self, shot):
        """Factual collection progress, not a prescribed GUI action sequence."""
        places = self.state.get("places", {})
        status = self._place_status()
        missing = self._missing_place_fields()
        correction = ({"missing_roles": list(missing), "missing_fields": missing}
                      if self.state.get("evidence_correction") and missing else None)
        destination = places.get("destination", {}).get("name")
        city = getattr(self, "request", {}).get("city", "")
        endpoint = bool(destination and shot.get("current_app") == PACKAGE and any(
            place_key(label(node), city) == place_key(destination, city) and bounds(node) and bounds(node)[1] > 650
            and node.get("class") == "android.view.ViewGroup"
            for node in self._visible_place_nodes(shot.get("nodes", []))))
        focused = [node for node in shot.get("nodes", []) if shot.get("current_app") == PACKAGE
                   and node.get("package") == PACKAGE and "EditText" in node.get("class", "")
                   and node.get("focused") == "true" and node.get("enabled") != "false"
                   and node.get("visible-to-user") != "false" and node.get("password") != "true"
                   and bounds(node)]
        from autoglm.safety import focused_input_context
        focused_input = focused_input_context(shot, getattr(self, "request", {}), self.state)
        loading = self._loading_info(shot)
        from autoglm.hints import native_control_hints, _normalized_bounds
        from autoglm.safety import GroundingError, page_kind, resolve_action, _query_matches, preference_scroll_context, preference_swipe_geometry
        try:
            current_page = page_kind(shot) if shot.get("current_app") == PACKAGE else "foreign"
        except GroundingError:
            current_page = "unknown"  # Incomplete metadata grants no route controls.
        on_route_page = current_page in {"route_input", "route_list"}
        # A cached place is not necessarily the currently displayed POI. Only
        # this frame's independently collected name AND city can describe it
        # to the model; old observations cannot authorize a navigation cue.
        current_place, navigation = None, []
        evidence = shot.get("evidence", {})
        current_id = evidence.get("evidence_id") or evidence.get("observation_id")
        current_query = evidence.get("query_id")
        if (current_page == "place_detail" and not loading["visible"] and current_id
                and current_query and current_query == getattr(self, "query_id", None)):
            displayed = []
            for role in ("origin", "destination"):
                place = places.get(role, {})
                refs = place.get("evidence_refs", {})
                actual_city, requested_city = place.get("city"), self.request.get("city")
                city_matches = (isinstance(actual_city, str) and bool(actual_city)
                                and (not requested_city or actual_city.removesuffix("市") == requested_city.removesuffix("市")))
                if (status[role]["missing_fields"] == [] and city_matches
                        and all(current_id in refs.get(field, []) for field in ("name", "city"))):
                    displayed.append({"role": role, "name": place["name"], "city": actual_city})
            if len(displayed) == 1:
                current_place = displayed[0]
                from autoglm.protocol import Action
                try:
                    resolve_action(Action("Back", {}), shot, self.request, self.state)
                    navigation.append("Back")
                except GroundingError:
                    pass
        route_inputs, route_matches = {}, {}
        visible = self._visible_place_nodes(shot.get("nodes", [])) if on_route_page else []
        for role, suffix in (("origin", "start"), ("destination", "end")):
            fields = [n for n in visible if n.get("resource-id") == ID + "route_edit_summary_" + suffix
                      and n.get("class") == "android.widget.EditText"]
            if len(fields) == 1:
                route_inputs[role] = fields[0].get("text", "")
                route_matches[role] = bool(_query_matches(shot, role, self.request, self.state))
        transit_tabs = [n for n in visible if n.get("resource-id") == ID + "route_input_conbine_text"
                        and n.get("class") == "android.widget.TextView" and n.get("text") == "公共交通"]
        observed_transit = (transit_tabs[0].get("selected") == "true") if len(transit_tabs) == 1 else None
        hints = native_control_hints(shot, getattr(self, "request", {}), self.state)
        scroll = preference_scroll_context(shot, getattr(self, "request", {}), self.state)
        scroll_regions = []
        if scroll is not None:
            normalized = _normalized_bounds(scroll["bounds"], shot["width"], shot["height"])
            gesture = _normalized_bounds(scroll.get("gesture_bounds", scroll["bounds"]), shot["width"], shot["height"])
            start_pixels, end_pixels, min_dx = preference_swipe_geometry(scroll["bounds"], scroll["gesture_bounds"])
            safe_start = _normalized_bounds(start_pixels, shot["width"], shot["height"])
            safe_end = _normalized_bounds(end_pixels, shot["width"], shot["height"])
            import math
            min_normalized_dx = math.ceil((min_dx + 1) * 1000 / shot["width"])
            if normalized is not None:
                scroll_regions.append({"role": "preference_scroll", "axis": "horizontal",
                                       "bounds": scroll["bounds"], "normalized_bounds": normalized,
                                       "gesture_bounds": scroll.get("gesture_bounds", scroll["bounds"]),
                                       "gesture_normalized_bounds": gesture,
                                       "safe_start_normalized_bounds": safe_start,
                                       "safe_end_normalized_bounds": safe_end,
                                       "minimum_leftward_normalized_distance": min_normalized_dx})
        if missing:
            hints = [item for item in hints if item["role"] not in {"route", "transit"}]
        return {"checked_places": list(places), "place_status": status,
                "page_kind": current_page, "current_place": current_place,
                "available_navigation_actions": navigation,
                "native_control_hints": hints,
                "horizontal_scroll_regions": scroll_regions,
                "on_route_page": on_route_page, "route_inputs": route_inputs,
                "route_inputs_match": route_matches, "observed_public_transit": observed_transit,
                "route_correction": self.state.get("route_correction"),
                "target_correction": self.state.get("target_correction"),
                "model_correction": self.state.get("model_correction"),
                "unique_focused_input": len(focused) == 1 and not loading["visible"],
                "focused_input": focused_input if not loading["visible"] else None,
                "loading": loading, "loading_correction": self.state.get("loading_correction"),
                "loading_recovery": self.state.get("loading_recovery"),
                "input_correction": self.state.get("input_correction"),
                "evidence_correction": correction,
                "last_input_role": self.state.get("input_role") or None,
                "selected_route": bool(self.route_id), "applied_filter": self.state.get("filter") or None,
                "public_transit_applied": bool(self.state.get("transit_applied")),
                "collected_detail_frames": sum(1 for item in self.observations if self.route_id
                    and item.get("route_id") == self.route_id and item.get("query_id") == self.query_id
                    and item.get("page_kind") == "route_detail"),
                "visible_destination_endpoint": endpoint and not loading["visible"],
                "required_evidence": "核对两端主地点详情；实际应用偏好并点击方案；滚动读取完整公共交通详情"}

    def _loading_info(self, shot):
        from autoglm.loading import loading_scene
        scene = loading_scene(shot)
        if scene is None:
            for key in ("loading_identity", "loading_since", "loading_correction"):
                self.state.pop(key, None)
            return {"visible": False, "age_seconds": 0, "cancel_bounds": None}
        identity = (shot.get("epoch"), shot.get("width"), shot.get("height"), shot.get("rotation"),
                    shot.get("current_app"), tuple(scene["bounds"]), tuple(scene["cancel_bounds"]))
        if self.state.get("loading_identity") != identity:
            self.state["loading_identity"] = identity
            self.state["loading_since"] = time.monotonic()
        return {"visible": True, "age_seconds": round(max(0, time.monotonic() - self.state["loading_since"]), 1),
                "cancel_bounds": scene["cancel_bounds"]}

    def _retry_unknown_target(self, action, shot, record, checkpoint):
        """Reject an ungrounded proposal without dispatching or relocating it."""
        self._check(checkpoint)
        if not self._model_screen_info(shot)["native_control_hints"]:
            return False
        recoveries = self.state.get("unknown_target_recoveries", 0)
        retry = recoveries < 2
        self._record(**{**record, "status": "rejected", "reason_code": "unrecognized_target",
                        "recovery_scheduled": retry})
        if not retry:
            raise WorkflowError("模型多次选择未核实的高德入口，已停止；这些点击均未执行") from None
        self.state["unknown_target_recoveries"] = recoveries + 1
        self.state["target_correction"] = {"reason_code": "unrecognized_target", "executed": False,
                                           "proposed_point": action.params["element"]}
        return True

    def _selected_route(self, shot, grounded):
        if grounded.node is None: return
        if grounded.role != "route" or grounded.node not in self._route_cards(shot): return
        if not all(r in self.state["places"] for r in ("origin", "destination")):
            raise WorkflowError("AutoGLM 尚未从高德独立核对两端地点详情，请先查看地点")
        names = {}
        visible = self._visible_place_nodes(shot["nodes"])
        for role, suffix in (("origin", "start"), ("destination", "end")):
            fields = [n for n in visible if n.get("resource-id") == ID + "route_edit_summary_" + suffix
                      and n.get("class") == "android.widget.EditText" and n.get("enabled") != "false"]
            if len(fields) != 1:
                raise WorkflowError("AutoGLM 无法唯一核对路线列表起终点输入栏")
            names[role] = fields[0].get("text", "")
            expected = self.state["places"][role]["name"] if self.request.get(role) else "我的位置"
            city = self.request.get("city", "")
            if place_key(names[role], city) != place_key(expected, city):
                raise WorkflowError("AutoGLM 高德路线列表起终点与本轮请求不一致")
        preference = {"fewer_transfers": "换乘少", "less_walking": "步行少", "recommended": "高德推荐"}[self.request.get("preference", "recommended")]
        if self.state.get("filter") != preference:
            raise WorkflowError("AutoGLM 尚未实际应用本轮路线偏好")
        self.route_id = hashlib.sha256((self.query_id + label(grounded.node)).encode()).hexdigest()[:24]
        source = {**shot["evidence"], "evidence_id": uuid.uuid4().hex, "route_id": self.route_id,
                  "page_kind": "route_list", "text": label(grounded.node) + "\n起点：" + names["origin"] + "\n终点：" + names["destination"] + "\n本轮已点击筛选：" + preference}
        return source

    def _execute(self, grounded, checkpoint):
        epoch = self.base.context_epoch
        self._action(checkpoint)
        if epoch != self.base.context_epoch:
            raise ScreenContextChanged("AutoGLM 操作前暂停恢复，旧目标已废弃")
        if grounded.kind == "Launch":
            self.base.shell("monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        elif grounded.kind == "Tap":
            if self.base.transport: self.base.transport.tap(*grounded.point)
            else: self.base.shell("input", "tap", *(str(v) for v in grounded.point))
        elif grounded.kind == "Type":
            self._enter(grounded.shot, grounded.node, grounded.text, grounded.role, checkpoint)
        elif grounded.kind == "Swipe":
            self.base.shell("input", "swipe", *(str(v) for v in (*grounded.start, *grounded.end)), "500")
        elif grounded.kind == "Back":
            self.base.shell("input", "keyevent", "KEYCODE_BACK")
        elif grounded.kind == "Wait":
            until = time.monotonic() + grounded.duration
            while time.monotonic() < until:
                self._check(checkpoint); time.sleep(min(.1, max(0, until - time.monotonic())))
        self._check(checkpoint)

    def _finish(self, shot, checkpoint):
        if not self.request.get("origin") and not self._location_fresh(checkpoint):
            return {"kind": "clarification", "fields": ["origin"],
                    "message": "AutoGLM 尚未核对到新鲜的真实当前位置，请填写明确出发地；未采用默认位置"}
        absent = re.search(r"暂无(?:公交|公共交通|可用)?(?:方案|路线)|未找到.*(?:公交|公共交通|路线)|无(?:公交|公共交通)(?:方案|路线)", shot["evidence"]["text"])
        if absent and not self._route_cards(shot) and all(r in self.state["places"] for r in ("origin", "destination")):
            from autoglm.safety import _query_matches
            if not self.state.get("transit_applied") or not all(_query_matches(shot, r, self.request, self.state) for r in ("origin", "destination")):
                raise WorkflowError("AutoGLM 无路线提示的公共交通模式或起终点不能核对，未采纳")
            self.route_id = uuid.uuid4().hex
            shot["evidence"].update(route_id=self.route_id, page_kind="route_list")
            return {"kind": "result", "result": {"task_id": self.task_id, "query_id": self.query_id,
                    "route_id": self.route_id, "queried_at": self.queried_at, "collected_at": now(),
                    "origin": self.state["places"]["origin"], "destination": self.state["places"]["destination"],
                    "classification": "NO_ROUTE", "segments": []}, "evidence": self.observations,
                    "collection_complete": False}
        if not self.route_id or not self.state.get("selected_list") or not all(r in self.state["places"] for r in ("origin", "destination")):
            raise WorkflowError("AutoGLM 提出完成，但本轮起终点或选中方案证据不足，未标记成功")
        details = [e for e in self.observations if e["page_kind"] == "route_detail" and e.get("route_id") == self.route_id]
        if not details or shot["evidence"]["page_kind"] != "route_detail":
            raise WorkflowError("AutoGLM 提出完成，但未在本轮路线详情页，未标记成功")
        city = self.request.get("city", "")
        destination = place_key(self.state["places"]["destination"]["name"], city)
        complete = any(place_key(label(n), city) == destination and bounds(n) and bounds(n)[1] > 650
                       and n.get("class") == "android.view.ViewGroup"
                       for n in self._visible_place_nodes(shot["nodes"]))
        self._progress("validating_transit", "AutoGLM 已结束动作，正在独立核对本轮路线证据", checkpoint)
        return self.collect_observed_result(self.request, self.task_id, self.query_id, self.state["places"],
                                           self.queried_at, self.state["selected_list"], complete, checkpoint, last=shot)

    def query(self, request, task_id, query_id, checkpoint, choices=None):
        # Configuration validation happens before any read or side effect.
        from autoglm.safety import GroundingError, focused_input_context, resolve_action
        new = self.query_id != query_id
        self.request = dict(request); self.task_id = task_id; self.base.task_id = task_id
        self.folder = self.base.evidence_root / task_id / "transit" / query_id
        self.folder.mkdir(parents=True, exist_ok=True)
        from autoglm.client import AutoGLMClient
        if isinstance(self.client, AutoGLMClient): self.client.directory = self.folder
        if new:
            self.steps = 0; self.actions = 0; self.history = []; self.query_id = query_id
            self.route_id = None; self.observations = []; self.spent = 0; self.queried_at = now()
            self.state = {"places": {}, "choices": choices or {}, "selected_list": None, "adapter": self,
                          "location_verified": False, "input_role": "", "input_value_recoveries": 0,
                          "consecutive_input_rejections": 0, "place_evidence_recoveries": 0,
                          "loading_blocked_recoveries": 0}
            oid = uuid.uuid4().hex
            self.observations.append({"evidence_id": oid, "observation_id": oid, "query_id": query_id,
                                      "route_id": None, "page_kind": "user_request", "text": json.dumps(request, ensure_ascii=False)})
        else:
            self.state["choices"] = choices or {}; self.history.clear()
        self.started = time.monotonic() - self.spent
        transport = self.base.transport
        previous = getattr(transport, "prefer_transit_bridge", False) if transport else False
        if transport: transport.prefer_transit_bridge = True
        try:
            self.check_ready(checkpoint)
            repeats = {}; native_cycles = _NativeCycleGuard(); last_epoch = self.base.context_epoch
            while self.steps < self.config.max_steps:
                self._check(checkpoint)
                self.steps += 1
                self._progress("planning_transit", f"AutoGLM 第 {self.steps} 步：观察屏幕并决定下一动作", checkpoint)
                try:
                    shot = self._agent_observe(checkpoint)
                except ScreenContextChanged:
                    self._reset_context(); continue
                if last_epoch != shot["epoch"]:
                    self._reset_context(); last_epoch = shot["epoch"]
                loading = self._loading_info(shot)
                if loading["visible"] and loading["age_seconds"] >= 60:
                    self._record(step=self.steps, action="Observe", status="stopped", reason_code="loading_timeout",
                                 before_observation_id=shot["observation_id"])
                    raise WorkflowError("高德加载弹窗持续超过本轮等待上限，已停止；请检查网络后重新查询，未自动取消或操作背景")
                outcome = self._facts(shot, checkpoint)
                if outcome: return outcome
                progress_tokens = set()
                for role, status in self._place_status().items():
                    for field in ('name', 'city', 'address'):
                        refs = self.state.get('places', {}).get(role, {}).get('evidence_refs', {}).get(field)
                        if status.get(field) and refs and field not in status['missing_fields']:
                            progress_tokens.add(('place', role, field, status[field]))
                if self.state.get('filter'):
                    progress_tokens.add(('filter', self.state['filter']))
                if self.route_id and self.state.get('selected_list'):
                    progress_tokens.add(('route', self.route_id))
                native_cycles.note_progress(progress_tokens)
                shot["screen_info"] = self._model_screen_info(shot)
                if shot["current_app"] != PACKAGE:
                    shot["screen_info"]["allowed_actions"] = ["Launch", "Wait", "Take_over"]
                    shot["screen_info"]["constraint"] = "当前不是高德。不要在小银助手内输入需求或点击按钮；先通过 Launch 打开高德地图，或等待／请求协助。"
                from autoglm.safety import allowed_inputs
                shot["screen_info"]["allowed_inputs"] = {role: sorted(allowed_inputs(role, self.request, self.state))
                                                          for role in ("origin", "destination", "query")}
                instruction = getattr(self, "instruction", "") or "在高德地图中按本轮起终点和偏好查询公共交通路线。"
                from autoglm.client import IncompleteResponse
                from autoglm.protocol import ProtocolError
                try:
                    decision = self.client.decide({"instruction": instruction, "request": self.request,
                        "task_id": task_id, "query_id": query_id,
                        "transit_place_selections": self.state.get("choices", {})}, shot, self.history, checkpoint)
                except IncompleteResponse:
                    self._check(checkpoint)
                    if shot["epoch"] != self.base.context_epoch:
                        self._record(step=self.steps, action="Observe", status="discarded",
                                     before_observation_id=shot["observation_id"], reason_code="screen_or_context_changed")
                        self._reset_context()
                        continue
                    recoveries = self.state.get("incomplete_response_recoveries", 0)
                    retry = recoveries < 2
                    self._record(step=self.steps, action="Observe", model=self.config.model, status="rejected",
                                 before_observation_id=shot["observation_id"], reason_code="model_response_incomplete",
                                 recovery_scheduled=retry)
                    if not retry:
                        raise WorkflowError("AutoGLM 多次回复不完整，已达到有限重新观察上限；这些回复均未执行") from None
                    self.state["incomplete_response_recoveries"] = recoveries + 1
                    self.state["model_correction"] = {"reason_code": "model_response_incomplete", "executed": False}
                    continue
                except ProtocolError as error:
                    invalid_swipe = (error.reason_code == "invalid_coordinates"
                                     and getattr(error, "action_hint", None) == "Swipe")
                    if error.reason_code != "invalid_syntax" and not invalid_swipe:
                        raise
                    # Invalid output never becomes an Action or reaches the device.
                    # Re-observe before asking again; keep the strict parser and
                    # do not include the model's untrusted raw output in feedback.
                    self._check(checkpoint)
                    if shot["epoch"] != self.base.context_epoch:
                        self._record(step=self.steps, action="Observe", status="discarded",
                                     before_observation_id=shot["observation_id"], reason_code="screen_or_context_changed")
                        self._reset_context()
                        continue
                    recoveries = self.state.get("protocol_error_recoveries", 0)
                    retry = recoveries < 2
                    self._record(step=self.steps, action="Observe", model=self.config.model, status="rejected",
                                 before_observation_id=shot["observation_id"], reason_code="model_protocol_invalid",
                                 protocol_error_code=error.reason_code, recovery_scheduled=retry)
                    if not retry:
                        raise WorkflowError("AutoGLM 多次返回无效动作格式，已达到有限重新观察上限；这些回复均未执行") from None
                    self.state["protocol_error_recoveries"] = recoveries + 1
                    self.state["model_correction"] = {"reason_code": "model_protocol_invalid", "executed": False}
                    if invalid_swipe:
                        self.state["model_correction"]["invalid_swipe_coordinates"] = True
                    if (getattr(error, "syntax_category", None) == "unterminated_string"
                            and getattr(error, "syntax_action", None) == "Finish"):
                        self.state["model_correction"]["finish_unterminated"] = True
                    continue
                self._check(checkpoint)
                action = decision.action
                record = {"step": self.steps, "model": decision.model, "latency_ms": decision.latency_ms,
                          "action": action.kind, "before_observation_id": shot["observation_id"], "status": "proposed"}
                if action.kind == "Tap": record["proposed_point"] = action.params.get("element")
                if action.kind == "Swipe": record["proposed_swipe"] = [action.params.get("start"), action.params.get("end")]
                try:
                    fresh = self._agent_observe(checkpoint)
                except ScreenContextChanged:
                    self._record(**{**record, "status": "discarded", "reason_code": "screen_or_context_changed"})
                    self._reset_context(); continue
                if (action.kind == "Tap" and fresh["epoch"] == self.base.context_epoch
                        and screen_signature(shot)[:5] == screen_signature(fresh)[:5]):
                    # An invalid point can already be rejected on the image
                    # the model actually saw. A cosmetic animation must not
                    # prevent bounded corrective feedback forever. This never
                    # authorizes a stale point: valid proposals still require
                    # freshness AND independent grounding on the fresh frame.
                    try:
                        resolve_action(action, shot, self.request, self.state)
                    except GroundingError as error:
                        if error.code == "unrecognized_target" and self._retry_unknown_target(action, shot, record, checkpoint):
                            continue
                if not proposal_is_current(action, shot, fresh, request=self.request, state=self.state):
                    self._record(**{**record, "status": "discarded", "reason_code": "screen_or_context_changed"})
                    if screen_signature(shot)[:5] != screen_signature(fresh)[:5]:
                        self._reset_context()
                    # Discard this proposal, not the factual record of earlier
                    # executed actions. A repaint is new input for the next
                    # decision; it does not undo a confirmed past operation.
                    continue
                dispatched = False
                try:
                    grounded = resolve_action(action, fresh, self.request, self.state)
                    if (grounded.kind == "Tap" and ((grounded.binding or {}).get("home_transit_entry") is True
                            or grounded.role == "route" and grounded.page == "place_detail")
                            and self._missing_place_fields()):
                        raise _PlaceEvidenceRequired("本轮地点详情证据仍有缺项，未执行公共交通入口点击")
                    if action.kind == "Help":
                        raise WorkflowError("AutoGLM 请求用户协助，请核对手机页面后重新查询；未自动继续")
                    if action.kind == "Finish":
                        self._record(**{**record, "status": "verification_requested", "after_observation_id": fresh["observation_id"]})
                        return self._finish(fresh, checkpoint)
                    if grounded.role == "place" and (grounded.binding or {}).get("current_location") and not self._location_fresh(checkpoint):
                        return {"kind": "clarification", "fields": ["origin"], "message": "当前位置已过期或定位健康变化，请补充明确起点"}
                    selected = self._selected_route(fresh, grounded)
                    self._check(checkpoint)
                    if fresh["epoch"] != self.base.context_epoch:
                        raise ScreenContextChanged("AutoGLM 动作因暂停恢复失效")
                    # Only this invocation's freshly grounded observation is
                    # used by the existing Unicode bridge before writing.
                    if action.kind == "Type":
                        from types import SimpleNamespace
                        grounded = SimpleNamespace(**vars(grounded), shot=fresh)
                    dispatched_record = {**record, "status": "dispatching", "executed_point": grounded.point}
                    if action.kind == "Swipe": dispatched_record["executed_swipe"] = [grounded.start, grounded.end]
                    self._record(**dispatched_record)
                    dispatched = True
                    from autoglm.loading import loading_scene
                    loading_before = loading_scene(fresh)
                    self._execute(grounded, checkpoint)
                    if selected: self.state["selected_list"] = selected
                    if grounded.kind == "Tap" and grounded.role == "preference":
                        self.state["filter"] = label(grounded.node)
                    if grounded.kind == "Tap" and grounded.role == "transit":
                        self.state["transit_applied"] = True
                    if grounded.kind == "Tap" and grounded.role in {"origin", "destination", "query"}:
                        self.state["input_role"] = grounded.role
                    after = self._agent_observe(checkpoint)
                    self._check(checkpoint)
                    if after["epoch"] != fresh["epoch"]:
                        self._reset_context()
                        raise ScreenContextChanged("AutoGLM 操作后上下文已变化，已废弃旧结果")
                    loading_after = self._loading_info(after)
                    cancelled_loading = bool(loading_before and not loading_after["visible"] and (
                        grounded.kind == "Back" or grounded.kind == "Tap"
                        and (grounded.binding or {}).get("loading_cancel") is True
                        and grounded.node is loading_before["cancel_node"]))
                    if cancelled_loading:
                        self.state["loading_recovery"] = {"cancelled": True, "action": grounded.kind}
                    elif grounded.kind == "Type" and not loading_after["visible"]:
                        self.state.pop("loading_recovery", None)
                    executed_record = {**record, "status": "executed", "executed_point": grounded.point,
                                       "after_observation_id": after["observation_id"]}
                    if cancelled_loading:
                        executed_record["loading_cancelled"] = True
                    if action.kind == "Swipe": executed_record["executed_swipe"] = [grounded.start, grounded.end]
                    self._record(**executed_record)
                    if native_cycles.repeated(action, grounded, fresh, after):
                        raise WorkflowError("AutoGLM 多次重复同一界面操作而未取得新的核对进展，已停止；请检查高德页面后重试")
                    key = (action.kind, json.dumps(action.params, sort_keys=True, ensure_ascii=False), screen_signature(after))
                    waiting_for_loading = action.kind == "Wait" and loading_scene(after) is not None
                    repeats[key] = 0 if waiting_for_loading else repeats.get(key, 0) + 1
                    if repeats[key] >= 3:
                        raise WorkflowError("AutoGLM 重复操作未取得进展，已达到有限重试上限")
                    self.history.append({"action": action.kind, "params": action.params,
                                         "result": "executed", "observation_id": after["observation_id"],
                                         "screen_changed": screen_signature(fresh) != screen_signature(after)})
                    self.history[:] = self.history[-self.config.history_limit:]
                    self.state["consecutive_input_rejections"] = 0
                    self.state.pop("input_correction", None)
                    self.state.pop("evidence_correction", None)
                    self.state.pop("route_correction", None)
                    self.state.pop("target_correction", None)
                    self.state.pop("model_correction", None)
                except _PlaceEvidenceRequired:
                    self._check(checkpoint)
                    if fresh["epoch"] != self.base.context_epoch:
                        self._record(**{**record, "status": "discarded", "reason_code": "screen_or_context_changed"})
                        self._reset_context()
                        continue
                    recoveries = self._place_evidence_retry_count()
                    retry = recoveries < 2
                    self._record(**{**record, "status": "rejected", "reason_code": "place_evidence_required",
                                    "recovery_scheduled": retry})
                    if not retry:
                        raise WorkflowError("AutoGLM 本轮地点详情证据仍不完整，已达到有限重新决策上限；未执行公共交通入口点击") from None
                    self.state["place_evidence_recoveries"] = recoveries + 1
                    self.state["evidence_correction"] = True
                    continue
                except GroundingError as error:
                    from autoglm.loading import loading_scene
                    if not dispatched and action.kind == "Tap" and error.code == "unrecognized_target":
                        self._check(checkpoint)
                        if fresh["epoch"] != self.base.context_epoch:
                            self._record(**{**record, "status": "discarded", "reason_code": "screen_or_context_changed"})
                            self._reset_context()
                            continue
                        if self._retry_unknown_target(action, fresh, record, checkpoint):
                            continue
                    if not dispatched and error.code == "route_prerequisite_missing":
                        self._check(checkpoint)
                        if fresh["epoch"] != self.base.context_epoch:
                            self._record(**{**record, "status": "discarded", "reason_code": "screen_or_context_changed"})
                            self._reset_context()
                            continue
                        recoveries = self.state.get("route_precondition_recoveries", 0)
                        retry = recoveries < 2
                        self._record(**{**record, "status": "rejected", "reason_code": error.code,
                                        "recovery_scheduled": retry})
                        if not retry:
                            raise WorkflowError("高德当前起终点或路线偏好仍与本轮要求不同，已达到有限重新决策上限；未选择该方案") from None
                        self.state["route_precondition_recoveries"] = recoveries + 1
                        self.state["route_correction"] = {"reason_code": error.code, "executed": False}
                        continue
                    if (not dispatched and error.code == "loading_overlay_active" and loading_scene(fresh) is not None):
                        self._check(checkpoint)
                        if fresh["epoch"] != self.base.context_epoch:
                            self._record(**{**record, "status": "discarded", "reason_code": "screen_or_context_changed"})
                            self._reset_context()
                            continue
                        recoveries = self.state.get("loading_blocked_recoveries", 0)
                        retry = recoveries < 2
                        self._record(**{**record, "status": "rejected", "reason_code": "loading_overlay_active",
                                        "recovery_scheduled": retry})
                        if not retry:
                            raise WorkflowError("高德加载弹窗仍在，模型多次选择被遮挡操作，已达到有限重新决策上限；未操作背景") from None
                        self.state["loading_blocked_recoveries"] = recoveries + 1
                        self.state["loading_correction"] = {"reason_code": "loading_overlay_active", "executed": False}
                        continue
                    # A proven field/value mismatch is a rejected proposal,
                    # not a device failure. Give the model a new observation
                    # with factual focus information; never replace its action
                    # or text, and never turn the proposal into executed history.
                    context = (focused_input_context(fresh, self.request, self.state)
                               if not dispatched and action.kind == "Type"
                               and error.code == "input_value_mismatch" else None)
                    if context:
                        self._check(checkpoint)
                        if fresh["epoch"] != self.base.context_epoch:
                            self._record(**{**record, "status": "discarded", "reason_code": "screen_or_context_changed"})
                            self._reset_context()
                            continue
                        rejected = self.state.get("consecutive_input_rejections", 0) + 1
                        recoveries = self.state.get("input_value_recoveries", 0)
                        retry = recoveries < 2 and rejected < 3
                        self.state["consecutive_input_rejections"] = rejected
                        self._record(**{**record, "status": "rejected", "reason_code": "input_value_mismatch",
                                        "recovery_scheduled": retry})
                        if not retry:
                            raise WorkflowError("AutoGLM 输入与当前已核实字段不一致，已达到有限重新决策上限；未执行该输入") from None
                        self.state["input_value_recoveries"] = recoveries + 1
                        self.state["input_correction"] = {
                            "action": "Type", "reason_code": "input_value_mismatch", "executed": False,
                            "message": "上一步输入提议未执行：其内容与当前已核实字段允许值不一致。请按最新截图和 focused_input 重新决定下一动作。"}
                        continue
                    self._record(**{**record, "status": "unconfirmed" if dispatched else "rejected",
                                    "reason_code": "post_dispatch_interrupted" if dispatched else "action_or_evidence_not_verified"})
                    raise
                except Exception:
                    self._record(**{**record, "status": "unconfirmed" if dispatched else "rejected",
                                    "reason_code": "post_dispatch_interrupted" if dispatched else "action_or_evidence_not_verified"})
                    raise
            raise WorkflowError("AutoGLM 已达到本轮模型步骤预算，未标记成功")
        finally:
            self.spent = time.monotonic() - self.started
            if transport: transport.prefer_transit_bridge = previous
