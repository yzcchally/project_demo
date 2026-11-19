"""Selected history rows can only return through the guarded workflow."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import gmail_demo
from task_history import return_options
from test_workflow import Device
from workflow import Engine, Store


@pytest.fixture
def mail_task(tmp_path):
    device = Device()
    engine = Engine(Store(tmp_path), device)
    try:
        task = engine.create("receiver@example.org", "workflow-test", "sample", "", "text", "request-0001")
        engine.worker.join(2)
        yield engine, device, engine.store.get(task["id"])
    finally:
        if engine.worker:
            engine.worker.join(2)
        engine.close()


def test_return_to_draft_is_revision_bound_and_requires_fresh_confirmation(mail_task):
    engine, device, task = mail_task
    task_id = task["id"]
    step = next(row for row in engine.store.events(task_id) if row["state"] == "awaiting_confirmation")
    path = f"/api/tasks/{task_id}/return-step/{step['seq']}"
    with patch("api.routes.get_engine", return_value=engine):
        client = TestClient(gmail_demo.app)
        option = client.get(path).json()
        assert option["action"]["kind"] == "review_draft"
        assert client.post(path, json={"expected_revision": option["revision"]}).status_code == 200
        assert client.post(path, json={"expected_revision": option["revision"]}).status_code == 409
        engine.worker.join(2)
        current = engine.store.get(task_id)
        assert current["state"] == "awaiting_confirmation"
        assert current["approval_nonce"] != task["approval_nonce"]
        assert not current["send_attempts"]
        assert "send" not in [call[0] for call in device.calls]


def test_return_after_send_is_read_only_even_for_ambiguous_results(mail_task):
    engine, device, task = mail_task
    device.send_crash = True
    task_id = task["id"]
    engine.approve(task_id, task["draft_digest"], True, task["approval_nonce"])
    engine.worker.join(2)
    rows = engine.store.events(task_id)
    draft_step = next(row for row in rows if row["state"] == "awaiting_confirmation")
    send_step = next(row for row in rows if row["send_attempts"] == 1)
    assert return_options(engine, task_id, draft_step["seq"])["action"] is None

    with patch("api.routes.get_engine", return_value=engine):
        client = TestClient(gmail_demo.app)
        path = f"/api/tasks/{task_id}/return-step/{send_step['seq']}"
        option = client.get(path).json()
        assert option["action"]["kind"] == "verify_only"
        assert client.post(path, json={"expected_revision": option["revision"]}).status_code == 200
        assert client.post(path, json={"expected_revision": option["revision"]}).status_code == 409
        engine.worker.join(2)
    assert [call[0] for call in device.calls].count("send") == 1
    assert engine.store.get(task_id)["send_attempts"] == 1


def test_navigation_attempt_closes_route_checkpoints(mail_task):
    engine, _, task = mail_task
    task_id = task["id"]
    task.update(task_type="plan_transit", query_id="route-a", state="planning_transit")
    engine.store.save(task, "正在高德查询公交地铁路线")
    step = engine.store.events(task_id)[-1]
    task.update(state="completed", transit_navigation_attempts=0)
    engine.store.save(task, "已核对高德路线")
    assert return_options(engine, task_id, step["seq"])["action"]["kind"] == "requery_transit"

    task.update(transit_navigation_attempts=1, transit_navigation_status="unknown")
    engine.store.save(task, "导航是否开始暂时无法确认")
    assert return_options(engine, task_id, step["seq"])["action"] is None
