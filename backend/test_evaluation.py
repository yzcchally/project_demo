from evaluation.metrics import summarize, outcome, explanation_audit


def test_unknown_stop_and_failed_are_not_counted_as_verified_success():
    tasks = [{"state": "completed", "sent_record_verified": True}, {"state": "completed"}, {"state": "cancelled"}, {"state": "failed"}, {"state": "outcome_unknown"}]
    report = summarize(tasks)
    assert report["verified_task_success_rate"] == .2
    assert report["independent_completion_rate"] is None
    assert report["recovery_rate"] is None


def test_open_url_success_requires_browser_evidence():
    assert outcome({"state": "completed", "task_type": "open_url", "webpage_verified": True}) == "SUCCESS"
    assert outcome({"state": "completed", "task_type": "open_url"}) == "UNKNOWN"


def test_assistance_is_not_mistaken_for_independent_completion():
    report = summarize([{"state": "completed", "sent_record_verified": True, "third_party_assistance": True}, {"state": "completed", "delivery_confirmed": True, "third_party_assistance": False}])
    assert report["independent_completion_rate"] == .5


def test_success_explanation_needs_evidence_and_legacy_unknown_is_separate():
    result = explanation_audit([{"message": "邮件已发送", "evidence": {}}, {"message": "已核对已发送记录", "evidence": {"sent_record_verified": True}}, {"message": "发送成功"}])
    assert result["explanation_consistency"] == .5
    assert result["legacy_claims_without_snapshot"] == 1


def test_safe_handling_requires_explicit_complete_assessment():
    assert summarize([{"state": "cancelled", "abnormal_case": True}])["safe_handling_rate"] is None
    cases = [{"state": "cancelled", "abnormal_case": True, "safe_handling_verified": True},
             {"state": "failed", "abnormal_case": True, "safe_handling_verified": False}]
    assert summarize(cases)["safe_handling_rate"] == .5
    assert summarize(cases)["action_success_rate"] is None


def test_report_reads_all_event_pages_and_only_matching_real_task_model_calls(tmp_path):
    import json
    from types import SimpleNamespace
    from evaluation.metrics import build_report
    def events(task_id, cursor):
        assert task_id == "phone"
        return [{"seq": cursor + 1, "message": "已核对已发送记录", "evidence": {"sent_record_verified": True}}] if cursor < 102 else []
    folder = tmp_path / "model-calls"; folder.mkdir()
    (folder / "calls.jsonl").write_text('\n'.join([json.dumps({"task_id": "phone", "stage": "perception", "ok": True, "latency_ms": 1500, "tokens": 20}), json.dumps({"task_id": "mock", "stage": "understand", "ok": True}), 'partial line']))
    store = SimpleNamespace(directory=tmp_path, all=lambda: [{"id": "phone", "state": "cancelled", "send_attempts": 0, "environment": "real_device"}, {"id": "mock", "state": "completed", "send_attempts": 0, "environment": "test_double"}], events=events)
    report = build_report(store)
    assert report["explanation_audit"]["evaluated_claims"] == 102
    assert report["model_calls"]["count"] == 1
    assert report["model_calls"]["mean_latency_ms"] == 1500
    assert report["model_calls"]["malformed_log_lines"] == 1
