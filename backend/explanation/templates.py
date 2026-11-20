DETAILS = {
    "awaiting_confirmation": "发送邮件会影响收件人，需要您核对收件地址和完整内容。批准只对应当前草稿，内容变化后需要重新确认。",
    "outcome_unknown": "已经登记过发送尝试，但当前证据不足。请先检查已发送或收件箱，避免再次发送相同邮件。",
    "awaiting_qr": "请对准纸张或其他设备上的二维码。助手会使用实际识别的网址打开网页，并将同一网址放入邮件。",
    "needs_clarification": "联系人称呼不等于邮箱地址，需要您指定或保存一个明确映射。助手不会猜测收件人。",
}


def explain(task):
    if task.get("task_type") == "contact_family":
        return task["message"] + "\n号码由手机上的家人资料提供，核对后可以打开拨号页。"
    if task.get("task_type") == "plan_transit":
        if task.get("transit_result"):
            from transit.explain import explain_result
            return explain_result(task["transit_result"])
        return task["message"] + "\n小银只读取本次高德页面，不猜测起点、分馆、线路方向或出口。定位无法核实时请填写明确起点。"
    return task["message"] + "\n" + DETAILS.get(task["state"], "小银会在关键步骤重新读取手机界面，核对通过才继续。您可以暂停或取消任务。")
