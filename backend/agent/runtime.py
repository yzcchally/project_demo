from __future__ import annotations
import hashlib
import re
import threading
import uuid
import secrets
import os
from langgraph.types import Command
from pathlib import Path
from domain import *
from storage.tasks import Store
from storage.contacts import Contacts
from agent.models import ModelGateway

class Engine:
    def __init__(self, store: Store, device, interpreter=None):
        self.store, self.device = store, device
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.worker: threading.Thread | None = None
        self.cancelled = False
        self.paused = False
        self.resume_pending = False
        self.current_task_id = None
        self.contacts = Contacts(store)
        self.models = interpreter or ModelGateway(store.directory / "model-calls")
        from agent.graph import build_graph
        self.graph = build_graph(self)
        # A restart must never replay a possible external send.
        for task in store.all():
            if task["state"] not in TERMINAL:
                task["state"] = "outcome_unknown" if task["send_attempts"] else "interrupted"
                task["approval_digest"] = None
                store.save(task, MESSAGES[task["state"]])

    def active(self) -> bool:
        return any(t["state"] not in TERMINAL | {"interrupted"} for t in self.store.all())

    def checkpoint(self):
        with self.condition:
            while self.paused and not self.cancelled:
                self.condition.wait(.25)
            if self.cancelled:
                raise Cancelled("任务已取消")
            restore = self.resume_pending
            self.resume_pending = False
            stage = self.store.get(self.current_task_id)["state"] if restore and self.current_task_id else ""
        if restore and hasattr(self.device, "resume_context"):
            self.device.resume_context(stage)

    def _launch(self, function, task_id: str):
        if self.worker and self.worker.is_alive():
            raise WorkflowError("手机还有操作正在结束，请稍后再试")
        self.cancelled = self.paused = False
        self.resume_pending = False
        self.worker = threading.Thread(target=self._work, args=(function, task_id), daemon=True)
        self.worker.start()

    def create(self, recipient: str, subject: str, body: str, url: str, source: str, request_key: str) -> dict:
        draft = make_draft(recipient, subject, body, url)
        if source not in {"qr", "manual", "text"} or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_key):
            raise WorkflowError("任务来源或请求编号无效")
        if source == "qr" and not url:
            raise WorkflowError("请先完成二维码扫描")
        original_digest = digest(draft)
        # This visible marker binds the Sent record to this particular task,
        # rather than accidentally accepting an older mail with the same subject.
        marker = "[SilverGuide:" + hashlib.sha256(request_key.encode()).hexdigest()[:12] + "]"
        draft["body"] += "\n\n" + marker
        with self.lock:
            for old in self.store.all():
                if old["request_key"] == request_key:
                    if old["draft"] != draft:
                        raise WorkflowError("同一请求编号不能对应不同内容")
                    return old
            if self.active() or self.worker and self.worker.is_alive():
                raise WorkflowError("请先完成或取消当前任务")
            for old in self.store.all():
                if old.get("original_digest") == original_digest and old["send_attempts"]:
                    raise WorkflowError("相同邮件已有发送尝试，请先检查该任务的结果，不能再次发送")
            task = {"id": uuid.uuid4().hex, "request_key": request_key, "draft": draft, "source": source,
                    "state": "preparing", "created_at": now(), "send_attempts": 0, "approval_digest": None,
                    "original_digest": original_digest, "verification_marker": marker,
                    "draft_digest": digest(draft), "webpage_verified": False, "draft_verified": False,
                    "sent_record_verified": False, "delivery_confirmed": False,
                    "approval_nonce": secrets.token_urlsafe(24), "graph_run": 1, "task_type": "share_url", "agent_mode": False}
            self.store.save(task, MESSAGES[task["state"]])
            self._launch(self._prepare, task["id"])
            return task

    def _stage(self, task_id: str, state: str, **changes) -> dict:
        with self.condition:
            self.checkpoint()
            task = self.store.get(task_id)
            task.update(changes)
            task["state"] = state
            message = ("已核对已发送记录；你也已在收件箱确认收到"
                       if state == "completed" and task.get("delivery_confirmed") else MESSAGES[state])
            if state == "awaiting_qr" and task.get("task_type") == "open_url":
                message = "请将镜头对准二维码，小银会继续打开网页"
            return self.store.save(task, message)

    def _prepare(self, task_id: str):
        self.graph.invoke({"task_id": task_id}, self._graph_config(task_id))
        if self.store.get(task_id)["state"] not in {"awaiting_qr", "needs_clarification", "cancelled"}:
            self.device.show_app()

    def _graph_config(self, task_id):
        return {"configurable": {"thread_id": f"{task_id}:{self.store.get(task_id).get('graph_run', 1)}"}, "recursion_limit": 40}

    def _continue_graph(self, task_id):
        self.graph.invoke(Command(resume=self.store.get(task_id)["pending_graph_reply"]), self._graph_config(task_id))
        if self.store.get(task_id)["state"] not in {"awaiting_qr", "needs_clarification", "cancelled"}:
            self.device.show_app()

    def _precheck(self, task_id):
        task = self.store.get(task_id)
        if task.get("instruction"):
            if task.get("task_type") == "share_current_page" and not task["draft"]["url"]:
                task["draft"]["url"] = self.device.read_current_url(self.checkpoint)
            raw = task["draft"]
            if task["task_type"] == "open_url":
                raw["url"] = validate_url(raw["url"])
            else:
                clean = make_draft(raw["recipient"], raw["subject"], raw["body"].removesuffix("\n\n" + task["verification_marker"]), raw["url"])
                original = digest(clean)
                if any(t["id"] != task_id and t.get("original_digest") == original and t["send_attempts"] for t in self.store.all()):
                    raise WorkflowError("相同邮件已有发送尝试，请先核查原任务")
                task["original_digest"] = original
                clean["body"] += "\n\n" + task["verification_marker"]
                task["draft"] = clean
            task.update(draft_digest=digest(task["draft"]), intake_complete=True)
            self.store.save(task, "需求已确认，正在检查手机环境")
        task = self.store.get(task_id)
        if task.get("original_digest") and any(t["id"] != task_id and t.get("original_digest") == task["original_digest"] and t["send_attempts"] for t in self.store.all()):
            raise WorkflowError("相同邮件已有发送尝试，请先核查原任务")
        self.device.check_ready()

    def _browser(self, task_id):
        task = self.store.get(task_id)
        if task["draft"]["url"]:
            self._stage(task_id, "opening_webpage")
            self.device.open_webpage(task["draft"]["url"], self.checkpoint)
            if task.get("task_type") == "open_url":
                task.update(state="completed", webpage_verified=True)
                self.store.save(task, "网页已经打开，地址已核对")
                return
            self._stage(task_id, "preparing_draft", webpage_verified=True)

    def _draft(self, task_id):
        task = self._stage(task_id, "preparing_draft")
        self.device.prepare(task["draft"], self.checkpoint)
        self.device.verify_draft(task["draft"], self.checkpoint)
        self._stage(task_id, "awaiting_confirmation", draft_verified=True,
                    agent_decision=getattr(self.device, "agent_decision", None))

    def approve(self, task_id: str, approved_digest: str, confirmed: bool, approval_nonce=None) -> dict:
        with self.lock:
            task = self.store.get(task_id)
            if task["send_attempts"]:
                return task  # Repeated HTTP requests cannot replay a send.
            if not confirmed or task["state"] != "awaiting_confirmation" or approved_digest != task["draft_digest"]:
                raise WorkflowError("请在草稿核对完成后，确认当前版本的收件人和正文")
            if self.worker and self.worker.is_alive():
                raise WorkflowError("草稿准备正在结束，请稍后再确认")
            if approval_nonce != task.get("approval_nonce"):
                raise WorkflowError("本次确认已失效，请重新核对当前草稿")
            task["approval_digest"] = approved_digest
            task["pending_graph_reply"] = {"confirmed": True, "draft_digest": approved_digest, "approval_nonce": approval_nonce}
            task["state"] = "sending"
            self.store.save(task, "已记录你对本次草稿的确认，发送前将再次核对")
            self._launch(self._continue_graph, task_id)
            return task

    def _validate_graph_approval(self, task_id, reply):
        task = self.store.get(task_id)
        if (not reply.get("confirmed") or reply.get("approval_nonce") != task.get("approval_nonce")
                or reply.get("draft_digest") != task["draft_digest"] or task.get("approval_digest") != task["draft_digest"]):
            raise WorkflowError("审批对象已经变化，请重新核对草稿")

    def _send(self, task_id: str):
        task = self.store.get(task_id)
        self.device.check_ready()
        self.device.restore_compose(self.checkpoint)
        try:
            self.device.verify_draft(task["draft"], self.checkpoint)
        except WorkflowError:
            self._stage(task_id, "needs_review", approval_digest=None, draft_verified=False)
            self.device.show_app()
            return
        self.checkpoint()
        with self.lock:
            if self.cancelled:
                raise Cancelled("任务已取消")
            task = self.store.get(task_id)
            if task["approval_digest"] != digest(task["draft"]) or task["send_attempts"]:
                raise WorkflowError("发送批准已失效")
            # Commit BEFORE dispatch. An ambiguous crash is UNKNOWN, never retried.
            task["send_attempts"] = 1
            task["send_started_at"] = now()
            self.store.save(task, "已登记唯一一次发送尝试")
            self.device.send_once(task["draft"])
        self._stage(task_id, "verifying")

    def _verify_result(self, task_id):
        task = self.store.get(task_id)
        verified = self.device.verify_sent(task["draft"], self.checkpoint)
        self._stage(task_id, "completed" if verified else "outcome_unknown", sent_record_verified=bool(verified),
                    verification_detail=getattr(self.device, "last_verification_reason", ""))
        self.device.show_app()

    def _work(self, function, task_id: str):
        try:
            self.device.task_id = task_id
            self.device.agent_models = self.models if self.store.get(task_id).get("agent_mode") else None
            self.current_task_id = task_id
            self.models.task_id = task_id
            function(task_id)
        except Exception as error:
            with self.lock:
                task = self.store.get(task_id)
                task["approval_digest"] = None
                if task.get("delivery_confirmed") or task.get("sent_record_verified"):
                    task["state"] = "completed"
                    task["verification_detail"] = "本次重新核查未完成：" + str(error)[:300]
                    self.store.save(task, "已保留此前确认的邮件结果；本次重新核查未完成")
                else:
                    task["state"] = ("outcome_unknown" if task["send_attempts"] else
                                     "cancelled" if isinstance(error, Cancelled) else "failed")
                    self.store.save(task, MESSAGES[task["state"]] + "：" + str(error)[:300])
            try:
                self.device.show_app()
            except Exception:
                pass

    def control(self, task_id: str, action: str) -> dict:
        with self.condition:
            task = self.store.get(task_id)
            if action == "verify_only":
                if task["state"] not in {"outcome_unknown", "completed"} or task["send_attempts"] != 1:
                    raise WorkflowError("只有发送结果未知的任务可以重新核查")
                if self.worker and self.worker.is_alive():
                    raise WorkflowError("上次操作正在结束，请稍后核查")
                task["state"] = "verifying"
                self.store.save(task, "仅重新核查已发送记录，不会重新发送")
                self._launch(self._verify_only, task_id)
                return task
            if action == "retry_prepare":
                if task["send_attempts"] or task["state"] not in {"failed", "needs_review", "interrupted"}:
                    raise WorkflowError("这个任务不能重新准备；已经尝试发送的邮件不得重发")
                if self.worker and self.worker.is_alive():
                    raise WorkflowError("上次操作正在结束，请稍后重新准备")
                task.update(state="preparing", approval_digest=None, draft_verified=False)
                task.update(graph_run=task.get("graph_run", 1) + 1, approval_nonce=secrets.token_urlsafe(24))
                self.store.save(task, "重新准备并核对草稿，需要再次确认")
                self._launch(self._prepare, task_id)
                return task
            if action == "pause":
                if task["state"] in TERMINAL | {"paused", "interrupted", "needs_review"}:
                    raise WorkflowError("当前任务不能暂停")
                self.paused = True
                task["resume_state"] = task["state"]
                task["state"] = "paused"
            elif action == "resume":
                if task["state"] != "paused":
                    raise WorkflowError("任务没有暂停")
                task["state"] = task.pop("resume_state")
                if task["state"] == "awaiting_qr":
                    task["scan_nonce"] = secrets.token_urlsafe(24)
                self.paused = False
                self.resume_pending = bool(self.worker and self.worker.is_alive())
                self.condition.notify_all()
            elif action == "cancel":
                if task["state"] in TERMINAL:
                    return task
                self.cancelled = True
                self.paused = False
                self.condition.notify_all()
                task.update(state="outcome_unknown" if task["send_attempts"] else "cancelled", approval_digest=None)
            else:
                raise WorkflowError("不支持这个操作")
            return self.store.save(task, MESSAGES[task["state"]])

    def _verify_only(self, task_id: str):
        task = self.store.get(task_id)
        self.device.check_ready()
        verified = self.device.verify_sent(task["draft"], self.checkpoint)
        if task.get("delivery_confirmed") and not verified:
            with self.lock:
                task.update(state="completed", verification_detail=getattr(self.device, "last_verification_reason", ""))
                self.store.save(task, "你已在收件箱人工核对收到；已发送记录暂未加载")
        else:
            self._stage(task_id, "completed" if verified else "outcome_unknown", sent_record_verified=bool(verified),
                        verification_detail=getattr(self.device, "last_verification_reason", ""))
        self.device.show_app()

    def confirm_delivery(self, task_id: str, approved_digest: str, confirmed: bool) -> dict:
        with self.lock:
            task = self.store.get(task_id)
            if (not confirmed or approved_digest != task["draft_digest"] or task["send_attempts"] != 1
                    or task["state"] not in {"completed", "outcome_unknown"}):
                raise WorkflowError("请仅在收件箱中核对这封邮件后确认收到")
            task.update(state="completed", delivery_confirmed=True, delivery_evidence="recipient_user_confirmation")
            return self.store.save(task, "你已在收件箱确认收到这封邮件（人工核对）")

    def latest(self) -> dict | None:
        tasks = self.store.all()
        return tasks[0] if tasks else None

    def create_goal(self, instruction, request_key, assistance_mode="silverguide", scenario_id=None, third_party_assistance=None):
        instruction = instruction.strip()
        if not instruction or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_key):
            raise WorkflowError("请说出或填写需要完成的任务")
        with self.lock:
            for old in self.store.all():
                if old["request_key"] == request_key:
                    if old.get("instruction") != instruction:
                        raise WorkflowError("同一请求编号不能对应不同需求")
                    return old
            if self.active() or self.worker and self.worker.is_alive():
                raise WorkflowError("请先完成或取消当前任务")
            draft = {"recipient": "", "subject": "网页分享", "body": "", "url": ""}
            task = {"id": uuid.uuid4().hex, "request_key": request_key, "instruction": instruction,
                    "draft": draft, "source": "text", "state": "understanding", "created_at": now(),
                    "send_attempts": 0, "approval_digest": None, "draft_digest": digest(draft),
                    "verification_marker": "[SilverGuide:" + hashlib.sha256(request_key.encode()).hexdigest()[:12] + "]",
                    "webpage_verified": False, "draft_verified": False, "sent_record_verified": False,
                    "delivery_confirmed": False, "graph_run": 1, "agent_mode": True,
                    "assistance_mode": assistance_mode, "scenario_id": scenario_id, "third_party_assistance": third_party_assistance,
                    "environment": "real_device" if self.device.__class__.__module__.startswith("device.") else "test_double",
                    "approval_nonce": secrets.token_urlsafe(24), "scan_nonce": secrets.token_urlsafe(24)}
            self.store.save(task, MESSAGES["understanding"])
            self._launch(self._prepare, task["id"])
            return task

    def _understand(self, task_id):
        task = self._stage(task_id, "understanding")
        intent = self.models.understand(task["instruction"], [c["alias"] for c in self.contacts.all()])
        self.checkpoint()
        task = self.store.get(task_id)
        if intent.task_type == "unsupported":
            raise WorkflowError("目前支持扫码、打开网页和邮件分享，请换一个相关需求")
        instruction = task["instruction"]
        emails = re.findall(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", instruction)
        alias = intent.recipient_alias if intent.recipient_alias and intent.recipient_alias in instruction else ""
        recipient = next((a for a in emails if a.casefold() == intent.recipient_address.casefold()), "")
        if not recipient and len(emails) == 1:
            recipient = emails[0]
        mentioned_aliases = [c["alias"] for c in self.contacts.all() if c["alias"] in instruction]
        ambiguous = len({a.casefold() for a in emails}) > 1 or (not emails and len(mentioned_aliases) > 1)
        recipient = "" if ambiguous else recipient or self.contacts.resolve(alias)
        if ambiguous:
            alias = ""
        urls = re.findall(r"https://[^\s，。；]+", instruction)
        url = validate_url(urls[0]) if len(urls) == 1 else ""
        task.update(task_type=intent.task_type, recipient_alias=alias, recipient_ambiguous=ambiguous, parser="llm",
                    request_qr=intent.task_type == "scan_and_share" or any(w in instruction for w in ("扫码", "二维码", "扫描")),
                    plan=(["理解需求", "识别二维码或网址", "核对网页"] if intent.task_type == "open_url" else
                          ["理解需求", "澄清收件人", "识别二维码或网址", "核对网页", "准备草稿", "用户确认", "单次发送并核验"]),
                    draft={"recipient": recipient, "subject": intent.subject or "网页分享", "body": intent.body, "url": url},
                    source="manual" if url else "text")
        self.store.save(task, "已理解需求，将先核对信息，再准备网页或邮件")

    def _missing(self, task):
        missing = []
        if task.get("task_type") != "open_url" and not task["draft"]["recipient"]:
            missing.append("recipient")
        if task.get("task_type") in {"share_url", "open_url"} and not task.get("request_qr") and not task["draft"]["url"]:
            missing.append("url")
        return missing

    def _question(self, task_id):
        task = self._stage(task_id, "needs_clarification")
        fields = self._missing(task)
        task["clarification_fields"] = fields
        alias = task.get("recipient_alias", "")
        message = ((f"请填写‘{alias}’对应的完整邮箱。" if alias else "请填写收件人的完整邮箱。")
                   if "recipient" in fields else "请填写需要打开或分享的完整 HTTPS 网址。")
        if "recipient" in fields and task.get("recipient_ambiguous"):
            message = "需求中包含多个收件人，请明确选择本次唯一收件人的完整邮箱。"
        self.store.save(task, message)

    def _apply_clarification(self, task_id, reply):
        from api.schemas import HumanReply
        answer = HumanReply.model_validate(reply)
        task = self.store.get(task_id)
        address = answer.recipient.strip() or self.contacts.resolve(answer.alias)
        if address:
            task["draft"]["recipient"] = make_draft(address, "核对", "test")["recipient"]
        if answer.save_contact and address and (answer.alias or task.get("recipient_alias")):
            self.contacts.put(answer.alias or task["recipient_alias"], address)
        for field in ("subject", "body", "url"):
            value = getattr(answer, field)
            if value is not None:
                task["draft"][field] = validate_url(value) if field == "url" and value else value
        self.store.save(task, "已记录您补充的信息")

    def _apply_qr(self, task_id, reply):
        task = self.store.get(task_id)
        if reply.get("scan_nonce") != task["scan_nonce"]:
            raise WorkflowError("扫码结果已过期，请重新扫描")
        task["draft"]["url"] = validate_url(reply["url"])
        task.update(source="qr", qr_received=True)
        self.store.save(task, "二维码已识别，接下来打开并核对网页")

    def _human_resume(self, task_id, expected, reply):
        with self.lock:
            task = self.store.get(task_id)
            if task["state"] != expected:
                if task.get("last_human_reply") == reply:
                    return task
                raise WorkflowError("任务状态已变化，请查看当前步骤")
            if self.worker and self.worker.is_alive():
                raise WorkflowError("上一步正在结束，请稍后再试")
            task.update(pending_graph_reply=reply, last_human_reply=reply)
            self.store.save(task, "已收到您的输入，正在继续任务")
            self._launch(self._continue_graph, task_id)
            return task

    def clarify(self, task_id, reply):
        if reply.get("recipient", "").strip():
            make_draft(reply["recipient"], "核对", "test")
        if reply.get("url"):
            validate_url(reply["url"])
        return self._human_resume(task_id, "needs_clarification", reply)

    def submit_qr(self, task_id, url, scan_nonce):
        task = self.store.get(task_id)
        if scan_nonce != task.get("scan_nonce"):
            raise WorkflowError("扫码结果已过期，请重试")
        return self._human_resume(task_id, "awaiting_qr", {"url": validate_url(url), "scan_nonce": scan_nonce})

    def edit(self, task_id, changes):
        with self.lock:
            task = self.store.get(task_id)
            if task["send_attempts"] or task["state"] not in {"awaiting_confirmation", "needs_review", "failed", "interrupted"}:
                raise WorkflowError("当前任务不能修改草稿，已尝试发送的邮件不能重发")
            if self.worker and self.worker.is_alive():
                raise WorkflowError("上一步正在结束，请稍后修改")
            raw = dict(task["draft"])
            raw["body"] = raw["body"].removesuffix("\n\n" + task["verification_marker"])
            raw.update({k: v for k, v in changes.items() if k in raw and v is not None})
            draft = make_draft(raw["recipient"], raw["subject"], raw["body"], raw["url"])
            task["original_digest"] = digest(draft)
            draft["body"] += "\n\n" + task["verification_marker"]
            task.update(draft=draft, draft_digest=digest(draft), approval_digest=None,
                        approval_nonce=secrets.token_urlsafe(24), graph_run=task.get("graph_run", 1) + 1,
                        intake_complete=True, draft_verified=False, state="preparing")
            self.store.save(task, "已更新草稿，重新核对后需要再次确认")
            self._launch(self._prepare, task_id)
            return task

    def close(self):
        self.graph_db.close()


_engine = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            from huawei_mail import HuaweiDevice
            from device.mobilerun import MobilerunTransport
            _engine = Engine(Store(Path(__file__).resolve().parents[2] / ".run" / "workflows"), HuaweiDevice(transport=None if os.environ.get("SILVERGUIDE_DEVICE_BACKEND") == "adb" else MobilerunTransport()))
    return _engine
