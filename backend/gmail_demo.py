"""硬编码的 Gmail 演示。不调用模型。"""

import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent / "gmail_demo_log.txt"
RECEIVER = "com.example.demo/.A11yCommandReceiver"
ACTION = "com.example.demo.A11Y_COMMAND"
SCAN_INTERVAL_SECONDS = 3
MAX_SCANS = 24
RECIPIENT = "your_send_email@example.com"

_lock = threading.Lock()
_running = False


def running() -> bool:
    return _running


def start(dump_screen, adb: str) -> dict:
    global _running
    with _lock:
        if _running:
            return _message(False, "Gmail 演示还在进行")
        if not _accessibility_enabled(adb):
            return _message(False, "请先在系统设置里打开 Demo 的无障碍服务")
        _running = True
    LOG_FILE.write_text("", encoding="utf-8")
    threading.Thread(target=_run, args=(dump_screen, adb), daemon=True).start()
    return _message(True, "已回到桌面。之后每 3 秒看一次界面，并按固定步骤打开 Gmail")


def _run(dump_screen, adb: str) -> None:
    global _running
    step = "open_gmail"
    try:
        _send(adb, ["--es", "op", "home"])
        _log("已发送回到桌面")
        for scan in range(1, MAX_SCANS + 1):
            time.sleep(SCAN_INTERVAL_SECONDS)
            try:
                xml = dump_screen()
            except Exception as error:
                _log(f"第 {scan} 次扫描失败：{error}")
                continue
            nodes = _nodes(xml)
            step = _act(adb, step, nodes, scan)
            if step == "done":
                _log("演示结束：已经点击发送")
                return
        _log(f"演示停止：超过 {MAX_SCANS} 次扫描，停在 {step}")
    finally:
        with _lock:
            _running = False


def _act(adb: str, step: str, nodes: list[ET.Element], scan: int) -> str:
    if step == "open_gmail":
        point = _find_text(nodes, {"gmail"})
        if point is None:
            _log(f"第 {scan} 次：桌面上还没看到 Gmail")
            return step
        _tap(adb, point)
        _log(f"第 {scan} 次：点击 Gmail {point}")
        return "wait_inbox"

    if step == "wait_inbox":
        if not _has_search_in(nodes):
            _log(f"第 {scan} 次：还没进入 Gmail")
            return step
        _send(adb, ["--es", "op", "swipe_down"])
        _log(f"第 {scan} 次：看到 Search in，向下拉")
        return "open_menu"

    if step == "open_menu":
        point = _find_menu(nodes)
        if point is None:
            _log(f"第 {scan} 次：还没看到左上角三条横线")
            return step
        _tap(adb, point)
        _log(f"第 {scan} 次：点击三条横线 {point}")
        return "open_sent"

    if step == "open_sent":
        point = _find_text(nodes, {"sent", "send"})
        if point is None:
            _log(f"第 {scan} 次：菜单里还没看到 Sent")
            return step
        _tap(adb, point)
        _log(f"第 {scan} 次：点击 Sent {point}")
        return "open_compose"

    if step == "open_compose":
        point = _find_compose(nodes)
        if point is None:
            _log(f"第 {scan} 次：还没看到 Compose")
            return step
        _tap(adb, point)
        _log(f"第 {scan} 次：点击 Compose {point}")
        return "fill_to"

    if step == "fill_to":
        point = _find_field_point(nodes, "to")
        if point is None:
            _log(f"第 {scan} 次：没找到 To 输入框")
            return step
        _type_text(adb, "to", RECIPIENT, point)
        _log(f"第 {scan} 次：先写入邮箱，下一轮再确认是否写完")
        return "confirm_to"

    if step == "confirm_to":
        if not _contains(nodes, RECIPIENT):
            _log(f"第 {scan} 次：邮箱还没写完")
            return "fill_to"
        _press_enter(adb)
        _log(f"第 {scan} 次：邮箱已写完，按回车")
        return "fill_demo"

    if step == "fill_demo":
        if _has_suggestion(nodes):
            _press_enter(adb)
            _log(f"第 {scan} 次：收件人还没变成标签，再按回车")
            return step
        if _has_demo(nodes):
            point = _find_send(nodes)
            if point is None:
                _log(f"第 {scan} 次：已经是标签，还没看到右上角发送")
                return "tap_send"
            _tap(adb, point)
            _log(f"第 {scan} 次：已经是标签，点击发送 {point}")
            return "done"
        _type_text(adb, "subject", "demo", _find_field_point(nodes, "subject"))
        time.sleep(0.4)
        _type_text(adb, "body", "demo", _find_field_point(nodes, "compose email"))
        _log(f"第 {scan} 次：已向主题和正文注入 demo，下一轮再确认")
        return step

    if step == "tap_send":
        point = _find_send(nodes)
        if point is None:
            _log(f"第 {scan} 次：还没看到右上角发送")
            return step
        _tap(adb, point)
        _log(f"第 {scan} 次：点击发送 {point}")
        return "done"

    return step


