"""Read-only Amap location health from Android system metadata.

Coordinates are never parsed, returned, logged or written. This proves only
system permission/access/fix health; the caller must independently read the
actual Amap place name, address and city from this query's GUI.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from domain import Cancelled

PACKAGE = "com.autonavi.minimap"
SUPPORTED_PROVIDERS = {"gps", "network", "fused"}


def _duration(value):
    """Android TimeUtils durations, not a guessed wall/UI capture timestamp."""
    value = value.strip().removeprefix("+")
    if not value or value.startswith("-"):
        return None
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1, "ms": .001}
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|d|h|m|s)", value)
    if not parts or "".join(n + u for n, u in parts) != value:
        return None
    return sum(float(n) * units[u] for n, u in parts)


def _iso(epoch):
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _permission(package_dump, user_id):
    active_user = None
    runtime_indent = None
    fine_values = set()
    for line in package_dump.splitlines():
        user = re.match(r"\s*User (\d+):", line)
        if user:
            active_user, runtime_indent = int(user[1]), None
            continue
        if active_user != user_id:
            continue
        indent = len(line) - len(line.lstrip())
        if line.strip().lower() == "runtime permissions:":
            runtime_indent = indent
            continue
        if runtime_indent is None:
            continue
        if line.strip() and indent <= runtime_indent:
            runtime_indent = None
            continue
        match = re.match(r"\s*android\.permission\.ACCESS_FINE_LOCATION:\s*granted=(true|false)\b", line)
        if match:
            fine_values.add(match[1] == "true")
    return next(iter(fine_values)) if len(fine_values) == 1 else None


def _app_access(appops_dump, max_age):
    uid_mode = re.search(r"^\s*Uid mode:\s*FINE_LOCATION:\s*(\w+)", appops_dump, re.M)
    records = re.findall(r"^\s*FINE_LOCATION:\s*(\w+)([^\n]*)", appops_dump, re.M)
    if not records:
        return None, False, None
    # Conflicting operation entries are not silently treated as authorization.
    modes = {mode.lower() for mode, _ in records}
    if len(modes) != 1:
        return None, False, None
    mode = next(iter(modes))
    if mode not in {"allow", "foreground"} or uid_mode and uid_mode[1].lower() not in {"allow", "foreground", "default"}:
        return mode, False, None
    ages = []
    for _, tail in records:
        # Match time= only, never rejectTime= or a duration of a denied access.
        access = re.search(r"(?:^|[;\s])time=([^;\s]+)\s+ago(?:[;\s]|$)", tail)
        if access:
            age = _duration(access[1])
            if age is not None: ages.append(age)
    age = min(ages) if ages else None
    return mode, age is not None and age <= max_age, age


def _fixes(location_dump, user_id, device_epoch, device_uptime):
    provider = None
    scoped_user = None
    provider_mock = False
    user_indent = None
    pending = None
    records = []
    for line in location_dump.splitlines():
        header = re.match(r"\s*([A-Za-z0-9_.-]+) provider:\s*$", line)
        if header:
            provider, scoped_user, provider_mock, user_indent, pending = header[1], None, False, None, None
            continue
        user = re.match(r"\s*user (\d+):\s*$", line, re.I)
        if user:
            scoped_user = int(user[1])
            user_indent = len(line) - len(line.lstrip())
            pending = None
            continue
        if user_indent is not None and line.strip() and len(line) - len(line.lstrip()) <= user_indent:
            scoped_user, user_indent, pending = None, None, None
        enabled = re.match(r"\s*enabled=(true|false)\s*$", line)
        if enabled and scoped_user == user_id and pending is not None:
            pending["provider_enabled"] = enabled[1] == "true"
        if scoped_user in {None, user_id} and re.search(r"\b(?:mock|isMock)(?:Provider)?\s*[=:]\s*true\b", line, re.I):
            provider_mock = True
            for previous in records:
                if previous["provider"] == provider:
                    previous["is_mock"] = True
        if provider not in SUPPORTED_PROVIDERS or scoped_user != user_id:
            continue
        # Read metadata keys only. In particular, do not split the coordinate
        # pair following Location[provider or retain the raw line in a result.
        if not re.search(r"\blast location\s*=\s*Location\[" + re.escape(provider) + r"\b", line):
            continue
        accuracy = re.search(r"\bhAcc=(\d+(?:\.\d+)?)(?=[\s,\]}])", line)
        epoch = re.search(r"\btime=(\d{12,})\b", line)
        elapsed = re.search(r"\bet=([^\s\]]+)", line)
        mock = provider_mock or bool(re.search(r"(?:\b(?:mock|isMock)\s*[=:]\s*true\b|(?<![A-Za-z])mock(?=[\s\],}]))", line, re.I))
        accuracy_m = float(accuracy[1]) if accuracy else None
        if accuracy_m is not None and (not math.isfinite(accuracy_m) or accuracy_m <= 0):
            accuracy_m = None
        fix_epoch = int(epoch[1]) / 1000 if epoch else None
        elapsed_at_fix = _duration(elapsed[1]) if elapsed else None
        wall_age = device_epoch - fix_epoch if fix_epoch is not None else None
        elapsed_age = device_uptime - elapsed_at_fix if elapsed_at_fix is not None and device_uptime is not None else None
        clock_ok = wall_age is not None and wall_age >= -1.1
        if elapsed_age is not None:
            clock_ok = clock_ok and elapsed_age >= -.01 and abs(wall_age - elapsed_age) <= 5
        age = max(0.0, wall_age, elapsed_age or 0.0) if clock_ok else None
        pending = {"provider": provider, "age_seconds": age, "accuracy_m": accuracy_m,
                   "fix_time": _iso(fix_epoch), "is_mock": mock, "provider_enabled": None,
                   "clock_basis": "device_epoch_and_elapsed" if elapsed_age is not None else "device_epoch",
                   "clock_ok": clock_ok}
        records.append(pending)
    yield from (record for record in records if record["provider_enabled"] is True)


def assess_location_status(*, user_id, enabled_output, package_dump, appops_dump,
                           location_dump, device_epoch, device_uptime=None,
                           max_age_seconds=120, max_accuracy_m=100, max_access_age_seconds=120):
    """Pure conservative parser. All inputs stay in memory; output has no GPS pair."""
    result = {"verified": False, "reason": "尚未核对定位健康状态", "enabled": None,
              "permission_granted": None, "app_access_recent": False, "provider": None,
              "age_seconds": None, "accuracy_m": None, "checked_at": None,
              "fix_time": None, "is_mock": None, "user_id": user_id,
              "appops_mode": None, "app_access_age_seconds": None, "clock_basis": None}
    try:
        epoch = float(device_epoch)
        uptime = float(str(device_uptime).split()[0]) if device_uptime is not None else None
        if not math.isfinite(epoch) or epoch < 946684800 or uptime is not None and (not math.isfinite(uptime) or uptime < 0):
            raise ValueError
    except (TypeError, ValueError, IndexError):
        result["reason"] = "无法读取设备真实时钟，不能核实定位时间"
        return result
    result["checked_at"] = _iso(epoch)
    if not isinstance(user_id, int) or user_id < 0:
        result["reason"] = "无法确认手机当前用户，不能核对定位权限"
        return result
    token = enabled_output.strip().lower()
    result["enabled"] = {"true": True, "false": False}.get(token)
    result["permission_granted"] = _permission(package_dump, user_id)
    mode, recent, access_age = _app_access(appops_dump, max_access_age_seconds)
    result.update(appops_mode=mode, app_access_recent=recent, app_access_age_seconds=access_age)
    if result["enabled"] is not True:
        result["reason"] = "手机系统定位尚未开启或无法核对，请填写明确起点"
        return result
    if result["permission_granted"] is not True:
        result["reason"] = "当前用户的高德精确定位权限未获授权或无法核对，请填写明确起点"
        return result
    if not recent:
        result["reason"] = "未核对到高德近期被允许的定位访问，请在高德核对定位或填写起点"
        return result
    fixes = list(_fixes(location_dump, user_id, epoch, uptime))
    if not fixes:
        result["reason"] = "系统没有当前用户可核实的实际定位记录，请填写明确起点"
        return result
    if any(f["is_mock"] for f in fixes):
        result.update({k: v for k, v in next(f for f in fixes if f["is_mock"]).items() if k not in {"clock_ok", "provider_enabled"}})
        result["reason"] = "系统定位记录带模拟标志，不能作为真实当前位置"
        return result
    valid = [f for f in fixes if f["clock_ok"] and f["fix_time"] and f["age_seconds"] is not None
             and f["age_seconds"] <= max_age_seconds and f["accuracy_m"] is not None
             and 0 < f["accuracy_m"] <= max_accuracy_m]
    chosen = min(valid or fixes, key=lambda f: (f["age_seconds"] is None, f["age_seconds"] or 0))
    result.update({k: v for k, v in chosen.items() if k not in {"clock_ok", "provider_enabled"}})
    if not chosen["clock_ok"] or not chosen["fix_time"]:
        result["reason"] = "定位实际时间缺失或设备时钟不一致，不能确认新鲜定位"
    elif chosen["age_seconds"] > max_age_seconds:
        result["reason"] = "系统最近定位已过期，请刷新高德定位或填写明确起点"
    elif chosen["accuracy_m"] is None or not 0 < chosen["accuracy_m"] <= max_accuracy_m:
        result["reason"] = "系统定位精度不足或无法核对，请填写明确起点"
    else:
        result.update(verified=True, reason="系统定位已开启，当前用户已授权，高德近期访问及非模拟新鲜定位已核对")
    return result


def read_location_status(base, checkpoint=lambda: None, *, max_age_seconds=120,
                         max_accuracy_m=100, max_access_age_seconds=120):
    """Inspect Android metadata only; never request a location or permission."""
    def read(*args):
        checkpoint()
        try:
            value = base.shell(*args, timeout=12)
        except Cancelled:
            raise
        except Exception:
            value = ""
        checkpoint()
        return value if isinstance(value, str) else ""

    current = read("am", "get-current-user").strip()
    user_id = int(current) if re.fullmatch(r"\d+", current) else None
    if user_id is None:
        return assess_location_status(user_id=None, enabled_output="", package_dump="", appops_dump="",
            location_dump="", device_epoch=read("date", "+%s"))
    enabled = read("cmd", "location", "is-location-enabled", "--user", str(user_id))
    package = read("dumpsys", "package", PACKAGE)
    appops = read("appops", "get", "--user", str(user_id), PACKAGE, "FINE_LOCATION")
    locations = read("dumpsys", "location")
    # Device clocks are read after the dump so a newly received fix is not
    # rejected because an earlier time sample preceded it.
    epoch = read("date", "+%s").strip()
    uptime = read("cat", "/proc/uptime").strip()
    return assess_location_status(user_id=user_id, enabled_output=enabled, package_dump=package,
        appops_dump=appops, location_dump=locations, device_epoch=epoch, device_uptime=uptime or None,
        max_age_seconds=max_age_seconds, max_accuracy_m=max_accuracy_m,
        max_access_age_seconds=max_access_age_seconds)
