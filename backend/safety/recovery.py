"""Conservative dismissal of known native hints, never of approval dialogs.

The result is a proposal, not a device command. The caller must obtain a fresh
screen and classify it again immediately before resolving the returned ID.
"""

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class RecoveryAction:
    kind: str
    target_id: str | None = None
    message: str = ""


_PACKAGES = {"com.huawei.browser", "com.android.chrome", "com.huawei.email"}
_TITLE = "android:id/alertTitle"
_MESSAGE = "android:id/message"
_BUTTONS = {"android:id/button1", "android:id/button2", "android:id/button3"}
_MARKERS = _BUTTONS | {_TITLE, _MESSAGE}
_TITLES = {
    "使用提示", "功能提示", "邮件提示", "邮件填写提示", "浏览器提示", "温馨提示",
    "新功能介绍", "功能介绍", "更新提示", "更新提醒", "版本更新", "发现新版本",
    "tips", "tip", "usage tips", "update", "update available", "app update",
    "software update", "new version available",
}
_GENERIC_TITLES = {"提示", "帮助", "notice", "help"}
_INFORMATION = re.compile(r"填写|输入框|功能|介绍|帮助|版本|更新|usage|feature|update|version", re.I)
_DISMISS = {"取消", "稍后", "暂不", "以后再说", "稍后再说", "稍后提醒", "关闭",
            "cancel", "later", "not now", "maybe later", "remind me later", "close"}
_CLOSE = {"关闭", "close"}
_SENSITIVE_ZH = (
    "发送", "发出", "删除", "丢弃", "舍弃", "草稿", "清空", "注销", "卸载",
    "恢复出厂", "重置", "账号", "帐户", "账户", "登录", "登陆", "授权", "权限",
    "允许", "密码", "验证码", "证书", "安全警告", "隐私", "同意", "条款",
    "支付", "付款", "转账", "购买", "订阅",
)
_SENSITIVE_EN = re.compile(
    r"\b(send|sent|delete|discard|draft|reset|uninstall|account|login|authorization|"
    r"permission|allow|password|certificate|privacy|consent|payment|pay|purchase|"
    r"subscribe|subscription)\b|sign[\s-]*in|security\s+warning|verification\s+code", re.I)


def _blocked(message):
    return RecoveryAction("blocked", message=message)


def _native_nodes(root):
    """Exclude web content even if it imitates native labels and resource IDs."""
    result = []

    def visit(node, in_web=False):
        in_web = in_web or "WebView" in node.get("class", "")
        if not in_web:
            result.append(node)
        for child in node:
            visit(child, in_web)

    visit(root)
    return result


def _scope(root, native):
    dialogs = [n for n in native if n.get("class", "").split(".")[-1].endswith("Dialog")]
    if len(dialogs) == 1:
        return dialogs[0]
    if len(dialogs) > 1:
        return None
    markers = [n for n in native if n.get("resource-id") in _MARKERS]
    parents = {child: parent for parent in root.iter() for child in parent}
    # A parentPanel is Android's native dialog container. Prefer its boundary to
    # prevent the background composer's Send button or text from influencing us.
    panels = [n for n in native if n.get("resource-id") == "android:id/parentPanel"]
    if len(panels) == 1 and all(n in set(panels[0].iter()) for n in markers):
        return panels[0]
    if not markers:
        return None
    # Some OEM dumps omit the Dialog class and parentPanel. Use the smallest
    # shared native container, but never combine independent top-level windows.
    candidate = markers[0]
    while candidate is not None:
        descendants = set(candidate.iter())
        if all(n in descendants for n in markers):
            return candidate if candidate is not root else None
        candidate = parents.get(candidate)
    return None