def _nodes(xml: str) -> list[ET.Element]:
    start = xml.find("<")
    if start < 0:
        return []
    return list(ET.fromstring(xml[start:]).iter("node"))


def _find_text(nodes: list[ET.Element], names: set[str]) -> tuple[int, int] | None:
    for node in nodes:
        if _text(node).lower() in names:
            return _center(node)
    return None


def _find_compose(nodes: list[ET.Element]) -> tuple[int, int] | None:
    for node in nodes:
        blob = f"{_text(node)} {_desc(node)}".lower()
        if "compose" in blob:
            return _center(node)
    return None


def _find_menu(nodes: list[ET.Element]) -> tuple[int, int] | None:
    for node in nodes:
        desc = _desc(node).lower()
        if "navigation drawer" in desc or desc in {"open menu", "show navigation drawer"}:
            return _center(node)
    for node in nodes:
        if node.attrib.get("clickable") != "true" or _text(node):
            continue
        center = _center(node)
        class_name = node.attrib.get("class", "")
        if center and center[0] < 220 and center[1] < 500 and ("Image" in class_name or "Button" in class_name):
            return center
    return None


def _has_search_in(nodes: list[ET.Element]) -> bool:
    for node in nodes:
        blob = f"{_text(node)} {_desc(node)}".lower()
        marker = "search in"
        if marker in blob and blob.split(marker, 1)[1].strip():
            return True
    return False


def _text(node: ET.Element) -> str:
    return node.attrib.get("text", "").strip()


def _desc(node: ET.Element) -> str:
    return node.attrib.get("content-desc", "").strip()


def _center(node: ET.Element) -> tuple[int, int] | None:
    numbers = [int(value) for value in re.findall(r"-?\d+", node.attrib.get("bounds", ""))]
    if len(numbers) != 4:
        return None
    return (numbers[0] + numbers[2]) // 2, (numbers[1] + numbers[3]) // 2


def _has_suggestion(nodes: list[ET.Element]) -> bool:
    for node in nodes:
        blob = f"{_text(node)} {_desc(node)}".lower()
        if "suggestion" in blob:
            return True
    return False


def _has_demo(nodes: list[ET.Element]) -> bool:
    return any(
        _text(node).lower() == "demo" or _desc(node).lower() == "demo"
        for node in nodes
    )


