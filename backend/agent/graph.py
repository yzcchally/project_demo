import sqlite3
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt


class GraphState(TypedDict, total=False):
    task_id: str
    decision: str


def build_graph(engine):
    graph = StateGraph(GraphState)
    def task(state):
        return engine.store.get(state["task_id"])
    def understand(state):
        engine._understand(state["task_id"])
        return {"decision": "clarify" if engine._missing(task(state)) else "scan"}
    def clarify(state):
        engine._question(state["task_id"])
        answer = interrupt({"kind": "clarification", "fields": engine._missing(task(state)), "message": task(state)["message"]})
        engine._apply_clarification(state["task_id"], answer)
        return {"decision": "clarify" if engine._missing(task(state)) else "scan"}
    def scan(state):
        current = task(state)
        if current.get("instruction") and current.get("request_qr") and not current.get("qr_received"):
            engine._stage(state["task_id"], "awaiting_qr")
            reply = interrupt({"kind": "qr", "scan_nonce": current["scan_nonce"]})
            engine._apply_qr(state["task_id"], reply)
        return {}
    def precheck(state):
        engine._precheck(state["task_id"])
        return {}
    def browser(state):
        engine._browser(state["task_id"])
        return {"decision": "done" if task(state).get("task_type") == "open_url" else "draft"}
    def draft(state):
        engine._draft(state["task_id"])
        return {}
    def approval(state):
        current = task(state)
        reply = interrupt({"kind": "approval", "task_id": current["id"], "draft_digest": current["draft_digest"], "approval_nonce": current["approval_nonce"]})
        engine._validate_graph_approval(state["task_id"], reply)
        return {}
    def send(state):
        engine._send(state["task_id"])
        return {"decision": "verify" if task(state)["state"] == "verifying" else "done"}
    def verify(state):
        engine._verify_result(state["task_id"])
        return {}
    for name, node in [("understand", understand), ("clarify", clarify), ("scan", scan), ("precheck", precheck),
                       ("browser", browser), ("draft", draft), ("approval", approval), ("send", send), ("verify", verify)]:
        graph.add_node(name, node)
    graph.add_conditional_edges(START, lambda s: "understand" if task(s).get("instruction") and not task(s).get("intake_complete") else "precheck")
    graph.add_conditional_edges("understand", lambda s: s["decision"])
    graph.add_conditional_edges("clarify", lambda s: s["decision"])
    graph.add_edge("scan", "precheck")
    graph.add_edge("precheck", "browser")
    graph.add_conditional_edges("browser", lambda s: END if s["decision"] == "done" else "draft")
    graph.add_edge("draft", "approval")
    graph.add_edge("approval", "send")
    graph.add_conditional_edges("send", lambda s: "verify" if s["decision"] == "verify" else END)
    graph.add_edge("verify", END)
    engine.graph_db = sqlite3.connect(engine.store.directory / "checkpoints.sqlite3", check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(engine.graph_db))
