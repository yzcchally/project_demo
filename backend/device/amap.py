"""Bounded Amap GUI adapter for route queries and confirmed navigation.

No route service, private API, coordinates from a model, or recorded route is
used. URI search is only an app entry; all places and route facts are read back.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode

from domain import Cancelled, WorkflowError, now
from device.huawei import ScreenContextChanged

PACKAGE = "com.autonavi.minimap"
ID = PACKAGE + ":id/"


class ExpectedTransitControlMissing(WorkflowError):
    """Only a bounded search on a verified route page missed its target."""


class TransitHomeInterrupted(ScreenContextChanged):
    """A recorded Amap task was replaced by the verified Huawei launcher."""


def clean(value):
    return html.unescape(re.sub(r"<[^>]*>", "", value or "")).strip()


def place_key(value, city=""):
    value = re.sub(r"[\s（）()·]", "", clean(value))
    # A city can also be the start of a POI's actual name (广州塔). Strip only
    # an explicit administrative suffix, never a bare city-name substring.
    for prefix in [city if city.endswith("市") else city + "市" if city else ""]:
        if prefix and value.startswith(prefix):
            value = value[len(prefix):].removeprefix("市")
            break
    return value.replace("地铁站", "")


def bounds(node):
    result = [int(n) for n in re.findall(r"-?\d+", node.get("bounds", ""))]
    return result if len(result) == 4 and min(result) >= 0 and result[0] < result[2] and result[1] < result[3] else None


def label(node):
    return clean(node.get("content-desc") or node.get("text", ""))


def screen_text(nodes):
    # Adjacent split glyphs are kept in their original order. Validation removes
    # whitespace only, so a native '3' + '号线' remains grounded as '3号线'.
    return "\n".join(dict.fromkeys(clean(n.get(k)) for n in nodes if bounds(n)
                                 for k in ["content-desc", "text"] if clean(n.get(k))))


class AmapDevice:
    def __init__(self, base_device, models):
        self.base, self.models = base_device, models
        self.total_seconds = float(os.environ.get("SILVERGUIDE_TRANSIT_TIMEOUT", "180"))
        self.action_limit = int(os.environ.get("SILVERGUIDE_TRANSIT_ACTIONS", "40"))
        self.scroll_limit = int(os.environ.get("SILVERGUIDE_TRANSIT_SCROLLS", "10"))
        self.observations = []
        self.actions = 0
        self.started = time.monotonic()
        self.query_id = ""
        self.route_id = None
        self._detail_contexts = {}
        self.progress_callback = None
        self._assist_sessions = {}
        self._resume_attempts = {}
        self._resume_actions = {}

    def _progress(self, stage, message, checkpoint):
        self._check(checkpoint)
        if self.progress_callback:
            self.progress_callback(stage, message)

    def _check(self, checkpoint):
        checkpoint()
        if (getattr(self, "_query_in_progress", False)
                and getattr(self, "_query_epoch", self.base.context_epoch) != self.base.context_epoch
                and getattr(self.base, "transit_resume_epoch", None) == self.base.context_epoch):
            # Test the explicit resume before the wall-clock budget: the user
            # may deliberately have kept the task paused for several minutes.
            raise ScreenContextChanged("暂停前的高德页面已失效，需要全新查询")
        if time.monotonic() - self.started > self.total_seconds:
            raise WorkflowError("高德查询超过本轮时限，请检查网络后重新查询")

    def _action(self, checkpoint):
        self._check(checkpoint)
        if self.actions >= self.action_limit:
            raise WorkflowError("高德操作已达本轮上限，请检查页面后重新查询")
        self.actions += 1

    def _audit_action(self,target,action,*,status="executed"):
        if not getattr(self,"folder",None):return
        record={"time":now(),"query_id":self.query_id,"action":action,"status":status,
                "attempt":getattr(self,"query_attempt",0),
                **{k:target.get(k) for k in ["target_id","observation_id","role","bounds","resource_id"]}}
        with (self.folder/"gui-actions.jsonl").open("a",encoding="utf-8") as f:
            f.write(json.dumps(record,ensure_ascii=False)+"\n")

    def check_ready(self, checkpoint=lambda: None):
        import gmail_demo
        checkpoint()
        adb = gmail_demo._adb()
        state = subprocess.run([adb, "get-state"], capture_output=True, text=True, timeout=10)
        checkpoint()
        if state.returncode or state.stdout.strip() != "device":
            raise WorkflowError("请连接并解锁手机，允许 USB 调试")
        if not gmail_demo._accessibility_enabled(adb):
            raise WorkflowError("请先开启 Demo 无障碍服务")
        if re.search(r"\bshowing=true\b", self.base.shell("dumpsys", "window", "policy")):
            raise WorkflowError("手机已锁屏，请解锁后重新查询")
        if not self.base.shell("pm", "path", PACKAGE).strip().startswith("package:"):
            raise WorkflowError("手机未安装高德地图")
        checkpoint()

    def _png(self):
        import gmail_demo
        if self.base.transport:
            value = self.base.transport.screenshot()
            return base64.b64decode(value.split(",", 1)[-1]) if isinstance(value, str) else value
        return subprocess.run([gmail_demo._adb(), "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=12, check=True).stdout

    def _observe(self, checkpoint, page="screen", *, screenshot=False):
        self._check(checkpoint)
        epoch = self.base.context_epoch
        try:
            xml = self.base.observe(checkpoint)
        except (Cancelled, ScreenContextChanged):
            raise
        except WorkflowError:
            # A protected read can fail while its caller was paused. The
            # explicit resume invalidation takes precedence over that old
            # read's error, just as it does over a late successful frame.
            self._check(checkpoint)
            reason = getattr(self.base.transport, "last_bridge_reason", None)
            if (reason == "root_package" and getattr(self, "_query_in_progress", False)
                    and getattr(self, "_query_map_context", None)):
                self._check_home_recovery(self._query_map_context, checkpoint)
                raise TransitHomeInterrupted("已离开高德回到桌面，需要重新核对本轮路线") from None
            messages = {
                "root_package": "高德当前窗口不在前台，请关闭遮挡弹窗并返回高德后重新查询",
                "service_unavailable": "Demo 无障碍服务当前不可用，请重新开启服务后再查询",
                "tree_changed": "高德页面正在变化，暂时无法稳定读取，请等待页面加载完成后重新查询",
                "bridge_busy": "高德页面读取繁忙，请稍后重新查询",
                "bridge_timeout": "高德页面读取超时，请稍后重新查询",
                "bridge_unavailable": "App 尚未提供高德读取接口，请更新 Demo 后重新查询",
            }
            if isinstance(reason, str):
                (self.folder / "observation-error.json").write_text(json.dumps({
                    "query_id": self.query_id, "reason": reason, "time": now(),
                }, ensure_ascii=False), encoding="utf-8")
            if isinstance(reason, str) and reason in messages:
                raise WorkflowError(messages[reason]) from None
            raise
        self._check(checkpoint)
        all_nodes = list(ET.fromstring(xml).iter("node"))
        ns = [n for n in all_nodes if n.get("package") == PACKAGE]
        if not ns:
            raise WorkflowError("高德不在当前页面，请返回高德后重新查询")
        if getattr(self, "_query_in_progress", False) and not getattr(self, "_query_map_context", None):
            snapshot = self.base.shell("dumpsys", "activity", "activities")
            self._check(checkpoint)
            identity = self._resumed_map_identity(snapshot)
            if identity is not None:
                context = {"android_task_id": identity[0], "activity": identity[1]}
                if self._map_task_still_exists(snapshot, context):
                    self._query_map_context = context
        oid = uuid.uuid4().hex
        file = self.folder / (oid + ".xml")
        file.write_text(xml, encoding="utf-8")
        actual_page = page
        evidence_nodes = ns
        if page == "route_detail":
            parents = {child: parent for parent in all_nodes for child in parent}
            def current_native(node):
                while node is not None:
                    if (node.get("package") != PACKAGE or node.get("visible-to-user") == "false"
                            or node.get("enabled") == "false"):
                        return False
                    node = parents.get(node)
                return True
            # Hidden or foreign ancestor branches are neither page markers
            # nor textual evidence for the selected itinerary. Keep the full
            # original XML on disk without promoting its invisible captions.
            evidence_nodes = [n for n in ns if current_native(n)]
            detail_marker = any(label(n) == "公交详情页面" and bounds(n) for n in evidence_nodes)
            query_fields = any(n.get("resource-id") in {ID + "route_edit_summary_start", ID + "route_edit_summary_end"}
                               and bounds(n) for n in evidence_nodes)
            # Waiting *for* details does not make an old list/loading frame
            # into detail evidence. Do not mix its other route candidates into
            # either the native parser or the model's selected itinerary.
            actual_page = "route_detail" if detail_marker and not query_fields else "route_list" if query_fields else "screen"
        evidence = {"evidence_id": oid, "query_id": self.query_id, "route_id": self.route_id,
                    "observation_id": oid, "text": screen_text(evidence_nodes), "xml_path": str(file),
                    "page_kind": actual_page}
        if screenshot:
            png = self._png()
            self._check(checkpoint)
            image = self.folder / (oid + ".png"); image.write_bytes(png)
            evidence["screenshot_path"] = str(image)
        self.observations.append(evidence)
        if any(x in evidence["text"] for x in ["网络不佳", "网络连接失败", "网络不可用"]):
            raise WorkflowError("高德提示网络不佳，请检查手机网络后重新查询")
        return {"xml": xml, "nodes": ns, "epoch": epoch, "evidence": evidence}

    def _wait(self, predicate, checkpoint, page="screen", attempts=8, *, missing_error=WorkflowError):
        for attempt in range(attempts):
            shot = self._observe(checkpoint, page)
            value = predicate(shot)
            if value is not None and value is not False:
                return shot, value
            self._check(checkpoint)
            if attempt + 1 < attempts:
                time.sleep(.5)
        raise missing_error("高德页面未出现预期控件，请检查网络或弹窗后重新查询")

    def _assist_page_safe(self, shot, checkpoint):
        """Fail closed before an assist call or its postcondition.

        `_observe` exposes filtered Amap nodes for the rules adapter; inspect
        its *complete* original XML here so a foreign ancestor/window cannot
        be silently dropped. Never use this as proof of a successful click.
        """
        from autoglm.executor import AutoGLMTransitDevice
        self._check(checkpoint)
        if AutoGLMTransitDevice._foreground(self) != PACKAGE:
            raise WorkflowError("高德不在前台，不能使用 AutoGLM 辅助")
        try:
            full = list(ET.fromstring(shot["xml"]).iter("node"))
        except (ET.ParseError, TypeError, KeyError):
            raise WorkflowError("无法完整核对高德窗口，未使用 AutoGLM 辅助") from None
        if not full:
            raise WorkflowError("无法完整核对高德窗口，未使用 AutoGLM 辅助")
        parents = {child: node for node in full for child in node}
        def visible(node):
            while node is not None:
                if node.get("visible-to-user") == "false" or node.get("enabled") == "false":
                    return False
                node = parents.get(node)
            return True
        visible_nodes = [n for n in full if visible(n)]
        for node in visible_nodes:
            package = node.get("package", "")
            if package and package != PACKAGE and bounds(node):
                raise WorkflowError("当前页面存在其他应用的可见覆盖，未使用 AutoGLM 辅助")
            if package == PACKAGE and bounds(node):
                ancestor = parents.get(node)
                while ancestor is not None:
                    ancestor_package = ancestor.get("package", "")
                    if ancestor_package and ancestor_package != PACKAGE:
                        raise WorkflowError("当前高德控件位于其他应用窗口内，未使用 AutoGLM 辅助")
                    ancestor = parents.get(ancestor)
            rid = node.get("resource-id", "").lower()
            cls = node.get("class", "").lower()
            text = label(node)
            if (any(mark in rid for mark in ("loading_view", "loading_text", "permission", "parentpanel"))
                    or any(mark in cls for mark in ("dialog", "popupwindow"))
                    or re.search(r"正在加载|网络不佳|网络不可用|网络连接失败|登录账号|登录账户|输入密码|验证码|权限请求|允许访问", text)):
                raise WorkflowError("高德页面有加载、权限、登录或错误提示，未使用 AutoGLM 辅助")
        for suffix in ("route_edit_summary_start", "route_edit_summary_end"):
            fields = [n for n in visible_nodes if n.get("package") == PACKAGE
                      and n.get("resource-id") == ID + suffix and bounds(n)]
            if len(fields) != 1:
                raise WorkflowError("当前高德路线字段未完整可见，未使用 AutoGLM 辅助")
        self._check(checkpoint)
        if shot.get("epoch") != self.base.context_epoch:
            raise ScreenContextChanged("高德页面上下文已变化，辅助动作已废弃")

    def _select_public_transit(self, places, checkpoint):
        """Rules first; only this verified route-page miss can invoke assist.

        A successful assist owns the click and its postcondition, so there is
        no second rule click. Wrong pages, bridge errors and cancellation are
        not target misses and propagate unchanged.
        """
        def route_page(shot, *, verify_assist=False):
            if verify_assist:
                self._assist_page_safe(shot, checkpoint)
            fields = {}
            for role, suffix in (("origin", "route_edit_summary_start"), ("destination", "route_edit_summary_end")):
                nodes = [n for n in shot["nodes"] if n.get("resource-id") == ID + suffix and bounds(n)]
                if len(nodes) != 1:
                    raise WorkflowError("当前页面不是可核对起终点的高德路线页")
                fields[role] = clean(nodes[0].get("text"))
            expected = self.request.get("origin")
            if ((place_key(fields["origin"]) != place_key(places["origin"]["name"]) if expected
                 else fields["origin"] != "我的位置")
                    or place_key(fields["destination"]) != place_key(places["destination"]["name"])):
                raise WorkflowError("高德起终点与本次明确需求不一致，未操作公共交通入口")

        def target(shot):
            route_page(shot)
            # Preserve the existing exact-label rule and its clickable-parent
            # preference; assistance must not replace a working rule.
            original = self._text_target(shot, "公共交通")
            if original is not None:
                return original
            # Known text aliases are accepted only on the verified native
            # route-mode control, not arbitrary map/search text.
            aliases = {"公交", "公交地铁", "公交/地铁"}
            candidates = [n for n in shot["nodes"] if n.get("resource-id") == ID + "route_input_conbine_text"
                          and label(n) in aliases and bounds(n) and n.get("visible-to-user") != "false"
                          and n.get("enabled") != "false"]
            if len(candidates) > 1:
                raise WorkflowError("高德公共交通入口存在歧义，未执行点击")
            if candidates:
                return candidates[0]
            # Normal rule hits retain their established path. Only a real
            # miss that might later invoke the model needs the stricter full
            # window/foreground preflight on every bounded observation.
            if os.environ.get("SILVERGUIDE_AUTOGLM_ASSIST_ENABLED", "false").lower() == "true":
                self._assist_page_safe(shot, checkpoint)
            return None

        def completed(shot):
            route_page(shot, verify_assist=True)
            # Amap marks multiple transport tabs selected=true simultaneously;
            # those flags do not prove transit mode. Require the distinct,
            # current public-transit preference strip instead.
            parents = {child: parent for parent in shot["nodes"] for child in parent}
            strips = [n for n in shot["nodes"]
                      if n.get("class", "").endswith("SpringHorizontalScrollView")
                      and n.get("scrollable") == "true" and bounds(n)]
            valid = []
            def visible(node):
                while node is not None:
                    if node.get("package") not in (None, "", PACKAGE) or node.get("visible-to-user") == "false" or node.get("enabled") == "false":
                        return False
                    node = parents.get(node)
                return True
            for strip in strips:
                if not visible(strip):
                    continue
                names = {label(n) for n in strip.iter("node") if visible(n) and bounds(n)}
                if (len(names & {"高德推荐", "地铁优先", "步行少", "换乘少", "时间短", "不坐地铁"}) >= 2
                        and names & {"地铁优先", "换乘少", "不坐地铁"}):
                    valid.append(strip)
            return len(valid) == 1

        try:
            shot, node = self._wait(target, checkpoint, "route_list", attempts=8,
                                    missing_error=ExpectedTransitControlMissing)
        except ExpectedTransitControlMissing:
            if os.environ.get("SILVERGUIDE_AUTOGLM_ASSIST_ENABLED", "false").lower() != "true":
                raise
            self._assist_trigger = "expected_control_not_found"
        else:
            inject = False
            if (os.environ.get("SILVERGUIDE_AUTOGLM_ASSIST_ENABLED", "false").lower() == "true"
                    and os.environ.get("SILVERGUIDE_AUTOGLM_ASSIST_TEST_MISS", "").lower() == "transit"):
                self._assist_page_safe(shot, checkpoint)
                # Persistent exclusive claim: restarting/re-entering the same
                # task/query cannot inject a second artificial rules miss.
                try:
                    with (self.folder / "autoglm-assist-controlled-test.claim").open("x", encoding="utf-8") as claim:
                        claim.write("transit\n")
                    inject = True
                except FileExistsError:
                    pass
            if not inject:
                self._tap(shot, node, checkpoint, role="transit", page="route_list")
                return
            self._assist_trigger = "controlled_test_miss"
        from autoglm.assist import AutoGLMAssist
        if self._assist is None:
            self._assist = AutoGLMAssist(self)
            self._assist_sessions[(self.task_id, self.query_id)] = self._assist
        self._assist.tap("transit", checkpoint, completed, precondition=route_page)
        self._check(checkpoint)
        # The step is confirmed and control has returned to the rules flow;
        # this explicitly does not assert that the final route is verified.
        self._assist._record(step="transit", status="continued_rules", call_number=self._assist.calls)

    def _guard(self, shot, node, role, page, *, action="tap_element", text=""):
        from perception.transit import describe_transit
        from safety.transit import guard_transit
        index=shot["nodes"].index(node)
        oid=shot["evidence"]["observation_id"]
        candidates=describe_transit(shot["nodes"],oid,context_epoch=shot["epoch"],role_map={index:role})
        target=next((c for c in candidates if c["role"]==role and c["bounds"]==bounds(node)
                     and c["resource_id"]==node.get("resource-id", "")),None)
        if target is None: raise WorkflowError("当前高德查询目标不在已观察候选中")
        if action=="scroll" and label(node)=="公交详情页面":
            # This specific custom-rendered itinerary panel exposes no native
            # scrollable flag. Its observed root bounds define the gesture area.
            target["scrollable"]=True
        decision={"observation_id":oid,"page":page,"actions":[{"action":action,"target_id":target["target_id"],"role":role,"text":text}]}
        guard_transit(decision,candidates,{role:text} if action=="enter_text" else {},
                      observation_id=oid,context_epoch=self.base.context_epoch,
                      allowed_place_ids=[target["target_id"]] if role=="place" else [])
        return target

    def _tap(self, shot, node, checkpoint, *, role="place", page="place_list", place_binding=None):
        self._action(checkpoint)
        if shot["epoch"] != self.base.context_epoch:
            raise ScreenContextChanged("暂停恢复后需重新观察高德页面")
        sig = lambda n: (n.get("resource-id"), n.get("bounds"), n.get("text"), n.get("content-desc"))
        fresh = self._observe(checkpoint)
        found = [n for n in fresh["nodes"] if sig(n) == sig(node) and bounds(n)]
        if len(found) != 1:
            raise WorkflowError("高德控件已变化，已停止本次点击，请重新查询")
        if place_binding is not None:
            matches = [(candidate, row) for candidate, row in self._place_candidates(
                fresh, place_binding["query"], include_related=True)
                if candidate["candidate_id"] == place_binding["candidate_id"]
                and sig(row) == place_binding["row_signature"]]
            if len(matches) != 1 or found[0] not in list(matches[0][1].iter("node")):
                raise WorkflowError("高德地点名称或地址已变化，已停止点击，请重新选择地点")
            if self._place_title_target(matches[0][1], nodes=fresh["nodes"]) is not found[0]:
                raise WorkflowError("高德地点主标题已变化，已停止点击，请重新读取地点")
        self._check(checkpoint)
        if shot["epoch"] != fresh["epoch"] or fresh["epoch"] != self.base.context_epoch:
            raise ScreenContextChanged("暂停恢复已废弃原高德控件")
        target=self._guard(fresh,found[0],role,page)
        self.base.tap(found[0], checkpoint)
        self._audit_action(target,"tap_element")

    @staticmethod
    def _visible_place_nodes(nodes):
        parents = {child: parent for parent in nodes for child in parent}
        def shown(node):
            while node is not None:
                if node.get("visible-to-user") == "false":
                    return False
                node = parents.get(node)
            return True
        return [node for node in nodes if shown(node)]

    @staticmethod
    def _place_title_target(row, *, nodes=None):
        """A result card may contain gates, parking and navigation shortcuts.

        The large card's center is not its main-place target. Only an exact
        title descendant belonging to this card is an allowed hit area.
        """
        rect = bounds(row)
        visible = set(AmapDevice._visible_place_nodes(nodes if nodes is not None else list(row.iter("node"))))
        if rect is None or row not in visible:
            raise WorkflowError("高德地点卡片当前不可见，请重新读取地点")
        matches = [n for n in row.iter("node") if n is not row and n in visible and bounds(n)
                   and label(n) == label(row)
                   and rect[0] <= bounds(n)[0] < bounds(n)[2] <= rect[2]
                   and rect[1] <= bounds(n)[1] < bounds(n)[3] <= rect[3]]
        boxes = {tuple(bounds(n)) for n in matches}
        if len(boxes) != 1:
            raise WorkflowError("高德地点卡片的主标题入口无法唯一核对，请补充具体地点后重新查询")
        return min(matches, key=lambda n: (bool(list(n)), n.get("clickable") == "true"))

    @staticmethod
    def _poi_title_names(shot):
        """Recognize a visible POI title group, including a preview card.

        Only an exact title leaf or its split glyphs prove the identity; a
        large search row or unrelated recommendation title does not.
        """
        names = set()
        visible = set(AmapDevice._visible_place_nodes(shot["nodes"]))
        for node in shot["nodes"]:
            rect, text = bounds(node), label(node)
            if (node not in visible or rect is None or node.get("class") != "android.view.ViewGroup"
                    or rect[2] - rect[0] < 500 or rect[3] - rect[1] > 180
                    or not 300 < rect[1] < 2000 or not 2 <= len(text) <= 120):
                continue
            leaves = [label(n) for n in node.iter("node") if n is not node and n in visible and not list(n) and bounds(n) and label(n)]
            # A compact station preview appends category/recent-view badges
            # after the exact title glyphs. Those badges are not part of the
            # identity and no arbitrary trailing text is discarded.
            glyphs = leaves[:len(text)]
            split_title = (glyphs and all(len(s) == 1 for s in glyphs) and "".join(glyphs) == text
                           and all(s in {"地铁站", "最近浏览", "浏览过", "刚刚浏览"} for s in leaves[len(text):]))
            if text in leaves or split_title:
                names.add(text)
        return names

    @staticmethod
    def _collapsed_station_card(shot, name):
        """Find the visible bottom sheet for an exact station, before its address is shown."""
        if not name.endswith(("(地铁站)", "（地铁站）")):
            return None
        visible = {n for n in AmapDevice._visible_place_nodes(shot["nodes"])
                   if n.get("enabled") != "false" and bounds(n)}
        labels = {label(n) for n in visible}
        if not {"分享", "路线", "导航"} <= labels or not {"周边", "搜周边"} & labels:
            return None
        parents = {child: parent for parent in shot["nodes"] for child in parent}
        targets = []
        for title in visible:
            if label(title) != name or title.get("class") != "android.view.ViewGroup":
                continue
            leaves = [label(n) for n in title.iter("node") if n is not title and n in visible and not list(n) and label(n)]
            if leaves != list(name) and leaves != [name]:
                continue
            sheet = parents.get(title)
            while sheet is not None:
                if sheet in visible and sheet.get("class", "").endswith(".RecyclerView") and sheet.get("scrollable") == "true":
                    left, top, right, bottom = bounds(sheet)
                    if top >= bottom * .70 and right - left >= 1.5 * (bottom - top):
                        targets.append((bounds(title), bounds(sheet)))
                    break
                sheet = parents.get(sheet)
        return targets[0] if len(targets) == 1 else None

    def _expand_station_card(self, shot, name, checkpoint):
        original = self._collapsed_station_card(shot, name)
        fresh = self._observe(checkpoint, "place_detail")
        if original is None or self._collapsed_station_card(fresh, name) != original:
            raise WorkflowError("高德地点预览已变化，请重新查询")
        if fresh["epoch"] != self.base.context_epoch or shot["epoch"] != fresh["epoch"]:
            raise ScreenContextChanged("重新进入高德后需重新核对地点预览")
        left, top, right, bottom = original[1]
        x = left + (right - left) // 2
        start = top + (bottom - top) // 3
        end = top // 2
        self._action(checkpoint)
        self.base.shell("input", "swipe", str(x), str(start), str(x), str(end), "450")
        self._audit_action({"bounds": original[1], "role": "place_preview",
                            "observation_id": fresh["evidence"]["observation_id"]}, "expand_station_card")

    @staticmethod
    def _compact_poi_header(shot):
        """Recognize one actual compact main-POI header, never a result row.

        The POI footer distinguishes this page from search/history. Identity
        and a literal address must share a bounded, full-width native header
        above gates and recommendation sections. Returned bounds are original
        device bounds; no coordinates or evidence are shifted into a header.
        """
        ns = [n for n in AmapDevice._visible_place_nodes(shot.get("nodes", []))
              if n.get("package") == PACKAGE and n.get("enabled") != "false"]
        labels = {label(n) for n in ns}
        if not {"分享", "导航", "路线"} <= labels or not {"周边", "搜周边"} & labels:
            return None
        if labels.intersection({"搜索历史", "历史记录", "历史搜索", "清空历史", "清除历史", "最近搜索", "搜索发现"}):
            return None
        rects = [bounds(n) for n in ns if bounds(n)]
        full = [r for r in rects if r[:2] == [0, 0]]
        width = shot.get("width") or max((r[2] for r in full), default=0)
        height = shot.get("height") or max((r[3] for r in full), default=0)
        if not width or not height:
            return None
        parents = {child: parent for parent in ns for child in parent}
        visible = set(ns)
        names = AmapDevice._poi_title_names({"nodes": ns})
        excluded = re.compile(r"^(?:相似服务推荐|相关推荐|热门推荐|用户评价|用户评论|用户点评|全部评价|全部评论|网友评论|热门评论|精选评论)")
        stop = min((bounds(n)[1] for n in ns if bounds(n) and excluded.match(label(n))), default=height)
        footer_top = min((bounds(n)[1] for n in ns if bounds(n) and label(n) in {"周边", "搜周边", "分享", "导航", "路线"}), default=height)
        administrative = r"(?:[\u4e00-\u9fff]{2,}(?:省|市|区|县|自治区|自治州)){1,5}"
        def contained(inner, outer):
            return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]
        found = {}
        for title in ns:
            tr = bounds(title)
            if (label(title) not in names or title.get("class") != "android.view.ViewGroup" or not tr
                    or tr[1] < 800 or tr[1] >= stop):
                continue
            parent = parents.get(title)
            while parent is not None:
                rect = bounds(parent)
                if (parent in visible and rect and not label(parent) and parent.get("class") == "android.view.ViewGroup"
                        and rect[2] - rect[0] >= width * .85 and 180 <= rect[3] - rect[1] <= min(600, height * .28)
                        and 0 <= tr[1] - rect[1] <= 120 and rect[3] <= min(stop, footer_top) and contained(tr, rect)):
                    children = [n for n in parent.iter("node") if n in visible and bounds(n) and contained(bounds(n), rect)]
                    primary = {label(n) for n in children if n.get("class") == "android.view.ViewGroup" and label(n) in names}
                    addresses = [n for n in children if not list(n) and bounds(n)[1] >= tr[3] and len(clean(n.get("text"))) > 1
                                 and (re.fullmatch(administrative, clean(n.get("text"))) or re.search(
                                     r"[\u4e00-\u9fffA-Za-z\d]{2,}(?:路|街|大道|巷|弄|道|村|镇|乡)", clean(n.get("text"))))
                                 and not re.search(r"榜|排名|搜索|评价|评论|评分|人气|推荐|附近|途经线路|开往|号线", clean(n.get("text")))]
                    forbidden = any(excluded.match(label(n)) or re.fullmatch(r"[A-Z]口", label(n)) for n in children)
                    if primary == {label(title)} and addresses and not forbidden:
                        key = (label(title), tuple(rect))
                        found[key] = {"name": label(title), "bounds": list(rect), "title_node": title}
                        break
                parent = parents.get(parent)
        return next(iter(found.values())) if len(found) == 1 else None

    def _text_target(self, shot, text):
        matches = [n for n in shot["nodes"] if label(n) == text and bounds(n)]
        # The clickable parent and its text child may share a label. Prefer the
        # parent, then the smallest visible rectangle; never pick another name.
        matches.sort(key=lambda n: (n.get("clickable") != "true",
                                   (bounds(n)[2]-bounds(n)[0])*(bounds(n)[3]-bounds(n)[1])))
        return matches[0] if matches else None

    def _ocr(self, screenshot):
        """Use local Apple Vision only to ground a missing native text target."""
        import sys
        if sys.platform != "darwin" or not shutil.which("clang"):
            raise WorkflowError("当前系统缺少截图文字定位能力，无法核对高德路线按钮")
        source = Path(__file__).resolve().parents[2] / "tools" / "amap_ocr.m"
        binary = self.base.evidence_root / "amap-ocr"
        if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
            args = ["clang", "-fobjc-arc"]
            # This Mac has a newer SDK alongside an older compiler. Prefer the
            # installed compatible SDK; do not replace the user's toolchain.
            sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk")
            if sdk.exists(): args += ["-isysroot", str(sdk)]
            for framework in ["Foundation", "Vision", "ImageIO", "CoreGraphics"]:
                args += ["-framework", framework]
            args += [str(source), "-o", str(binary)]
            try: subprocess.run(args, check=True, capture_output=True, timeout=30)
            except (subprocess.SubprocessError, OSError):
                raise WorkflowError("截图文字定位工具无法构建，请检查 macOS 命令行工具") from None
        try:
            return json.loads(subprocess.run([str(binary), str(screenshot)], capture_output=True,
                                             text=True, timeout=12, check=True).stdout)
        except (subprocess.SubprocessError, ValueError):
            raise WorkflowError("未能读取高德截图中的路线控件") from None

    def _tap_label(self, text, checkpoint, page="screen"):
        shot = self._observe(checkpoint, page)
        node = self._text_target(shot, text)
        if node is not None:
            role={"路线":"route","公共交通":"transit"}.get(text,"preference")
            self._tap(shot, node, checkpoint, role=role, page=page)
            return
        # Amap 15.13 renders its POI footer outside the accessible tree bounds.
        # Only the query-entry '路线' is permitted through this OCR fallback.
        if text != "路线" or page != "place_detail":
            raise WorkflowError(f"当前高德页面无法核对“{text}”控件")
        fresh = self._observe(checkpoint, page, screenshot=True)
        data = self._ocr(fresh["evidence"]["screenshot_path"])
        self._check(checkpoint)
        candidates = [i for i in data["items"] if i["text"] == text and i["confidence"] >= .7]
        if len(candidates) != 1:
            raise WorkflowError("高德路线按钮无法唯一定位，已停止，未开始导航")
        r = candidates[0]["bounds"]
        if (len(r) != 4 or min(r) < 0 or not (r[0] < r[2] <= data["width"] and r[1] < r[3] <= data["height"])):
            raise WorkflowError("截图控件边界无效")
        again = self._observe(checkpoint, page, screenshot=True)
        current = self._ocr(again["evidence"]["screenshot_path"])
        found = [i for i in current["items"] if i["text"] == text and i["confidence"] >= .7 and
                 max(abs(a-b) for a,b in zip(i["bounds"],r)) <= 12]
        if len(found) != 1 or fresh["epoch"] != self.base.context_epoch:
            raise WorkflowError("截图页面已变化，已停止过期点击")
        self._action(checkpoint)
        if again["epoch"] != self.base.context_epoch:
            raise ScreenContextChanged("暂停恢复已废弃原截图目标")
        r = found[0]["bounds"]
        from safety.transit import guard_transit
        oid=again["evidence"]["observation_id"]
        target_id=hashlib.sha256((oid+text+str(r)).encode()).hexdigest()[:24]
        candidate={"target_id":target_id,"observation_id":oid,"context_epoch":again["epoch"],
                   "package":PACKAGE,"role":"route","bounds":r,"clickable":True}
        guard_transit({"observation_id":oid,"page":"place_detail","actions":[{"action":"tap_element","target_id":target_id,"role":"route"}]},
                      [candidate],{},observation_id=oid,context_epoch=self.base.context_epoch)
        self.base.shell("input", "tap", str((r[0]+r[2])//2), str((r[1]+r[3])//2))
        self._audit_action(candidate,"tap_ocr_route")

    def _enter(self, shot, node, value, role, checkpoint):
        self._action(checkpoint)
        if role not in {"origin", "destination", "query"} or len(value) > 300:
            raise WorkflowError("路线输入超出允许范围")
        fresh = self._observe(checkpoint)
        same = [n for n in fresh["nodes"] if n.get("class") == "android.widget.EditText" and
                n.get("bounds") == node.get("bounds") and n.get("text") == node.get("text")]
        if len(same) != 1 or shot["epoch"] != self.base.context_epoch:
            raise WorkflowError("高德输入框已变化，请重新查询")
        self._check(checkpoint)
        if shot["epoch"] != fresh["epoch"] or fresh["epoch"] != self.base.context_epoch:
            raise ScreenContextChanged("暂停恢复已废弃原高德输入框")
        target=self._guard(fresh,same[0],"search" if role=="query" else role,"route_input",action="enter_text",text=value)
        enc = lambda s: base64.urlsafe_b64encode(s.encode()).decode()
        from device.mobilerun import call_transit_bridge
        reply = call_transit_bridge("set_transit_field", {
            "field": role, "text": enc(value), "resource_id": node.get("resource-id", ""),
            "bounds": node.get("bounds", ""), "expected_text": enc(node.get("text", "")),
        })
        self._check(checkpoint)
        if reply.get("result") != 1:
            self._audit_action(target,"enter_text",status="rejected")
            (self.folder/"input-rejection.txt").write_text(json.dumps(reply, ensure_ascii=False)[:2000],encoding="utf-8")
            raise WorkflowError("高德中文输入未通过当前控件校验，请更新 App 或开启无障碍服务")
        self._audit_action(target,"enter_text")
        self._wait(lambda s: next((n for n in s["nodes"] if n.get("class") == "android.widget.EditText" and n.get("text") == value),None), checkpoint)

    def _place_candidates(self, shot, query, *, include_related=False):
        matches = []
        key = place_key(query, self.request.get("city", ""))
        parents = {c: n for n in shot["nodes"] for c in n}
        ancillary = lambda s: bool(re.search(
            r"停车场|停车位|(?:出入口|入口|出口)$|[（(][^）)]*(?:门|出入口|入口|出口)[）)]$|(?:东|南|西|北)(?:[一二三四五六七八九十\d]+号)?门$", s))
        def result_row(n):
            parent = parents.get(n)
            while parent is not None:
                if "RecyclerView" in parent.get("class", "") and bounds(parent):
                    return True
                parent = parents.get(parent)
            return False
        for n in shot["nodes"]:
            name = label(n)
            r = bounds(n)
            if not r or n.get("clickable") != "true" or "ViewGroup" not in n.get("class", "") or r[2]-r[0] < 500:
                continue
            if not name or len(name)>100 or any(x in name for x in ["搜索框", "展开列表", "我的位置", "返回", "公里", "分钟"]):
                continue
            address=next((clean(c.get("text")) for c in n.iter("node") if c is not n and
                          bounds(c) and
                          re.search(r"(?:省|市|区|县|路|街|号)",clean(c.get("text"))) and clean(c.get("text"))!=name
                          and "号线" not in clean(c.get("text")) and not re.fullmatch(r"(?:夜|高峰快线)?\d+路",clean(c.get("text")))),None)
            # Search may use an alias that Amap does not repeat in its POI
            # title. An alternative is offered only as an explicit human
            # choice, backed by a real result row, title and address. It is
            # never silently equated with the requested name.
            native_title = any(c is not n and bounds(c) and label(c) == name for c in n.iter("node"))
            if key not in place_key(name) and not (address and native_title and result_row(n)):
                continue
            if not include_related and ancillary(name) and not ancillary(query):
                continue
            city=self.request.get("city")
            actual_city=city if city and city.removesuffix("市")+"市" in (address or "") else None
            # A municipal designation is metadata of THIS POI, not its address
            # and not a city inferred from the search box or a ranking scope.
            tags = {match.group(1) for child in n.iter("node") if bounds(child)
                    if (match := re.fullmatch(r"([\u4e00-\u9fff]{2,10}市)科普基地", label(child)))}
            if not actual_city and len(tags) == 1:
                actual_city = next(iter(tags))
            cid = hashlib.sha256(json.dumps([name,address,actual_city],ensure_ascii=False).encode()).hexdigest()[:24]
            matches.append(({"candidate_id":cid,"name":name,"address":address,"city":actual_city,
                             "evidence_id":shot["evidence"]["evidence_id"]},n))
        return matches

    def _resolve_place(self, role, query, choices, checkpoint):
        self.route_id = None
        self._action(checkpoint)
        city = self.request.get("city", "")
        keyword = query if city and city in query else city + query
        uri = "androidamap://poi?" + urlencode({"sourceApplication":"SilverGuide","keywords":keyword,"dev":"0"})
        self.base.shell("am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", uri, "-p", PACKAGE)
        already_detail=lambda s:any("附近搜索" in label(n) and bounds(n) for n in s["nodes"])
        previous=[None]
        def settled(s):
            if any(bounds(n) and ("ProgressBar" in n.get("class", "") or re.fullmatch(
                    r"(?:正在(?:搜索|加载)|(?:搜索|加载)中|请稍(?:候|后))[\s.。…]*", label(n))) for n in s["nodes"]):
                previous[0]=None;return None
            if already_detail(s):
                value="detail"
                signature=("detail",tuple(label(n) for n in s["nodes"] if bounds(n) and bounds(n)[1]<800))
            else:
                # am start -W can return while a previous same-place search is
                # still visible. Wait for this URI's actual query caption and
                # two stable observations before selecting a POI.
                if not any(label(n) in {keyword,"搜索框，"+keyword} for n in s["nodes"]):
                    previous[0]=None;return None
                value=self._place_candidates(s,query)
                if not value:
                    related=self._place_candidates(s,query,include_related=True)
                    empty=tuple(label(n) for n in s["nodes"] if bounds(n) and label(n) in {
                        "没有找到相关地点", "未找到相关地点", "暂无搜索结果", "无搜索结果", "没有搜索结果"})
                    if not related and not empty:previous[0]=None;return None
                    value="unmatched"
                    signature=(value,tuple(c["candidate_id"] for c,n in related),empty)
                else:
                    signature=tuple((c["candidate_id"],tuple(bounds(n))) for c,n in value)
            signature=(s["epoch"],signature)
            if previous[0]!=signature:
                previous[0]=signature;return None
            return value
        shot, values = self._wait(settled,
                                  checkpoint,"place_list",attempts=12)
        chosen = choices.get(role)
        if values=="unmatched":
            return {"kind":"clarification","fields":[role],"message":"高德已显示本次搜索结果，但未读到可安全核对的主要地点，请补充具体名称或街道地址；不会代选门口或停车场。"}
        if values=="detail":
            names=[label(n) for n in shot["nodes"] if n.get("class")=="android.view.ViewGroup" and bounds(n)
                   and bounds(n)[1]<600 and place_key(label(n))==place_key(query,city)]
            names=list(dict.fromkeys(names))
            if len(names)!=1:
                return {"kind":"clarification","fields":[role],"message":"高德直接打开的地点与名称不能唯一核对，请补充具体站名、分馆或地址。"}
            name=names[0]
            shot["evidence"]["page_kind"]="place_detail"
            place=self._read_place(shot,name,selected=chosen)
            candidate={"candidate_id":hashlib.sha256(json.dumps([name,place["address"],place["city"]],ensure_ascii=False).encode()).hexdigest()[:24],
                       "name":name,"address":place["address"],"city":place["city"],"evidence_id":shot["evidence"]["evidence_id"]}
            if chosen and (chosen.get("name")!=name or chosen.get("address") and chosen.get("address")!=place["address"]):
                raise WorkflowError("高德直接打开的地点与已选名称或地址不同，请重新选择")
            if not chosen and "地铁站" not in query:
                return {"kind":"choice","role":role,"candidates":[candidate]}
            return {"kind":"place","place":place}
        exact = [(c,n) for c,n in values if place_key(c["name"]) == place_key(query,city)]
        if chosen:
            exact = [(c,n) for c,n in values if c["candidate_id"] == chosen.get("candidate_id") and c["name"] == chosen.get("name")]
            if len(exact) != 1:
                raise WorkflowError("已选地点与当前高德候选不一致，请重新选择")
        elif query.endswith(("地铁站", "（地铁站）", "(地铁站)")):
            # The station category in the exact user request disambiguates a
            # same-named street, without collapsing two different station rows.
            station_rows = [(c, n) for c, n in exact if c["name"].endswith(("(地铁站)", "（地铁站）"))]
            if station_rows:
                exact = station_rows
        if not chosen and len(exact) != 1:
            if len({c["candidate_id"] for c,n in values})!=len(values):
                return {"kind":"clarification","fields":[role],"message":"高德出现无法区分地址的同名地点，请补充具体分馆、院区或街道地址。"}
            return {"kind":"choice","role":role,"candidates":[c for c,n in values]}
        candidate,node = exact[0]
        title = self._place_title_target(node, nodes=shot["nodes"])
        signature = (node.get("resource-id"), node.get("bounds"), node.get("text"), node.get("content-desc"))
        self._tap(shot,title,checkpoint,place_binding={"query":query,
                  "candidate_id":candidate["candidate_id"],"row_signature":signature})
        name=candidate["name"]
        def detail_ready(s, *, allow_expand=True):
            if already_detail(s) and any(n.get("class")=="android.view.ViewGroup" and bounds(n)
                    and bounds(n)[1]<800 and place_key(label(n))==place_key(name) for n in s["nodes"]):
                return True
            if self._text_target(s,"路线") is not None and self._text_target(s,"导航") is not None:
                compact = self._compact_poi_header(s)
                if compact and place_key(compact["name"]) == place_key(name):
                    return compact
                if allow_expand and self._collapsed_station_card(s, name):
                    return "expand"
                names = self._poi_title_names(s)
                if len(names) == 1 and place_key(next(iter(names))) != place_key(name):
                    raise WorkflowError("高德打开的地点与本次选择不一致（"+next(iter(names))+"），已停止，请重新选择地点")
            return None
        try:
            detail, match = self._wait(detail_ready,checkpoint,"place_detail")
            if match == "expand":
                self._expand_station_card(detail, name, checkpoint)
                detail, match = self._wait(lambda s: detail_ready(s, allow_expand=False), checkpoint, "place_detail")
        except (Cancelled, ScreenContextChanged):
            raise
        except WorkflowError as error:
            if str(error).startswith("高德页面未出现预期控件"):
                raise WorkflowError("已选择高德地点，但完整地点详情尚未展开；请返回高德核对主地点后重新查询") from None
            raise
        header = {} if match is True else {"header_bounds": match["bounds"]}
        return {"kind":"place","place":self._read_place(detail,name,selected=chosen,candidate=candidate,
                                                           **header)}

    def _read_place(self,detail,name,*,selected=None,candidate=None,header_bounds=None):
        city=self.request.get("city", "")
        visible_nodes = self._visible_place_nodes(detail["nodes"])
        if header_bounds is not None:
            compact = self._compact_poi_header(detail)
            if not compact or compact["bounds"] != list(header_bounds) or compact["name"] != name:
                raise WorkflowError("高德紧凑地点主卡片无法独立核对，未采纳地点字段")
            visible_nodes = [n for n in visible_nodes if bounds(n) and
                             header_bounds[0] <= bounds(n)[0] < bounds(n)[2] <= header_bounds[2] and
                             header_bounds[1] <= bounds(n)[1] < bounds(n)[3] <= header_bounds[3]]
        administrative = r"(?:[\u4e00-\u9fff]{2,}(?:省|市|区|县|自治区|自治州)){1,5}"
        addresses=[]
        for n in visible_nodes:
            text=clean(n.get("text"))
            if (not bounds(n) or (header_bounds is None and bounds(n)[1]>=800) or len(text)<=1
                    or place_key(text,city)==place_key(name,city)
                    or re.search(r"榜|排名|搜索|评价|评论|评分|人气|推荐|附近|途经线路|开往|景区|共省",text)
                    or "号线" in text or re.fullmatch(r"(?:夜|高峰快线)?\d+路",text)):
                continue
            # A title may expose one glyph per child. Require an actual
            # administrative address or street/address form, not merely a
            # character such as 省/路 appearing somewhere in a header.
            admin=bool(re.fullmatch(administrative,text))
            street=bool(re.search(r"[\u4e00-\u9fffA-Za-z\d]{2,}(?:路|街|大道|巷|弄|道|村|镇|乡)",text))
            number=bool(re.search(r"\d+(?:[-—]\d+)?号",text))
            if not admin and not street:
                continue
            addresses.append((int(street)*2+int(number),text))
        address=max(addresses,key=lambda item:item[0])[1] if addresses else None
        # The city is a fact only when the actual address has its explicit
        # administrative token; 广州 in a street/POI name is not a city proof.
        actual_city=(city if city and city.removesuffix("市")+"市" in (address or "") else None)
        city_eid = detail["evidence"]["evidence_id"] if actual_city else None
        if not actual_city:
            # Some POI headers omit the city, while their own visible overview
            # states e.g. '<this exact campus>，位于广州市番禺区'. Bind that
            # literal location sentence to this POI, never a recommendation,
            # ranking, review or a city supplied only in the search query.
            excluded = re.compile(r"^(?:相似服务推荐|相关推荐|热门推荐|用户评价|用户评论|用户点评|全部评价|全部评论|网友评论|热门评论|精选评论)(?:[（(：:\s\d]|$)")
            stops = [bounds(n)[1] for n in visible_nodes if bounds(n) and excluded.match(label(n))]
            stop = min(stops) if stops else 1500
            key = place_key(name)
            cities = set()
            parents = {child: parent for parent in detail["nodes"] for child in parent}
            visible_set = set(visible_nodes)
            for n in visible_nodes:
                text = clean(n.get("text"))
                if not bounds(n) or not 800 < bounds(n)[1] < stop:
                    continue
                intro = re.match(r"^(.+?)[，,]\s*(?:位于|坐落于|地处)\s*(.+)", text)
                if not intro or place_key(intro.group(1)) != key:
                    continue
                # The observed overview is one bounded card containing its
                # own 概述 / 查看全部 controls. A same-name sentence elsewhere,
                # including a visible review, is not this POI's overview.
                ancestor = parents.get(n)
                overview = False
                forbidden = False
                while ancestor is not None:
                    forbidden = forbidden or bool(excluded.match(label(ancestor)))
                    rect = bounds(ancestor)
                    labels = {label(child) for child in ancestor.iter("node") if child in visible_set and bounds(child)}
                    if rect and rect[3] - rect[1] <= 500 and {"概述", "查看全部"} <= labels:
                        overview = True
                    ancestor = parents.get(ancestor)
                if forbidden or not overview:
                    continue
                token = re.match(r"^(?:中国)?(?:[\u4e00-\u9fff]{2,10}(?:省|自治区))?([\u4e00-\u9fff]{2,10}?市)", intro.group(2))
                if token: cities.add(token.group(1))
            if len(cities) == 1:
                actual_city = next(iter(cities)); city_eid = detail["evidence"]["evidence_id"]
        if (not actual_city and candidate and candidate.get("name") == name and candidate.get("city")
                and candidate.get("evidence_id")):
            actual_city = candidate["city"]
            city_eid = candidate["evidence_id"]
        expected=(selected or {}).get("address")
        if expected and address and address.endswith(expected):
            prefix=address[:-len(expected)]
            if (not prefix or re.fullmatch(administrative,prefix)) and not re.search(r"路|街|巷|弄|号|大道",prefix):
                # Retain the exact human-selected address only when the new
                # literal readback merely adds an administrative prefix.
                # No unit, door number or parenthetical text is discarded.
                address=expected
        eid=detail["evidence"]["evidence_id"]
        return {"name":name,"address":address,"city":actual_city,"source":"explicit",
                "evidence_refs":{"name":[eid],"address":[eid] if address else [],"city":[city_eid] if actual_city else []}}

    def _set_origin(self, origin, checkpoint):
        shot,n = self._wait(lambda s:next((n for n in s["nodes"] if n.get("resource-id")==ID+"route_edit_summary_start" and bounds(n)),None),checkpoint,"route_list")
        self._tap(shot,n,checkpoint,role="origin",page="route_input")
        shot,fields = self._wait(lambda s: sorted([n for n in s["nodes"] if n.get("class")=="android.widget.EditText" and bounds(n)
                    and n.get("resource-id") not in {ID+"route_edit_summary_start",ID+"route_edit_summary_end"}],key=lambda n:bounds(n)[1]) or None, checkpoint,"place_list")
        value=(self.request.get("city", "")+origin["name"])
        self._enter(shot,fields[0],value,"origin",checkpoint)
        shot,node = self._wait(lambda s: next((n for n in s["nodes"] if n.get("class")=="android.view.View" and
                              place_key(label(n))==place_key(origin["name"]) and bounds(n) and bounds(n)[1]>400),None),checkpoint,"place_list",attempts=12)
        # Never select by old index. If multiple same-name rows are observed,
        # return to the user instead of guessing a branch.
        equal=[n for n in shot["nodes"] if n.get("class")=="android.view.View" and place_key(label(n))==place_key(origin["name"]) and bounds(n) and bounds(n)[1]>400]
        if len(equal)!=1: raise WorkflowError("起点存在同名候选，请补充具体地址后重新查询")
        self._tap(shot,node,checkpoint)

    @staticmethod
    def _location_place(shot):
        """Read only the official My Location card, never a nearby POI list."""
        ns = [n for n in shot["nodes"] if bounds(n)]
        if not any(re.fullmatch(r"您当前位置是在.+附近", label(n)) for n in ns):
            return None
        if not any(re.fullmatch(r"直线\s*[<＜]\s*100米", label(n)) for n in ns):
            return None
        address_pattern = (r"^(?:中国)?(?:(?:[\u4e00-\u9fff]{2,8}省|"
                           r"[\u4e00-\u9fff]{2,10}自治区))?([\u4e00-\u9fff]{2,10}市)"
                           r"[\u4e00-\u9fff\d].*(?:路|街|大道|巷|弄|道|村|镇|乡).+")
        titles = [(label(n), re.fullmatch(address_pattern, label(n))) for n in ns
                  if n.get("class") == "android.view.ViewGroup" and n.get("clickable") == "true"
                  and bounds(n)[1] > 300]
        titles = [(title, match.group(1)) for title, match in titles if match]
        if len(set(titles)) != 1:
            return None
        address, city = titles[0]
        eid = shot["evidence"]["evidence_id"]
        return {"name": address, "address": address, "city": city, "source": "current_location",
                "evidence_refs": {field: [eid] for field in ["name", "address", "city"]}}

    def _resolve_current_location(self, checkpoint):
        self._progress("planning_transit", "正在高德核对真实当前位置、城市和定位新鲜度", checkpoint)
        self._action(checkpoint)
        # Official GUI entry. No coordinates, route API or reverse geocoder.
        uri = "androidamap://myLocation?" + urlencode({"sourceApplication": "SilverGuide"})
        self.base.shell("am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", uri, "-p", PACKAGE)
        previous = [None]
        def settled(shot):
            place = self._location_place(shot)
            signature = (shot["epoch"], place["name"], place["city"]) if place else None
            stable = signature is not None and previous[0] == signature
            previous[0] = signature
            return place if stable else None
        try:
            shot, place = self._wait(settled, checkpoint, "location", attempts=12)
        except (Cancelled, ScreenContextChanged):
            raise
        except WorkflowError:
            self._check(checkpoint)
            return {"kind": "clarification", "fields": ["origin"],
                    "message": "高德当前位置的真实地址尚未能核对，请填写明确出发地。"}
        from device.location_status import read_location_status
        status = read_location_status(self.base, checkpoint)
        self._check(checkpoint)
        if shot["epoch"] != self.base.context_epoch:
            raise ScreenContextChanged("暂停恢复后需重新读取当前位置和定位健康状态")
        (self.folder / "location-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        allowed = {"verified", "enabled", "permission_granted", "app_access_recent", "provider", "age_seconds",
                   "accuracy_m", "is_mock", "checked_at", "fix_time"}
        shot["evidence"]["location_status"] = {key: value for key, value in status.items() if key in allowed}
        if not status.get("verified"):
            return {"kind": "clarification", "fields": ["origin"],
                    "message": status.get("reason") or "当前位置的权限或定位新鲜度未通过核对，请填写明确出发地；不会沿用旧位置。"}
        city = self.request.get("city", "").removesuffix("市")
        if city and city != place["city"].removesuffix("市"):
            return {"kind": "clarification", "fields": ["origin", "city"],
                    "message": "高德真实定位城市与本次填写城市不同，请核对城市或填写明确出发地。"}
        return {"kind": "place", "place": place}

    def _set_current_origin(self, checkpoint):
        shot, node = self._wait(lambda s: next((n for n in s["nodes"]
            if n.get("resource-id") == ID + "route_edit_summary_start" and bounds(n)), None), checkpoint, "route_input")
        self._tap(shot, node, checkpoint, role="origin", page="route_input")
        previous = [None]
        def location_row(s):
            matches = [n for n in s["nodes"] if label(n) == "我的位置" and bounds(n)
                       and n.get("class") == "android.view.View" and bounds(n)[1] > 400]
            node = matches[0] if len(matches) == 1 else None
            signature = (s["epoch"], node.get("bounds"), label(node)) if node is not None else None
            stable = signature is not None and previous[0] == signature
            previous[0] = signature
            return node if stable else None
        shot, node = self._wait(location_row, checkpoint, "route_input", attempts=10)
        self._tap(shot, node, checkpoint, role="origin", page="route_input")

    def _scroll_detail(self, shot, checkpoint, *, reverse=False):
        self._action(checkpoint)
        root=next((n for n in shot["nodes"] if label(n)=="公交详情页面" and bounds(n)),None)
        if root is None: raise WorkflowError("未确认公交详情容器，停止滚动")
        if shot["epoch"]!=self.base.context_epoch: raise ScreenContextChanged("暂停恢复后滚动目标已过期")
        target=self._guard(shot,root,"scroll","route_detail",action="scroll")
        x1,y1,x2,y2=bounds(root)
        x=x1+int((x2-x1)*.84)
        start,end=y1+int((y2-y1)*.91),y1+int((y2-y1)*.30)
        if reverse:start,end=end,start
        self.base.shell("input","swipe",str(x),str(start),str(x),str(end),"420")
        self._audit_action(target,"scroll_detail")

    def _route_cards(self, shot):
        values=[]
        for n in shot["nodes"]:
            s=label(n);r=bounds(n)
            # Amap may show a walking-only calorie card inside the transit
            # tab. A time/distance alone is not a public-transport itinerary.
            route_text = re.sub(r"(?:步行|骑行|驾车|推荐|其他)?路线(?:方案)?", "", s)
            riding_line = bool(re.search(r"\d+[A-Za-z]?号线|(?:夜|高峰快线|旅游)?\d+[A-Za-z]?路|APM(?:线)?|[\u4e00-\u9fff]{2,12}线|(?:乘坐|搭乘)(?:公交|地铁)|(?:公交|地铁)(?:方案|出行)", route_text))
            if (r and n.get("clickable")=="true" and re.match(r"\d+(?:小时|分钟)",s)
                    and r[2]-r[0]>900 and riding_line
                    and not any(w in s for w in ["骑行","打车","网约车","顺风车"])):
                values.append(n)
        values.sort(key=lambda n:(bounds(n)[1],-(bounds(n)[3]-bounds(n)[1])))
        return values

    def _wait_route_cards(self, checkpoint, attempts=16):
        previous = None
        consecutive = 0

        def stable_result(shot):
            nonlocal previous, consecutive
            absent = re.search(r"暂无(?:公交|公共交通|可用)?(?:方案|路线)|未找到.*(?:公交|公共交通|路线)|无(?:公交|公共交通)(?:方案|路线)", shot["evidence"]["text"])
            cards = self._route_cards(shot)
            if absent is not None and cards:
                previous = None
                consecutive = 0
                return None  # Mixed old/new states are not a current result.
            if absent is not None:
                signature = ("no_route", shot["epoch"], absent.group(0))
                value = "no_route"
            elif cards:
                signature = ("cards", shot["epoch"], tuple((label(n), n.get("bounds")) for n in cards))
                value = cards
            else:
                # Loading/empty frames invalidate every earlier candidate.
                previous = None
                consecutive = 0
                return None
            consecutive = consecutive + 1 if signature == previous else 1
            previous = signature
            return value if consecutive >= 2 else None

        return self._wait(stable_result, checkpoint, "route_list", attempts=attempts)

    def query(self, request, task_id, query_id, checkpoint, choices=None):
        transport=getattr(self.base,"transport",None)
        previous=getattr(transport,"prefer_transit_bridge",False) if transport else False
        if transport: transport.prefer_transit_bridge=True
        self._query_in_progress = True
        try:
            identity = (task_id, query_id)
            epoch = self.base.context_epoch
            attempt = self._resume_attempts.get(identity, 0)
            try:
                if attempt:
                    outcome = self._query(request,task_id,query_id,checkpoint,choices,attempt=attempt,
                                          action_offset=self._resume_actions.get(identity, 0))
                else:
                    outcome = self._query(request,task_id,query_id,checkpoint,choices)
                checkpoint()
                if epoch != self.base.context_epoch:
                    raise ScreenContextChanged("暂停恢复后，旧轮次采集结果已废弃")
                outcome["context_epoch"] = epoch
                return outcome
            except ScreenContextChanged as error:
                checkpoint()
                # A single explicit resume can restart the *query*, never the
                # old click/model action. Do not hide arbitrary context errors.
                home = isinstance(error, TransitHomeInterrupted)
                if (attempt >= 1 or not home and (epoch == self.base.context_epoch
                        or getattr(self.base, "transit_resume_epoch", None) != self.base.context_epoch)):
                    raise
                context = dict(self._query_map_context) if home else None
                self._discard_resumed_attempt(task_id, query_id, checkpoint, cause="home" if home else "resume")
                self._resume_attempts[identity] = 1
                if home:
                    self._restore_query_from_home(context, checkpoint)
                actions_used = self.actions
                # New UI reads, native place resolution and location consent
                # start from scratch. Action allowance is shared across both
                # attempts; a long user pause gets a new bounded read budget.
                resumed_epoch = self.base.context_epoch
                outcome = self._query(request, task_id, query_id, checkpoint, {},
                                      attempt=1, action_offset=actions_used)
                checkpoint()
                if resumed_epoch != self.base.context_epoch:
                    raise ScreenContextChanged("恢复期间页面再次变化，请重新查询")
                outcome["context_epoch"] = resumed_epoch
                event = {"time": now(), "stage": "transit_query", "action": "restart_transit_query",
                         "outcome": "reobserved", "attempt": 1, "query_id": query_id,
                         "cause": "home" if home else "resume",
                         "message": "已重新读取中断后的高德页面；地点仍需本轮确认，路线仍须独立核对"}
                callback = getattr(self.base, "recovery_callback", None)
                if callback:
                    callback(event)
                with (self.base.evidence_root / task_id / "recoveries.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps(event, ensure_ascii=False) + "\n")
                return outcome
        finally:
            if self._resume_attempts.get((task_id, query_id), 0):
                self._resume_actions[(task_id, query_id)] = self.actions
            self._query_in_progress = False
            if transport: transport.prefer_transit_bridge=previous

    def _check_home_recovery(self, context, checkpoint):
        """Allow only HOME, never an unknown app, dialog or lock screen."""
        for attempt in range(3):
            self._check(checkpoint)
            policy = self.base.shell("dumpsys", "window", "policy")
            self._check(checkpoint)
            if re.search(r"\b(?:showing|mShowingLockscreen|mDreamingLockscreen)=true\b", policy):
                raise WorkflowError("手机已锁屏，不能自动恢复高德，请解锁后重新查询")
            snapshot = self.base.shell("dumpsys", "activity", "activities")
            self._check(checkpoint)
            if not re.search(r"mResumedActivity:\s*ActivityRecord\{[^}\n]*\scom\.huawei\.android\.launcher/[A-Za-z0-9_.$/]+\s+t\d+\b", snapshot):
                raise WorkflowError("高德当前窗口被其他应用或提示遮挡，请本人返回高德后重新查询")
            if not context or not self._map_task_still_exists(snapshot, context):
                raise WorkflowError("原高德任务已结束或被替换，请重新查询")
            # Current focus belongs to the display dump, not the windows-only
            # list on this Huawei build. A HOME animation may briefly expose
            # an explicit null focus; re-read every guard, never infer focus
            # from mFocusedApp or a launcher window somewhere in the list.
            displays = self.base.shell("dumpsys", "window", "displays")
            self._check(checkpoint)
            focuses = re.findall(r"^\s*mCurrentFocus=([^\n]+)", displays, re.M)
            if len(focuses) == 1 and re.fullmatch(
                    r"Window\{[^}\n]*\scom\.huawei\.android\.launcher/[A-Za-z0-9_.$/]+\}", focuses[0].strip()):
                return
            if focuses == ["null"] and attempt < 2:
                time.sleep(.2)
                continue
            raise WorkflowError("当前窗口不是可核对的华为桌面，不能自动恢复高德")

    def _restore_query_from_home(self, context, checkpoint):
        # Recheck after recording the recovery, immediately before moving the
        # exact observed task. This counts against the same GUI action limit.
        self._check_home_recovery(context, checkpoint)
        self._action(checkpoint)
        self.base.shell("am", "task", "focus", str(context["android_task_id"]))
        self._check(checkpoint)
        expected = (context["android_task_id"], context["activity"])
        for index in range(3):
            snapshot = self.base.shell("dumpsys", "activity", "activities")
            self._check(checkpoint)
            if self._resumed_map_identity(snapshot) == expected:
                self.base.context_epoch += 1
                return
            if index < 2:
                time.sleep(.2)
        raise WorkflowError("高德未恢复到原先记录的任务，已停止自动恢复，请重新查询")

    def _discard_resumed_attempt(self, task_id, query_id, checkpoint, *, cause="resume"):
        checkpoint()
        folder = self.base.evidence_root / task_id / "transit" / query_id
        folder.mkdir(parents=True, exist_ok=True)
        discarded = {"query_id": query_id, "time": now(), "attempt": 0,
                     "actions": self.actions, "observations": self.observations,
                     "route_id": self.route_id}
        (folder / ("discarded-attempt-" + uuid.uuid4().hex + ".json")).write_text(
            json.dumps(discarded, ensure_ascii=False, indent=2), encoding="utf-8")
        for key in list(self._detail_contexts):
            if key[:2] == (task_id, query_id):
                del self._detail_contexts[key]
        context_path = folder / "view-context.json"
        if context_path.exists():
            context_path.rename(folder / ("discarded-view-context-" + uuid.uuid4().hex + ".json"))
        self.observations = []
        self.route_id = None
        event = {"time": now(), "stage": "transit_query", "action": "restart_transit_query",
                 "outcome": "attempting", "attempt": 1, "query_id": query_id,
                 "cause": cause,
                 "message": ("离开高德后正在返回已记录的原任务，并从头重新读取路线；本轮不会自动分享到钉钉" if cause == "home"
                             else "已继续任务：正在废弃暂停前页面并从头重新核对路线；本轮不会自动分享到钉钉")}
        checkpoint()
        callback = getattr(self.base, "recovery_callback", None)
        if callback:
            callback(event)
        with (self.base.evidence_root / task_id / "recoveries.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(event, ensure_ascii=False) + "\n")
        checkpoint()

    def _query(self, request, task_id, query_id, checkpoint, choices=None, *, attempt=0, action_offset=0):
        self.started=time.monotonic();self.actions=action_offset;self.request=dict(request)
        self._query_epoch=self.base.context_epoch
        self._query_map_context=None
        self.query_attempt=attempt
        self.query_id=query_id;self.route_id=None;self.observations=[]
        self.task_id=task_id;self._assist=self._assist_sessions.get((task_id,query_id));self._assist_trigger=None
        self.base.task_id=task_id
        self.folder=self.base.evidence_root/task_id/"transit"/query_id
        if attempt:
            self.folder = self.folder / ("resume-" + str(attempt))
        self.folder.mkdir(parents=True,exist_ok=True)
        self.check_ready(checkpoint)
        queried_at=now();choices=choices or {};places={}
        user_eid=uuid.uuid4().hex
        self.observations.append({"evidence_id":user_eid,"query_id":query_id,"route_id":None,"observation_id":user_eid,
                                  "text":json.dumps(request,ensure_ascii=False),"page_kind":"user_request"})
        if not request.get("origin"):
            if not request.get("use_current_location"):
                return {"kind": "clarification", "fields": ["origin"], "message": "请填写出发地，或选择从真实当前位置出发。"}
            resolution = self._resolve_current_location(checkpoint)
            if resolution["kind"] != "place": return resolution
            places["origin"] = resolution["place"]
        for role in ["origin","destination"]:
            value=request.get(role,"")
            if role=="origin" and not value: continue
            self._progress("planning_transit", "正在高德核对"+("出发地" if role=="origin" else "目的地"), checkpoint)
            resolution=self._resolve_place(role,value,choices,checkpoint)
            if resolution["kind"]!="place": return resolution
            places[role]=resolution["place"]
        self._tap_label("路线",checkpoint,"place_detail")
        self._progress("planning_transit", "正在设置出发地并查询公共交通", checkpoint)
        def entry_state(s):
            if any(n.get("resource-id")==ID+"route_edit_summary_start" and bounds(n) for n in s["nodes"]):return "input"
            if any(label(n)=="公交详情页面" and bounds(n) for n in s["nodes"]):return "detail"
            return None
        shot,entry=self._wait(entry_state,checkpoint,"route_input",attempts=12)
        if entry=="detail":
            # Amap may restore its last expanded plan for the same destination.
            # Return to the actual query fields before setting an explicit start.
            self._action(checkpoint)
            self.base.shell("input","keyevent","KEYCODE_BACK")
            shot,_=self._wait(lambda s:next((n for n in s["nodes"] if n.get("resource-id")==ID+"route_edit_summary_start" and bounds(n)),None),
                              checkpoint,"route_input",attempts=12)
        cancel=self._text_target(shot,"暂不开启")
        if cancel is not None: self._tap(shot,cancel,checkpoint,role="back",page="route_input")
        if request.get("origin"):
            self._set_origin(places["origin"],checkpoint)
        else:
            self._set_current_origin(checkpoint)
        self._select_public_transit(places, checkpoint)
        preference={"fewer_transfers":"换乘少","less_walking":"步行少","recommended":"高德推荐"}[request.get("preference","recommended")]
        shot,_=self._wait(lambda s:self._text_target(s,preference),checkpoint,"route_list")
        self._tap_label(preference,checkpoint,"route_list")
        shot,cards=self._wait_route_cards(checkpoint)
        start=next((clean(n.get("text")) for n in shot["nodes"] if n.get("resource-id")==ID+"route_edit_summary_start"),"")
        end=next((clean(n.get("text")) for n in shot["nodes"] if n.get("resource-id")==ID+"route_edit_summary_end"),"")
        origin_matches = (place_key(start) == place_key(places["origin"]["name"]) if request.get("origin") else start == "我的位置")
        if not origin_matches or place_key(end)!=place_key(places["destination"]["name"]):
            raise WorkflowError("高德起终点与本次明确需求不一致，未采纳路线")
        if cards=="no_route":
            rid=uuid.uuid4().hex
            self.route_id=rid;shot["evidence"]["route_id"]=rid
            return {"kind":"result","result":{"task_id":task_id,"query_id":query_id,"route_id":rid,
                    "queried_at":queried_at,"collected_at":now(),"origin":places["origin"],"destination":places["destination"],
                    "classification":"NO_ROUTE","segments":[]},"evidence":self.observations,"collection_complete":False}
        self.route_id=hashlib.sha256((query_id+label(cards[0])).encode()).hexdigest()[:24]
        selected_eid=uuid.uuid4().hex
        selected_list={**shot["evidence"],"evidence_id":selected_eid,"route_id":self.route_id,
                       "text":label(cards[0])+"\n起点："+start+"\n终点："+end+"\n本轮已点击筛选："+preference}
        self._tap(shot,cards[0],checkpoint,role="route",page="route_list")
        self._progress("reading_transit", "正在展开选中方案，逐段读取路线详情", checkpoint)
        self._wait(lambda s:next((n for n in s["nodes"] if label(n)=="公交详情页面" and bounds(n)
                                   and s["evidence"]["page_kind"] == "route_detail"),None),checkpoint,"route_detail")
        previous=None;stable=0;collection_complete=False;last=None
        for index in range(self.scroll_limit+1):
            detail=self._observe(checkpoint,"route_detail",screenshot=True);last=detail
            text=detail["evidence"]["text"]
            meaningful=re.sub(r"(?:预计)?\d{1,2}:\d{2}(?:进站|到站)?|即将进站|第一班|第二班","",text)
            fingerprint=hashlib.sha256(meaningful.encode()).hexdigest()
            stable=stable+1 if fingerprint==previous else 0
            previous=fingerprint
            destination=place_key(places["destination"]["name"])
            endpoints=[n for n in detail["nodes"] if bounds(n) and bounds(n)[1]>650 and
                       n.get("class")=="android.view.ViewGroup" and place_key(label(n))==destination]
            if stable and endpoints:
                collection_complete=True;break
            if index<self.scroll_limit:self._scroll_detail(detail,checkpoint)
        self._check(checkpoint)
        self._progress("validating_transit", "正在核对已读取的线路、方向和上下车站", checkpoint)
        return self.collect_observed_result(request, task_id, query_id, places, queried_at,
                                            selected_list, collection_complete, checkpoint, last=last)

    def collect_observed_result(self, request, task_id, query_id, places, queried_at,
                                selected_list, collection_complete, checkpoint, *, last=None):
        """Extract only observations already collected; never drive GUI navigation.

        Both rules and AutoGLM use this collector and the runtime's independent
        validator. Calling it does not grant a model's Finish any success status.
        """
        place_refs = {ref for place in places.values() for refs in place.get("evidence_refs", {}).values() for ref in refs}
        selected=[e for e in self.observations if e["page_kind"] in {"user_request","location","place_detail","route_detail"}
                  or e["evidence_id"] in place_refs]
        selected.append(selected_list)
        # Native text is the primary fact source. Only request visual fallback
        # when the actual detail text is sparse; avoid sending a large map image
        # for an already-readable native itinerary.
        sparse=any(len(e["text"])<80 for e in selected if e["page_kind"]=="route_detail")
        image=base64.b64encode(Path(last["evidence"]["screenshot_path"]).read_bytes()).decode() if last and sparse else None
        extraction_request = self._extraction_request(request, places)
        from transit.native import extract_native
        extracted=extract_native(selected,extraction_request,task_id=task_id,query_id=query_id,route_id=self.route_id)
        if extracted is None:
            extracted=self.models.extract_transit(selected,extraction_request,task_id=task_id,query_id=query_id,route_id=self.route_id,image=image)
        self._check(checkpoint)
        result=extracted.model_dump() if hasattr(extracted,"model_dump") else extracted
        # Resolved place facts come from inspected POI pages, not model memory.
        result.update(task_id=task_id,query_id=query_id,route_id=self.route_id,origin=places["origin"],destination=places["destination"],
                      queried_at=queried_at,collected_at=now(),preference=request.get("preference","recommended"))
        self._remember_detail_context(result, checkpoint)
        (self.folder/"collection.json").write_text(json.dumps({"actions":self.actions,"elapsed_seconds":time.monotonic()-self.started,
                    "collection_complete":collection_complete,"route_id":self.route_id},ensure_ascii=False,indent=2))
        return {"kind":"result","result":result,"evidence":selected,"collection_complete":collection_complete}

    @staticmethod
    def _extraction_request(request, places):
        # These names have just been read from the real selected POI pages.
        # Preserve the original task request for the independent validator and
        # audit. Current-location mode must remain empty-origin, not explicit.
        value = dict(request)
        for role in ("origin", "destination"):
            if request.get(role) and places.get(role, {}).get("name"):
                value[role] = places[role]["name"]
        return value

    @staticmethod
    def _resumed_map_identity(snapshot):
        match = re.search(r"mResumedActivity:\s*ActivityRecord\{[^}\n]*?\s(com\.autonavi\.minimap/[A-Za-z0-9_.$/]+)\s+t(\d+)\b", snapshot)
        return (int(match.group(2)), match.group(1)) if match else None

    @staticmethod
    def _map_task_still_exists(snapshot, context):
        task_id = str(context["android_task_id"])
        # Both the real current task and the previously observed ActivityRecord
        # must still exist. A matching launcher/package by itself is not enough.
        task = re.search(r"Task\{[^}\n]*#" + re.escape(task_id) + r"\b[^}\n]*(?:A|I)=[^}\n]*com\.autonavi\.minimap(?:[/\s}]|$)", snapshot)
        record = re.search(r"ActivityRecord\{[^}\n]*\s" + re.escape(context["activity"]) + r"\s+t" + re.escape(task_id) + r"\b", snapshot)
        return bool(task and record)

    def _context_key(self, result):
        values = tuple(result.get(k, "") for k in ["task_id", "query_id", "route_id"])
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value) for value in values):
            raise WorkflowError("路线任务身份记录不完整，请重新查询")
        return values

    def _context_path(self, key):
        return self.base.evidence_root / key[0] / "transit" / key[1] / "view-context.json"

    @staticmethod
    def _visible_detail_nodes(xml):
        root = ET.fromstring(xml)
        visible = []
        def visit(node, shown=True):
            shown = shown and node.get("visible-to-user", "true") != "false"
            if shown and node.get("package") == PACKAGE and bounds(node):
                visible.append(node)
            for child in node: visit(child, shown)
        visit(root)
        markers = [n for n in visible if label(n) == "公交详情页面"]
        if len(markers) != 1:
            raise WorkflowError("高德已离开可见的公交详情，请重新查询")
        viewport = bounds(markers[0])
        return [n for n in visible if bounds(n)[0] < viewport[2] and bounds(n)[2] > viewport[0]
                and bounds(n)[1] < viewport[3] and bounds(n)[3] > viewport[1]], viewport

    @staticmethod
    def _detail_strip(nodes, viewport, result):
        normalize = lambda value: re.sub(r"\s+", "", clean(value))
        lines = {normalize(s.get("line", "")) for s in result.get("segments", []) if s.get("mode") in {"bus", "subway"}} - {""}
        candidates = []
        for node in nodes:
            text, rect = label(node), bounds(node)
            if ("ViewGroup" not in node.get("class", "") or not re.match(r"^\d+(?:小时|分钟)", text)
                    or rect[2] - rect[0] < (viewport[2] - viewport[0]) * .65):
                continue
            stable = normalize(re.sub(r"(?:预计)?\d{1,2}:\d{2}(?:到达|出发)?", "", text))
            if lines and all(line in stable for line in lines):
                candidates.append({"fingerprint": hashlib.sha256(stable.encode()).hexdigest(), "text": stable,
                                   "bottom": rect[3]})
        fingerprints = {candidate["fingerprint"] for candidate in candidates}
        return candidates[0] if len(fingerprints) == 1 else None

    def _saved_detail_strip(self, result):
        """Backfill old contexts only from this query's trusted raw detail XML."""
        key = self._context_key(result)
        directory = self._context_path(key).parent.resolve()
        strips = {}
        evidence = result.get("evidence") or self.observations
        for record in evidence:
            if (record.get("query_id") != key[1] or record.get("route_id") != key[2]
                    or record.get("page_kind") != "route_detail" or not record.get("xml_path")):
                continue
            path = Path(record["xml_path"]).resolve()
            if path.suffix != ".xml" or not path.is_relative_to(directory):
                continue
            try:
                nodes, viewport = self._visible_detail_nodes(path.read_text(encoding="utf-8"))
                strip = self._detail_strip(nodes, viewport, result)
            except (OSError, ET.ParseError, WorkflowError):
                continue
            if strip: strips[strip["fingerprint"]] = strip
        return next(iter(strips.values())) if len(strips) == 1 else None

    def _remember_detail_context(self, result, checkpoint):
        self._check(checkpoint)
        key = self._context_key(result)
        snapshot = self.base.shell("dumpsys", "activity", "activities")
        self._check(checkpoint)
        identity = self._resumed_map_identity(snapshot)
        if identity is None:
            return None  # Route facts remain available, but reopening is not guessed.
        context = {"task_id": key[0], "query_id": key[1], "route_id": key[2],
                   "android_task_id": identity[0], "activity": identity[1], "captured_at": now()}
        if not self._map_task_still_exists(snapshot, context):
            return None
        strip = self._saved_detail_strip(result)
        if strip:
            context.update(detail_strip_fingerprint=strip["fingerprint"], detail_strip_text=strip["text"])
        self._detail_contexts[key] = context
        path = self._context_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        return context

    def _detail_context(self, result):
        key = self._context_key(result)
        context = self._detail_contexts.get(key)
        if context is None:
            try:
                context = json.loads(self._context_path(key).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise WorkflowError("本次高德页面身份记录已丢失，请重新查询；已保留的路线仍可在小银查看") from None
        if (tuple(context.get(k) for k in ["task_id", "query_id", "route_id"]) != key
                or not isinstance(context.get("android_task_id"), int) or context["android_task_id"] <= 0
                or not re.fullmatch(r"com\.autonavi\.minimap/[A-Za-z0-9_.$/]+", context.get("activity", ""))):
            raise WorkflowError("高德页面身份与当前路线不一致，请重新查询")
        return context

    def show_details(self, result, checkpoint):
        return self._detail_operation(result, checkpoint)

    def start_navigation(self, result, checkpoint, before_tap):
        if result.get("classification") != "COMPLETE" or not result.get("route_verified"):
            raise WorkflowError("只有本次已核对的完整公交地铁路线可以启动导航")
        proof = self._detail_operation(result, checkpoint, before_tap=before_tap)
        return {**proof, **{field: result[field] for field in ("task_id", "query_id", "route_id")}}

    def _detail_operation(self, result, checkpoint, *, before_tap=None):
        transport = getattr(self.base, "transport", None)
        previous = getattr(transport, "prefer_transit_bridge", False) if transport else False
        if transport:
            transport.prefer_transit_bridge = True
        try:
            return self._show_details(result, checkpoint, before_tap=before_tap)
        finally:
            if transport:
                transport.prefer_transit_bridge = previous

    def _show_details(self, result, checkpoint, *, before_tap=None):
        # The operation has its own bounded lifetime and evidence directory; a query
        # may have completed minutes ago or in a previous backend process.
        fields = ("started", "actions", "query_id", "route_id", "folder", "observations")
        previous = {field: getattr(self, field, None) for field in fields}
        key = self._context_key(result)
        self.started = time.monotonic(); self.actions = 0
        self.query_id, self.route_id = key[1], key[2]
        operation = "navigation-" if before_tap is not None else "show-"
        self.folder = self._context_path(key).parent / (operation + uuid.uuid4().hex)
        self.folder.mkdir(parents=True, exist_ok=True); self.observations = []
        try:
            return self._verify_shown_details(result, checkpoint, before_tap=before_tap)
        finally:
            for field, value in previous.items():
                if value is None and hasattr(self, field): delattr(self, field)
                else: setattr(self, field, value)

    def _verify_shown_details(self, result, checkpoint, *, before_tap=None):
        self._check(checkpoint)
        context = self._detail_context(result)
        snapshot = self.base.shell("dumpsys", "activity", "activities")
        self._check(checkpoint)
        if not self._map_task_still_exists(snapshot, context):
            raise WorkflowError("记录的高德任务已结束或被替换，请重新查询")
        self.base.shell("am", "task", "focus", str(context["android_task_id"]))
        self._check(checkpoint)
        identity = (context["android_task_id"], context["activity"])
        for attempt in range(3):
            snapshot = self.base.shell("dumpsys", "activity", "activities")
            self._check(checkpoint)
            if self._resumed_map_identity(snapshot) == identity: break
            if attempt < 2: time.sleep(.2)
        else:
            raise WorkflowError("高德未恢复到记录的路线任务，请重新查询")
        epoch = self.base.context_epoch
        rides = [s for s in result.get("segments", []) if s.get("mode") in {"bus", "subway"}]
        partial = result.get("classification") == "PARTIAL" and not result.get("route_verified")
        if (not rides or (not partial and any(not s.get(field) for s in rides for field in ["line", "board", "direction", "alight"]))
                or partial and any(not s.get("line") or not (s.get("board") or s.get("alight")) for s in rides)):
            raise WorkflowError("本次乘车段事实不足，不能核对高德详情")
        trusted_strip = self._saved_detail_strip(result)
        expected_strip = context.get("detail_strip_fingerprint") or (trusted_strip or {}).get("fingerprint")
        if partial and (not expected_strip or any(not (result.get(role) or {}).get("name") for role in ["origin", "destination"])):
            raise WorkflowError("部分路线缺少可信方案或真实起终点身份，请重新查询")
        normalize = lambda value: re.sub(r"\s+", "", clean(value))

        def observe():
            self._check(checkpoint)
            snapshot = self.base.shell("dumpsys", "activity", "activities")
            self._check(checkpoint)
            if self.base.context_epoch != epoch or self._resumed_map_identity(snapshot) != identity:
                raise ScreenContextChanged("查看期间高德页面身份已变化，已停止旧目标操作")
            shot = self._observe(checkpoint, "route_detail")
            if self.base.context_epoch != epoch or shot["epoch"] != epoch:
                raise ScreenContextChanged("暂停恢复后需重新核对本次高德详情")
            nodes, viewport = self._visible_detail_nodes(shot["xml"])
            if shot["evidence"]["page_kind"] != "route_detail":
                raise WorkflowError("当前仍是列表或过渡页，不能作为本次详情")
            strip = self._detail_strip(nodes, viewport, result)
            if expected_strip and (strip is None or strip["fingerprint"] != expected_strip):
                raise WorkflowError("高德当前选中方案与本次保存详情不一致")
            body = [n for n in nodes if not strip or bounds(n)[1] >= strip["bottom"] - 2]
            body_text = normalize(screen_text(body))
            return shot, body, body_text

        def matches(body, text):
            matched = {index for index, ride in enumerate(rides)
                       if all(normalize(ride[field]) in text for field in ["line", "board", "direction", "alight"] if ride.get(field))}
            def endpoint(role, fallback):
                place = result.get(role) or {}
                value = place.get("name") or fallback
                if role == "origin" and place.get("source") == "current_location" and result.get("route_verified"):
                    return any(label(node) == "我的位置" for node in body)
                return any(place_key(label(node)) == place_key(value) for node in body)
            return matched, endpoint("origin", rides[0].get("board")), endpoint("destination", rides[-1].get("alight"))

        def finish(shot):
            if before_tap is None:
                return None

            def observe_same_route():
                current, body, text = observe()
                if not expected_strip:
                    seen, start_seen, end_seen = matches(body, text)
                    if len(seen) != len(rides) or not start_seen or not end_seen:
                        raise WorkflowError("当前详情已无法完整核对本次路线")
                return current

            return self._start_shown_navigation(shot, observe_same_route, identity, rides, before_tap, checkpoint)

        shot, body, text = observe()
        seen, start_seen, end_seen = matches(body, text)
        if len(seen) == len(rides) and start_seen and end_seen:
            return finish(shot)
        if not expected_strip:
            raise WorkflowError("本次长路线的可信方案身份不足，请重新查询；小银仍保留原路线")
        if not context.get("detail_strip_fingerprint") and trusted_strip:
            context.update(detail_strip_fingerprint=trusted_strip["fingerprint"], detail_strip_text=trusted_strip["text"])
            self._context_path(self._context_key(result)).write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        # Seek the top, then inspect ordered overlapping visible frames. Never
        # accept off-screen nodes or use the final riding segment as a shortcut.
        for _ in range(self.scroll_limit):
            previous_text = text
            self._scroll_detail(shot, checkpoint, reverse=True)
            shot, body, text = observe()
            if text == previous_text: break
        seen = set(); start_seen = end_seen = False
        for index in range(self.scroll_limit + 1):
            current, start, end = matches(body, text)
            seen.update(current); start_seen |= start; end_seen |= end
            if len(seen) == len(rides) and start_seen and end_seen:
                return finish(shot)
            if index == self.scroll_limit: break
            previous_text = text
            self._scroll_detail(shot, checkpoint)
            shot, body, text = observe()
            if text == previous_text: break
        raise WorkflowError("已受限核对多屏详情，仍未读全本次起终点及各乘车段，请重新查询")


    @staticmethod
    def _visible_navigation_nodes(shot):
        all_nodes = list(ET.fromstring(shot["xml"]).iter("node"))
        parents = {child: parent for parent in all_nodes for child in parent}

        def shown(node):
            while node is not None:
                if node.get("visible-to-user") == "false" or node.get("enabled") == "false":
                    return False
                node = parents.get(node)
            return True

        visible = [node for node in all_nodes if bounds(node) and shown(node)]
        if any(node.get("package") != PACKAGE or re.search(r"Dialog|PopupWindow", node.get("class", ""))
               for node in visible):
            raise WorkflowError("高德页面可能有其他窗口遮挡，请核对手机界面")
        return visible

    @classmethod
    def _navigation_control(cls, shot, *, active):
        if shot["evidence"]["page_kind"] != "route_detail":
            return None
        visible = cls._visible_navigation_nodes(shot)
        markers = [node for node in visible if label(node) == "公交详情页面"]
        if len(markers) != 1:
            return None
        left, top, right, bottom = bounds(markers[0])
        owner_label = "结束导航" if active else "开始导航，开启下车提醒"
        child_label = "结束导航" if active else "开始导航"
        candidates = []
        for node in visible:
            name = label(node) if active else clean(node.get("content-desc"))
            if (node.get("class") != "android.view.ViewGroup" or node.get("clickable") != "true"
                    or name != owner_label):
                continue
            x1, y1, x2, y2 = bounds(node)
            if not (left <= x1 < x2 <= right and top + (bottom - top) * .75 <= y1 < y2 <= bottom):
                continue
            if any(child is not node and child in visible and clean(child.get("text")) == child_label
                   and x1 <= bounds(child)[0] < bounds(child)[2] <= x2
                   and y1 <= bounds(child)[1] < bounds(child)[3] <= y2
                   for child in node.iter("node")):
                candidates.append(node)
        if len(candidates) > 1:
            raise WorkflowError("高德导航按钮无法唯一核对")
        return candidates[0] if candidates else None

    def _start_shown_navigation(self, shot, observe_same_route, identity, rides, before_tap, checkpoint):
        if self._navigation_control(shot, active=True) is not None:
            raise WorkflowError("高德当前已显示进行中的导航，请查看手机界面")
        target = self._navigation_control(shot, active=False)
        for _ in range(self.scroll_limit):
            if target is not None:
                break
            self._scroll_detail(shot, checkpoint)
            previous_text = shot["evidence"]["text"]
            shot = observe_same_route()
            target = self._navigation_control(shot, active=False)
            if shot["evidence"]["text"] == previous_text:
                break
        if target is None:
            raise WorkflowError("本次高德详情未找到开始导航按钮")

        fresh = observe_same_route()
        current = self._navigation_control(fresh, active=False)
        if (current is None or bounds(current) != bounds(target)
                or current.get("resource-id") != target.get("resource-id")
                or self._navigation_control(fresh, active=True) is not None):
            raise WorkflowError("高德开始导航按钮已经变化")
        self._action(checkpoint)
        epoch = fresh["epoch"]

        def check_epoch():
            self._check(checkpoint)
            if self.base.context_epoch != epoch:
                raise ScreenContextChanged("已废弃暂停之前的导航按钮")

        current_identity = self._resumed_map_identity(self.base.shell("dumpsys", "activity", "activities"))
        check_epoch()
        if current_identity != identity:
            raise ScreenContextChanged("高德已离开本次路线页面")
        before_tap()
        self.base.tap(current, check_epoch)
        self._audit_action({"role": "start_navigation", "bounds": bounds(current),
                            "resource_id": current.get("resource-id"),
                            "observation_id": fresh["evidence"]["observation_id"]}, "start_navigation")
        return self._verify_native_navigation(identity, bounds(current), rides, check_epoch)

    def _verify_native_navigation(self, identity, submitted_bounds, rides, checkpoint):
        for attempt in range(4):
            checkpoint()
            current_identity = self._resumed_map_identity(self.base.shell("dumpsys", "activity", "activities"))
            checkpoint()
            if current_identity == identity:
                try:
                    shot = self._observe(checkpoint, "route_detail")
                    end = self._navigation_control(shot, active=True)
                    start = self._navigation_control(shot, active=False)
                except (ScreenContextChanged, Cancelled):
                    raise
                except WorkflowError:
                    end = start = None
                # Amap keeps the same detail page and replaces this route's
                # start control with the live end control in the same footer.
                if end is not None and start is None and bounds(end) == submitted_bounds:
                    visible, _ = self._visible_detail_nodes(shot["xml"])
                    names = {place_key(label(node)) for node in visible}
                    body = re.sub(r"\s+", "", screen_text(visible))
                    same_ride = any(re.sub(r"\s+", "", ride["line"]) in body
                                    and re.sub(r"\s+", "", ride["direction"]) in body
                                    and any(place_key(ride[stop]) in names for stop in ("board", "alight"))
                                    for ride in rides)
                    final_identity = self._resumed_map_identity(self.base.shell("dumpsys", "activity", "activities"))
                    checkpoint()
                    if same_ride and final_identity == identity:
                        return {"observation_id": shot["evidence"]["observation_id"],
                                "observed_at": now(), "android_task_id": identity[0], "activity": identity[1]}
            if attempt < 3:
                time.sleep(.5)
        raise WorkflowError("高德尚未显示可核对的实时导航界面")