def _press_enter(adb: str) -> None:
    subprocess.run(
        [adb, "shell", "input", "keyevent", "66"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _find_send(nodes: list[ET.Element]) -> tuple[int, int] | None:
    for node in nodes:
        desc = _desc(node).lower()
        if desc != "send":
            continue
        center = _center(node)
        if center and center[0] > 700 and center[1] < 400:
            return center
    return None


def _contains(nodes: list[ET.Element], value: str) -> bool:
    needle = value.lower()
    return any(needle in f"{_text(node)} {_desc(node)}".lower() for node in nodes)


def _label(node: ET.Element) -> str:
    return _text(node) or _desc(node) or node.attrib.get("hint", "").strip()


def _is_editor(node: ET.Element) -> bool:
    class_name = node.attrib.get("class", "")
    return any(token in class_name for token in ("EditText", "AutoComplete", "Recipient"))


def _find_field_point(nodes: list[ET.Element], label: str) -> tuple[int, int] | None:
    matched = [node for node in nodes if _label(node).lower() == label]
    for node in matched:
        if _is_editor(node):
            return _center(node)
    if not matched:
        return None
    anchor = _center(matched[0])
    if anchor is None:
        return None
    editor = None
    editor_score = 10**9
    for node in nodes:
        if not _is_editor(node):
            continue
        center = _center(node)
        if center is None or abs(center[1] - anchor[1]) > 90:
            continue
        score = abs(center[1] - anchor[1]) + max(0, anchor[0] - center[0])
        if score < editor_score:
            editor = center
            editor_score = score
    if editor is not None:
        return editor
    return min(anchor[0] + 280, 760), anchor[1]


def _type_text(adb: str, field: str, value: str, point: tuple[int, int] | None) -> None:
    if point is None:
        _log(f"注入 {field} 失败：没有落点")
        return
    tap = subprocess.run(
        [adb, "shell", "input", "tap", str(point[0]), str(point[1])],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    time.sleep(0.5)
    subprocess.run(
        [adb, "shell", "input", "keycombination", "113", "29"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    subprocess.run(
        [adb, "shell", "input", "keyevent", "67"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    parts = value.split("@")
    details: list[str] = []
    for index, part in enumerate(parts):
        if part:
            typed = part.replace("%", "\\%").replace(" ", "%s")
            typed_command = "input text '" + typed.replace("'", "'\\''") + "'"
            result = subprocess.run(
                [adb, "shell", typed_command],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            detail = (result.stdout or result.stderr or "").strip().replace("\n", " ")
            details.append(f"text={result.returncode} {detail}".strip())
        if index < len(parts) - 1:
            at_key = subprocess.run(
                [adb, "shell", "input", "keyevent", "77"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            details.append(f"at={at_key.returncode}")
    _log(f"注入 {field}={value} 点={point} tap={tap.returncode} {' '.join(details)}")


def _tap(adb: str, point: tuple[int, int]) -> None:
    _send(adb, ["--es", "op", "tap", "--ei", "x", str(point[0]), "--ei", "y", str(point[1])])


def _send(adb: str, extras: list[str]) -> None:
    subprocess.run(
        [adb, "shell", "am", "broadcast", "-n", RECEIVER, "-a", ACTION, *extras],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _accessibility_enabled(adb: str) -> bool:
    result = subprocess.run(
        [adb, "shell", "settings", "get", "secure", "enabled_accessibility_services"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return "MailAccessibilityService" in result.stdout


def _log(line: str) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")


def _message(ok: bool, message: str) -> dict:
    return {
        "ok": ok,
        "message": message,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _dump_screen_xml() -> str:
    adb = os.path.join(os.environ["LOCALAPPDATA"], "Android", "Sdk", "platform-tools", "adb.exe")
    dump = subprocess.run(
        [adb, "shell", "uiautomator", "dump", "/sdcard/window.xml"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if dump.returncode != 0:
        detail = (dump.stderr or dump.stdout or "").strip()
        raise RuntimeError(detail or f"uiautomator dump 退出码 {dump.returncode}")
    read_back = subprocess.run(
        [adb, "exec-out", "cat", "/sdcard/window.xml"],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if read_back.returncode != 0 or not read_back.stdout.strip():
        detail = read_back.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "没有读到界面 XML")
    return read_back.stdout.decode("utf-8", errors="replace")


def _adb() -> str:
    return os.path.join(os.environ["LOCALAPPDATA"], "Android", "Sdk", "platform-tools", "adb.exe")


if __name__ == "__main__":
    from fastapi import FastAPI
    import uvicorn

    app = FastAPI()

    @app.post("/gmail/start")
    def start_gmail() -> dict:
        return start(_dump_screen_xml, _adb())

    uvicorn.run(app, host="0.0.0.0", port=8000)
