"""Recovery behavior with synthetic UI trees; these tests never touch a phone."""

import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from agent.runtime import Engine
from device.huawei import HuaweiDevice, ScreenContextChanged
from domain import Cancelled, WorkflowError, make_draft
from safety.recovery import classify_dialog
from storage.tasks import Store


MAIL = "com.huawei.email"
BROWSER = "com.huawei.browser"


def native_dialog(
    title="邮件填写提示",
    message="可在主题和正文输入框填写内容。",
    button="稍后",
    *,
    package=MAIL,
    button_id="android:id/button2",
    button_attrs=None,
    duplicate=False,
):
    root = ET.Element("hierarchy")
    modal = ET.SubElement(root, "node", {"package": package, "class": "android.app.Dialog",
                                         "bounds": "[20,100][1080,1500]"})
    ET.SubElement(modal, "node", {"package": package, "resource-id": "android:id/alertTitle",
                                  "class": "android.widget.TextView", "text": title,
                                  "bounds": "[40,120][1040,220]"})
    ET.SubElement(modal, "node", {"package": package, "resource-id": "android:id/message",
                                  "class": "android.widget.TextView", "text": message,
                                  "bounds": "[40,230][1040,900]"})
    attrs = {"package": package, "resource-id": button_id, "class": "android.widget.Button",
             "text": button, "bounds": "[60,950][300,1100]", "enabled": "true", "clickable": "true"}
    attrs.update(button_attrs or {})
    ET.SubElement(modal, "node", attrs)
    if duplicate:
        ET.SubElement(modal, "node", attrs)
    return ET.tostring(root, encoding="unicode")


@pytest.mark.parametrize("package", [MAIL, BROWSER, "com.android.chrome"])
@pytest.mark.parametrize("label", ["取消", "稍后", "暂不", "以后再说", "稍后提醒", "关闭"])
def test_known_informational_dialog_can_choose_only_its_native_negative_button(package, label):
    decision = classify_dialog(native_dialog(package=package, button=label), package)
    assert decision is not None and decision.kind == "tap"
    assert decision.target_id == "android:id/button2"
    assert decision.message


def test_known_single_button_hint_can_close_without_a_positive_confirmation():
    decision = classify_dialog(native_dialog(button="关闭", button_id="android:id/button1"), MAIL)
    assert decision is not None and decision.kind == "tap"
    assert decision.target_id == "android:id/button1"


@pytest.mark.parametrize("label", ["确定", "同意", "立即更新", "知道了", "发送"])
def test_general_confirmation_is_not_an_automatic_recovery_action(label):
    decision = classify_dialog(native_dialog(button=label, button_id="android:id/button1"), MAIL)
    assert decision is not None and decision.kind == "blocked"
    assert not decision.target_id


@pytest.mark.parametrize("title,message", [
    ("删除邮件", "是否删除此邮件？"),
    ("邮件填写提示", "是否发送此邮件？"),
    ("Discard draft", "Discard the current draft?"),
    ("更新提示", "请授予读取联系人的权限。"),
    ("Permission required", "Allow access to contacts?"),
    ("帮助", "请登录账号继续。"),
    ("使用提示", "请输入密码。"),
    ("证书有问题", "是否仍要访问这个网站？"),
    ("温馨提示", "请支付费用以继续。"),
    ("更新提醒", "是否购买订阅？"),
])
def test_sensitive_dialog_is_blocked_even_if_it_has_a_cancel_button(title, message):
    decision = classify_dialog(native_dialog(title, message), MAIL)
    assert decision is not None and decision.kind == "blocked"
    assert not decision.target_id
    assert decision.message


@pytest.mark.parametrize("title,message", [
    ("未知窗口", "请选择接下来的操作。"),
    ("", "请继续。"),
    ("提示", "是否继续这项操作？"),
])
def test_unknown_modal_requires_human_intervention(title, message):
    decision = classify_dialog(native_dialog(title, message), MAIL)
    assert decision is not None and decision.kind == "blocked"


