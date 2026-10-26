"""Huawei Email adapter. UI readback, never fixed screen coordinates or blind resend."""

import base64
import json
import re
import shlex
import subprocess
import time
import uuid
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from domain import WorkflowError, normalize_text, validate_url

from verification.mail import *

class HuaweiDevice:
    def __init__(self, evidence_root=None, transport=None):
        self.compose_activity = None
        self.task_id = "device"
        self.observation = 0
        self.context_url = None
        self.transport = transport
        self.agent_models = None
        self.agent_decision = None
        self.evidence_root = Path(evidence_root) if evidence_root is not None else Path(__file__).resolve().parents[2] / ".run" / "workflows"

    def shell(self, *args, timeout=20):
        import gmail_demo
        r = subprocess.run([gmail_demo._adb(), "shell", shlex.join(args)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        safe_args = list(args)
        for i, value in enumerate(safe_args):
            if value.startswith("mailto:") or i > 0 and safe_args[i - 1] == "text":
                safe_args[i] = "<本任务邮件内容>"
        folder = self.evidence_root / self.task_id
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "actions.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "command": safe_args,
                                  "returncode": r.returncode}, ensure_ascii=False) + "\n")
        if r.returncode:
            raise WorkflowError((r.stderr or r.stdout or "手机命令执行失败").strip()[:250])
        return r.stdout

    def observe(self, checkpoint=lambda: None, evidence=False):
        import gmail_demo
        checkpoint()
        for attempt in range(3):
            try:
                xml = self.transport.xml() if self.transport else gmail_demo._dump_screen_xml()
                ns = nodes(xml)
                if any(n.get("resource-id") == ID + "send_email_btn" for n in ns):
                    activity = self.shell("dumpsys", "activity", "activities")
                    found = re.search(r"mResumedActivity:.*?\s(com\.huawei\.email/[^\s}]+)", activity)
                    if found:
                        self.compose_activity = found.group(1)
                checkpoint()
                if evidence:
                    folder = self.evidence_root / self.task_id
                    folder.mkdir(parents=True, exist_ok=True)
                    self.observation += 1
                    with (folder / f"screen-{time.time_ns()}-{uuid.uuid4().hex}.xml").open("x", encoding="utf-8") as saved:
                        saved.write(xml)
                return xml
            except (OSError, RuntimeError, subprocess.TimeoutExpired, ET.ParseError):
                if attempt == 2:
                    raise WorkflowError("连续三次无法读取手机界面，请检查 USB 连接并解锁手机") from None
                checkpoint()
        raise AssertionError("unreachable")

    def check_ready(self):
        import gmail_demo
        adb = gmail_demo._adb()
        r = subprocess.run([adb, "get-state"], capture_output=True, text=True, timeout=10)
        if r.returncode or r.stdout.strip() != "device":
            raise WorkflowError("请连接并解锁手机，允许 USB 调试")
        if not gmail_demo._accessibility_enabled(adb):
            raise WorkflowError("请先开启 Demo 无障碍服务，再重新准备")
        policy = self.shell("dumpsys", "window", "policy")
        if re.search(r"\bshowing=true\b", policy):
            raise WorkflowError("手机已锁屏，请解锁后重新准备")
        if not self.shell("pm", "path", PACKAGE).strip().startswith("package:"):
            raise WorkflowError("手机未安装华为电子邮件")

    def tap(self, node, checkpoint=lambda: None):
        checkpoint()
        if node.get("enabled") == "false":
            raise WorkflowError("当前按钮不可用，请检查页面内容")
        x, y = point(node)
        if self.transport:
            self.transport.tap(x, y)
        else:
            self.shell("input", "tap", str(x), str(y))

    def open_webpage(self, url: str, checkpoint):
        url = validate_url(url)
        self.context_url = url
        checkpoint()
        out = self.shell("am", "start", "-a", "android.intent.action.VIEW", "-d", url)
        if "Error:" in out:
            raise WorkflowError("没有可打开网址的浏览器")
        for _ in range(5):
            xml = self.observe(checkpoint, evidence=True)
            ns = nodes(xml)
            if any(any(w in n.get("text", "") for w in ("网络未连接", "无法访问此网站", "网页无法打开")) for n in ns):
                raise WorkflowError("浏览器无法联网，请恢复网络后重新准备")
            if self._browser_address(ns) == url:
                return
            self._reveal_address(ns, checkpoint)
            time.sleep(.5)
        raise WorkflowError("网页已尝试打开，但无法从地址栏核对完整网址；请检查浏览器后重试")

    @staticmethod
    def _address_nodes(ns):
        # Only native browser chrome is evidence. A URL in page content is not.
        packages = {"com.huawei.browser", "com.android.chrome"}
        names = {"url_bar", "urlbar", "url_input", "address_bar", "url_text", "location_bar", "tv_search_web_url"}
        return [n for n in ns if n.get("package") in packages
                and n.get("resource-id", "").split(":id/")[-1] in names]

    @classmethod
    def _browser_address(cls, ns):
        for node in cls._address_nodes(ns):
            value = node.get("text", "").strip()
            # Never infer HTTPS or a hidden path from a shortened host label.
            if value.startswith("https://") and "…" not in value:
                try:
                    return validate_url(value)
                except WorkflowError:
                    pass
        return None

    def _reveal_address(self, ns, checkpoint):
        bar = next((n for n in self._address_nodes(ns) if n.get("class", "").endswith("EditText")), None)
        if bar is not None:
            self.tap(bar, checkpoint)
            return
        # Huawei hides the toolbar after scrolling; reveal it within the
        # observed WebView rather than tapping a guessed toolbar position.
        view = next((n for n in ns if n.get("package") in {"com.huawei.browser", "com.android.chrome"}
                     and n.get("class", "").endswith("WebView")), None)
        if view is not None:
            bounds = list(map(int, re.findall(r"-?\d+", view.get("bounds", ""))))
            if len(bounds) == 4 and bounds[2] > bounds[0] and bounds[3] > bounds[1]:
                x = (bounds[0] + bounds[2]) // 2
                height = bounds[3] - bounds[1]
                checkpoint()
                self.shell("input", "swipe", str(x), str(bounds[1] + height // 3),
                           str(x), str(bounds[1] + height * 2 // 3), "250")

    def _set_field(self, field, value, checkpoint):
        encoded = base64.urlsafe_b64encode(value.encode()).decode()
        for clipboard in ("false", "true"):
            checkpoint()
            self.shell("am", "broadcast", "-n", "com.example.demo/.A11yCommandReceiver",
                       "-a", "com.example.demo.A11Y_COMMAND", "--es", "op", "set_field", "--es", "field", field,
                       "--es", "text", encoded, "--ez", "use_clipboard", clipboard)
            xml = self.observe(checkpoint)
            actual = read_draft(xml)
            if ((field == "body" and actual["body"] == normalize_text(value)) or
                (field == "subject" and actual["subject"] == value) or
                (field == "to" and actual["recipients"] == [value])):
                return
        raise WorkflowError(f"{field} 写入后核对失败，已停止，未发送")

    def prepare(self, draft: dict, checkpoint):
        checkpoint()
        mailto = ("mailto:" + quote(draft["recipient"], safe="@") + "?subject=" + quote(draft["subject"], safe="")
                  + "&body=" + quote(draft["body"], safe=""))
        out = self.shell("am", "start", "-a", "android.intent.action.SENDTO", "-d", mailto, "-p", PACKAGE)
        if "Error:" in out:
            raise WorkflowError("无法打开华为邮件写信页，请先登录学校邮箱")
        xml = self.observe(checkpoint)
        actual = read_draft(xml)
        if actual["extra"] or actual["recipients"] != [draft["recipient"]]:
            raise WorkflowError("收件人预填不一致，请检查账号与写信页后重新准备")
        if self.agent_models:
            from perception.observe import describe
            from safety.actions import guard_preparation
            for attempt in range(2):
                checkpoint()
                if attempt:
                    xml = self.observe(checkpoint)
                    actual = read_draft(xml)
                    if actual["extra"] or actual["recipients"] != [draft["recipient"]]:
                        raise WorkflowError("重新观察时收件人发生变化，已停止，未发送")
                candidates = describe(nodes(xml))
                image = self.transport.screenshot() if self.transport else None
                decision = self.agent_models.perceive({"stage": "prepare_email", "candidates": candidates, "expected_draft": draft}, image)
                record = {"time": datetime.now(timezone.utc).isoformat(), "attempt": attempt + 1, "page": decision.page,
                          "actions": [{"action": a.action, "role": a.role, "target_id": a.target_id} for a in decision.actions]}
                try:
                    guard_preparation(decision, candidates, draft)
                    record["guard_accepted"] = True
                except WorkflowError as error:
                    record.update(guard_accepted=False, reason=str(error))
                    if attempt:
                        raise
                finally:
                    folder = self.evidence_root / self.task_id
                    folder.mkdir(parents=True, exist_ok=True)
                    with (folder / "decisions.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps(record, ensure_ascii=False) + "\n")
                if record["guard_accepted"]:
                    break
            self.agent_decision = {"page": decision.page, "proposal_attempts": attempt + 1,
                                   "actions": [{"action": a.action, "role": a.role} for a in decision.actions]}
            for action in decision.actions:
                checkpoint()
                if action.action == "enter_text":
                    self._set_field(action.role, action.text, checkpoint)
                elif action.action == "tap_element":
                    rid = {"to": ID + "to_content", "subject": ID + "subject", "body": "main"}[action.role]
                    fresh = next((n for n in nodes(self.observe(checkpoint)) if n.get("resource-id") == rid), None)
                    if fresh is None:
                        raise WorkflowError("当前控件已变化，请重新准备")
                    self.tap(fresh, checkpoint)
            actual = read_draft(self.observe(checkpoint))
        if actual["subject"] != draft["subject"]:
            self._set_field("subject", draft["subject"], checkpoint)
        if actual["body"] != normalize_text(draft["body"]):
            self._set_field("body", draft["body"], checkpoint)
        activity = self.shell("dumpsys", "activity", "activities")
        found = re.search(r"mResumedActivity:.*?\s(com\.huawei\.email/[^\s}]+)", activity)
        self.compose_activity = found.group(1) if found else None

    def restore_compose(self, checkpoint):
        checkpoint()
        if not self.compose_activity:
            raise WorkflowError("草稿页面记录已丢失，请重新准备并确认")
        self.shell("am", "start", "-f", "0x00020000", "-n", self.compose_activity)
        self.observe(checkpoint)

    def verify_draft(self, draft: dict, checkpoint):
        xml = self.observe(checkpoint, evidence=True)
        if not draft_matches(read_draft(xml), draft):
            raise WorkflowError("草稿与预览内容不一致，请重新准备并确认")

    def send_once(self, draft: dict):
        xml = self.observe(evidence=True)
        if not draft_matches(read_draft(xml), draft):
            raise WorkflowError("点击发送前草稿发生变化")
        button = next(n for n in nodes(xml) if n.get("resource-id") == ID + "send_email_btn")
        self.tap(button)

    def verify_sent(self, draft: dict, checkpoint) -> bool:
        # Navigation is asynchronous. Wait for each observed destination rather
        # than interpreting a still-animating old page as a missing Sent folder.
        self.open_mailbox(checkpoint)
        def unverified(reason):
            self.last_verification_reason = reason
            self.observe(checkpoint, evidence=True)
            return False
        def wait_for(predicate):
            for _ in range(5):
                ns = nodes(self.observe(checkpoint))
                result = predicate(ns)
                if result is not None and (isinstance(result, ET.Element) or bool(result)):
                    return result
                checkpoint()
                time.sleep(.4)
            return None
        def locate_list(ns):
            menu_sent = next((n for n in ns if n.get("resource-id") == ID + "name"
                              and n.get("text", "").strip() in {"已发送", "Sent"}), None)
            if menu_sent is not None:
                return None, False, menu_sent
            spinner = next((n for n in ns if n.get("resource-id") == ID + "folder_spinner"), None)
            if spinner is None and any(n.get("resource-id") == ID + "conversation_subject" for n in ns):
                checkpoint()
                self.shell("input", "keyevent", "4")  # Leave read-only detail; never discard a composer.
            if spinner is not None:
                already_sent = any(n.get("resource-id") == ID + "spinner_name"
                                   and n.get("text") in {"已发送", "Sent"} for n in ns)
                return spinner, already_sent, None
            return None
        location = wait_for(locate_list)
        if location is None:
            return unverified("未回到邮件列表，请稍后仅重新核查结果")
        spinner, already_sent, menu_sent = location
        if menu_sent is not None:
            self.tap(menu_sent, checkpoint)
        elif not already_sent:
            self.tap(spinner, checkpoint)
            sent = wait_for(lambda ns: next((n for n in ns if n.get("text", "").strip() in {"已发送", "Sent"}
                                            and n.get("resource-id") == ID + "name"), None))
            if sent is None:
                return unverified("暂未找到已发送文件夹")
            self.tap(sent, checkpoint)
        ready = wait_for(lambda ns: any(n.get("resource-id") == ID + "spinner_name"
                                       and n.get("text") in {"已发送", "Sent"} for n in ns))
        if not ready:
            return unverified("已发送文件夹尚未加载完成")
        matches = wait_for(lambda ns: [n for n in ns if n.get("resource-id") == ID + "subject"
                                      and n.get("text", "") == draft["subject"]])
        if not matches or len(matches) != 1:
            return unverified("暂未找到唯一匹配的邮件记录，请等待同步后仅重新核查")
        self.tap(matches[0], checkpoint)
        verified = bool(wait_for(lambda ns: sent_record_matches(ns, draft)))
        self.last_verification_reason = "已在已发送记录核对收件人、主题和完整正文" if verified else "记录详情尚未完整加载或内容不一致，不能确认发送结果"
        self.observe(checkpoint, evidence=True)
        return verified

    def show_app(self):
        self.shell("am", "start", "-n", "com.example.demo/.MainActivity")

    def read_current_url(self, checkpoint):
        self.shell("am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.APP_BROWSER")
        for _ in range(5):
            ns = nodes(self.observe(checkpoint, evidence=True))
            address = self._browser_address(ns)
            if address:
                return address
            self._reveal_address(ns, checkpoint)
            time.sleep(.5)
        raise WorkflowError("无法读取当前网页的完整地址，请在任务中提供网址")

    def open_mailbox(self, checkpoint):
        checkpoint()
        # Bring the existing mailbox above old composers without closing drafts.
        self.shell("am", "start", "-f", "0x00020000", "-n", PACKAGE + "/com.android.email.activity.Welcome")

    def resume_context(self, stage: str):
        if stage == "opening_webpage" and self.context_url:
            self.shell("am", "start", "-a", "android.intent.action.VIEW", "-d", self.context_url)
        elif stage in {"preparing_draft", "sending"} and self.compose_activity:
            self.shell("am", "start", "-f", "0x00020000", "-n", self.compose_activity)
        elif stage == "verifying":
            self.open_mailbox(lambda: None)
