"""Persistent QR → browser → reviewed email workflow; no model may approve a send."""


import hashlib
import ipaddress
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

TERMINAL = {"completed", "failed", "cancelled", "outcome_unknown"}
MESSAGES = {
    "understanding": "正在理解您的需求", "needs_clarification": "请补充收件信息",
    "awaiting_qr": "请将镜头对准二维码，小银会继续打开并分享网页",
    "preparing": "正在准备任务", "opening_webpage": "正在打开并核对扫码网址",
    "preparing_draft": "正在填写邮件并核对内容", "awaiting_confirmation": "草稿已核对，请检查后确认发送",
    "paused": "任务已暂停", "sending": "正在发送本次已确认的邮件",
    "verifying": "正在检查已发送记录，请勿重发", "completed": "已在已发送文件夹核对邮件；送达仍需收件方确认",
    "failed": "任务未完成", "cancelled": "任务已取消", "outcome_unknown": "已尝试发送，结果尚未确认，请勿重发",
    "needs_review": "草稿发生变化，请重新核对", "interrupted": "上次准备被中断，可重新准备",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowError(Exception):
    pass


class Cancelled(WorkflowError):
    pass


def validate_url(value: str) -> str:
    value = value.strip()
    if len(value) > 2048 or any(ord(c) <= 32 for c in value) or "\\" in value:
        raise WorkflowError("网址中包含无效字符")
    try:
        u = urlsplit(value)
        host = (u.hostname or "").encode("idna").decode("ascii").lower()
        port = u.port
    except (ValueError, UnicodeError):
        raise WorkflowError("无法识别二维码中的网址") from None
    if u.scheme.lower() != "https" or not host or u.username or u.password or port not in (None, 443):
        raise WorkflowError("请使用不含账号密码的公开 HTTPS 网页网址")
    if host == "localhost" or host.endswith((".local", ".localhost")) or "." not in host:
        raise WorkflowError("不能使用本机或局域网网址")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if (any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                or all(re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", label) for label in labels)):
            raise WorkflowError("网址域名无效") from None
    else:
        raise WorkflowError("请使用公开网页域名，不能直接使用 IP 地址")
    return urlunsplit(("https", host, u.path or "/", u.query, u.fragment))


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").strip()


def make_draft(recipient: str, subject: str, body: str, url: str = "") -> dict:
    recipient, subject, body = recipient.strip(), subject.strip(), normalize_text(body)
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", recipient):
        raise WorkflowError("请填写一个完整的收件邮箱，不能包含多个收件人")
    if not subject or len(subject) > 150 or any(c in subject for c in "\r\n\x00"):
        raise WorkflowError("请填写 1 至 150 字的单行主题")
    if url:
        url = validate_url(url)
        if url not in body:
            body = (body + "\n\n" + url).strip()
    if not body or len(body) > 10000 or "\x00" in body:
        raise WorkflowError("请填写 1 至 10000 字的正文，或先扫描网页二维码")
    return {"recipient": recipient, "subject": subject, "body": body, "url": url}


def digest(draft: dict) -> str:
    return hashlib.sha256(json.dumps(draft, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
