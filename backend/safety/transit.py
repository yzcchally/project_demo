"""A separate query-only guard. It grants no mail, shell or navigation actions."""
from api.schemas import TransitScreenDecision
from domain import WorkflowError

PACKAGE = "com.autonavi.minimap"
PAGE_ROLES = {
    "search": {"search", "origin", "destination", "place", "back"},
    "place_list": {"search", "place", "back"},
    "place_detail": {"route", "back"},
    "route_input": {"origin", "destination", "transit", "search", "back"},
    "route_list": {"route", "transit", "preference", "details", "scroll", "back"},
    "route_detail": {"details", "scroll", "back"},
    "permission": set(), "unknown": set(),
}


def guard_transit(decision, candidates, allowed_inputs, *, observation_id, context_epoch=0, allowed_place_ids=()):
    """Validate one current observation. The caller re-observes after each act."""
    decision = decision if isinstance(decision, TransitScreenDecision) else TransitScreenDecision.model_validate(decision)
    if decision.observation_id != observation_id:
        raise WorkflowError("高德控件观察已过期，请重新读取页面")
    indexed = {c["target_id"]: c for c in candidates}
    for action in decision.actions:
        if action.action == "wait":
            continue
        target = indexed.get(action.target_id)
        if (target is None or target.get("package") != PACKAGE or target.get("observation_id") != observation_id
                or target.get("context_epoch") != context_epoch or target.get("role") != action.role):
            raise WorkflowError("高德动作不属于当前页面或已因暂停恢复失效")
        if action.role not in PAGE_ROLES[decision.page]:
            raise WorkflowError("高德动作超出当前查询页面允许范围")
        label = (target.get("text", "") + " " + target.get("content_description", "")).strip()
        if any(word in label for word in ("开始导航", "开启导航", "开始出发", "打车", "支付", "付款", "登录", "删除", "收藏", "下车提醒")):
            raise WorkflowError("该控件不属于出发前路线查询范围")
        bounds = target.get("bounds", [])
        if len(bounds) != 4 or min(bounds) < 0 or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise WorkflowError("高德目标当前不可见")
        if action.role == "place" and action.target_id not in allowed_place_ids:
            raise WorkflowError("地点尚未唯一确认，请先让用户选择")
        if action.action == "enter_text":
            if not target.get("editable") or action.role not in {"origin", "destination", "search"}:
                raise WorkflowError("高德输入目标不是允许的路线搜索框")
            if action.role not in allowed_inputs or action.text != allowed_inputs[action.role]:
                raise WorkflowError("高德输入与当前已确认的起终点不一致")
        elif action.action == "scroll":
            if action.role != "scroll" or not target.get("scrollable"):
                raise WorkflowError("只能在当前可见路线容器中受限滚动")
        elif action.action == "tap_element":
            if action.text:
                raise WorkflowError("点击不能夹带输入或额外指令")
    return decision
