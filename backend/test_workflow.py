"""Workflow boundaries and recovery, using a device double; never sends real mail."""

import copy
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import gmail_demo
import recognize
from huawei_mail import PACKAGE, ID, HuaweiDevice, draft_matches, read_draft, sent_record_matches, nodes
from workflow import Engine, Store, WorkflowError, make_draft, validate_url


class Device:
    def __init__(self):
        self.calls = []
        self.changed = False
        self.offline = False
        self.send_crash = False
        self.sent_record = True
        self.gate = None
        self.entered = threading.Event()
        self.restored = []

    def check_ready(self):
        if self.offline:
            raise WorkflowError("设备断开")

    def open_webpage(self, url, checkpoint):
        checkpoint()
        self.calls.append(("url", url))

    def prepare(self, draft, checkpoint):
        self.entered.set()
        if self.gate:
            self.gate.wait(2)
        checkpoint()
        self.calls.append(("prepare", copy.deepcopy(draft)))

    def verify_draft(self, draft, checkpoint):
        checkpoint()
        if self.changed:
            raise WorkflowError("草稿被修改")
        self.calls.append(("readback", draft["subject"]))

    def restore_compose(self, checkpoint):
        checkpoint()

    def send_once(self, draft):
        self.calls.append(("send", draft["recipient"]))
        if self.send_crash:
            raise OSError("发送点击后 USB 断开")

    def verify_sent(self, draft, checkpoint):
        checkpoint()
        return self.sent_record

    def open_mailbox(self, checkpoint):
        checkpoint()

    def show_app(self):
        pass

    def resume_context(self, stage):
        self.restored.append(stage)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.device = Device()
        self.store = Store(Path(self.directory.name))
        self.engine = Engine(self.store, self.device)

    def tearDown(self):
        self.engine.cancelled = True
        self.engine.paused = False
        if self.device.gate:
            self.device.gate.set()
        with self.engine.condition:
            self.engine.condition.notify_all()
        if self.engine.worker:
            self.engine.worker.join(2)
        self.engine.close()
        self.directory.cleanup()

    def create(self, url="", source="text", key="request-0001"):
        return self.engine.create("receiver@example.org", "workflow-test", "中文正文", url, source, key)

    def settle(self, task):
        self.engine.worker.join(2)
        self.assertFalse(self.engine.worker.is_alive(), "worker did not settle")
        return self.store.get(task["id"])

    def approve(self, task):
        return self.engine.approve(task["id"], task["draft_digest"], True, task["approval_nonce"])

    def test_qr_webpage_and_draft_wait_for_explicit_approval(self):
        task = self.settle(self.create("https://example.org/", "qr"))
        self.assertEqual(task["state"], "awaiting_confirmation")
        self.assertTrue(task["webpage_verified"] and task["draft_verified"])
        self.assertIn("https://example.org/", task["draft"]["body"])
        self.assertEqual([c[0] for c in self.device.calls], ["url", "prepare", "readback"])
        self.approve(task)
        task = self.settle(task)
        self.assertEqual(task["state"], "completed")
        self.assertTrue(task["sent_record_verified"])
        self.assertFalse(task["delivery_confirmed"])
        self.assertEqual(task["send_attempts"], 1)

    def test_wrong_or_missing_approval_cannot_send(self):
        task = self.settle(self.create())
        for fingerprint, confirmed in [("0" * 64, True), (task["draft_digest"], False)]:
            with self.assertRaises(WorkflowError):
                self.engine.approve(task["id"], fingerprint, confirmed)
        self.assertEqual(self.store.get(task["id"])["send_attempts"], 0)

    def test_changed_phone_draft_invalidates_approval(self):
        task = self.settle(self.create())
        self.device.changed = True
        self.approve(task)
        task = self.settle(task)
        self.assertEqual(task["state"], "needs_review")
        self.assertIsNone(task["approval_digest"])
        self.assertEqual(task["send_attempts"], 0)
        self.assertNotIn("send", [c[0] for c in self.device.calls])

    def test_repeated_approval_and_create_never_duplicate_send(self):
        task = self.settle(self.create())
        self.assertEqual(self.create()["id"], task["id"])
        self.approve(task)
        task = self.settle(task)
        self.approve(task)
        self.assertEqual(sum(c[0] == "send" for c in self.device.calls), 1)
        with self.assertRaises(WorkflowError):
            self.create(key="request-0002")

    def test_disconnect_after_dispatch_is_unknown_and_not_retryable(self):
        task = self.settle(self.create())
        self.device.send_crash = True
        self.approve(task)
        task = self.settle(task)
        self.assertEqual(task["state"], "outcome_unknown")
        with self.assertRaises(WorkflowError):
            self.engine.control(task["id"], "retry_prepare")
        restarted = Engine(self.store, self.device)
        self.assertEqual(restarted.store.get(task["id"])["send_attempts"], 1)
        self.assertEqual(sum(c[0] == "send" for c in self.device.calls), 1)

    def test_restart_at_send_intent_cannot_replay_side_effect(self):
        task = self.settle(self.create())
        task.update(state="sending", send_attempts=1)
        self.store.save(task, "simulated crash after durable send intent")
        restarted = Engine(self.store, self.device)
        recovered = restarted.store.get(task["id"])
        self.assertEqual(recovered["state"], "outcome_unknown")
        self.assertEqual(sum(c[0] == "send" for c in self.device.calls), 0)

    def test_unconfirmed_record_can_be_rechecked_without_send(self):
        task = self.settle(self.create())
        self.device.sent_record = False
        self.approve(task)
        task = self.settle(task)
        self.assertEqual(task["state"], "outcome_unknown")
        self.device.sent_record = True
        self.engine.control(task["id"], "verify_only")
        task = self.settle(task)
        self.assertEqual(task["state"], "completed")
        self.assertEqual(sum(c[0] == "send" for c in self.device.calls), 1)

    def test_pause_then_cancel_stops_pending_device_action(self):
        self.device.gate = threading.Event()
        task = self.create()
        self.assertTrue(self.device.entered.wait(1))
        self.engine.control(task["id"], "pause")
        self.device.gate.set()
        self.assertEqual(self.store.get(task["id"])["state"], "paused")
        self.engine.control(task["id"], "cancel")
        task = self.settle(task)
        self.assertEqual(task["state"], "cancelled")
        self.assertEqual(self.device.calls, [])

    def test_pause_resume_continues_prepare_and_still_needs_approval(self):
        self.device.gate = threading.Event()
        task = self.create()
        self.assertTrue(self.device.entered.wait(1))
        self.engine.control(task["id"], "pause")
        self.device.gate.set()
        self.engine.control(task["id"], "resume")
        task = self.settle(task)
        self.assertEqual(task["state"], "awaiting_confirmation")
        self.assertEqual(task["send_attempts"], 0)
        self.assertEqual(self.device.restored, ["preparing_draft"])

    def test_device_failure_before_send_is_safe_to_prepare_again(self):
        self.device.offline = True
        task = self.settle(self.create())
        self.assertEqual(task["state"], "failed")
        self.device.offline = False
        self.engine.control(task["id"], "retry_prepare")
        task = self.settle(task)
        self.assertEqual(task["state"], "awaiting_confirmation")

    def test_persistent_events_survive_store_reopen(self):
        task = self.settle(self.create())
        events = Store(Path(self.directory.name)).events(task["id"])
        self.assertEqual(events[-1]["state"], "awaiting_confirmation")
        self.assertTrue((Path(self.directory.name) / task["id"] / "events.jsonl").is_file())
        self.assertEqual(len(self.store.events(task["id"], events[-1]["seq"])), 0)

    def test_receipt_report_is_explicit_and_bound_to_draft(self):
        task = self.settle(self.create())
        with self.assertRaises(WorkflowError):
            self.engine.confirm_delivery(task["id"], task["draft_digest"], True)
        self.approve(task)
        task = self.settle(task)
        task = self.engine.confirm_delivery(task["id"], task["draft_digest"], True)
        self.assertTrue(task["delivery_confirmed"])
        self.assertEqual(task["delivery_evidence"], "recipient_user_confirmation")

    def test_recheck_disconnect_preserves_confirmed_delivery_without_resending(self):
        task = self.settle(self.create())
        self.device.sent_record = False
        self.approve(task)
        task = self.settle(task)
        self.engine.confirm_delivery(task["id"], task["draft_digest"], True)
        self.device.offline = True
        self.engine.control(task["id"], "verify_only")
        task = self.settle(task)
        self.assertEqual(task["state"], "completed")
        self.assertTrue(task["delivery_confirmed"])
        self.assertFalse(task["sent_record_verified"])
        self.assertEqual(task["send_attempts"], 1)
        self.assertIn("设备断开", task["verification_detail"])
        self.assertEqual(sum(c[0] == "send" for c in self.device.calls), 1)

    def test_successful_recheck_reports_both_sent_record_and_confirmed_delivery(self):
        task = self.settle(self.create())
        self.approve(task)
        task = self.settle(task)
        self.engine.confirm_delivery(task["id"], task["draft_digest"], True)
        self.engine.control(task["id"], "verify_only")
        task = self.settle(task)
        self.assertTrue(task["sent_record_verified"])
        self.assertTrue(task["delivery_confirmed"])
        self.assertIn("你也已在收件箱确认收到", task["message"])

    def test_api_validates_inputs_and_requires_review_confirmation(self):
        with patch("workflow_api.get_engine", return_value=self.engine):
            client = TestClient(gmail_demo.app)
            self.assertEqual(client.post("/tasks", json={"recipient": "bad"}).status_code, 422)
            response = client.post("/tasks", json={"recipient": "receiver@example.org", "subject": "workflow-test",
                "body": "中文正文", "request_key": "request-0001"})
            self.assertEqual(response.status_code, 200)
            task = self.settle(response.json()["task"])
            response = client.post(f"/tasks/{task['id']}/approve", json={"draft_digest": task["draft_digest"]})
            self.assertEqual(response.status_code, 409)


