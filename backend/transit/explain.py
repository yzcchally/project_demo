"""Large-card and speech facts share this deterministic narration source."""

import re


def segment_text(segment, index=None):
    prefix = f"第{index}步：" if index is not None else ""
    if segment.get("mode") == "walk":
        if (index in {None, 1} and segment.get("board") and segment.get("alight")
                and re.fullmatch(r"步行\s*\d+(?:\.\d+)?(?:米|公里)\s*(?:[·•]\s*)?\d+(?:\.\d+)?分钟", segment.get("instructions") or "")):
            return (f"{prefix}从{segment['board']}步行至{segment['alight']}。"
                    f"页面标注：{segment['instructions']}；具体街道走法未显示，请结合高德地图查看。")
        if (segment.get("instructions") == "站内外共步行" and segment.get("board") and segment.get("alight")):
            # This literal label survives verification only with a query-bound
            # native itinerary relation. Its total meters/minutes stay in the
            # overview, so narration does not allocate them to this one leg.
            return (f"{prefix}从{segment['board']}步行至{segment['alight']}。"
                    "页面标注“站内外共步行”；具体街道走法未显示，请结合高德地图查看。")
        return prefix + (segment.get("instructions") or "这段步行说明尚未读取，请查看高德详情。")
    mode = "地铁" if segment.get("mode") == "subway" else "公交"
    line = segment.get("line") or f"{mode}线路待核对"
    board = segment.get("board") or "上车站待核对"
    alight = segment.get("alight") or "下车站待核对"
    direction = f"，方向：{segment['direction']}" if segment.get("direction") else "，方向尚未读取"
    stops = f"，乘坐{segment['stops']}站" if segment.get("stops") is not None else ""
    entrance = f"，从{segment['entrance']}进入" if segment.get("entrance") else ""
    exit_text = f"，从{segment['exit']}出站" if segment.get("exit") else ""
    return f"{prefix}在{board}{entrance}乘坐{line}{direction}{stops}，到{alight}下车{exit_text}。"


def explain_result(result):
    """Only validated structure is narrated; no second LLM may invent facts."""
    status = result.get("classification", "FAILED")
    if status == "NO_ROUTE":
        return "高德本次页面显示没有公共交通方案。可以换一个起点或终点重新查询。"
    if status == "FAILED":
        return result.get("failure_reason") or "本次未能读取高德路线，请检查手机后重新查询。"
    start = result.get("origin", {}).get("name") or "起点待核对"
    end = result.get("destination", {}).get("name") or "终点待核对"
    parts = [f"从{start}前往{end}。"]
    if status != "COMPLETE" or not result.get("route_verified"):
        parts.append("本次只读到了部分路线，关键内容尚未核对完整，请结合高德详情查看。")
    if result.get("duration_minutes") is not None:
        parts.append(f"预计{result['duration_minutes']:g}分钟。")
    if result.get("transfers") is not None:
        parts.append(f"按本次读取的乘车段计算，需要换乘{result['transfers']}次。")
    if result.get("walking_meters") is not None:
        parts.append(f"总步行约{result['walking_meters']:g}米。")
    parts.extend(segment_text(s, index + 1) for index, s in enumerate(result.get("segments", [])))
    parts.append("路线来源为高德地图，以本次查询页面为准；出发前可以重新查询。")
    return "\n".join(parts)


def answer_question(result, question):
    """Answer only the current validated result. Replanning is never prose-only."""
    if not result.get("route_verified") or result.get("classification") != "COMPLETE":
        return "当前路线尚未核对完整，无法可靠回答这项路线追问；请查看缺失项或重新查询。"
    if any(word in question for word in ["换一个", "更少", "少走", "换条", "重新查询", "更快"]):
        return "更换方案需要重新查询高德或比较实际读取的候选，请点击“重新查询”并选择偏好。"
    if any(word in question for word in ["第一步", "先怎么", "先走", "首先"]):
        segments = result.get("segments", [])
        return segment_text(segments[0], 1) if segments else "当前结果没有可核对的第一步，请查看高德详情。"
    if any(word in question for word in ["换乘", "转车", "转乘"]):
        value = result.get("transfers")
        return f"按本次已读取并去重的乘车段，需要换乘{value}次。" if value is not None else "换乘次数尚未读取，不能确定。"
    if any(word in question for word in ["下车", "哪站下", "哪一站下"]):
        rides = [s for s in result.get("segments", []) if s.get("mode") in {"subway", "bus"}]
        if not rides or any(not s.get("alight") for s in rides):
            return "下车站尚未完整读取，请查看高德详情。"
        return "\n".join(f"第{i+1}段乘坐{s.get('line') or '所示线路'}，在{s['alight']}下车。" for i, s in enumerate(rides))
    return "目前支持询问“第一步怎么走？”“我应该在哪一站下车？”“需要换乘几次？”。其他信息请查看本次高德详情。"
