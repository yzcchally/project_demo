"""Graph, API and ownership boundaries using map doubles, never real GUI claims."""
import copy
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import gmail_demo
from agent.runtime import Engine
from api.schemas import TaskIntent, TransitRequest
from domain import WorkflowError
from evaluation.metrics import outcome
from storage.tasks import Store
from test_workflow import Device


class Models:
    def __init__(self, request=None):
        self.request = request or TransitRequest(origin="广州塔", destination="体育西路", city="广州", preference="fewer_transfers")
        self.gate = None
        self.entered = threading.Event()

    def understand(self, instruction, aliases):
        self.entered.set()
        if self.gate: self.gate.wait(3)
        return TaskIntent(task_type="plan_transit", transit=self.request)


def route_result(task_id, query_id, *, classification="COMPLETE"):
    route_id = "route-" + query_id
    places = {"evidence_id": "places", "query_id": query_id, "observation_id": "o-places", "page_kind": "location",
              "text": "广州塔 广州市海珠区 体育西路 广州市天河区 广州", "route_id": None}
    detail = {"evidence_id": "details", "query_id": query_id, "observation_id": "o-details", "route_id": route_id,
              "page_kind": "route_detail", "text": "广州塔 体育西路 3号线 天河客运站 广州塔上车 体育西路下车 2站 20分钟 步行500米 3元 少换乘"}
    result = {"task_id": task_id, "query_id": query_id, "route_id": route_id, "classification": classification,
              "origin": {"name": "广州塔", "city": "广州", "evidence_refs": {"name": ["places"], "city": ["places"]}},
              "destination": {"name": "体育西路", "city": "广州", "evidence_refs": {"name": ["places"], "city": ["places"]}},
              "applied_filter": "少换乘", "duration_minutes": 20, "walking_meters": 500, "fare_yuan": 3,
              "evidence_refs": {k: ["details"] for k in ["applied_filter", "duration_minutes", "walking_meters", "fare_yuan"]},
              "segments": [{"segment_id": "ride1", "mode": "subway", "line": "3号线", "direction": "天河客运站", "board": "广州塔", "alight": "体育西路", "stops": 2,
                            "evidence_refs": {k: ["details"] for k in ["line", "direction", "board", "alight", "stops"]}}]}
    return {"kind": "result", "result": result, "evidence": [places, detail], "collection_complete": True}


class Map:
    def __init__(self):
        self.calls = []
        self.choice = False
        self.location_missing = False
        self.gate = None
        self.entered = threading.Event()
        self.kind = "COMPLETE"
        self.return_stale = False
        self.ignore_selected_place = False

    def check_ready(self, checkpoint):
        checkpoint()
        self.calls.append("check-map")

    def query(self, request, task_id, query_id, checkpoint, choices=None):
        checkpoint()
        self.entered.set()
        self.calls.append(("query", copy.deepcopy(request), query_id, copy.deepcopy(choices)))
        if self.gate: self.gate.wait(3)
        checkpoint()
        if self.location_missing and not request["origin"]:
            return {"kind": "clarification", "fields": ["origin", "city"], "message": "定位不可核对，请填写起点与城市"}
        if self.choice and not choices.get("destination"):
            return {"kind": "choice", "role": "destination", "candidates": [
                {"candidate_id": "east", "name": "体育西路东口", "address": "广州天河路", "city": "广州"},
                {"candidate_id": "west", "name": "体育西路西口", "address": "广州体育西路", "city": "广州"}]}
        result = route_result(task_id, "old-query" if self.return_stale else query_id, classification=self.kind)
        if choices and not self.ignore_selected_place:
            for role, selected in choices.items():
                result["result"][role].update({k: selected[k] for k in ["name", "city", "address"] if k in selected})
                result["result"][role]["evidence_refs"] = {k: ["places"] for k in ["name", "city", "address"]}
                result["evidence"][0]["text"] += " " + " ".join(str(selected.get(k) or "") for k in ["name", "city", "address"])
                # A selected exit is a different endpoint from the platform;
                # include an explicit grounded walking connection in this
                # synthetic fixture rather than silently equating the names.
                rides = [s for s in result["result"]["segments"] if s["mode"] in {"bus", "subway"}]
                start = rides[-1]["alight"] if role == "destination" else selected["name"]
                end = selected["name"] if role == "destination" else rides[0]["board"]
                quote = f"从{start}步行至{end}"
                result["evidence"][1]["text"] += "\n" + quote
                walk = {"segment_id": "selected-place-walk-" + role, "mode": "walk", "instructions": quote,
                        "board": start, "alight": end,
                        "evidence_refs": {k: ["details"] for k in ["instructions", "board", "alight"]}}
                if role == "destination": result["result"]["segments"].append(walk)
                else: result["result"]["segments"].insert(0, walk)
        if self.kind == "PARTIAL":
            result["result"]["segments"][0]["direction"] = None
        if self.kind == "NO_ROUTE":
            result["evidence"][1]["text"] = "暂无公交方案"
            result["result"]["segments"] = []
        return result

    def show_details(self, result, checkpoint):
        checkpoint()
        self.calls.append(("show", result["query_id"]))


