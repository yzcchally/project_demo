"""Local-only Mobilerun SDK transport, with the existing ADB adapter as fallback."""
import os
import xml.etree.ElementTree as ET


class MobilerunTransport:
    def __init__(self):
        self._device = None

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

    def xml(self):
        state = self.device.ui(filter=False)
        if state.dead_tree:
            raise RuntimeError("Mobilerun 未取得可用界面")
        root = ET.Element("hierarchy")
        def add(value, parent):
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

    def tap(self, x, y):
        self.device.tap(x, y)

    def screenshot(self):
        return self.device.screenshot()
