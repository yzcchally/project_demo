"""Maintain the local API tunnel while the official backend process is alive.

Only adb's port forwarding is touched. Reconnecting never launches an app,
restarts a workflow, reinstalls an APK, or repeats an external action.
"""
import logging
import os
import subprocess
import threading


log = logging.getLogger("silverguide.connection")


class ReverseMonitor:
    def __init__(self, *, adb=None, serial=None, interval=3, run=None):
        self.adb = adb
        self.serial = serial or os.environ.get("ANDROID_SERIAL") or None
        self.interval = interval
        self.run = run or subprocess.run
        self.stopped = threading.Event()
        self.thread = None
        self.last_status = None

    def _command(self, *args):
        if self.stopped.is_set():
            return None
        if self.adb is None:
            from gmail_demo import _adb
            self.adb = _adb()
        return self.run([self.adb, *args], capture_output=True, text=True, timeout=3)

    def poll(self):
        """Restore a missing tunnel for one pinned, authorized device only."""
        try:
            result = self._command("devices")
            if result is None:
                return "stopped"
            if result.returncode:
                return "adb_unavailable"
            devices = {}
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 2 and fields[1] in {"device", "offline", "unauthorized"}:
                    devices[fields[0]] = fields[1]
            if self.serial is None:
                if not devices:
                    return "waiting_for_device"
                if len(devices) != 1:
                    return "multiple_devices"
                serial, state = next(iter(devices.items()))
                if state != "device":
                    return state
                # Keep the same identity across disconnects. A different phone
                # must not acquire this backend just because it was plugged in.
                self.serial = serial
            if self.serial not in devices:
                return "waiting_for_device"
            if devices[self.serial] != "device":
                return devices[self.serial]
            result = self._command("-s", self.serial, "reverse", "--list")
            if result is None:
                return "stopped"
            if result.returncode:
                return "tunnel_unavailable"
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 2 and fields[-2] == "tcp:8000":
                    return "connected" if fields[-1] == "tcp:8000" else "port_in_use"
            result = self._command("-s", self.serial, "reverse", "--no-rebind", "tcp:8000", "tcp:8000")
            if result is None:
                return "stopped"
            return "restored" if result.returncode == 0 else "tunnel_unavailable"
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return "adb_unavailable"

    def _watch(self):
        messages = {
            "restored": "手机到后端的 USB 转发已恢复。",
            "connected": "手机到后端的 USB 转发正常。",
            "waiting_for_device": "等待原测试手机连接；连接后将自动恢复 USB 转发。",
            "multiple_devices": "检测到多台设备，请只连接测试手机，或设置 ANDROID_SERIAL 后重启后端。",
            "unauthorized": "请解锁手机并允许 USB 调试。",
            "offline": "手机调试连接离线，正在等待重新连接。",
            "adb_unavailable": "暂时无法连接 ADB，稍后重试。",
            "tunnel_unavailable": "暂时无法恢复 USB 转发，稍后重试。",
            "port_in_use": "手机 8000 端口已有其他转发，请手动核对。",
        }
        while not self.stopped.is_set():
            status = self.poll()
            if status != self.last_status and status in messages:
                log.warning("[USB] %s", messages[status])
            self.last_status = status
            self.stopped.wait(self.interval)

    def __enter__(self):
        self.thread = threading.Thread(target=self._watch, name="usb-reverse", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=3.5)