@pytest.fixture
def engine(tmp_path):
    device = Device()
    device.show_app = lambda: device.calls.append(("show-app",))
    e = Engine(Store(tmp_path), device, Models())
    e.transit_device = Map()
    yield e
    e.cancelled = True; e.paused = False
    if e.models.gate: e.models.gate.set()
    if e.transit_device.gate: e.transit_device.gate.set()
    with e.condition: e.condition.notify_all()
    if e.worker: e.worker.join(4)
    e.close()


def settle(engine, task):
    engine.worker.join(4)
    assert not engine.worker.is_alive()
    return engine.store.get(task["id"])


def create(engine, key="transit-request-01", instruction="在广州从广州塔去体育西路，坐地铁，尽量少换乘"):
    return settle(engine, engine.create_goal(instruction, key))


def test_route_graph_never_needs_mail_or_https_and_preserves_result(engine):
    engine.device.check_ready = lambda: (_ for _ in ()).throw(AssertionError("mail precheck used"))
    task = create(engine)
    assert task["task_type"] == "plan_transit" and task["state"] == "completed"
    assert task["route_verified"] and task["send_attempts"] == 0
    assert "draft" not in task and "draft_digest" not in task
    assert [x for x in engine.device.calls if x[0] != "show-app"] == []
    assert outcome(task) == "SUCCESS"
    assert engine.store.events(task["id"])[-1]["evidence"]["route_verified"]
    assert engine.store.events(task["id"])[-1]["query_id"] == task["query_id"]


@pytest.mark.parametrize("operation", ["approve", "received", "edit", "verify_only", "retry_prepare", "qr", "mail_clarify"])
def test_routes_reject_all_mail_effect_paths(engine, operation):
    task = create(engine)
    with pytest.raises(WorkflowError, match="路线"):
        if operation == "approve": engine.approve(task["id"], "0" * 64, True)
        elif operation == "received": engine.confirm_delivery(task["id"], "0" * 64, True)
        elif operation == "edit": engine.edit(task["id"], {"subject": "bad"})
        elif operation == "qr": engine.submit_qr(task["id"], "https://example.org/", "nonce")
        elif operation == "mail_clarify": engine.clarify(task["id"], {"recipient": "demo@example.org"})
        else: engine.control(task["id"], operation)
    assert engine.store.get(task["id"])["send_attempts"] == 0


def test_unknown_home_and_city_are_clarified_without_guessing(engine):
    engine.models.request = TransitRequest(origin="家里", destination="体育西路", city="广州")
    task = create(engine, instruction="在广州从家里去体育西路，坐公交")
    assert task["state"] == "needs_clarification"
    assert task["clarification_fields"] == ["origin"]
    assert not engine.transit_device.calls
    task = settle(engine, engine.clarify_transit(task["id"], {"query_id": task["query_id"], "origin": "广州塔"}))
    assert task["state"] == "completed" and task["route_verified"]
    assert not task["transit_request"]["use_current_location"]


def test_route_clarification_and_graph_resume_never_emit_recipient_prompt(engine):
    engine.models.request = TransitRequest(origin="家里", destination="体育西路", city="广州")
    task = create(engine, instruction="在广州从家里去体育西路，坐公交")
    assert task["state"] == "needs_clarification"
    query_id = task["query_id"]
    initial_events = engine.store.events(task["id"])
    assert any(event["message"] == "请补充出行信息" for event in initial_events)
    task = settle(engine, engine.clarify_transit(task["id"], {"query_id": query_id, "origin": "广州塔"}))
    assert task["state"] == "completed" and task["query_id"] == query_id
    events = engine.store.events(task["id"])
    # LangGraph reruns the interrupt node on resume. Both visits publish the
    # generic stage before the field-specific question, including over WS.
    assert sum(event["message"] == "请补充出行信息" for event in events) == 2
    assert all("收件" not in event["message"] for event in events)


