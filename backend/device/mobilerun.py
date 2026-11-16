"""Local-only Mobilerun SDK transport, with the existing ADB adapter as fallback."""
import os
import xml.etree.ElementTree as ET


TRANSIT_BRIDGE_REASONS = frozenset({
    "ok", "service_unavailable", "bridge_unavailable", "bridge_busy", "bridge_timeout",
    "bridge_interrupted", "bridge_error", "bridge_no_response", "content_bridge_required",
    "root_unavailable", "root_package", "foreign_node", "tree_changed", "tree_error", "tree_limit",
    "nonce_invalid", "input_invalid", "input_error", "field_match_0", "field_match_multiple", "sensitive_field",
    "write_false", "unsupported_method", "not_requested",
})


def call_transit_bridge(operation, extras=None):
    """Call the DUMP-protected provider, never an ordered broadcast.

    The provider's Binder call is bounded at 6.5 seconds. The local process gets
    9 seconds. Only the two map methods and their current-field string payloads
    are allowed; a returned Bundle must echo this call's fresh nonce.
    """
    import re
    import subprocess
    import uuid
    import gmail_demo
    if operation not in {"dump_transit_ui", "set_transit_field"}:
        raise ValueError("不允许的高德受保护操作")
    extras = dict(extras or {})
    allowed = {"field", "text", "resource_id", "bounds", "expected_text"}
    if operation == "dump_transit_ui" and extras or set(extras) - allowed:
        raise ValueError("高德受保护接口参数超出允许范围")
    if operation == "set_transit_field":
        if set(extras) != allowed or any(not isinstance(v, str) for v in extras.values()):
            raise ValueError("高德输入必须绑定当前字段的完整字符串参数")
        if extras["field"] not in {"origin", "destination", "query"}:
            raise ValueError("不允许的高德输入字段")
        if not re.fullmatch(r"[A-Za-z0-9_:.\/-]{0,240}", extras["resource_id"]):
            raise ValueError("高德资源标识无效")
        if not re.fullmatch(r"\[\d+,\d+\]\[\d+,\d+\]", extras["bounds"]):
            raise ValueError("高德控件边界无效")
        for key in ["text", "expected_text"]:
            if len(extras[key]) > 4096 or not re.fullmatch(r"[A-Za-z0-9_=-]*", extras[key]):
                raise ValueError("高德输入编码无效")
    nonce = uuid.uuid4().hex
    command = [gmail_demo._adb()]
    if os.environ.get("ANDROID_SERIAL"):
        command += ["-s", os.environ["ANDROID_SERIAL"]]
    command += ["shell", "content", "call", "--uri", "content://com.example.demo.transit",
                "--method", operation, "--arg", nonce]
    for key, value in extras.items():
        command += ["--extra", f"{key}:s:{value}"]
    try:
        response = subprocess.run(command, capture_output=True, text=True, timeout=9)
    except subprocess.TimeoutExpired:
        return {"result": 0, "data": None, "reason": "bridge_timeout", "nonce": None}
    except OSError:
        return {"result": 0, "data": None, "reason": "bridge_unavailable", "nonce": None}
    output = response.stdout.decode("utf-8", errors="replace") if isinstance(response.stdout, bytes) else response.stdout or ""
    if response.returncode:
        return {"result": 0, "data": None, "reason": "bridge_unavailable", "nonce": None}
    bundles = re.findall(r"Bundle\[\{([^\r\n]*)\}\]", output)
    if not bundles:
        return {"result": 0, "data": None, "reason": "bridge_unavailable", "nonce": None}
    if len(bundles) != 1:
        raise RuntimeError("高德受保护响应不唯一")
    fields = {}
    for entry in bundles[0].split(", "):
        key, separator, value = entry.partition("=")
        if not separator or key in fields or key not in {"code", "result", "data", "reason", "nonce"}:
            raise RuntimeError("高德受保护响应格式无效")
        fields[key] = None if value == "null" else value
    if fields.get("nonce") != nonce:
        raise RuntimeError("高德受保护响应已过期")
    if fields.get("result") not in {"0", "1"} or fields.get("code", fields["result"]) != fields["result"]:
        raise RuntimeError("高德受保护响应状态无效")
    reason = fields.get("reason")
    if reason not in TRANSIT_BRIDGE_REASONS:
        raise RuntimeError("高德受保护响应原因无效")
    success = fields["result"] == "1"
    data = fields.get("data") if success and operation == "dump_transit_ui" else None
    if success and reason != "ok":
        raise RuntimeError("高德受保护响应状态与原因不一致")
    return {"result": int(success), "data": data, "reason": reason, "nonce": nonce}


