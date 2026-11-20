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

    def next_intake(current):
        if current.get("task_type") == "contact_family":
            return "done"
        if engine._missing(current):
            return "transit_clarify" if current.get("task_type") == "plan_transit" else "clarify"
        return "transit_precheck" if current.get("task_type") == "plan_transit" else "scan"

    def understand(state):
        engine._understand(state["task_id"])
        return {"decision": next_intake(task(state))}

    def clarify(state):
        engine._question(state["task_id"])
        answer = interrupt({"kind": "clarification", "fields": engine._missing(task(state)), "message": task(state)["message"]})
        engine._apply_clarification(state["task_id"], answer)
        return {"decision": next_intake(task(state))}

    def transit_clarify(state):
        engine._question(state["task_id"])
        current = task(state)
        answer = interrupt({"kind": "transit_clarification", "query_id": current["query_id"],
                            "fields": engine._missing(current), "message": current["message"]})
        engine._apply_transit_clarification(state["task_id"], answer)
        return {"decision": next_intake(task(state))}

    def transit_precheck(state):
        engine._transit_precheck(state["task_id"])
        return {}

    def transit_query(state):
        return {"decision": engine._query_transit(state["task_id"])}

    def transit_choice(state):
        # This node has no GUI side effects before interrupt. Resuming a human
        # choice never replays the device query which produced the candidate set.
        current = task(state)
        answer = interrupt({"kind": "transit_place", **current["place_choice"]})
        engine._apply_place_choice(state["task_id"], answer)
        return {}

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
                       ("browser", browser), ("draft", draft), ("approval", approval), ("send", send), ("verify", verify),
                       ("transit_clarify", transit_clarify), ("transit_precheck", transit_precheck),
                       ("transit_query", transit_query), ("transit_choice", transit_choice)]:
        graph.add_node(name, node)
    def entry(state):
        current = task(state)
        if current.get("instruction") and not current.get("intake_complete"):
            return "understand"
        if current.get("task_type") == "contact_family":
            return END
        return next_intake(current) if current.get("task_type") == "plan_transit" else "precheck"

    graph.add_conditional_edges(START, entry)
    graph.add_conditional_edges("understand", lambda s: END if s["decision"] == "done" else s["decision"])
    graph.add_conditional_edges("clarify", lambda s: s["decision"])
    graph.add_conditional_edges("transit_clarify", lambda s: s["decision"])
    graph.add_edge("transit_precheck", "transit_query")
    graph.add_conditional_edges("transit_query", lambda s: END if s["decision"] == "done" else s["decision"])
    graph.add_edge("transit_choice", "transit_query")
    graph.add_edge("scan", "precheck")
    graph.add_edge("precheck", "browser")
    graph.add_conditional_edges("browser", lambda s: END if s["decision"] == "done" else "draft")
    graph.add_edge("draft", "approval")
    graph.add_edge("approval", "send")
    graph.add_conditional_edges("send", lambda s: "verify" if s["decision"] == "verify" else END)
    graph.add_edge("verify", END)
    engine.graph_db = sqlite3.connect(engine.store.directory / "checkpoints.sqlite3", check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(engine.graph_db))