def test_invented_model_city_is_not_accepted(engine):
    engine.models.request = TransitRequest(origin="起点公园", destination="终点医院", city="广州")
    task = create(engine, instruction="从起点公园去终点医院坐地铁")
    assert task["state"] == "needs_clarification"
    assert task["clarification_fields"] == ["city"]
    assert not engine.transit_device.calls


def test_destination_only_attempts_actual_location_then_asks_if_unavailable(engine):
    engine.models.request = TransitRequest(destination="体育西路")
    engine.transit_device.location_missing = True
    task = create(engine, instruction="我想去体育西路，帮我看看怎么坐公交过去")
    assert task["state"] == "needs_clarification"
    assert task["transit_request"]["use_current_location"]
    assert set(task["clarification_fields"]) == {"origin", "city"}
    assert engine.device.calls[-1][0] == "show-app"
    assert "recipient" not in task["clarification_fields"]
    task = settle(engine, engine.clarify_transit(task["id"], {"query_id": task["query_id"], "origin": "广州塔", "city": "广州"}))
    assert task["state"] == "completed"


def test_choice_interrupt_is_bound_to_query_nonce_and_candidate_set(engine):
    engine.transit_device.choice = True
    task = create(engine)
    assert task["state"] == "awaiting_place_choice"
    assert engine.graph.get_state(engine._graph_config(task["id"])).next == ("transit_choice",)
    selection = task["place_choice"]
    reply = {"query_id": task["query_id"], "choice_nonce": selection["choice_nonce"], "candidate_id": "east"}
    for invalid in [dict(reply, query_id="wrong-query"), dict(reply, choice_nonce="invalid-nonce-value"), dict(reply, candidate_id="not-offered")]:
        with pytest.raises(WorkflowError): engine.choose_transit_place(task["id"], invalid)
    assert len([v for v in engine.transit_device.calls if isinstance(v, tuple)]) == 1
    task = settle(engine, engine.choose_transit_place(task["id"], reply))
    assert task["state"] == "completed"
    assert engine.transit_device.calls[-1][-1]["destination"]["candidate_id"] == "east"
    with pytest.raises(WorkflowError): engine.choose_transit_place(task["id"], reply)
    assert len([v for v in engine.transit_device.calls if isinstance(v, tuple)]) == 2


def test_cancel_late_map_reply_has_no_commit_no_foreground_jump_and_no_new_worker(engine):
    engine.transit_device.gate = threading.Event()
    task = engine.create_goal("在广州从广州塔去体育西路，坐地铁", "cancel-route-01")
    assert engine.transit_device.entered.wait(2)
    before = list(engine.device.calls)
    engine.control(task["id"], "cancel")
    with pytest.raises(WorkflowError): engine.create_goal("在广州从广州塔去体育西路", "other-route-01")
    engine.transit_device.gate.set()
    task = settle(engine, task)
    assert task["state"] == "cancelled" and not task.get("transit_result")
    assert engine.device.calls == before
    assert task["cancel_delay_seconds"] >= 0


def test_pause_resume_rechecks_context_before_device_continues(engine):
    engine.transit_device.gate = threading.Event()
    task = engine.create_goal("在广州从广州塔去体育西路坐地铁", "pause-route-01")
    assert engine.transit_device.entered.wait(2)
    engine.control(task["id"], "pause")
    engine.transit_device.gate.set()
    assert engine.store.get(task["id"])["state"] == "paused"
    engine.control(task["id"], "resume")
    task = settle(engine, task)
    assert task["state"] == "completed"
    assert engine.device.restored == []
    assert engine.device.context_epoch == 1


def test_requery_has_new_generation_history_and_reacquires_global_ownership(engine):
    task = create(engine)
    old = task["query_id"]
    task = settle(engine, engine.requery_transit(task["id"], {"query_id": old, "preference": "less_walking"}))
    assert task["query_id"] != old
    assert task["transit_history"][0]["query_id"] == old
    assert task["transit_request"]["preference"] == "less_walking"
    for operation in [lambda: engine.requery_transit(task["id"], {"query_id": old}), lambda: engine.ask_transit(task["id"], old, "第一步怎么走？")]:
        with pytest.raises(WorkflowError): operation()
    engine.models.gate = threading.Event(); engine.models.entered.clear()
    other = engine.create_goal("在广州从广州塔去体育西路坐公交", "occupied-route-02")
    assert engine.models.entered.wait(1)
    with pytest.raises(WorkflowError): engine.requery_transit(task["id"], {"query_id": task["query_id"]})
    with pytest.raises(WorkflowError): engine.show_transit(task["id"], task["query_id"])
    engine.control(other["id"], "cancel"); engine.models.gate.set(); settle(engine, other)


