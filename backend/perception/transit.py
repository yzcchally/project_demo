"""Observation-scoped controls for Amap; no mail selectors are widened."""
import hashlib
import re
import xml.etree.ElementTree as ET

PACKAGE = "com.autonavi.minimap"


def describe_transit(observation, observation_id, *, context_epoch=0, role_map=None):
    """Build candidates from the current tree only.

    `role_map` may associate observed node indices with roles after a native
    page-specific resolver has classified them. It never supplies coordinates.
    """
    ns = list(ET.fromstring(observation).iter("node")) if isinstance(observation, str) else list(observation)
    roles = role_map or {}
    result = []
    for index, node in enumerate(ns):
        if node.get("package") != PACKAGE or node.get("enabled") == "false":
            continue
        bounds = [int(x) for x in re.findall(r"-?\d+", node.get("bounds", ""))]
        if len(bounds) != 4 or min(bounds) < 0 or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            continue
        text, desc = node.get("text", ""), node.get("content-desc", "")
        label = text or desc
        role = roles.get(index)
        editable = "EditText" in node.get("class", "")
        if role is None:
            if editable:
                role = "origin" if any(x in label for x in ["起点", "从哪", "出发地"]) else "destination" if any(x in label for x in ["终点", "目的地"]) else "search"
            elif node.get("scrollable") == "true":
                role = "scroll"
            elif label in {"返回", "返回上一页", "后退"}:
                role = "back"
            elif label in {"公交", "公交地铁", "公交/地铁", "公共交通"}:
                role = "transit"
            elif label in {"少换乘", "换乘少", "步行少", "少步行", "推荐", "高德推荐", "最佳路线", "地铁优先"}:
                role = "preference"
            elif label in {"详情", "展开", "路线详情", "查看详情", "展开详情", "全部详情"}:
                role = "details"
        if role is None:
            continue
        material = f"{observation_id}:{context_epoch}:{index}:{node.get('resource-id')}:{node.get('bounds')}:{text}:{desc}"
        target_id = hashlib.sha256(material.encode()).hexdigest()[:24]
        result.append({"target_id": target_id, "observation_id": observation_id,
                       "context_epoch": context_epoch, "package": PACKAGE, "role": role,
                       "resource_id": node.get("resource-id", ""), "class": node.get("class", ""),
                       "bounds": bounds, "text": text[:500], "content_description": desc[:500],
                       "editable": editable, "scrollable": node.get("scrollable") == "true",
                       "clickable": node.get("clickable") == "true"})
    return result