@pytest.mark.parametrize("attrs", [
    {"enabled": "false"},
    {"bounds": "[0,0][0,0]"},
    {"bounds": "[-10,0][50,50]"},
    {"bounds": "invalid"},
    {"visible-to-user": "false"},
    {"package": "untrusted.example"},
])
def test_dialog_button_must_be_visible_enabled_and_owned_by_the_expected_app(attrs):
    decision = classify_dialog(native_dialog(button_attrs=attrs), MAIL)
    assert decision is not None and decision.kind == "blocked"


def test_duplicate_native_button_is_not_a_unique_click_target():
    decision = classify_dialog(native_dialog(duplicate=True), MAIL)
    assert decision is not None and decision.kind == "blocked"


def test_hidden_dialog_container_cannot_authorize_a_visible_looking_child_button():
    root = ET.fromstring(native_dialog())
    root.find("node").set("visible-to-user", "false")
    decision = classify_dialog(ET.tostring(root, encoding="unicode"), MAIL)
    assert decision is not None and decision.kind == "blocked"


def test_dialog_from_another_app_is_not_dismissed_as_the_expected_app():
    decision = classify_dialog(native_dialog(package="untrusted.example"), MAIL)
    assert decision is not None and decision.kind == "blocked"


def test_page_content_with_native_looking_ids_cannot_authorize_a_dialog_click():
    root = ET.Element("hierarchy")
    webview = ET.SubElement(root, "node", {"package": BROWSER, "class": "android.webkit.WebView",
                                           "bounds": "[0,0][1080,2000]"})
    fake_modal = ET.fromstring(native_dialog(package=BROWSER)).find("node")
    webview.append(fake_modal)
    decision = classify_dialog(ET.tostring(root, encoding="unicode"), BROWSER)
    assert decision is None or decision.kind == "blocked"


def test_normal_composer_is_not_mistaken_for_a_dialog():
    xml = ('<hierarchy><node package="com.huawei.email" class="android.widget.LinearLayout">'
           '<node package="com.huawei.email" resource-id="com.huawei.email:id/send_email_btn" text="发送"/>'
           '<node package="com.huawei.email" resource-id="main" text="这封草稿需要保留原文。"/>'
           '</node></hierarchy>')
    assert classify_dialog(xml, MAIL) is None


def test_underlying_composer_text_does_not_change_the_meaning_of_an_isolated_hint():
    root = ET.fromstring(native_dialog())
    ET.SubElement(root, "node", {"package": MAIL, "resource-id": MAIL + ":id/send_email_btn",
                                 "class": "android.widget.Button", "text": "发送"})
    ET.SubElement(root, "node", {"package": MAIL, "resource-id": "main",
                                 "class": "android.widget.EditText", "text": "请保留草稿，不要删除。"})
    decision = classify_dialog(ET.tostring(root, encoding="unicode"), MAIL)
    assert decision is not None and decision.kind == "tap"


def composer(*, recipient="receiver@example.org", subject="recovery-test", body="欢迎参加。", missing=None):
    root = ET.Element("hierarchy")
    window = ET.SubElement(root, "node", {"package": MAIL, "class": "android.widget.LinearLayout"})
    to = ET.SubElement(window, "node", {"package": MAIL, "resource-id": MAIL + ":id/to_content"})
    ET.SubElement(to, "node", {"package": MAIL, "text": recipient})
    for rid, text in [(MAIL + ":id/send_email_btn", "发送"), (MAIL + ":id/subject", subject), ("main", body)]:
        if rid == missing:
            continue
        ET.SubElement(window, "node", {"package": MAIL, "resource-id": rid, "text": text,
                                       "bounds": "[40,300][1040,700]", "enabled": "true"})
    return ET.tostring(root, encoding="unicode")


HOME = '<hierarchy><node package="com.huawei.android.launcher" class="android.widget.FrameLayout"/></hierarchy>'