@pytest.mark.parametrize("kind, expected", [("PARTIAL", "PARTIAL"), ("NO_ROUTE", "NO_ROUTE")])
def test_route_terminal_classifications_are_not_mail_success(engine, kind, expected):
    engine.transit_device.kind = kind
    task = create(engine)
    assert task["state"] == {"PARTIAL": "partial", "NO_ROUTE": "no_route"}[kind]
    assert not task["route_verified"] and outcome(task) == expected
    assert not engine.active()
    with pytest.raises(WorkflowError): engine.ask_transit(task["id"], task["query_id"], "在哪站下车？")


def test_stale_result_id_is_rejected_even_when_model_claims_complete(engine):
    engine.transit_device.return_stale = True
    task = create(engine)
    assert task["state"] == "failed" and not task.get("transit_result") and not task["route_verified"]


@pytest.mark.parametrize("question, fragment", [("第一步怎么走？", "广州塔"), ("我应该在哪一站下车？", "体育西路"), ("需要换乘几次？", "0次")])
def test_followups_only_use_current_verified_route(engine, question, fragment):
    task = create(engine)
    answer = engine.ask_transit(task["id"], task["query_id"], question)
    assert answer["query_id"] == task["query_id"] and fragment in answer["answer"]


def test_show_details_serialized_without_requery_or_navigation_and_keeps_result(engine):
    task = create(engine)
    original = task["transit_result"]
    task = settle(engine, engine.show_transit(task["id"], task["query_id"]))
    assert task["state"] == "completed" and task["transit_result"] == original
    assert engine.transit_device.calls[-1] == ("show", task["query_id"])


def test_restart_retains_route_branch_and_requeries_without_email_precheck(tmp_path):
    e = Engine(Store(tmp_path), Device(), Models(TransitRequest(destination="体育西路")))
    e.transit_device = Map(); e.transit_device.location_missing = True
    task = settle(e, e.create_goal("我想去体育西路坐公交", "restart-route-01"))
    assert task["state"] == "needs_clarification"
    query_id = task["query_id"]
    e.close()
    e = Engine(Store(tmp_path), Device(), Models()); e.transit_device = Map()
    try:
        task = e.store.get(task["id"])
        assert task["state"] == "interrupted"
        with pytest.raises(WorkflowError): e.control(task["id"], "retry_prepare")
        e.transit_device.location_missing = True
        task = settle(e, e.requery_transit(task["id"], {"query_id": query_id}))
        assert task["state"] == "needs_clarification"
        task = settle(e, e.clarify_transit(task["id"], {"query_id": task["query_id"], "origin": "广州塔", "city": "广州"}))
        assert task["task_type"] == "plan_transit" and task["state"] == "completed"
    finally:
        e.close()


