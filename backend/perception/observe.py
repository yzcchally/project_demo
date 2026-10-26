import hashlib
from api.schemas import ScreenDecision
from domain import WorkflowError


def describe(ns):
    candidates = []
    roles = {"com.huawei.email:id/to_content": "to", "com.huawei.email:id/subject": "subject", "main": "body"}
    for index, node in enumerate(ns):
        rid = node.get("resource-id", "")
        if node.get("enabled") == "false" or rid not in roles:
            continue
        key = hashlib.sha256(f"{index}:{rid}:{node.get('bounds')}".encode()).hexdigest()[:16]
        candidates.append({"target_id": key, "resource_id": rid, "role": roles[rid], "class": node.get("class"), "bounds": node.get("bounds"),
                           "text": node.get("text", "")[:120], "content_description": node.get("content-desc", "")[:300]})
    return candidates
