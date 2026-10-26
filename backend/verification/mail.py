"""Pure, independent draft and sent-record verification rules."""
import re
import xml.etree.ElementTree as ET
from domain import WorkflowError, normalize_text

PACKAGE = "com.huawei.email"
ID = PACKAGE + ":id/"
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}")


def nodes(xml: str):
    return list(ET.fromstring(xml[xml.find("<"):]).iter("node"))


def point(node):
    b = list(map(int, re.findall(r"-?\d+", node.get("bounds", ""))))
    if len(b) != 4 or b[0] < 0 or b[1] < 0 or b[2] <= b[0] or b[3] <= b[1]:
        raise WorkflowError("控件当前不可见，请返回邮件页面")
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


def read_draft(xml: str) -> dict:
    ns = nodes(xml)
    if not any(n.get("resource-id") == ID + "send_email_btn" for n in ns):
        raise WorkflowError("当前不是华为邮件写信页")
    def field(rid):
        n = next((n for n in ns if n.get("resource-id") == rid), None)
        if n is None:
            raise WorkflowError("写信页缺少可核对的主题或正文")
        return normalize_text(n.get("text", ""))
    containers = [n for n in ns if n.get("resource-id") == ID + "to_content"]
    if len(containers) != 1:
        raise WorkflowError("无法唯一识别收件人区域")
    addresses = set()
    for n in containers[0].iter("node"):
        addresses.update(EMAIL.findall(n.get("text", "") + " " + n.get("content-desc", "")))
    extra = []
    for n in ns:
        rid = n.get("resource-id", "")
        if rid in {ID + "cc_content", ID + "bcc_content"}:
            for child in n.iter("node"):
                if EMAIL.search(child.get("text", "") + " " + child.get("content-desc", "")):
                    extra.append("抄送/密送")
        if "attachment_name" in rid and n.get("text", "").strip():
            extra.append("附件")
    return {"recipients": sorted(addresses), "subject": field(ID + "subject"),
            "body": field("main"), "extra": extra}


def draft_matches(actual: dict, expected: dict) -> bool:
    return (actual["recipients"] == [expected["recipient"]] and not actual["extra"]
            and actual["subject"] == expected["subject"]
            and actual["body"] == normalize_text(expected["body"]))


def sent_record_matches(ns, draft: dict) -> bool:
    subject = next((n for n in ns if n.get("resource-id") == ID + "conversation_subject"), None)
    recipients = next((n for n in ns if n.get("resource-id") == ID + "conversation_to_content"), None)
    body = next((n for n in ns if n.get("resource-id") == "mail-content-div"), None)
    if body is None:
        body = next((n for n in ns if n.get("resource-id") == "conversation-body"), None)
    if subject is None or recipients is None or body is None:
        return False
    addresses = set()
    for n in recipients.iter("node"):
        addresses.update(EMAIL.findall(n.get("text", "") + " " + n.get("content-desc", "")))

    def rendered_text(node):
        # A URL can be split into a link and a separate trailing slash. Preserve
        # leaf order and explicit line breaks; don't duplicate a link's label.
        children = "".join(rendered_text(child) for child in node)
        return children or node.get("text", "") or node.get("content-desc", "")

    return (addresses == {draft["recipient"]} and subject.get("text", "") == draft["subject"]
            and normalize_text(rendered_text(body)) == normalize_text(draft["body"]))