class MobilerunTransport:
    def __init__(self):
        self._device = None
        self.last_ui_source = "mobilerun"
        self.file_xml_mode = False
        self.prefer_transit_bridge = False
        self.last_bridge_reason = None
        self.protocol_seen = False

    @property
    def device(self):
        if self._device is None:
            import subprocess
            import gmail_demo
            from mobilerun_core import Mobilerun
            serial = os.environ.get("ANDROID_SERIAL")
            if not serial:
                result = subprocess.run([gmail_demo._adb(), "get-serialno"], capture_output=True, text=True, timeout=10, check=True)
                serial = result.stdout.strip()
            self._device = Mobilerun().connect(serial, backend="local-android-adb")
        return self._device

    def xml(self, checkpoint=lambda: None):
        checkpoint()
        if self.prefer_transit_bridge:
            import time
            # A changing tree is common during a real Amap page transition.
            # Re-read it a few times, with fresh bridge nonces, but never use
            # the unstable tree as evidence. Other failures keep the smaller
            # two-call budget; callers must not add a second retry loop.
            for attempt in range(4):
                checkpoint()
                # An exception before the bridge returns a new reason must not
                # inherit a tree_changed (or other) reason from an earlier read.
                self.last_bridge_reason = None
                try:
                    protected = self._protected_transit_xml()
                except Exception:
                    self.last_bridge_reason = None
                    raise
                checkpoint()
                if protected is not None:
                    return protected
                if attempt >= 1 and self.last_bridge_reason != "tree_changed":
                    break
                if attempt < 3:
                    checkpoint()
                    time.sleep(.5 if self.last_bridge_reason == "tree_changed" else .25)
                    checkpoint()
            # Starting UiAutomation now could unbind the very service which
            # must perform the next Unicode input. Never fall back to it here.
            raise RuntimeError("高德无障碍窗口暂不可用，请检查页面状态后重试")
        if self.file_xml_mode:
            return self._compatible_xml()
        try:
            state = self.device.ui(filter=False)
        except ValueError as error:
            if "uiautomator output did not contain" not in str(error):
                raise
            self.file_xml_mode = True
            # Some Huawei builds do not return XML through /dev/tty, which the
            # SDK's local ADB reader uses. Keep SDK gestures/screenshots, but
            # read a newly-created file, never a possibly stale window.xml.
            return self._compatible_xml()
        self.last_ui_source = "mobilerun"
        if state.dead_tree:
            raise RuntimeError("Mobilerun 未取得可用界面")
        root = ET.Element("hierarchy")
        def add(value, parent):
            # SDK snapshots can contain several root windows, for example
            # Amap plus the input method. Keep all window roots in order.
            if isinstance(value, (list, tuple)):
                for entry in value:
                    add(entry, parent)
                return
            if not isinstance(value, dict):
                return
            attrs = {}
            names = {"resourceId": "resource-id", "className": "class", "packageName": "package", "contentDescription": "content-desc"}
            for key in ["text", "resourceId", "className", "packageName", "contentDescription", "clickable", "enabled", "focused", "scrollable", "checked", "checkable"]:
                v = value.get(key, "")
                attrs[names.get(key, key)] = str(v).lower() if isinstance(v, bool) else str(v or "")
            bounds = value.get("boundsInScreen", value.get("bounds", {}))
            if isinstance(bounds, dict):
                bounds = [bounds.get(k, 0) for k in ("left", "top", "right", "bottom")]
            if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                attrs["bounds"] = f"[{bounds[0]},{bounds[1]}][{bounds[2]},{bounds[3]}]"
            else:
                attrs["bounds"] = str(bounds)
            child = ET.SubElement(parent, "node", attrs)
            for entry in value.get("children", []):
                add(entry, child)
        add(state.get("a11y_tree", {}), root)
        return ET.tostring(root, encoding="unicode")

    def _compatible_xml(self):
        # The email/browser compatibility path remains a fresh native dump.
        # Map queries never reach this path, regardless of provider failures.
        return self._fresh_file_xml()

    def _protected_transit_xml(self):
        """Read only the current Amap window through the DUMP-protected bridge.

        Each response is bound to this fresh nonce. Old/malformed successful
        responses fail closed instead of becoming route evidence.
        """
        import base64
        response = call_transit_bridge("dump_transit_ui")
        self.last_bridge_reason = ("bridge_no_response" if response["reason"] == "bridge_unavailable" and self.protocol_seen
                                   else response["reason"])
        if response["nonce"] is not None:
            self.protocol_seen = True
        if response["result"] != 1:
            return None
        if not response["data"]:
            raise RuntimeError("高德无障碍读取缺少当前响应")
        try:
            xml = base64.urlsafe_b64decode(response["data"]).decode("utf-8")
            root = ET.fromstring(xml)
        except (ValueError, UnicodeError, ET.ParseError):
            raise RuntimeError("高德无障碍页面响应无效") from None
        ns=list(root.iter("node"))
        packages={n.get("package") for n in ns if n.get("package")}
        if root.get("nonce") != response["nonce"] or packages != {"com.autonavi.minimap"}:
            raise RuntimeError("高德无障碍页面响应过期或包名不符")
        # Virtual children may omit their inherited package. The service has
        # already restricted every nonempty package and the entire window.
        for n in ns:
            if not n.get("package"): n.set("package","com.autonavi.minimap")
        self.last_ui_source = "protected-a11y-fallback"
        self.last_bridge_reason = None
        return ET.tostring(root,encoding="unicode")

    def _fresh_file_xml(self):
        import subprocess
        import uuid
        import gmail_demo
        path = "/sdcard/silverguide-" + uuid.uuid4().hex + ".xml"
        command = [gmail_demo._adb()]
        if os.environ.get("ANDROID_SERIAL"):
            command += ["-s", os.environ["ANDROID_SERIAL"]]
        try:
            subprocess.run(command + ["shell", "uiautomator", "dump", path],
                           check=True, capture_output=True, timeout=20)
            xml = subprocess.run(command + ["exec-out", "cat", path], check=True,
                                 capture_output=True, timeout=10).stdout.decode("utf-8")
            ET.fromstring(xml)
            self.last_ui_source = "adb-file-fallback"
            return xml
        finally:
            subprocess.run(command + ["shell", "rm", "-f", path], capture_output=True, timeout=5)

    def tap(self, x, y):
        self.device.tap(x, y)

    def screenshot(self):
        return self.device.screenshot()
