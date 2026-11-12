"""Pure mock checks of the DUMP-protected content call CLI contract."""
import base64
import subprocess
from unittest.mock import Mock

import pytest

import gmail_demo
from device.mobilerun import call_transit_bridge


def valid_extras():
    return {"field": "origin", "text": base64.urlsafe_b64encode("广州塔".encode()).decode(),
            "resource_id": "com.autonavi.minimap:id/input", "bounds": "[10,20][1100,200]", "expected_text": ""}


def provider_run(command, *, result=1, reason="ok", data="XML-payload", nonce=None):
    nonce = nonce if nonce is not None else command[command.index("--arg") + 1]
    output = f"Result: Bundle[{{code={result}, result={result}, data={data}, reason={reason}, nonce={nonce}}}]"
    return subprocess.CompletedProcess(command, 0, output, "")


def test_dump_uses_only_content_call_authority_method_and_fresh_nonce(monkeypatch, capsys):
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    monkeypatch.setenv("ANDROID_SERIAL", "offline-test-device")
    run = Mock(side_effect=lambda command, **kwargs: provider_run(command))
    monkeypatch.setattr(subprocess, "run", run)
    first, second = call_transit_bridge("dump_transit_ui"), call_transit_bridge("dump_transit_ui")
    assert first["result"] == 1 and first["data"] == "XML-payload" and first["reason"] == "ok"
    assert len(first["nonce"]) == 32 and first["nonce"] != second["nonce"]
    command = run.call_args.args[0]
    assert command[:3] == ["adb-offline", "-s", "offline-test-device"]
    assert command[3:9] == ["shell", "content", "call", "--uri", "content://com.example.demo.transit", "--method"]
    assert "broadcast" not in command and "uiautomator" not in command
    assert run.call_args.kwargs["timeout"] == 9 and run.call_args.kwargs["capture_output"]
    assert capsys.readouterr().out == ""


def test_input_extra_values_are_typed_strings_and_write_success_does_not_pretend_to_be_xml(monkeypatch):
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    run = Mock(side_effect=lambda command, **kwargs: provider_run(command, data="ok"))
    monkeypatch.setattr(subprocess, "run", run)
    extras = valid_extras();response = call_transit_bridge("set_transit_field", extras)
    assert response["result"] == 1 and response["data"] is None
    command = run.call_args.args[0]
    sent = {command[i+1] for i, part in enumerate(command) if part == "--extra"}
    assert sent == {f"{key}:s:{value}" for key, value in extras.items()}
    assert "set_transit_field" in command


@pytest.mark.parametrize("operation,change", [
    ("shell", None), ("dump_transit_ui", {"text": "ignored"}),
    ("set_transit_field", {"unknown": "x"}), ("set_transit_field", {"field": "body"}),
    ("set_transit_field", {"text": "$(not-a-command)"}),
    ("set_transit_field", {"resource_id": "id;not-a-command"}),
    ("set_transit_field", {"bounds": "10,20"}), ("set_transit_field", {"expected_text": 42}),
])
def test_unsupported_operations_or_unbound_payloads_never_start_a_process(monkeypatch, operation, change):
    run = Mock();monkeypatch.setattr(subprocess, "run", run)
    extras = valid_extras() if operation == "set_transit_field" else {}
    extras.update(change or {})
    with pytest.raises(ValueError): call_transit_bridge(operation, extras)
    run.assert_not_called()


@pytest.mark.parametrize("failure", ["stale_nonce", "duplicate_bundle", "wrong_status", "unknown_reason", "missing_result"])
def test_bundle_status_and_nonce_are_strictly_bound_to_this_call(monkeypatch, failure):
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    def run(command, **kwargs):
        response = provider_run(command, nonce="expired-nonce" if failure == "stale_nonce" else None)
        if failure == "duplicate_bundle": response.stdout += "\n" + response.stdout
        if failure == "wrong_status": response.stdout = response.stdout.replace("code=1", "code=0")
        if failure == "unknown_reason": response.stdout = response.stdout.replace("reason=ok", "reason=arbitrary_remote_text")
        if failure == "missing_result": response.stdout = response.stdout.replace("result=1, ", "")
        return response
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError): call_transit_bridge("dump_transit_ui")


@pytest.mark.parametrize("problem,expected", [("timeout", "bridge_timeout"), ("missing_provider", "bridge_unavailable"), ("process_error", "bridge_unavailable")])
def test_transport_failures_are_explicit_without_forging_a_provider_nonce(monkeypatch, problem, expected):
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    def run(command, **kwargs):
        if problem == "timeout": raise subprocess.TimeoutExpired(command, 9)
        if problem == "process_error": raise OSError("not installed")
        return subprocess.CompletedProcess(command, 0, "No content provider found", "")
    monkeypatch.setattr(subprocess, "run", run)
    response = call_transit_bridge("dump_transit_ui")
    assert response == {"result": 0, "data": None, "reason": expected, "nonce": None}


def test_known_provider_failure_reason_still_requires_matching_nonce(monkeypatch):
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: provider_run(command, result=0, reason="bridge_busy", data="bridge_busy"))
    response = call_transit_bridge("dump_transit_ui")
    assert response["result"] == 0 and response["reason"] == "bridge_busy" and len(response["nonce"]) == 32
    assert response["data"] is None


def test_actual_zero_matching_input_field_reason_is_preserved_from_provider_bundle(monkeypatch):
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-offline")
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: provider_run(command, result=0, reason="field_match_0", data="field_match_0"))
    response = call_transit_bridge("set_transit_field", valid_extras())
    assert response["result"] == 0 and response["reason"] == "field_match_0"
    assert response["data"] is None and len(response["nonce"]) == 32
