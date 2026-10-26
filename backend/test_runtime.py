"""Verify ADB discovery for both teammates' Windows SDKs and this Mac."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gmail_demo
import recognize


class AdbDiscoveryTests(unittest.TestCase):
    def make_adb(self, root: Path, name: str = "adb") -> Path:
        path = root / "platform-tools" / name
        path.parent.mkdir(parents=True)
        path.touch()
        path.chmod(0o755)
        return path

    def test_macos_default_sdk_without_path_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            adb = self.make_adb(home / "Library/Android/sdk")
            with patch.dict(os.environ, {}, clear=True), patch("gmail_demo.shutil.which", return_value=None), patch("gmail_demo.sys.platform", "darwin"), patch("gmail_demo.Path.home", return_value=home):
                self.assertEqual(gmail_demo._adb(), str(adb))

    def test_existing_windows_localappdata_sdk_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            adb = self.make_adb(local / "Android/Sdk", "adb.exe")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}, clear=True), patch("gmail_demo.shutil.which", return_value=None), patch("gmail_demo.sys.platform", "win32"):
                self.assertEqual(gmail_demo._adb(), str(adb))

    def test_explicit_bad_path_reports_configuration_error(self):
        with patch.dict(os.environ, {"ADB_PATH": "/missing/project-demo-adb"}), patch("gmail_demo.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ADB_PATH"):
                gmail_demo._adb()

    def test_custom_sdk_location_works(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = Path(directory)
            adb = self.make_adb(sdk)
            with patch.dict(os.environ, {"ANDROID_HOME": str(sdk)}, clear=True), patch("gmail_demo.shutil.which", return_value=None), patch("gmail_demo.sys.platform", "darwin"):
                self.assertEqual(gmail_demo._adb(), str(adb))


class ModelTaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        recognize._stop_event.clear()
        recognize._running = True
        recognize._state = "running"

    def tearDown(self):
        recognize._stop_event.clear()
        recognize._running = False
        recognize._state = "idle"

    def test_completed_task_stops_without_another_model_call(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(recognize, "LOG_FILE", Path(directory) / "run.log"), patch.object(recognize, "INTERVAL_SECONDS", 0), patch.object(recognize, "_screenshot", return_value=b"screen"), patch.object(recognize, "_ask", return_value="done\n解释：设置已经打开。") as ask, patch.object(gmail_demo, "_adb") as adb:
            recognize._run(lambda: "<hierarchy />", "打开设置", "", "")
            self.assertEqual(ask.call_count, 1)
            adb.assert_not_called()
            self.assertEqual(recognize.status()["state"], "completed")
            self.assertFalse(recognize.running())

    def test_stop_during_model_call_does_not_execute_late_command(self):
        def late_response(*args):
            recognize.stop()
            return "home\n解释：返回桌面。"

        with patch.object(recognize, "INTERVAL_SECONDS", 0), patch.object(recognize, "_screenshot", return_value=b"screen"), patch.object(recognize, "_ask", side_effect=late_response), patch.object(recognize, "_execute") as execute:
            recognize._run(lambda: "<hierarchy />", "打开设置", "", "")
            execute.assert_not_called()
            self.assertEqual(recognize.status()["state"], "stopped")
            self.assertFalse(recognize.running())


class GmailPrerequisiteTests(unittest.TestCase):
    def test_missing_gmail_does_not_start_phone_actions(self):
        missing = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(gmail_demo, "_running", False), patch.object(recognize, "_running", False), patch.object(gmail_demo.subprocess, "run", return_value=missing), patch.object(gmail_demo.threading, "Thread") as thread:
            result = gmail_demo.start(lambda: "<hierarchy />", "adb")
            self.assertFalse(result["ok"])
            self.assertIn("原生 Gmail", result["message"])
            thread.assert_not_called()


class QQMailBodyInputTests(unittest.TestCase):
    @staticmethod
    def editor(text: str) -> str:
        import xml.etree.ElementTree as ET

        root = ET.Element("hierarchy")
        ET.SubElement(root, "node", {
            "package": "com.tencent.androidqqmail",
            "resource-id": "QMUIEditor",
            "focused": "true",
            "text": text,
        })
        return ET.tostring(root, encoding="unicode")

    def test_repeated_body_input_does_not_append_duplicate(self):
        with patch.object(gmail_demo, "_dump_screen_xml", return_value=self.editor("demo\n\n")), patch.object(recognize.subprocess, "run") as run:
            self.assertIn("正文已核对", recognize._input_text("adb", "demo"))
            run.assert_not_called()

    def test_replacement_requires_old_body_to_be_cleared(self):
        with patch.object(gmail_demo, "_dump_screen_xml", return_value=self.editor("old body")), patch.object(recognize.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertIn("避免重复追加", recognize._input_text("adb", "demo"))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][2:4], ["input", "keyevent"])

    def test_replacement_checks_actual_body_after_keyboard_input(self):
        screens = [self.editor("old body"), self.editor(""), self.editor("demo\n\n")]
        with patch.object(gmail_demo, "_dump_screen_xml", side_effect=screens), patch.object(recognize.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertIn("正文已核对", recognize._input_text("adb", "demo"))
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0], ["adb", "shell", "input text demo"])


if __name__ == "__main__":
    unittest.main()
