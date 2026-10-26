from api.schemas import ScreenDecision
from domain import WorkflowError

def guard_preparation(decision: ScreenDecision, candidates, draft):
    if decision.page != "composer":
        raise WorkflowError("AI 未能确认写信页面，已停止准备，请检查手机后重试")
    indexed = {n["target_id"]: n for n in candidates}
    allowed = {"to": draft["recipient"], "subject": draft["subject"], "body": draft["body"]}
    for action in decision.actions:
        if action.action not in {"enter_text", "tap_element", "wait"}:
            raise WorkflowError("AI 建议超出草稿准备范围，已拦截")
        if action.action == "wait":
            continue
        target = indexed.get(action.target_id)
        if target is None or action.role not in allowed:
            raise WorkflowError("AI 使用了过期或不允许的控件，已拦截")
        expected_id = {"to": "com.huawei.email:id/to_content", "subject": "com.huawei.email:id/subject", "body": "main"}[action.role]
        if target["resource_id"] != expected_id:
            raise WorkflowError("AI 控件与字段用途不一致，已拦截")
        if action.action == "enter_text" and action.text != allowed[action.role]:
            raise WorkflowError("AI 建议的内容与当前任务不一致，已拦截")
    return decision
