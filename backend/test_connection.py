"""USB tunnel maintenance without an attached device or a real ADB process."""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from device.connection import ReverseMonitor


def response(stdout="", code=0):
    return SimpleNamespace(stdout=stdout, returncode=code)


def monitor(*responses, serial=None):
    run = Mock(side_effect=responses)
    return ReverseMonitor(adb="test-adb", serial=serial, run=run), run


def test_missing_tunnel_is_restored_only_for_the_observed_device(monkeypatch):
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    watch, run = monitor(response("List of devices attached\nphone\tdevice\n"), response(), response())
    assert watch.poll() == "restored"
    assert watch.serial == "phone"
    assert run.call_args_list[-1].args[0] == ["test-adb", "-s", "phone", "reverse", "--no-rebind", "tcp:8000", "tcp:8000"]
    assert all(call.kwargs["timeout"] == 3 for call in run.call_args_list)


def test_existing_tunnel_does_not_write_or_rebind():
    watch, run = monitor(response("phone\tdevice"), response("UsbFfs tcp:8000 tcp:8000"), serial="phone")
    assert watch.poll() == "connected"
    assert run.call_count == 2


def test_other_forwarding_is_preserved():
    watch, run = monitor(response("phone\tdevice"), response("UsbFfs tcp:8000 tcp:9000"), serial="phone")
    assert watch.poll() == "port_in_use"
    assert run.call_count == 2


@pytest.mark.parametrize("listing,status", [
    ("List of devices attached\n", "waiting_for_device"),
    ("phone\tunauthorized", "unauthorized"),
    ("phone\toffline", "offline"),
    ("phone\tdevice\nother\tdevice", "multiple_devices"),
    ("phone\tdevice\nother\tunauthorized", "multiple_devices"),
])
def test_no_mutations_without_one_authorized_device(monkeypatch, listing, status):
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    watch, run = monitor(response(listing))
    assert watch.poll() == status
    assert run.call_count == 1
    assert watch.serial is None


def test_disconnect_and_reconnect_restore_tunnel_without_switching_device(monkeypatch):
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    watch, run = monitor(response("phone\tdevice"), response(), response(),
                         response(), response("other\tdevice"),
                         response("phone\tdevice"), response(), response())
    assert watch.poll() == "restored"
    assert watch.poll() == "waiting_for_device"
    assert watch.poll() == "waiting_for_device"
    assert watch.poll() == "restored"
    assert watch.serial == "phone"
    assert not any("other" in call.args[0] for call in run.call_args_list)


def test_explicit_serial_selects_only_the_requested_device():
    watch, run = monitor(response("other\tdevice\nphone\tdevice"), response(), response(), serial="phone")
    assert watch.poll() == "restored"
    assert run.call_args_list[-1].args[0][1:3] == ["-s", "phone"]


def test_environment_serial_is_honored(monkeypatch):
    monkeypatch.setenv("ANDROID_SERIAL", "configured-phone")
    watch, run = monitor(response("other\tdevice"))
    assert watch.poll() == "waiting_for_device"
    assert run.call_count == 1


@pytest.mark.parametrize("error", [OSError("missing"), subprocess.TimeoutExpired("adb", 3)])
def test_adb_errors_are_retryable(error):
    watch, run = monitor(error, response("phone\tdevice"), response(), response(), serial="phone")
    assert watch.poll() == "adb_unavailable"
    assert watch.poll() == "restored"


def test_missing_adb_does_not_prevent_backend_construction_and_can_recover():
    with patch("gmail_demo._adb", side_effect=[RuntimeError("not installed"), "new-adb"]):
        watch = ReverseMonitor(serial="phone", run=Mock(return_value=response()))
        assert watch.poll() == "adb_unavailable"
        assert watch.poll() == "waiting_for_device"
        assert watch.adb == "new-adb"


def test_disconnect_during_reverse_read_is_not_reported_as_success():
    watch, run = monitor(response("phone\tdevice"), response(code=1), serial="phone")
    assert watch.poll() == "tunnel_unavailable"
    assert run.call_count == 2


def test_failed_reverse_is_retried_on_next_poll():
    watch, _ = monitor(response("phone\tdevice"), response(), response(code=1),
                       response("phone\tdevice"), response(), response(), serial="phone")
    assert watch.poll() == "tunnel_unavailable"
    assert watch.poll() == "restored"


def test_stop_between_observation_and_mutation_prevents_reverse():
    watch = ReverseMonitor(adb="test-adb", serial="phone")
    def run(command, **kwargs):
        if command[-1] == "devices":
            return response("phone\tdevice")
        watch.stopped.set()
        return response()
    watch.run = Mock(side_effect=run)
    assert watch.poll() == "stopped"
    assert watch.run.call_count == 2


def test_monitor_thread_stops_with_backend_context():
    watch, _ = monitor(response(), serial="phone")
    watch.interval = 60
    with watch:
        assert watch.thread is not None
    assert watch.stopped.is_set()
    assert not watch.thread.is_alive()