def test_route_api_is_typed_and_websocket_contains_current_route_snapshot(engine):
    with patch("workflow_api.get_engine", return_value=engine):
        client = TestClient(gmail_demo.app)
        response = client.post("/api/tasks", json={"instruction": "在广州从广州塔去体育西路坐地铁", "request_key": "api-route-01"})
        assert response.status_code == 200
        task = settle(engine, response.json()["task"])
        url = f"/api/tasks/{task['id']}/transit"
        assert client.post(url + "/ask", json={"query_id": task["query_id"], "question": "第一步怎么走？"}).json()["query_id"] == task["query_id"]
        assert client.post(url + "/ask", json={"query_id": "wrong-query", "question": "第一步怎么走？"}).status_code == 409
        assert client.post(url + "/requery", json={"query_id": task["query_id"], "recipient": "bad@example.org"}).status_code == 422
        assert client.post(f"/api/tasks/{task['id']}/confirm", json={"draft_digest": "0"*64, "confirmed": True}).status_code == 409
        with client.websocket_connect(f"/ws/tasks/{task['id']}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["task"]["route_verified"]
            assert snapshot["task"]["transit_result"]["query_id"] == task["query_id"]
            assert "draft" not in snapshot["task"]


def test_selected_branch_cannot_be_replaced_by_generic_or_other_place(engine):
    engine.transit_device.choice = True; engine.transit_device.ignore_selected_place = True
    task = create(engine); selection = task["place_choice"]
    task = settle(engine, engine.choose_transit_place(task["id"], {"query_id": task["query_id"], "choice_nonce": selection["choice_nonce"], "candidate_id": "east"}))
    assert task["state"] == "partial" and not task["route_verified"]
    assert any("destination" in field for field in task["transit_result"]["missing_fields"])


def test_cancel_during_intent_model_does_not_launch_or_return_phone(engine):
    engine.models.gate = threading.Event()
    task = engine.create_goal("在广州从广州塔去体育西路，坐地铁", "cancel-model-route-01")
    assert engine.models.entered.wait(1)
    engine.control(task["id"], "cancel"); engine.models.gate.set()
    task = settle(engine, task)
    assert task["state"] == "cancelled" and not engine.transit_device.calls and not engine.device.calls


def test_cancelling_old_interrupted_task_cannot_cancel_new_worker(engine):
    task = create(engine)
    task["state"] = "interrupted"; engine.store.save(task, "old interrupted")
    engine.models.gate = threading.Event(); engine.models.entered.clear()
    new = engine.create_goal("在广州从广州塔去体育西路坐地铁", "newer-route-01")
    assert engine.models.entered.wait(1)
    old = engine.control(task["id"], "cancel")
    assert old["state"] == "cancelled" and not engine.cancelled
    engine.models.gate.set(); new = settle(engine, new)
    assert new["state"] == "completed"


def test_route_api_location_fallback_then_clarification_and_no_route_are_consistent(engine):
    engine.models.request = TransitRequest(destination="体育西路")
    engine.transit_device.location_missing = True
    engine.transit_device.kind = "NO_ROUTE"
    with patch("workflow_api.get_engine", return_value=engine):
        client = TestClient(gmail_demo.app)
        response = client.post("/api/tasks", json={"instruction": "我想去体育西路坐公交", "request_key": "api-location-fallback"})
        task = settle(engine, response.json()["task"])
        assert task["state"] == "needs_clarification" and task["transit_request"]["use_current_location"]
        assert "draft" not in task
        base = f"/api/tasks/{task['id']}/transit"
        stale = client.post(base + "/clarify", json={"query_id": "stale-query", "origin": "广州塔", "city": "广州"})
        assert stale.status_code == 409
        valid = client.post(base + "/clarify", json={"query_id": task["query_id"], "origin": "广州塔", "city": "广州"})
        assert valid.status_code == 200
        task = settle(engine, valid.json()["task"])
        assert task["state"] == "no_route" and task["transit_result"]["classification"] == "NO_ROUTE"
        assert not task["route_verified"] and outcome(task) == "NO_ROUTE"
        assert client.post(base + "/ask", json={"query_id": task["query_id"], "question": "第一步怎么走？"}).status_code == 409
        engine.transit_device.kind = "COMPLETE"
        old = task["query_id"]
        response = client.post(base + "/requery", json={"query_id": old, "preference": "less_walking"})
        assert response.status_code == 200
        task = settle(engine, response.json()["task"])
        assert task["query_id"] != old and task["route_verified"]


def test_route_api_place_nonce_then_detail_view_keeps_result(engine):
    engine.transit_device.choice = True
    with patch("workflow_api.get_engine", return_value=engine):
        client = TestClient(gmail_demo.app)
        response = client.post("/api/tasks", json={"instruction": "在广州从广州塔去体育西路坐地铁", "request_key": "api-place-selection"})
        task = settle(engine, response.json()["task"])
        choice = task["place_choice"]
        base = f"/api/tasks/{task['id']}/transit"
        body = {"query_id": task["query_id"], "choice_nonce": choice["choice_nonce"], "candidate_id": "east"}
        assert client.post(base + "/place", json=dict(body, candidate_id="invented")).status_code == 409
        response = client.post(base + "/place", json=body)
        assert response.status_code == 200
        task = settle(engine, response.json()["task"])
        assert task["route_verified"]
        assert client.post(base + "/place", json=body).status_code == 409
        original = task["transit_result"]
        response = client.post(base + "/show", json={"query_id": task["query_id"]})
        assert response.status_code == 200
        task = settle(engine, response.json()["task"])
        assert task["state"] == "completed" and task["transit_result"] == original


def test_route_progress_is_query_bound_cannot_finalize_and_discards_cancelled_callbacks(engine):
    engine.transit_device.gate = threading.Event()
    task = engine.create_goal("在广州从广州塔去体育西路坐地铁", "map-progress-query-01")
    assert engine.transit_device.entered.wait(1)
    current = engine.store.get(task["id"])
    callback = engine.transit_device.progress_callback
    assert callback("reading_transit", "正在读取本次高德分段")
    assert engine.store.get(task["id"])["state"] == "reading_transit"
    with pytest.raises(WorkflowError): callback("completed", "伪造完成")
    with pytest.raises(WorkflowError): engine._transit_progress(task["id"], "stale-query", "reading_transit", "过期结果")
    engine.control(task["id"], "cancel")
    event_count = len(engine.store.events(task["id"]))
    with pytest.raises(WorkflowError): callback("validating_transit", "取消后的迟到进度")
    assert len(engine.store.events(task["id"])) == event_count
    assert engine.store.get(task["id"])["state"] == "cancelled"
    engine.transit_device.gate.set(); settle(engine, task)


class AliasAndLocationMap(Map):
    """A real-name alternative is selectable, then a location fallback occurs."""
    selected = {"candidate_id": "museum-main", "name": "广东省博物馆", "address": "广州市天河区珠江东路2号", "city": "广州", "evidence_id": "poi-museum-observation"}

    def query(self, request, task_id, query_id, checkpoint, choices=None):
        checkpoint()
        self.calls.append(("query", copy.deepcopy(request), query_id, copy.deepcopy(choices)))
        if not choices.get("destination"):
            return {"kind": "choice", "role": "destination", "message": "高德实际名称与输入不同，请核对地址后确认。", "candidates": [dict(self.selected)]}
        assert request["destination"] == self.selected["name"]
        assert choices["destination"]["address"] == self.selected["address"]
        if not request["origin"]:
            return {"kind": "clarification", "fields": ["origin"], "message": "定位不可核对，请填写明确起点"}
        eid = "no-route-observation"
        result = {"task_id": task_id, "query_id": query_id, "route_id": "no-route-" + query_id,
                  "origin": {"name": request["origin"]}, "destination": {"name": request["destination"]},
                  "classification": "NO_ROUTE", "segments": []}
        return {"kind": "result", "result": result, "evidence": [{"evidence_id": eid, "query_id": query_id,
                "observation_id": eid, "page_kind": "route_list", "text": "暂无公交方案"}], "collection_complete": False}


def alias_goal(engine, key="alias-location-01"):
    engine.models.request = TransitRequest(destination="广东省博物馆新馆", city="广州")
    engine.transit_device = AliasAndLocationMap()
    return create(engine, key=key, instruction="在广州，我想去广东省博物馆新馆坐公交")


def test_current_candidate_choice_canonicalizes_alias_preserves_original_and_audit(engine):
    task = alias_goal(engine)
    assert task["state"] == "awaiting_place_choice"
    assert "输入不同" in task["message"] and "多个" not in task["message"]
    original_request = dict(task["transit_request"])
    selection = task["place_choice"]
    body = {"query_id": task["query_id"], "choice_nonce": selection["choice_nonce"], "candidate_id": "museum-main"}
    task = settle(engine, engine.choose_transit_place(task["id"], body))
    assert task["state"] == "needs_clarification"
    assert task["instruction"] == "在广州，我想去广东省博物馆新馆坐公交"
    assert task["transit_original_request"] == original_request
    assert task["transit_request"]["destination"] == "广东省博物馆"
    assert task["transit_choices"]["destination"] == AliasAndLocationMap.selected
    audit = task["transit_place_selections"][0]
    assert audit["query_id"] == task["query_id"] and audit["requested_name"] == "广东省博物馆新馆"
    assert audit["selected"]["evidence_id"] == "poi-museum-observation"
    assert audit["offered_candidate_ids"] == ["museum-main"] and audit["candidate_set_digest"]
    assert audit["choice_nonce_digest"] and "choice_nonce" not in audit
    with pytest.raises(WorkflowError): engine.choose_transit_place(task["id"], body)


def test_origin_fallback_keeps_confirmed_destination_address_even_full_form_reply(engine):
    task = alias_goal(engine)
    body = {"query_id": task["query_id"], "choice_nonce": task["place_choice"]["choice_nonce"], "candidate_id": "museum-main"}
    task = settle(engine, engine.choose_transit_place(task["id"], body))
    task = settle(engine, engine.clarify_transit(task["id"], {"query_id": task["query_id"], "origin": "体育西路地铁站",
        "destination": "广东省博物馆", "city": "广州", "use_current_location": False}))
    assert task["state"] == "no_route"
    assert task["transit_choices"]["destination"]["address"] == AliasAndLocationMap.selected["address"]
    request, choices = engine.transit_device.calls[-1][1], engine.transit_device.calls[-1][3]
    assert request["destination"] == "广东省博物馆" and choices["destination"] == AliasAndLocationMap.selected
    assert not request["use_current_location"]
    with pytest.raises(WorkflowError): engine.choose_transit_place(task["id"], body)


def test_stale_or_cancelled_alias_selection_cannot_change_requested_name(engine):
    task = alias_goal(engine)
    body = {"query_id": task["query_id"], "choice_nonce": task["place_choice"]["choice_nonce"], "candidate_id": "museum-main"}
    for invalid in [dict(body, query_id="older-query"), dict(body, choice_nonce="older-choice-nonce"), dict(body, candidate_id="not-offered")]:
        with pytest.raises(WorkflowError): engine.choose_transit_place(task["id"], invalid)
    unchanged = engine.store.get(task["id"])
    assert unchanged["transit_request"]["destination"] == "广东省博物馆新馆"
    assert unchanged["transit_place_selections"] == []
    engine.control(task["id"], "cancel")
    with pytest.raises(WorkflowError): engine._apply_place_choice(task["id"], body)
    assert engine.store.get(task["id"])["transit_request"]["destination"] == "广东省博物馆新馆"


@pytest.mark.parametrize("changes, remaining", [
    ({"origin": "别的明确起点"}, {"destination"}),
    ({"destination": "别的明确终点"}, {"origin"}),
    ({"city": "北京"}, set()),
    ({"origin": "广州塔", "destination": "体育西路", "city": "广州"}, {"origin", "destination"}),
])
def test_clarification_invalidates_only_actually_changed_places(engine, changes, remaining):
    task = create(engine)
    task.update(state="needs_clarification", transit_choices={
        "origin": {"candidate_id": "origin-one", "name": "广州塔", "address": "起点原地址"},
        "destination": {"candidate_id": "destination-one", "name": "体育西路", "address": "终点原地址"}})
    engine.store.save(task, "请补充信息")
    engine._apply_transit_clarification(task["id"], {"query_id": task["query_id"], **changes})
    task = engine.store.get(task["id"])
    assert set(task["transit_choices"]) == remaining


class CurrentLocationMap(Map):
    """Query-bound current-position double for ownership/source tests."""
    def __init__(self):
        super().__init__()
        self.require_choice = False
        self.position_source = "current_location"
        self.stale_candidate = False
        self.bad_location_field = None

    def query(self, request, task_id, query_id, checkpoint, choices=None):
        checkpoint()
        self.entered.set()
        self.calls.append(("query", copy.deepcopy(request), query_id, copy.deepcopy(choices)))
        if self.gate: self.gate.wait(3)
        checkpoint()
        selected = {"candidate_id": "location-" + query_id, "name": "广州塔", "address": "广州市海珠区", "city": "广州",
                    "source": "current_location", "query_id": "old-location-query" if self.stale_candidate else query_id,
                    "evidence_refs": {k: ["places"] for k in ["name", "address", "city"]}}
        if self.bad_location_field:
            if self.bad_location_field == "name": selected["name"] = "我的位置"
            elif self.bad_location_field == "evidence_refs": selected["evidence_refs"] = {}
            else: selected[self.bad_location_field] = None
        if self.require_choice and not choices.get("origin"):
            return {"kind": "choice", "role": "origin", "candidates": [selected]}
        value = route_result(task_id, query_id)
        value["evidence"][0]["location_status"] = {
            "verified": True, "reason": "synthetic health fixture", "enabled": True,
            "permission_granted": True, "app_access_recent": True, "provider": "gps",
            "age_seconds": 20, "accuracy_m": 5, "is_mock": False,
            "checked_at": "2026-11-12T03:00:20+00:00", "fix_time": "2026-11-12T03:00:00+00:00",
            "user_id": 0, "appops_mode": "allow", "app_access_age_seconds": 1,
            "clock_basis": "device_epoch_and_elapsed"}
        value["result"]["origin"].update(source=self.position_source, address="广州市海珠区")
        value["result"]["origin"]["evidence_refs"]["address"] = ["places"]
        return value


def location_goal(engine, key="real-position-query-01"):
    engine.models.request = TransitRequest(destination="体育西路", city="广州", use_current_location=True)
    return create(engine, key=key, instruction="在广州，我想去体育西路坐地铁")


def test_verified_current_position_result_does_not_require_explicit_origin(engine):
    engine.transit_device = CurrentLocationMap()
    task = location_goal(engine)
    assert task["state"] == "completed" and task["route_verified"]
    assert task["transit_request"]["origin"] == "" and task["transit_request"]["use_current_location"]
    assert task["transit_result"]["origin"]["source"] == "current_location"
    assert task["transit_result"]["origin"]["address"] == "广州市海珠区"


@pytest.mark.parametrize("source, state", [("current_location", "completed"), ("explicit", "partial")])
def test_current_position_choice_preserves_location_proof_requirement(engine, source, state):
    engine.transit_device = CurrentLocationMap(); engine.transit_device.require_choice = True
    engine.transit_device.position_source = source
    task = location_goal(engine)
    assert task["state"] == "awaiting_place_choice"
    choice = task["place_choice"]
    task = settle(engine, engine.choose_transit_place(task["id"], {"query_id": task["query_id"], "choice_nonce": choice["choice_nonce"],
        "candidate_id": choice["candidates"][0]["candidate_id"]}))
    assert task["state"] == state
    assert task["transit_request"]["origin"] == "" and task["transit_request"]["use_current_location"]
    assert task["transit_choices"]["origin"]["source"] == "current_location"
    if source == "explicit":
        assert not task["route_verified"]
        assert "origin.current_location_not_verified" in task["transit_result"]["missing_fields"]


@pytest.mark.parametrize("bad_field", ["name", "address", "city", "evidence_refs"])
def test_unverifiable_current_position_candidate_is_not_offered(engine, bad_field):
    engine.transit_device = CurrentLocationMap(); engine.transit_device.require_choice = True
    engine.transit_device.bad_location_field = bad_field
    task = location_goal(engine)
    assert task["state"] == "failed" and not task.get("place_choice")
    assert task["transit_request"]["origin"] == "" and not task["route_verified"]


def test_old_query_location_candidate_is_rejected_before_user_confirmation(engine):
    engine.transit_device = CurrentLocationMap(); engine.transit_device.require_choice = True
    engine.transit_device.stale_candidate = True
    task = location_goal(engine)
    assert task["state"] == "failed" and not task.get("place_choice")
    assert "过期" in task["message"] and not task["route_verified"]


def test_current_location_choice_cannot_override_an_explicit_origin(engine):
    engine.transit_device = CurrentLocationMap(); engine.transit_device.require_choice = True
    task = create(engine)
    assert task["state"] == "failed"
    assert task["transit_request"]["origin"] == "广州塔"
    assert not task["transit_request"]["use_current_location"]


def test_requery_requires_fresh_location_and_new_position_confirmation(engine):
    engine.transit_device = CurrentLocationMap(); engine.transit_device.require_choice = True
    task = location_goal(engine)
    choice = task["place_choice"]
    old_body = {"query_id": task["query_id"], "choice_nonce": choice["choice_nonce"], "candidate_id": choice["candidates"][0]["candidate_id"]}
    task = settle(engine, engine.choose_transit_place(task["id"], old_body))
    old_query = task["query_id"]
    task = settle(engine, engine.requery_transit(task["id"], {"query_id": old_query}))
    assert task["query_id"] != old_query and task["state"] == "awaiting_place_choice"
    assert not task["transit_choices"]
    assert task["transit_request"]["origin"] == "" and task["transit_request"]["use_current_location"]
    assert task["place_choice"]["candidates"][0]["query_id"] == task["query_id"]
    with pytest.raises(WorkflowError): engine.choose_transit_place(task["id"], old_body)


def test_leaving_location_mode_invalidates_only_the_position_choice(engine):
    engine.transit_device = CurrentLocationMap(); engine.transit_device.require_choice = True
    task = location_goal(engine); choice = task["place_choice"]
    task = settle(engine, engine.choose_transit_place(task["id"], {"query_id": task["query_id"], "choice_nonce": choice["choice_nonce"],
        "candidate_id": choice["candidates"][0]["candidate_id"]}))
    task.update(state="needs_clarification")
    task["transit_choices"]["destination"] = {"candidate_id": "dest", "name": "体育西路", "address": "目标原地址"}
    engine.store.save(task, "请补充明确起点")
    engine._apply_transit_clarification(task["id"], {"query_id": task["query_id"], "use_current_location": False})
    task = engine.store.get(task["id"])
    assert set(task["transit_choices"]) == {"destination"}
    assert task["transit_request"]["origin"] == "" and not task["transit_request"]["use_current_location"]



def test_cancelled_position_selection_never_becomes_an_explicit_start(engine):
    engine.transit_device = CurrentLocationMap(); engine.transit_device.require_choice = True
    task = location_goal(engine); choice = task["place_choice"]
    body = {"query_id": task["query_id"], "choice_nonce": choice["choice_nonce"], "candidate_id": choice["candidates"][0]["candidate_id"]}
    engine.control(task["id"], "cancel")
    with pytest.raises(WorkflowError): engine.choose_transit_place(task["id"], body)
    task = engine.store.get(task["id"])
    assert task["state"] == "cancelled" and not task["transit_choices"]
    assert task["transit_request"]["origin"] == "" and task["transit_request"]["use_current_location"]
