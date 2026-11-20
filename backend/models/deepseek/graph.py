"""One LangGraph pass: look at the screen, then keep only a legal command."""
import json
from typing import TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from models.base import ModelCancelled
from models.decision import normalize_decision
from models.deepseek.images import prepare_image, scale_command
from models.deepseek.prompt import SYSTEM, user_text
from models.turn import Turn


class GraphState(TypedDict, total=False):
    messages: list
    attempts: int
    decision: dict | None
    error: str
    scale_x: float
    scale_y: float
    block_pointer: bool
    must_refuse: bool


def build_graph(llm, cancel):
    def ask(state: GraphState) -> dict:
        if cancel.is_set():
            raise ModelCancelled()
        reply = llm.invoke(state["messages"])
        return {
            "messages": [*state["messages"], AIMessage(content=_text(reply.content))],
            "attempts": int(state.get("attempts") or 0) + 1,
        }

    def validate(state: GraphState) -> dict:
        raw = _text(state["messages"][-1].content)
        try:
            data = _parse_json(raw)
            command = data.get("command")
            if isinstance(command, dict):
                blocked = _blocked(command, state)
                if blocked:
                    if int(state.get("attempts") or 0) >= 2 and state.get("must_refuse"):
                        data = {"command": {"type": "refuse", "message": "连续点击没有新进展，这条任务做不到"}, "progress": ""}
                        return {"decision": normalize_decision(data), "error": ""}
                    return {"decision": None, "error": blocked}
                data = dict(data)
                mapped = scale_command(command, state["scale_x"], state["scale_y"])
                print(f"坐标 {json.dumps(command, ensure_ascii=False)} -> {json.dumps(mapped, ensure_ascii=False)}")
                data["command"] = mapped
            return {"decision": normalize_decision(data), "error": ""}
        except (ValueError, json.JSONDecodeError, TypeError) as error:
            return {"decision": None, "error": str(error)}

    def repair(state: GraphState) -> dict:
        print(f"DeepSeek 输出不合法，再试一次: {state.get('error') or '空'}")
        return {
            "messages": [
                *state["messages"],
                HumanMessage(content=f"上一条不是合法指令：{state.get('error') or '空'}。只重新输出一个 JSON 对象。"),
            ]
        }

    def route(state: GraphState) -> str:
        if state.get("decision"):
            return "done"
        if int(state.get("attempts") or 0) >= 2:
            return "done"
        return "repair"

    graph = StateGraph(GraphState)
    graph.add_node("ask", ask)
    graph.add_node("validate", validate)
    graph.add_node("repair", repair)
    graph.add_edge(START, "ask")
    graph.add_edge("ask", "validate")
    graph.add_conditional_edges("validate", route, {"repair": "repair", "done": END})
    graph.add_edge("repair", "ask")
    return graph.compile()


POINTER = {"tap", "swipe", "long_press", "zoom"}


def initial_state(turn: Turn, notes: str, policy: dict | None = None) -> GraphState:
    url, seen_w, seen_h, scale_x, scale_y = prepare_image(turn.image_png)
    content: list[dict] = [{"type": "text", "text": user_text(turn, seen_w, seen_h, notes)}]
    previous = turn.previous[-1] if turn.previous else None
    if previous is not None and previous.image_png:
        previous_url, _, _, _, _ = prepare_image(previous.image_png)
        content.append({"type": "text", "text": "上一张截图："})
        content.append({"type": "image_url", "image_url": {"url": previous_url}})
    content.append({"type": "text", "text": "当前截图："})
    content.append({"type": "image_url", "image_url": {"url": url}})
    return {
        "messages": [SystemMessage(content=SYSTEM), HumanMessage(content=content)],
        "attempts": 0,
        "decision": None,
        "error": "",
        "scale_x": scale_x,
        "scale_y": scale_y,
        "block_pointer": bool((policy or {}).get("block_pointer")),
        "must_refuse": bool((policy or {}).get("must_refuse")),
    }


def _blocked(command: dict, state: GraphState) -> str:
    kind = str(command.get("type") or "")
    if state.get("must_refuse") and kind != "refuse":
        return "连续点击没有新进展。这一步只能输出 refuse，说明做不到的原因。"
    if state.get("block_pointer") and kind in POINTER:
        return "最近都在点击且没有新进度。禁止 tap、swipe、long_press、zoom。改用 home、back、input_text、launch、ask 或 refuse。"
    return ""


def _parse_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0].strip()
    start = raw.find("{")
    if start < 0:
        raise ValueError("需要一个 JSON 对象")
    data, _ = json.JSONDecoder().raw_decode(raw[start:])
    if not isinstance(data, dict):
        raise ValueError("需要一个 JSON 对象")
    return data


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return str(content or "")