def _usable_button(node):
    if node.get("enabled") == "false" or node.get("clickable") == "false":
        return False
    if any(node.get(key) == "false" for key in ("visible", "visible-to-user", "displayed")):
        return False
    cls = node.get("class", "")
    if cls and not cls.endswith("Button"):
        return False
    match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", node.get("bounds", ""))
    if match is None:
        return False
    left, top, right, bottom = map(int, match.groups())
    return left >= 0 and top >= 0 and right > left and bottom > top


def classify_dialog(xml: str, expected_package: str) -> RecoveryAction | None:
    """Return a known dismiss action, a safe stop, or None for a normal page.

    Only observed native button IDs can be returned. Unknown or sensitive dialogs
    remain for the user; there is no generic OK, Back, coordinate or consent tool.
    """
    if expected_package not in _PACKAGES:
        return _blocked("当前应用不在弹窗恢复适配范围，请手动处理后继续。")
    try:
        root = ET.fromstring(xml[xml.find("<"):])
    except (ET.ParseError, ValueError):
        return _blocked("无法可靠读取当前页面，已停止弹窗操作，请重新核对手机界面。")
    native = _native_nodes(root)
    has_dialog = any(n.get("resource-id") in _MARKERS or
                     n.get("class", "").split(".")[-1].endswith("Dialog") for n in native)
    if not has_dialog:
        return None
    scope = _scope(root, native)
    if scope is None:
        return _blocked("无法唯一确定弹窗范围，请手动处理后继续。")
    # A WebView anywhere inside an inferred scope means that boundary could
    # include the underlying page, so it cannot authorize an automatic click.
    members = list(scope.iter())
    if any("WebView" in n.get("class", "") for n in members):
        return _blocked("弹窗范围与网页内容重叠，请手动处理后继续。")
    owners = {n.get("package") for n in members if n.get("package")}
    if owners != {expected_package}:
        return _blocked("弹窗不属于当前任务应用，请手动核对，助手不会代为确认。")
    text = " ".join(n.get("text", "") + " " + n.get("content-desc", "") for n in members)
    if any(word in text for word in _SENSITIVE_ZH) or _SENSITIVE_EN.search(text):
        return _blocked("弹窗涉及发送、删除、账号、授权或其他重要操作，请您手动决定。")
    titles = [n.get("text", "").strip().casefold() for n in members if n.get("resource-id") == _TITLE]
    if len(titles) != 1:
        return _blocked("弹窗缺少唯一可核对的标题，请手动处理后继续。")
    title = titles[0]
    messages = " ".join(n.get("text", "") for n in members if n.get("resource-id") == _MESSAGE)
    if title not in _TITLES and not (title in _GENERIC_TITLES and _INFORMATION.search(messages)):
        return _blocked("遇到尚未适配的弹窗，请手动处理后继续。")
    buttons = [n for n in members if n.get("resource-id") in _BUTTONS]
    safe = [n for n in buttons if n.get("resource-id") == "android:id/button2"
            and n.get("text", "").strip().casefold() in _DISMISS]
    if not safe and len(buttons) == 1:
        safe = [n for n in buttons if n.get("resource-id") == "android:id/button1"
                and n.get("text", "").strip().casefold() in _CLOSE]
    if len(safe) != 1 or not _usable_button(safe[0]):
        return _blocked("没有找到唯一且可用的安全关闭按钮，请手动处理后继续。")
    parents = {child: parent for parent in root.iter() for child in parent}
    ancestor = parents.get(safe[0])
    while ancestor is not None:
        if ancestor.get("enabled") == "false" or any(
                ancestor.get(key) == "false" for key in ("visible", "visible-to-user", "displayed")):
            return _blocked("弹窗当前不可见或不可操作，请手动核对后继续。")
        if ancestor is scope:
            break
        ancestor = parents.get(ancestor)
    target_id = safe[0].get("resource-id")
    if sum(n.get("resource-id") == target_id for n in root.iter()) != 1:
        return _blocked("弹窗关闭控件不唯一，请手动处理后继续。")
    return RecoveryAction("tap", target_id, "发现已适配的提示，先关闭提示，再重新核对当前页面。")
