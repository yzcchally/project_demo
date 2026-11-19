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
                data = dict(data)
                data["command"] = scale_command(command, state["scale_x"], state["scale_y"])
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


def initial_state(turn: Turn, summary: str) -> GraphState:
    url, seen_w, seen_h, scale_x, scale_y = prepare_image(turn.image_png)
    content: list[dict] = [{"type": "text", "text": user_text(turn, seen_w, seen_h, summary)}]
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
    }


def _parse_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    data = json.loads(raw)
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