class InputTests(unittest.TestCase):
    def test_browser_restores_hidden_toolbar_and_reads_full_native_address(self):
        hidden = '<hierarchy><node package="com.huawei.browser" class="android.webkit.WebView" bounds="[0,154][1200,2457]" /></hierarchy>'
        collapsed = '<hierarchy><node package="com.huawei.browser" class="android.widget.EditText" resource-id="com.huawei.browser:id/url_bar" text="example.org" bounds="[173,206][988,288]" /></hierarchy>'
        focused = '<hierarchy><node package="com.huawei.browser" resource-id="com.huawei.browser:id/tv_search_web_url" text="https://example.org/full/path" /></hierarchy>'
        device = HuaweiDevice()
        with patch.object(device, "shell", return_value="") as shell, patch.object(device, "observe", side_effect=[hidden, collapsed, focused]), patch.object(device, "tap") as tap, patch("huawei_mail.time.sleep"):
            device.open_webpage("https://example.org/full/path", lambda: None)
        self.assertTrue(any(call.args[:2] == ("input", "swipe") for call in shell.call_args_list))
        self.assertEqual(tap.call_count, 1)

    def test_page_url_and_shortened_host_cannot_prove_full_browser_url(self):
        xml = '<hierarchy><node package="com.huawei.browser" resource-id="com.huawei.browser:id/url_bar" text="example.org"/><node package="com.huawei.browser" text="https://example.org/path"/><node package="evil.app" resource-id="com.huawei.browser:id/url_bar" text="https://example.org/path"/></hierarchy>'
        self.assertIsNone(HuaweiDevice._browser_address(nodes(xml)))

    def test_reopened_device_never_overwrites_existing_screen_evidence(self):
        xml = '<hierarchy><node package="com.huawei.email" text="test" /></hierarchy>'
        with tempfile.TemporaryDirectory() as folder, patch("gmail_demo._dump_screen_xml", return_value=xml), patch("huawei_mail.time.time_ns", return_value=1):
            for _ in range(2):
                device = HuaweiDevice(Path(folder))
                device.task_id = "same-task"
                device.observe(evidence=True)
            evidence = list((Path(folder) / "same-task").glob("screen-*.xml"))
            self.assertEqual(len(evidence), 2)
            self.assertTrue(all(p.read_text() == xml for p in evidence))

    def test_sent_verifier_waits_for_navigation_and_full_detail(self):
        def screen(*items):
            root = ET.Element("hierarchy")
            for rid, text in items:
                ET.SubElement(root, "node", {"resource-id": rid, "text": text, "package": PACKAGE,
                    "bounds": "[0,0][100,100]", "enabled": "true"})
            return ET.tostring(root, encoding="unicode")
        inbox = screen((ID + "folder_spinner", ""), (ID + "spinner_name", "收件箱"))
        menu = screen((ID + "name", "已发送"))
        sent = screen((ID + "spinner_name", "已发送"), (ID + "subject", "test"))
        detail = screen((ID + "conversation_subject", "test"), (ID + "conversation_to_content", "receiver@example.org"), ("conversation-body", "中文正文 [unique-marker]"))
        device = HuaweiDevice()
        sequence = [inbox, inbox, menu, inbox, sent, sent, inbox, detail, detail]
        with patch.object(device, "open_mailbox"), patch.object(device, "observe", side_effect=sequence), patch.object(device, "tap") as tap, patch("huawei_mail.time.sleep"):
            self.assertTrue(device.verify_sent(make_draft("receiver@example.org", "test", "中文正文 [unique-marker]"), lambda: None))
            self.assertEqual(tap.call_count, 3)

    def test_sent_body_reassembles_split_link_without_accepting_changed_content(self):
        draft = make_draft("receiver@example.org", "test", "中文正文\n\nhttps://example.org/\n\n[unique-marker]")
        root = ET.Element("hierarchy")
        ET.SubElement(root, "node", {"resource-id": ID + "conversation_subject", "text": "test"})
        to = ET.SubElement(root, "node", {"resource-id": ID + "conversation_to_content", "content-desc": "收件人：receiver@example.org"})
        ET.SubElement(to, "node", {"text": "receiver@example.org"})
        body = ET.SubElement(root, "node", {"resource-id": "mail-content-div"})
        for text in ("中文正文", "\n", "\n"):
            ET.SubElement(body, "node", {"text": text})
        link = ET.SubElement(body, "node", {"content-desc": "https://example.org"})
        ET.SubElement(link, "node", {"text": "https://example.org"})
        slash = ET.SubElement(body, "node", {"text": "/"})
        for text in ("\n", "\n", "[unique-marker]"):
            ET.SubElement(body, "node", {"text": text})
        def matches():
            return sent_record_matches(nodes(ET.tostring(root, encoding="unicode")), draft)
        self.assertTrue(matches())
        slash.set("text", "/changed")
        self.assertFalse(matches())
        slash.set("text", "/")
        ET.SubElement(to, "node", {"text": "other@example.org"})
        self.assertFalse(matches())
        to.remove(to[-1])
        ET.SubElement(body, "node", {"text": "默认手机签名"})
        self.assertFalse(matches())

    def test_sent_verifier_does_not_reopen_folder_menu_when_already_in_sent(self):
        root = ET.Element("hierarchy")
        for rid, text in (("folder_spinner", ""), ("spinner_name", "已发送"), ("subject", "test")):
            ET.SubElement(root, "node", {"resource-id": ID + rid, "text": text, "bounds": "[0,0][100,100]"})
        listing = ET.tostring(root, encoding="unicode")
        detail = '<hierarchy><node resource-id="com.huawei.email:id/conversation_subject" text="test"/><node resource-id="com.huawei.email:id/conversation_to_content" text="receiver@example.org"/><node resource-id="conversation-body" text="中文正文"/></hierarchy>'
        device = HuaweiDevice()
        with patch.object(device, "open_mailbox"), patch.object(device, "observe", side_effect=[listing, listing, listing, detail, detail]), patch.object(device, "tap") as tap:
            self.assertTrue(device.verify_sent(make_draft("receiver@example.org", "test", "中文正文"), lambda: None))
            self.assertEqual(tap.call_count, 1)

    def test_sent_verifier_recovers_when_previous_attempt_left_folder_menu_open(self):
        menu = '<hierarchy><node resource-id="com.huawei.email:id/name" text="已发送" bounds="[0,0][100,100]"/></hierarchy>'
        listing = '<hierarchy><node resource-id="com.huawei.email:id/spinner_name" text="已发送"/><node resource-id="com.huawei.email:id/subject" text="test" bounds="[0,0][100,100]"/></hierarchy>'
        detail = '<hierarchy><node resource-id="com.huawei.email:id/conversation_subject" text="test"/><node resource-id="com.huawei.email:id/conversation_to_content" text="receiver@example.org"/><node resource-id="conversation-body" text="中文正文"/></hierarchy>'
        device = HuaweiDevice()
        with patch.object(device, "open_mailbox"), patch.object(device, "observe", side_effect=[menu, listing, listing, detail, detail]), patch.object(device, "tap") as tap:
            self.assertTrue(device.verify_sent(make_draft("receiver@example.org", "test", "中文正文"), lambda: None))
            self.assertEqual(tap.call_count, 2)

    def test_only_public_https_urls_are_accepted(self):
        self.assertEqual(validate_url("https://EXAMPLE.org"), "https://example.org/")
        for url in ["javascript:alert(1)", "http://example.org/", "file:///sdcard/a", "https://127.0.0.1/",
                    "https://192.168.1.1/", "https://localhost/", "https://a.local/", "https://u:p@example.org/",
                    "https://example.org:8443/", "https://example.org/a\nb"]:
            with self.subTest(url=url), self.assertRaises(WorkflowError): validate_url(url)

    def test_multiple_recipients_and_header_injection_are_rejected(self):
        for recipient in ["a@example.org,b@example.org", "a@example.org\nBcc: b@example.org"]:
            with self.assertRaises(WorkflowError): make_draft(recipient, "test", "body")
        with self.assertRaises(WorkflowError): make_draft("a@example.org", "test\nbcc", "body")

    def test_huawei_readback_rejects_signature_extra_recipient_or_subject(self):
        root = ET.Element("hierarchy")
        container = ET.SubElement(root, "node", {"resource-id": ID + "to_content"})
        ET.SubElement(container, "node", {"text": "receiver@example.org"})
        ET.SubElement(root, "node", {"resource-id": ID + "send_email_btn"})
        subject = ET.SubElement(root, "node", {"resource-id": ID + "subject", "text": "test"})
        body = ET.SubElement(root, "node", {"resource-id": "main", "text": "正文"})
        expected = make_draft("receiver@example.org", "test", "正文")
        def matches(): return draft_matches(read_draft(ET.tostring(root, encoding="unicode")), expected)
        self.assertTrue(matches())
        body.set("text", "正文\n发自我的手机")
        self.assertFalse(matches())
        body.set("text", "正文")
        subject.set("text", "different")
        self.assertFalse(matches())
        subject.set("text", "test")
        ET.SubElement(container, "node", {"text": "extra@example.org"})
        self.assertFalse(matches())

    def test_generic_agent_cannot_bypass_review_in_email_app(self):
        xml = '<hierarchy><node package="com.huawei.email" /></hierarchy>'
        with patch.object(gmail_demo, "_dump_screen_xml", return_value=xml), patch.object(gmail_demo, "_tap") as tap:
            self.assertIn("用户确认", recognize._execute("tap 500 300"))
            tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