def recovering_device(tmp_path, sequence):
    """Keep the final screen stable, as a real UI normally remains after a retry."""
    device = HuaweiDevice(tmp_path)
    device.task_id = "recovery-test-task"
    device.compose_activity = MAIL + "/com.android.email.activity.MessageCompose"
    device.recovery_used = 0
    device.recovery_limit = 3
    screens = iter(sequence)
    current = sequence[-1]

    def observe(*args, **kwargs):
        nonlocal current
        current = next(screens, current)
        return current

    device.observe = Mock(side_effect=observe)
    device.shell = Mock(return_value="")
    device.tap = Mock()
    return device


def test_hint_is_dismissed_before_a_fresh_composer_is_accepted(tmp_path):
    ready = composer()
    device = recovering_device(tmp_path, [native_dialog(), native_dialog(), ready])
    with patch("device.huawei.time.sleep"):
        assert device._observe_composer(stage="draft") == ready
    assert device.observe.call_count >= 2
    device.tap.assert_called_once()
    assert device.tap.call_args.args[0].get("resource-id") == "android:id/button2"


def test_hint_that_disappears_before_the_click_is_not_clicked_at_its_old_position(tmp_path):
    ready = composer()
    device = recovering_device(tmp_path, [native_dialog(), ready])
    with patch("device.huawei.time.sleep"):
        assert device._observe_composer(stage="draft") == ready
    device.tap.assert_not_called()
    events = [json.loads(line) for line in (tmp_path / device.task_id / "recoveries.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["outcome"] == "skipped" for event in events)


def test_safe_hint_replaced_by_sensitive_dialog_between_observation_and_click_is_blocked(tmp_path):
    device = recovering_device(tmp_path, [native_dialog(), native_dialog("删除邮件", "是否删除此邮件？")])
    with patch("device.huawei.time.sleep"), pytest.raises(WorkflowError):
        device._observe_composer(stage="draft")
    device.tap.assert_not_called()
    device.shell.assert_not_called()


def test_pause_resume_at_final_tap_checkpoint_skips_the_old_hint_and_reobserves(tmp_path):
    ready = composer()
    device = recovering_device(tmp_path, [native_dialog(), native_dialog(), ready])
    device.transport = SimpleNamespace(tap=Mock())
    changed = False
    real_tap = HuaweiDevice.tap.__get__(device, HuaweiDevice)
    with patch.object(device, "tap", wraps=real_tap) as tap:
        def checkpoint():
            nonlocal changed
            if tap.call_count and not changed:
                # A real resume_context also increments this revision after
                # restoring the native page. Simulate that boundary here.
                device.context_epoch += 1
                changed = True

        with patch("device.huawei.time.sleep"):
            assert device._observe_composer(checkpoint, stage="draft") == ready
    assert changed
    device.transport.tap.assert_not_called()
    device.shell.assert_not_called()
    assert device.observe.call_count >= 3
    events = [json.loads(line) for line in (tmp_path / device.task_id / "recoveries.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["outcome"] == "skipped" for event in events)


def test_device_tap_never_dispatches_if_its_final_checkpoint_changes_the_screen_context(tmp_path):
    transport = SimpleNamespace(tap=Mock())
    device = HuaweiDevice(tmp_path, transport=transport)
    node = ET.fromstring(native_dialog()).find(".//node[@resource-id='android:id/button2']")

    def checkpoint():
        device.context_epoch += 1

    with pytest.raises(ScreenContextChanged):
        device.tap(node, checkpoint)
    transport.tap.assert_not_called()


def test_observe_discards_xml_read_across_a_resume_and_saves_only_the_fresh_screen(tmp_path):
    stale, fresh = native_dialog(), composer()
    device = HuaweiDevice(tmp_path)
    device.task_id = "observe-context-test"
    reads = 0

    def read_xml():
        nonlocal reads
        reads += 1
        if reads == 1:
            device.context_epoch += 1
            return stale
        return fresh

    device.transport = SimpleNamespace(xml=Mock(side_effect=read_xml))
    device.shell = Mock(return_value="")
    assert device.observe(evidence=True) == fresh
    assert device.transport.xml.call_count == 2
    screens = list((tmp_path / device.task_id).glob("screen-*.xml"))
    assert len(screens) == 1 and screens[0].read_text(encoding="utf-8") == fresh


def test_continuously_changing_context_stops_observation_at_the_read_limit(tmp_path):
    device = HuaweiDevice(tmp_path)

    def read_xml():
        device.context_epoch += 1
        return HOME

    device.transport = SimpleNamespace(xml=Mock(side_effect=read_xml))
    with pytest.raises(WorkflowError, match="页面连续变化"):
        device.observe()
    assert device.transport.xml.call_count == 3


def test_transient_missing_editor_waits_for_current_screen_without_opening_a_new_draft(tmp_path):
    ready = composer()
    device = recovering_device(tmp_path, [composer(missing="main"), ready])
    with patch("device.huawei.time.sleep"):
        assert device._observe_composer(stage="draft") == ready
    assert device.observe.call_count >= 2
    device.shell.assert_not_called()
    device.tap.assert_not_called()


def test_leaving_composer_restores_only_the_recorded_activity_then_reobserves(tmp_path):
    ready = composer()
    device = recovering_device(tmp_path, [HOME, HOME, ready])
    with patch("device.huawei.time.sleep"):
        assert device._observe_composer(stage="draft") == ready
    starts = [call.args for call in device.shell.call_args_list if call.args[:2] == ("am", "start")]
    assert len(starts) == 1
    assert device.compose_activity in starts[0]
    assert all("android.intent.action.SENDTO" not in args for args in starts)
    assert all(not any(str(arg).startswith("mailto:") for arg in args) for args in starts)
    assert device.observe.call_count >= 3


def test_no_restore_mode_cannot_reopen_an_activity_after_a_failed_observation(tmp_path):
    device = recovering_device(tmp_path, [HOME])
    with patch("device.huawei.time.sleep"), pytest.raises(WorkflowError):
        device._observe_composer(stage="review", allow_restore=False)
    device.shell.assert_not_called()
    device.tap.assert_not_called()
    assert device.observe.call_count <= 4


def test_permanently_missing_editor_restores_original_composer_at_most_once_then_stops(tmp_path):
    device = recovering_device(tmp_path, [composer(missing="main")])
    with patch("device.huawei.time.sleep"), pytest.raises(WorkflowError):
        device._observe_composer(stage="draft")
    starts = [call.args for call in device.shell.call_args_list if call.args[:2] == ("am", "start")]
    assert len(starts) == 1
    assert device.compose_activity in starts[0]
    device.tap.assert_not_called()
    assert device.observe.call_count <= 4


def test_repeated_hint_exhausts_a_bounded_action_budget_instead_of_looping(tmp_path):
    device = recovering_device(tmp_path, [native_dialog()])
    with patch("device.huawei.time.sleep"), pytest.raises(WorkflowError):
        device._observe_composer(stage="draft")
    assert 1 <= device.tap.call_count <= 3
    assert device.observe.call_count <= 8


def test_sensitive_modal_does_not_tap_cancel_or_restore_the_page(tmp_path):
    device = recovering_device(tmp_path, [native_dialog("删除邮件", "是否删除这封邮件？")])
    with patch("device.huawei.time.sleep"), pytest.raises(WorkflowError):
        device._observe_composer(stage="draft")
    device.tap.assert_not_called()
    device.shell.assert_not_called()


def test_cancel_checkpoint_interrupts_recovery_before_an_external_action(tmp_path):
    device = recovering_device(tmp_path, [native_dialog()])
    checkpoint = Mock(side_effect=Cancelled("用户取消任务"))
    with patch("device.huawei.time.sleep"), pytest.raises(Cancelled):
        device._observe_composer(checkpoint, stage="draft")
    device.tap.assert_not_called()
    device.shell.assert_not_called()


def test_failed_send_click_is_not_replayed_by_recovery(tmp_path):
    draft = make_draft("receiver@example.org", "recovery-test", "欢迎参加。")
    device = recovering_device(tmp_path, [composer()])
    device.tap.side_effect = OSError("发送点击结果未知")
    with pytest.raises(OSError):
        device.send_once(draft)
    device.tap.assert_called_once()
    device.shell.assert_not_called()


def test_browser_hint_is_dismissed_and_the_native_full_address_is_read_again(tmp_path):
    ready = ('<hierarchy><node package="com.huawei.browser" '
             'resource-id="com.huawei.browser:id/url_bar" text="https://example.org/"/></hierarchy>')
    hint = native_dialog("使用提示", "浏览器功能介绍可稍后查看。", package=BROWSER)
    device = recovering_device(tmp_path, [hint, hint, ready])
    with patch("device.huawei.time.sleep"):
        device.open_webpage("https://example.org/", lambda: None)
    device.tap.assert_called_once()
    assert device.tap.call_args.args[0].get("resource-id") == "android:id/button2"
    starts = [call.args for call in device.shell.call_args_list if call.args[:2] == ("am", "start")]
    assert len(starts) == 1
    assert "android.intent.action.VIEW" in starts[0]
    events = [json.loads(line) for line in (tmp_path / device.task_id / "recoveries.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["outcome"] == "recovered" for event in events)


def test_browser_cold_load_waits_for_native_progress_before_focusing_the_address_bar(tmp_path):
    target = "https://example.org/full/path?case=cold-load"
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", {"package": BROWSER, "class": "android.widget.EditText",
                                 "resource-id": BROWSER + ":id/url_bar", "text": "example.org",
                                 "bounds": "[80,160][980,280]"})
    progress = ET.SubElement(root, "node", {"package": BROWSER, "class": "android.widget.ProgressBar",
                                            "resource-id": BROWSER + ":id/location_bar_progress_bar"})
    # Page content and a shortened host must never substitute for the full
    # native address, even when the page displays the requested URL verbatim.
    ET.SubElement(root, "node", {"package": BROWSER, "class": "android.webkit.WebView", "text": target,
                                 "bounds": "[0,300][1080,2000]"})
    loading = ET.tostring(root, encoding="unicode")
    root.remove(progress)
    loaded = ET.tostring(root, encoding="unicode")
    full = ('<hierarchy><node package="com.huawei.browser" '
            'resource-id="com.huawei.browser:id/tv_search_web_url" '
            'text="https://example.org/full/path?case=cold-load"/></hierarchy>')
    device = recovering_device(tmp_path, [loading, loaded, full])
    tapped_after_reads = []
    device.tap.side_effect = lambda *args: tapped_after_reads.append(device.observe.call_count)
    with patch("device.huawei.time.sleep"):
        device.open_webpage(target, lambda: None)
    assert device.observe.call_count == 3
    assert tapped_after_reads == [2]
    assert device.tap.call_args.args[0].get("resource-id") == BROWSER + ":id/url_bar"
    starts = [call.args for call in device.shell.call_args_list if call.args[:2] == ("am", "start")]
    assert len(starts) == 1 and target in starts[0]


@pytest.mark.parametrize("second_write_works", [True, False])
def test_input_retry_is_freshly_located_and_reported_recovered_only_after_matching_readback(tmp_path, second_write_works):
    expected = "欢迎参加。\n\nhttps://example.org/"
    device = recovering_device(tmp_path, [composer(body="旧正文")])
    actual_body = "旧正文"
    broadcasts = []

    def observe(*args, **kwargs):
        return composer(body=actual_body)

    def shell(*args, **kwargs):
        nonlocal actual_body
        if args[:2] == ("am", "broadcast"):
            broadcasts.append(args)
            if len(broadcasts) == 2 and second_write_works:
                actual_body = expected
        return ""

    device.observe = Mock(side_effect=observe)
    device.shell = Mock(side_effect=shell)
    with patch("device.huawei.time.sleep"):
        if second_write_works:
            device._set_field("body", expected, lambda: None)
        else:
            with pytest.raises(WorkflowError):
                device._set_field("body", expected, lambda: None)
    assert len(broadcasts) == 2
    assert broadcasts[0][-1] == "false" and broadcasts[1][-1] == "true"
    assert device.observe.call_count >= 4
    events = [json.loads(line) for line in (tmp_path / device.task_id / "recoveries.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["action"] == "retry_input" and event["outcome"] == "attempting" for event in events)
    assert any(event["outcome"] == "recovered" for event in events) is second_write_works
    assert not any(expected in json.dumps(event, ensure_ascii=False) for event in events)


def test_recipient_change_after_page_recovery_is_not_automatically_overwritten(tmp_path):
    device = recovering_device(tmp_path, [HOME, HOME, composer(recipient="unexpected@example.org")])
    device._expected_draft = make_draft("receiver@example.org", "recovery-test", "欢迎参加。")
    with patch("device.huawei.time.sleep"), pytest.raises(WorkflowError, match="收件人"):
        device._observe_composer(stage="draft")
    device.tap.assert_not_called()
    assert all(call.args[:2] != ("am", "broadcast") for call in device.shell.call_args_list)


def test_restored_composer_with_same_recipient_and_subject_but_another_task_marker_is_rejected(tmp_path):
    another_body = "欢迎参加。\n\n[SilverGuide:abcdef987654]"
    device = recovering_device(tmp_path, [HOME, HOME, composer(body=another_body)])
    device._expected_draft = make_draft("receiver@example.org", "recovery-test",
                                       "欢迎参加。\n\n[SilverGuide:0123456789ab]")
    with patch("device.huawei.time.sleep"), pytest.raises(WorkflowError, match="本次原草稿"):
        device._observe_composer(stage="draft")
    device.tap.assert_not_called()
    assert all(call.args[:2] != ("am", "broadcast") for call in device.shell.call_args_list)
    events = [json.loads(line) for line in (tmp_path / device.task_id / "recoveries.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any(event["outcome"] == "recovered" for event in events)


def test_recovery_keeps_the_normal_user_approval_gate(tmp_path):
    """Exercise graph + actual preparation adapter after a popup, without any device I/O."""
    device = recovering_device(tmp_path, [native_dialog(), native_dialog(), composer()])
    device.check_ready = Mock()
    device.show_app = Mock()
    device.send_once = Mock()
    entered_body = ""
    reads = 0

    def shell(*args, **kwargs):
        nonlocal entered_body
        if args[:2] == ("am", "start"):
            mailto = next((str(arg) for arg in args if str(arg).startswith("mailto:")), None)
            if mailto:
                entered_body = parse_qs(urlsplit(mailto).query)["body"][0]
        return ""

    def observe(*args, **kwargs):
        nonlocal reads
        reads += 1
        return native_dialog() if reads <= 2 else composer(body=entered_body)

    device.shell = Mock(side_effect=shell)
    device.observe = Mock(side_effect=observe)
    engine = Engine(Store(tmp_path / "store"), device)
    try:
        with patch("device.huawei.time.sleep"):
            task = engine.create("receiver@example.org", "recovery-test", "欢迎参加。", "", "text", "recovery-gate-01")
            engine.worker.join(3)
        assert not engine.worker.is_alive()
        current = engine.store.get(task["id"])
        assert current["state"] == "awaiting_confirmation", current["message"]
        assert current["draft_verified"] and current["send_attempts"] == 0
        device.send_once.assert_not_called()
        starts = [call.args for call in device.shell.call_args_list if call.args[:2] == ("am", "start")]
        assert sum("android.intent.action.SENDTO" in args for args in starts) == 1
    finally:
        engine.cancelled = True
        engine.paused = False
        with engine.condition:
            engine.condition.notify_all()
        if engine.worker:
            engine.worker.join(3)
        engine.close()
