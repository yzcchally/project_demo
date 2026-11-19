"""Return through named workflow operations while preserving external action records."""

from domain import TERMINAL, WorkflowError


DRAFT_STEPS = {"preparing_draft", "awaiting_confirmation", "needs_review"}
DRAFT_STATES = {"awaiting_confirmation", "needs_review", "failed", "interrupted"}
ROUTE_QUERY_STEPS = {"planning_transit", "reading_transit", "validating_transit"}
ROUTE_SHOW_STATES = {"completed", "partial", "no_route", "failed"}
INPUT_STATES = {"needs_clarification", "awaiting_qr", "awaiting_place_choice"}


def _action(task: dict, step: dict) -> dict | None:
    state = task["state"]
    step_state = step["state"]
    if (state == step_state and state in INPUT_STATES
            and step.get("query_id") == task.get("query_id")):
        return {"kind": "open_input", "label": "返回填写这一步",
                "description": "打开当前正在等待您填写或选择的步骤。"}
    if task.get("task_type") == "plan_transit":
        if not step.get("query_id") or step["query_id"] != task.get("query_id"):
            return None
        if task.get("transit_navigation_attempts"):
            return None
        if task.get("dingtalk_send_attempts"):
            if (step.get("dingtalk_send_attempts") == 1 and state == "outcome_unknown"
                    and task.get("dingtalk_status") == "unknown"):
                return {"kind": "verify_dingtalk", "label": "核对钉钉发送结果",
                        "description": "只核对钉钉本人文件助手里的记录，不会再次发送。"}
            return None
        if step_state in ROUTE_QUERY_STEPS and state in ROUTE_SHOW_STATES | {"interrupted"}:
            return {"kind": "requery_transit", "label": "返回路线查询",
                    "description": "小银会用本次起终点重新查询高德，不会启动导航或发送消息。"}
        if step_state in ROUTE_SHOW_STATES and state in ROUTE_SHOW_STATES and task.get("transit_result"):
            return {"kind": "show_transit", "label": "返回高德路线详情",
                    "description": "小银会核对并打开这次查询的高德详情，不会启动导航。"}
        return None
    if task.get("send_attempts"):
        if step.get("send_attempts") == 1 and state in {"completed", "outcome_unknown"}:
            return {"kind": "verify_only", "label": "核对邮件发送结果",
                    "description": "只查看已发送记录，不会再次发送。"}
        return None
    if task.get("task_type") != "open_url" and step_state in DRAFT_STEPS and state in DRAFT_STATES:
        return {"kind": "review_draft", "label": "返回草稿核对",
                "description": "小银会重新准备当前草稿。核对完成后，发送仍需要您亲自确认。"}
    return None


def _resolve(engine, task_id: str, step_seq: int):
    task = engine.store.get(task_id)
    step, revision = engine.store.history_step(task_id, step_seq)
    action = _action(task, step)
    if (action is not None and action["kind"] != "open_input" and (
            (engine.worker and engine.worker.is_alive())
            or any(other["id"] != task_id and other["state"] not in TERMINAL | {"interrupted"}
                   for other in engine.store.all()))):
        action = None
    return task, revision, action


def return_options(engine, task_id: str, step_seq: int) -> dict:
    with engine.condition, engine.store.lock:
        _, revision, action = _resolve(engine, task_id, step_seq)
        return {"ok": True, "revision": revision, "action": action}


def return_to_step(engine, task_id: str, step_seq: int, expected_revision: int) -> dict:
    with engine.condition, engine.store.lock:
        task, revision, action = _resolve(engine, task_id, step_seq)
        if revision != expected_revision:
            raise WorkflowError("任务已有进展，请重新选择并核对这一步")
        if action is None:
            raise WorkflowError("这一步目前只能查看记录")
        kind = action["kind"]
        if kind == "open_input":
            return task
        if kind == "review_draft":
            return engine.edit(task_id, {})
        if kind == "requery_transit":
            return engine.requery_transit(task_id, {"query_id": task["query_id"]})
        if kind == "show_transit":
            return engine.show_transit(task_id, task["query_id"])
        if kind == "verify_dingtalk":
            return engine.verify_dingtalk(task_id, task["query_id"])
        if kind == "verify_only":
            return engine.control(task_id, "verify_only")
        raise WorkflowError("此步骤没有可用的返回操作")
