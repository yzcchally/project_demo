from __future__ import annotations
import hashlib
import json
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
        self.transit_device = None
        self.contacts = Contacts(store)
        self.models = interpreter or ModelGateway(store.directory / "model-calls")
        from agent.graph import build_graph
        self.graph = build_graph(self)
        # A restart must never replay a possible external send.
        for task in store.all():
            if task["state"] not in TERMINAL:
                task["state"] = "outcome_unknown" if task.get("send_attempts", 0) else "interrupted"
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
            current = self.store.get(self.current_task_id) if restore and self.current_task_id else {}
            stage = current.get("state", "")
        if restore and current.get("task_type") == "plan_transit":
            self.device.context_epoch = getattr(self.device, "context_epoch", 0) + 1
        elif restore and hasattr(self.device, "resume_context"):
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
            if state == "needs_clarification" and task.get("task_type") == "plan_transit":
                message = "请补充出行信息"
            if state == "awaiting_qr" and task.get("task_type") == "open_url":
                message = "请将镜头对准二维码，小银会继续打开网页"
            return self.store.save(task, message)

    def _return_to_app(self, task_id):
        task = self.store.get(task_id)
        if task.get("task_type") == "plan_transit":
            self._transit_checkpoint(task_id, task["query_id"])()
            if task["state"] == "cancelled":
                return
        self.device.show_app()

    def _prepare(self, task_id: str):
        self.graph.invoke({"task_id": task_id}, self._graph_config(task_id))
        current = self.store.get(task_id)
        if current["state"] != "cancelled" and (current.get("task_type") == "plan_transit" or current["state"] not in {"awaiting_qr", "needs_clarification"}):
            self._return_to_app(task_id)

    def _graph_config(self, task_id):
        return {"configurable": {"thread_id": f"{task_id}:{self.store.get(task_id).get('graph_run', 1)}"}, "recursion_limit": 40}

    def _continue_graph(self, task_id):
        self.graph.invoke(Command(resume=self.store.get(task_id)["pending_graph_reply"]), self._graph_config(task_id))
        current = self.store.get(task_id)
        if current["state"] != "cancelled" and (current.get("task_type") == "plan_transit" or current["state"] not in {"awaiting_qr", "needs_clarification"}):
            self._return_to_app(task_id)

    def _precheck(self, task_id):
        task = self.store.get(task_id)
        if task.get("task_type") == "plan_transit":
            return self._transit_precheck(task_id)
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
        if task.get("demo_fault") and self.store.get(task_id).get("demo_fault_status") != "observed":
            raise WorkflowError("恢复演示没有实际观察到页面中断，请重新选择演示案例后测试")
        self._stage(task_id, "awaiting_confirmation", draft_verified=True,
                    agent_decision=getattr(self.device, "agent_decision", None))

    def approve(self, task_id: str, approved_digest: str, confirmed: bool, approval_nonce=None) -> dict:
        with self.lock:
            task = self.store.get(task_id)
            self._require_mail(task)
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
            from device.demo import task_injection
            self.device.task_id = task_id
            task = self.store.get(task_id)
            self.device.agent_models = self.models if task.get("agent_mode") else None
            self.device.recovery_callback = lambda event: self._device_recovery(task_id, event)
            self.current_task_id = task_id
            self.models.task_id = task_id
            work_query_id = task.get("query_id")
            with task_injection(self.device, task,
                                before_inject=lambda event: self._demo_fault_registered(task_id, event),
                                after_observed=lambda event: self._demo_fault_observed(task_id, event)):
                function(task_id)
        except Exception as error:
            with self.lock:
                task = self.store.get(task_id)
                if task.get("task_type") == "plan_transit":
                    if locals().get("work_query_id") is not None and task.get("query_id") != work_query_id:
                        return
                    if task["state"] == "cancelled" or self.cancelled or isinstance(error, Cancelled):
                        task.update(state="cancelled", cancel_worker_finished_at=now())
                        if task.get("cancel_requested_at"):
                            task["cancel_delay_seconds"] = max(0.0, (datetime.fromisoformat(task["cancel_worker_finished_at"]) - datetime.fromisoformat(task["cancel_requested_at"])).total_seconds())
                        self.store.save(task, "路线任务已取消；进行中的调用已返回，后续操作已停止")
                    elif task.get("transit_result") and task.get("route_verified"):
                        task["state"] = "completed"
                        self.store.save(task, "已保留此前核验的路线；本次查看未完成：" + str(error)[:240])
                    else:
                        task.update(state="failed", route_verified=False)
                        self.store.save(task, "路线查询未完成：" + str(error)[:300])
                    if not self.cancelled and task["state"] != "cancelled":
                        try:
                            self._transit_checkpoint(task_id, task["query_id"])()
                            self.device.show_app()
                        except Exception:
                            pass
                    return
                task["approval_digest"] = None
                if task.get("delivery_confirmed") or task.get("sent_record_verified"):
                    task["state"] = "completed"
                    task["verification_detail"] = "本次重新核查未完成：" + str(error)[:300]
                    self.store.save(task, "已保留此前确认的邮件结果；本次重新核查未完成")
                else:
                    task["state"] = ("outcome_unknown" if task["send_attempts"] else
                                     "cancelled" if isinstance(error, Cancelled) else "failed")
                    self.store.save(task, MESSAGES[task["state"]] + "：" + str(error)[:300])
            if not isinstance(error, Cancelled) and not self.cancelled:
                try:
                    self.device.show_app()
                except Exception:
                    pass

    def _demo_fault_registered(self, task_id, event):
        with self.condition:
            task = self.store.get(task_id)
            if task.get("demo_fault_injected"):
                return False
            if self.cancelled or task["state"] in TERMINAL | {"interrupted"}:
                raise Cancelled("任务已取消，中断演示未执行")
            if task["send_attempts"] or task["state"] != "preparing_draft":
                raise WorkflowError("仅可在草稿准备阶段执行中断演示")
            task.update(demo_fault_injected=True, demo_fault_status="registered", demo_fault_event=dict(event))
            self.store.save(task, "恢复演示：已登记本次页面中断，正在切到桌面")
            return True

    def _demo_fault_observed(self, task_id, event):
        with self.condition:
            task = self.store.get(task_id)
            task.update(demo_fault_status="observed", demo_fault_event=dict(event))
            if task["state"] not in TERMINAL | {"interrupted"}:
                self.store.save(task, "恢复演示：已观察到离开写信页，正在恢复原草稿")

    def _device_recovery(self, task_id, event):
        """Publish observed recovery steps without changing approval or effect state."""
        with self.condition:
            task = self.store.get(task_id)
            if self.cancelled or task["state"] in TERMINAL | {"interrupted", "paused"}:
                return
            if event["outcome"] == "attempting":
                task["recovery_count"] = task.get("recovery_count", 0) + 1
            task["last_recovery"] = dict(event)
            task["recovery_events"] = (task.get("recovery_events", []) + [dict(event)])[-20:]
            self.store.save(task, event["message"])

    def control(self, task_id: str, action: str) -> dict:
        with self.condition:
            task = self.store.get(task_id)
            if task.get("task_type") == "plan_transit" and action in {"verify_only", "retry_prepare"}:
                raise WorkflowError("路线任务请使用重新查询，不使用邮件核验或草稿准备接口")
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
                owns_worker = not (self.worker and self.worker.is_alive()) or self.current_task_id == task_id
                if owns_worker:
                    self.cancelled = True
                    self.paused = False
                    self.condition.notify_all()
                task.update(state="outcome_unknown" if task["send_attempts"] else "cancelled", approval_digest=None)
                if task.get("task_type") == "plan_transit":
                    task.update(cancel_requested_at=now(), place_choice=None)
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
            self._require_mail(task)
            if (not confirmed or approved_digest != task["draft_digest"] or task["send_attempts"] != 1
                    or task["state"] not in {"completed", "outcome_unknown"}):
                raise WorkflowError("请仅在收件箱中核对这封邮件后确认收到")
            task.update(state="completed", delivery_confirmed=True, delivery_evidence="recipient_user_confirmation")
            return self.store.save(task, "你已在收件箱确认收到这封邮件（人工核对）")

    def latest(self) -> dict | None:
        tasks = self.store.all()
        return tasks[0] if tasks else None

    def create_goal(self, instruction, request_key, assistance_mode="silverguide", scenario_id=None, third_party_assistance=None,
                    demo_case_id=None, demo_fault=None):
        from api.schemas import validate_demo_request
        try:
            validate_demo_request(demo_case_id, demo_fault)
        except ValueError as error:
            raise WorkflowError(str(error)) from None
        instruction = instruction.strip()
        if not instruction or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_key):
            raise WorkflowError("请说出或填写需要完成的任务")
        with self.lock:
            for old in self.store.all():
                if old["request_key"] == request_key:
                    if (old.get("instruction") != instruction or old.get("demo_case_id") != demo_case_id
                            or old.get("demo_fault") != demo_fault):
                        raise WorkflowError("同一请求编号不能对应不同需求或演示配置")
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
                    "demo_case_id": demo_case_id, "demo_fault": demo_fault,
                    "demo_fault_injected": False,
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
            raise WorkflowError("目前支持扫码、网页、邮件分享和公交地铁路线，请换一个相关需求")
        if task.get("demo_fault") and intent.task_type == "open_url":
            raise WorkflowError("恢复演示需要邮件草稿场景，请使用内置恢复案例")
        if intent.task_type == "plan_transit":
            if task.get("demo_fault"):
                raise WorkflowError("写信恢复演示不能用于路线任务")
            from api.schemas import TransitRequest
            request = (intent.transit or TransitRequest()).model_dump()
            instruction = task["instruction"]
            for field in ("origin", "destination", "city"):
                if request[field] and request[field] not in instruction:
                    request[field] = ""
            explicit_from = bool(re.search(r"从.+?(?:去|到|前往)", instruction))
            if request["origin"] in {"当前位置", "我的位置", "这里", "现在的位置"}:
                request["origin"] = ""
                request["use_current_location"] = True
            if any(w in instruction for w in ("少换乘", "少转车", "少转乘")):
                request["preference"] = "fewer_transfers"
            elif any(w in instruction for w in ("少步行", "步行更少", "少走路", "少走一点")):
                request["preference"] = "less_walking"
            else:
                request["preference"] = "recommended"
            if not request["origin"] and not explicit_from:
                request["use_current_location"] = True
            for field in ("origin", "destination"):
                if request[field] in {"家", "家里", "我家", "家中"}:
                    request[field] = ""
                    if field == "origin": request["use_current_location"] = False
            if explicit_from and not request["origin"] and not any(w in instruction for w in ("当前位置", "我的位置", "现在的位置")):
                request["use_current_location"] = False
            request = TransitRequest.model_validate(request).model_dump()
            task.update(task_type="plan_transit", transit_request=request, transit_original_request=dict(request),
                        transit_place_selections=[], query_id=uuid.uuid4().hex,
                        query_started_at=now(), transit_result=None, route_verified=False,
                        transit_choices={}, place_choice=None, parser="llm", request_qr=False,
                        intake_complete=True, source="amap_gui",
                        plan=["理解出行需求", "核对起终点和城市", "高德查询公交地铁", "展开并核对各段", "返回小银分步讲解"])
            # Route tasks have an independent contract, not a fabricated mail draft.
            for field in ("draft", "draft_digest", "approval_nonce", "scan_nonce", "verification_marker"):
                task.pop(field, None)
            self.store.save(task, "已理解公交地铁需求，接下来核对地点并打开高德")
            return
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
        if task.get("task_type") == "plan_transit":
            request = task["transit_request"]
            missing = list(task.get("transit_missing_fields", []))
            if not request.get("destination"): missing.append("destination")
            if not request.get("origin") and not request.get("use_current_location"): missing.append("origin")
            if request.get("origin") and not request.get("city"): missing.append("city")
            return list(dict.fromkeys(missing))
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
        if task.get("task_type") == "plan_transit":
            labels = {"origin": "明确出发地", "destination": "同城目的地", "city": "所在城市"}
            self.store.save(task, task.get("transit_clarification_message") or "请补充" + "、".join(labels[x] for x in fields) + "；小银不会猜测地点或当前位置。")
            return
        alias = task.get("recipient_alias", "")
        message = ((f"请填写‘{alias}’对应的完整邮箱。" if alias else "请填写收件人的完整邮箱。")
                   if "recipient" in fields else "请填写需要打开或分享的完整 HTTPS 网址。")
        if "recipient" in fields and task.get("recipient_ambiguous"):
            message = "需求中包含多个收件人，请明确选择本次唯一收件人的完整邮箱。"
        self.store.save(task, message)

    def _apply_clarification(self, task_id, reply):
        if self.store.get(task_id).get("task_type") == "plan_transit":
            return self._apply_transit_clarification(task_id, reply)
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
        scan_source = reply.get("scan_source", "in_app_camera")
        external = scan_source == "external_share"
        task.update(source="manual" if external else "qr", qr_received=True, scan_source=scan_source)
        self.store.save(task, "已接收您确认的分享链接，接下来打开并核对网页" if external
                        else "二维码已识别，接下来打开并核对网页")

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
        self._require_mail(self.store.get(task_id))
        if reply.get("recipient", "").strip():
            make_draft(reply["recipient"], "核对", "test")
        if reply.get("url"):
            validate_url(reply["url"])
        return self._human_resume(task_id, "needs_clarification", reply)

    def submit_qr(self, task_id, url, scan_nonce, scan_source="in_app_camera"):
        task = self.store.get(task_id)
        self._require_mail(task)
        if scan_nonce != task.get("scan_nonce"):
            raise WorkflowError("扫码结果已过期，请重试")
        if scan_source not in {"in_app_camera", "external_share"}:
            raise WorkflowError("扫码结果来源无效")
        reply = {"url": validate_url(url), "scan_nonce": scan_nonce}
        if scan_source == "external_share":
            reply["scan_source"] = scan_source
        return self._human_resume(task_id, "awaiting_qr", reply)

    def edit(self, task_id, changes):
        with self.lock:
            task = self.store.get(task_id)
            self._require_mail(task)
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

    @staticmethod
    def _require_mail(task):
        if task.get("task_type") == "plan_transit":
            raise WorkflowError("这是路线任务，请使用路线查询和讲解接口")

    def _transit_task(self, task_id, query_id):
        task = self.store.get(task_id)
        if task.get("task_type") != "plan_transit":
            raise WorkflowError("只有路线任务支持这个操作")
        if not query_id or task.get("query_id") != query_id:
            raise WorkflowError("路线查询已经更新，请使用当前查询结果")
        if task["state"] == "cancelled":
            raise WorkflowError("路线任务已取消，请准备新任务")
        return task

    def _transit_checkpoint(self, task_id, query_id):
        def check():
            self.checkpoint()
            with self.condition:
                task = self.store.get(task_id)
                if (task.get("query_id") != query_id or task["state"] == "cancelled"
                        or self.current_task_id != task_id):
                    raise Cancelled("路线操作已取消或过期")
        return check

    def _transit_adapter(self):
        if self.transit_device is None:
            from device.amap import AmapDevice
            self.transit_device = AmapDevice(self.device, self.models)
        return self.transit_device

    def _transit_precheck(self, task_id):
        task = self.store.get(task_id)
        check = self._transit_checkpoint(task_id, task["query_id"])
        check()
        self._transit_adapter().check_ready(check)
        check()

    def _query_transit(self, task_id):
        from transit.validation import validate_result
        from transit.explain import explain_result
        task = self.store.get(task_id)
        query_id = task["query_id"]
        check = self._transit_checkpoint(task_id, query_id)
        self._stage(task_id, "planning_transit")
        adapter = self._transit_adapter()
        adapter.progress_callback = lambda stage, message: self._transit_progress(task_id, query_id, stage, message)
        outcome = adapter.query(dict(task["transit_request"]), task_id, query_id, check,
                                choices=dict(task.get("transit_choices", {})))
        check()  # A model/system call returning after cancellation cannot commit results.
        with self.condition:
            task = self._transit_task(task_id, query_id)
            if outcome.get("kind") == "clarification":
                fields = outcome.get("fields", ["origin"])
                if not fields or any(f not in {"origin", "destination", "city"} for f in fields):
                    raise WorkflowError("高德地点核对未返回可处理的澄清字段")
                task.update(state="needs_clarification", transit_missing_fields=fields,
                            transit_clarification_message=str(outcome.get("message", "请补充明确出发地和城市"))[:500])
                self.store.save(task, task["transit_clarification_message"])
                return "transit_clarify"
            if outcome.get("kind") == "choice":
                role = outcome.get("role")
                candidates = outcome.get("candidates", [])
                if role not in {"origin", "destination"} or not 1 <= len(candidates) <= 20:
                    raise WorkflowError("未取得可核对的地点候选，请补充更完整的地点名称")
                bound = []
                for candidate in candidates:
                    if not isinstance(candidate, dict) or not candidate.get("name"):
                        raise WorkflowError("地点候选缺少真实名称")
                    value = dict(candidate)
                    if value.get("source") == "current_location":
                        self._validate_location_choice(task, query_id, role, value)
                    value["candidate_id"] = value.get("candidate_id") or hashlib.sha256(
                        (query_id + role + str(value.get("name")) + str(value.get("address"))).encode()).hexdigest()[:24]
                    bound.append(value)
                if len({v["candidate_id"] for v in bound}) != len(bound):
                    raise WorkflowError("地点候选标识重复，请重新查询")
                task.update(state="awaiting_place_choice", place_choice={"query_id": query_id,
                    "choice_nonce": secrets.token_urlsafe(24), "role": role, "candidates": bound})
                provided_message = outcome.get("message")
                message = (re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", provided_message).strip()[:500]
                           if isinstance(provided_message, str) else "")
                self.store.save(task, message or "高德返回的地点需要确认，请核对名称和地址后选择本次" + ("起点" if role == "origin" else "终点"))
                return "transit_choice"
            if outcome.get("kind") != "result":
                raise WorkflowError("高德未返回可核对的路线结果")
            expected = dict(task["transit_request"])
            for role, selected in task.get("transit_choices", {}).items():
                if role == "origin" and selected.get("source") == "current_location":
                    self._validate_location_choice(task, query_id, role, selected)
                    # Keep the original location mode so the independent
                    # validator still requires real location-page evidence.
                    continue
                expected[role] = selected["name"]
            result = validate_result(outcome["result"], task_id, query_id, request=expected,
                                     evidence=outcome.get("evidence", []),
                                     collection_complete=bool(outcome.get("collection_complete")),
                                     selected_places=task.get("transit_choices", {}))
            check()
            task = self._transit_task(task_id, query_id)
            classification = result["classification"]
            state = {"COMPLETE": "completed", "PARTIAL": "partial", "NO_ROUTE": "no_route", "FAILED": "failed"}[classification]
            task.update(state=state, transit_result=result, route_verified=bool(result["route_verified"]),
                        transit_explanation=explain_result(result), place_choice=None, query_finished_at=now(),
                        transit_missing_fields=[], transit_clarification_message="")
            self.store.save(task, {"completed": "高德路线已核对，已保留分步路线供您查看和朗读",
                "partial": "路线信息只读取了一部分，请查看缺失提示并在高德核对",
                "no_route": "高德当前没有可用的公交地铁方案，请调整起终点或稍后再查",
                "failed": "高德路线查询未完成，请查看原因后重新查询"}[state])
        return "done"

    def _transit_progress(self, task_id, query_id, stage, message):
        """Publish only this worker's current query; progress cannot finalize it."""
        if stage not in {"planning_transit", "reading_transit", "validating_transit"}:
            raise WorkflowError("高德进度不能改变任务的完成或人工决定状态")
        self._transit_checkpoint(task_id, query_id)()
        with self.condition:
            task = self.store.get(task_id)
            if (self.current_task_id != task_id or task.get("query_id") != query_id
                    or task["state"] in TERMINAL | {"paused", "interrupted", "awaiting_place_choice", "needs_clarification"}):
                return False
            task["state"] = stage
            self.store.save(task, str(message)[:500])
            return True

    def _apply_transit_clarification(self, task_id, reply):
        from api.schemas import TransitClarification, TransitRequest
        answer = TransitClarification.model_validate(reply)
        self._transit_checkpoint(task_id, answer.query_id)()
        with self.condition:
            task = self._transit_task(task_id, answer.query_id)
            original = dict(task["transit_request"])
            request = dict(original)
            for field, value in answer.model_dump(exclude_none=True).items():
                if field != "query_id": request[field] = value
            for role in ("origin", "destination"):
                if request.get(role) in {"家", "家里", "我家", "家中"}:
                    raise WorkflowError("‘家里’尚未绑定地址，请填写明确地点")
            request = TransitRequest.model_validate(request).model_dump()
            choices = dict(task.get("transit_choices", {}))
            if request["city"] != original["city"]:
                choices.clear()
            else:
                for role in ("origin", "destination"):
                    if request[role] != original[role]:
                        choices.pop(role, None)
                if request["use_current_location"] != original["use_current_location"]:
                    choices.pop("origin", None)
            task.update(transit_request=request, transit_missing_fields=[], transit_clarification_message="",
                        transit_choices=choices, place_choice=None)
            self.store.save(task, "已记录您补充的出行信息；未改动地点的已确认名称和地址继续保留")

    def clarify_transit(self, task_id, reply):
        from api.schemas import TransitClarification
        answer = TransitClarification.model_validate(reply)
        with self.lock:
            task = self._transit_task(task_id, answer.query_id)
            if task["state"] != "needs_clarification":
                raise WorkflowError("当前路线不在等待补充信息")
            if not answer.model_dump(exclude_none=True).keys() - {"query_id"}:
                raise WorkflowError("请至少补充一项地点信息")
            if answer.origin in {"家", "家里", "我家", "家中"}:
                raise WorkflowError("请填写明确出发地点，不能使用尚未绑定地址的‘家里’")
            return self._human_resume(task_id, "needs_clarification", answer.model_dump(exclude_none=True))

    @staticmethod
    def _validate_location_choice(task, query_id, role, chosen):
        request = task["transit_request"]
        if (role != "origin" or request.get("origin") or not request.get("use_current_location")
                or chosen.get("query_id") != query_id):
            raise WorkflowError("当前位置候选已过期或不能覆盖本次明确起点")
        if (chosen.get("name") in {"", "我的位置", "当前位置", "这里", "现在的位置"}
                or not all(str(chosen.get(field) or "").strip() for field in ("name", "address", "city"))
                or not all(chosen.get("evidence_refs", {}).get(field) for field in ("name", "address", "city"))):
            raise WorkflowError("当前位置名称、地址或城市尚未从高德核对，请填写明确起点")

    def _apply_place_choice(self, task_id, reply):
        from api.schemas import TransitPlaceChoice, TransitRequest
        answer = TransitPlaceChoice.model_validate(reply)
        self._transit_checkpoint(task_id, answer.query_id)()
        with self.condition:
            task = self._transit_task(task_id, answer.query_id)
            selection = task.get("place_choice") or {}
            if (task["state"] != "awaiting_place_choice"
                    or answer.choice_nonce != selection.get("choice_nonce")
                    or answer.query_id != selection.get("query_id")):
                raise WorkflowError("地点选择已过期，请重新核对当前候选")
            chosen = next((v for v in selection.get("candidates", []) if v["candidate_id"] == answer.candidate_id), None)
            if chosen is None:
                raise WorkflowError("地点选择不属于当前候选集合")
            role = selection["role"]
            original = dict(task["transit_request"])
            request = dict(original)
            # Only a current, explicit human choice may replace an input alias
            # with the actual observed POI name. Keep the original instruction
            # and parsed request; the validator still checks selected addresses.
            if chosen.get("source") == "current_location":
                self._validate_location_choice(task, answer.query_id, role, chosen)
                # Confirm the observed position without converting it into an
                # explicitly typed place or disabling location proof checks.
            else:
                request[role] = chosen["name"]
            request = TransitRequest.model_validate(request).model_dump()
            task.setdefault("transit_original_request", original)
            task.setdefault("transit_place_selections", []).append({
                "query_id": answer.query_id, "role": role, "requested_name": original[role],
                "selected": dict(chosen), "selected_at": now(),
                "choice_nonce_digest": hashlib.sha256(answer.choice_nonce.encode()).hexdigest(),
                "candidate_set_digest": hashlib.sha256(json.dumps(selection["candidates"], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                "offered_candidate_ids": [candidate["candidate_id"] for candidate in selection["candidates"]],
            })
            task.setdefault("transit_choices", {})[role] = dict(chosen)
            task.update(transit_request=request, place_choice=None, state="planning_transit")
            self.store.save(task, "已按您的选择确认实际地点名称，将在高德重新读取并核对名称与地址")

    def choose_transit_place(self, task_id, reply):
        from api.schemas import TransitPlaceChoice
        answer = TransitPlaceChoice.model_validate(reply)
        with self.lock:
            task = self._transit_task(task_id, answer.query_id)
            selection = task.get("place_choice") or {}
            if (task["state"] != "awaiting_place_choice" or selection.get("query_id") != answer.query_id
                    or selection.get("choice_nonce") != answer.choice_nonce
                    or not any(v["candidate_id"] == answer.candidate_id for v in selection.get("candidates", []))):
                raise WorkflowError("地点选择已过期或不属于本次候选")
            return self._human_resume(task_id, "awaiting_place_choice", answer.model_dump())

    def requery_transit(self, task_id, reply):
        from api.schemas import TransitRequery, TransitRequest
        answer = TransitRequery.model_validate(reply)
        with self.condition:
            task = self._transit_task(task_id, answer.query_id)
            if task["state"] not in TERMINAL | {"interrupted"}:
                raise WorkflowError("请先完成或取消当前查询，再开始新的路线查询")
            if self.worker and self.worker.is_alive() or any(t["id"] != task_id and t["state"] not in TERMINAL | {"interrupted"} for t in self.store.all()):
                raise WorkflowError("手机正由其他任务使用，请稍后重新查询")
            if task.get("transit_result"):
                task.setdefault("transit_history", []).append({"query_id": task["query_id"], "result": task["transit_result"]})
            request = dict(task["transit_request"])
            if answer.preference is not None: request["preference"] = answer.preference
            task.update(query_id=uuid.uuid4().hex, query_started_at=now(), transit_request=TransitRequest.model_validate(request).model_dump(),
                        state="planning_transit", route_verified=False, transit_result=None, transit_explanation="",
                        transit_choices={}, place_choice=None, transit_missing_fields=[], transit_clarification_message="",
                        graph_run=task.get("graph_run", 1) + 1, intake_complete=True)
            self.store.save(task, "开始新的高德查询；旧结果来源已保留，新结果尚未核对")
            self._launch(self._prepare, task_id)
            return task

    def ask_transit(self, task_id, query_id, question):
        from transit.explain import answer_question
        with self.lock:
            task = self._transit_task(task_id, query_id)
            if not task.get("route_verified") or not task.get("transit_result"):
                raise WorkflowError("当前没有完整核验的路线，请先查询；缺失信息不能用于编造路线回答")
            answer = answer_question(task["transit_result"], question)
            return {"ok": True, "task_id": task_id, "query_id": query_id, "answer": answer}

    def show_transit(self, task_id, query_id):
        with self.condition:
            task = self._transit_task(task_id, query_id)
            if task["state"] not in {"completed", "partial", "no_route", "failed"}:
                raise WorkflowError("请在本次路线查询结束后查看高德详情")
            if not task.get("transit_result"):
                raise WorkflowError("当前没有可查看的高德路线结果")
            if self.worker and self.worker.is_alive() or any(t["id"] != task_id and t["state"] not in TERMINAL | {"interrupted"} for t in self.store.all()):
                raise WorkflowError("手机正由其他任务使用，请稍后查看")
            task.update(state="viewing_transit", transit_return_state=task["state"])
            self.store.save(task, "正在回到高德查看本次路线；不启动行中导航")
            self._launch(self._show_transit, task_id)
            return task

    def _show_transit(self, task_id):
        task = self.store.get(task_id)
        check = self._transit_checkpoint(task_id, task["query_id"])
        adapter = self._transit_adapter()
        if not hasattr(adapter, "show_details"):
            raise WorkflowError("当前高德版本不支持自动返回本次详情，请在高德核对后重新查询")
        adapter.show_details(task["transit_result"], check)
        check()
        with self.condition:
            task = self._transit_task(task_id, task["query_id"])
            task["state"] = task.pop("transit_return_state")
            self.store.save(task, "已回到高德，请核对本次起终点和路线；未启动导航")

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
