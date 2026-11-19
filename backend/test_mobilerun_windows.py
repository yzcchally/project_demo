import base64
import builtins
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import gmail_demo
from device import mobilerun


def patch_sdk_import(monkeypatch, platform, missing_module):
    original_import = builtins.__import__

    def import_module(name, *args, **kwargs):
        if name == "mobilerun_core":
            raise ModuleNotFoundError(name=missing_module)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(mobilerun, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(builtins, "__import__", import_module)


@pytest.fixture
def windows_transport(monkeypatch):
    patch_sdk_import(monkeypatch, "win32", "fcntl")
    monkeypatch.setattr(gmail_demo, "_adb", lambda: "adb-fixture")
    monkeypatch.setenv("ANDROID_SERIAL", "selected-device")
    return mobilerun.MobilerunTransport()


@pytest.mark.parametrize("explicit_serial", [False, True])
def test_windows_fallback_keeps_all_operations_on_selected_device(windows_transport, monkeypatch, explicit_serial):
    if not explicit_serial:
        monkeypatch.delenv("ANDROID_SERIAL")
    xml = b'<hierarchy><node package="com.huawei.email" /></hierarchy>'
    png = b"\x89PNG\r\n\x1a\nfixture"
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        stdout = {
            "get-serialno": "selected-device\n",
            "cat": xml,
            "screencap": png,
        }.get(command[-2] if len(command) > 2 else command[-1], b"")
        return subprocess.CompletedProcess(command, 0, stdout)

    monkeypatch.setattr(subprocess, "run", run)
    assert windows_transport.xml() == xml.decode()
    assert windows_transport.last_ui_source == "adb-file-fallback"
    windows_transport.tap(12, 34)
    assert base64.b64decode(windows_transport.screenshot()) == png

    if not explicit_serial:
        assert commands.pop(0) == ["adb-fixture", "get-serialno"]
    assert all(command[:3] == ["adb-fixture", "-s", "selected-device"] for command in commands)
    dump, read, cleanup, tap, screenshot = [command[3:] for command in commands]
    assert dump[:3] == ["shell", "uiautomator", "dump"]
    assert dump[-1].startswith("/sdcard/silverguide-") and dump[-1].endswith(".xml")
    assert read == ["exec-out", "cat", dump[-1]]
    assert cleanup == ["shell", "rm", "-f", dump[-1]]
    assert tap == ["shell", "input", "tap", "12", "34"]
    assert screenshot == ["exec-out", "screencap", "-p"]


def test_windows_fallback_rejects_invalid_screenshot(windows_transport, monkeypatch):
    monkeypatch.setattr(subprocess, "run", Mock(return_value=SimpleNamespace(stdout=b"invalid image")))

    with pytest.raises(RuntimeError, match="手机截图"):
        windows_transport.screenshot()


def test_windows_fallback_keeps_protected_map_observation(windows_transport, monkeypatch):
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    windows_transport.tap(12, 34)
    run.reset_mock()
    windows_transport.prefer_transit_bridge = True
    protected = Mock(side_effect=RuntimeError("invalid protected response"))
    monkeypatch.setattr(windows_transport, "_protected_transit_xml", protected)

    with pytest.raises(RuntimeError, match="invalid protected response"):
        windows_transport.xml()

    protected.assert_called_once_with()
    run.assert_not_called()


@pytest.mark.parametrize("platform, missing_module", [("win32", "unrelated"), ("linux", "fcntl")])
def test_sdk_import_errors_outside_windows_fcntl_still_surface(monkeypatch, platform, missing_module):
    patch_sdk_import(monkeypatch, platform, missing_module)

    with pytest.raises(ModuleNotFoundError) as error:
        mobilerun.MobilerunTransport().xml()

    assert error.value.name == missing_module
