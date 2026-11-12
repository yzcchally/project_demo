"""Synthetic system-metadata fixtures; no GPS collection or real coordinates."""
import copy
import json

import pytest

from device.location_status import assess_location_status, read_location_status
from domain import Cancelled

NOW = 1794457430
UPTIME = 156995.0
PACKAGE = "com.autonavi.minimap"


def permissions(current=True, other=False, current_user=0):
    def flag(value): return str(value).lower()
    return f"""Packages:
  Package [com.autonavi.minimap]:
    requested permissions:
      android.permission.ACCESS_FINE_LOCATION
    install permissions:
      android.permission.ACCESS_FINE_LOCATION: granted=false
    User {current_user}: installed=true
      runtime permissions:
        android.permission.ACCESS_FINE_LOCATION: granted={flag(current)}
        android.permission.ACCESS_COARSE_LOCATION: granted=true
    User 128: installed=false
      runtime permissions:
        android.permission.ACCESS_FINE_LOCATION: granted={flag(other)}
"""


def duration(seconds):
    millis = round(seconds * 1000)
    return f"+{millis // 86400000}d{millis // 3600000 % 24}h{millis // 60000 % 60}m{millis // 1000 % 60}s{millis % 1000}ms"


def fix(*, age=20, accuracy=5.0, provider="gps", user=0, mock="", wall_age=None, enabled=True):
    # Deliberately fake coordinates. The implementation never extracts them.
    wall_age = age if wall_age is None else wall_age
    return f"""  {provider} provider:
    service: ProviderRequest[OFF]
    user {user}:
      last location=Location[{provider} 1.234567,2.345678 time={round((NOW-wall_age)*1000)} hAcc={accuracy} et={duration(UPTIME-age)} {mock}]
      enabled={str(enabled).lower()}
"""


def inputs(**updates):
    result = dict(user_id=0, enabled_output="true", package_dump=permissions(),
                  appops_dump="Uid mode: FINE_LOCATION: foreground\nFINE_LOCATION: allow; time=+500ms ago; rejectTime=+1ms ago",
                  location_dump=fix(), device_epoch=NOW, device_uptime=UPTIME)
    result.update(updates)
    return result


def test_fresh_real_fix_precise_permission_and_recent_app_access_are_verified():
    value = assess_location_status(**inputs())
    assert value["verified"] and value["enabled"] and value["permission_granted"]
    assert value["app_access_recent"] and value["provider"] == "gps"
    assert value["age_seconds"] == 20 and value["accuracy_m"] == 5
    assert value["is_mock"] is False
    assert value["checked_at"] != value["fix_time"]
    assert value["clock_basis"] == "device_epoch_and_elapsed"


def test_other_user_denial_is_not_used_for_current_user_permission():
    value = assess_location_status(**inputs(package_dump=permissions(current=True, other=False)))
    assert value["verified"] and value["permission_granted"]
    value = assess_location_status(**inputs(package_dump=permissions(current=False, other=True)))
    assert not value["verified"] and value["permission_granted"] is False


def test_granted_install_or_another_user_permission_is_not_runtime_authorization():
    package = permissions().replace("runtime permissions:", "other section:")
    value = assess_location_status(**inputs(package_dump=package))
    assert not value["verified"] and value["permission_granted"] is None


@pytest.mark.parametrize("enabled", ["false", "", "unknown"])
def test_disabled_or_unknown_master_location_switch_falls_back(enabled):
    value = assess_location_status(**inputs(enabled_output=enabled))
    assert not value["verified"]


@pytest.mark.parametrize("age", [120.001, 600])
def test_stale_system_fix_is_not_made_fresh_by_observation_time(age):
    value = assess_location_status(**inputs(location_dump=fix(age=age)))
    assert not value["verified"] and value["age_seconds"] > 120
    assert "过期" in value["reason"]


def test_thresholds_accept_exact_120_seconds_100_meters():
    value = assess_location_status(**inputs(location_dump=fix(age=120, accuracy=100)))
    assert value["verified"]


@pytest.mark.parametrize("accuracy", [100.001, 0])
def test_imprecise_or_zero_accuracy_is_rejected(accuracy):
    value = assess_location_status(**inputs(location_dump=fix(accuracy=accuracy)))
    assert not value["verified"]


@pytest.mark.parametrize("marker", ["mock", "isMock=true", "mock=true"])
def test_mock_actual_fix_is_never_accepted(marker):
    value = assess_location_status(**inputs(location_dump=fix(mock=marker)))
    assert not value["verified"] and value["is_mock"] is True


def test_explicit_mock_false_and_another_user_mock_do_not_poison_current_fix():
    data = fix(user=128, mock="mock") + fix(mock="isMock=false")
    value = assess_location_status(**inputs(location_dump=data))
    assert value["verified"] and value["is_mock"] is False


@pytest.mark.parametrize("data", ["", "  gps provider:\n    user 0:\n      last location=null\n      enabled=true\n", fix(user=128), fix(enabled=False)])
def test_no_current_user_real_enabled_fix_falls_back(data):
    value = assess_location_status(**inputs(location_dump=data))
    assert not value["verified"] and value["provider"] is None


def test_fresh_accurate_network_fix_can_work_when_gps_is_absent_or_stale():
    value = assess_location_status(**inputs(location_dump=fix(age=900) + fix(provider="network", age=5, accuracy=15)))
    assert value["verified"] and value["provider"] == "network" and value["age_seconds"] == 5


def test_current_user_provider_scope_never_uses_other_profile_fresh_fix():
    value = assess_location_status(**inputs(location_dump=fix(age=700) + fix(user=128, age=1)))
    assert not value["verified"] and value["age_seconds"] == 700


@pytest.mark.parametrize("appops", [
    "FINE_LOCATION: ignore; time=+1s ago",
    "Uid mode: FINE_LOCATION: ignore\nFINE_LOCATION: allow; time=+1s ago",
    "Uid mode: FINE_LOCATION: foreground",
    "FINE_LOCATION: allow; rejectTime=+1s ago",
    "FINE_LOCATION: allow; time=+3m0s ago; rejectTime=+1ms ago",
    "No operations.",
])
def test_denied_old_or_unrecorded_amap_access_is_not_recent(appops):
    value = assess_location_status(**inputs(appops_dump=appops))
    assert not value["verified"] and not value["app_access_recent"]


def test_wall_elapsed_inconsistency_or_future_fix_fails_closed():
    for data in [fix(age=20, wall_age=200), fix(age=-20), fix().replace("time=", "missing=")]:
        value = assess_location_status(**inputs(location_dump=data))
        assert not value["verified"]


def test_wall_time_fallback_uses_actual_fix_timestamp_not_ui_capture_time():
    data = fix(age=35)
    data = __import__("re").sub(r"et=[^\s]+", "", data)
    value = assess_location_status(**inputs(location_dump=data))
    assert value["verified"] and value["age_seconds"] == 35 and value["clock_basis"] == "device_epoch"


@pytest.mark.parametrize("epoch,uptime", [("bad", UPTIME), (float("inf"), UPTIME), (NOW, "bad"), (NOW, -1)])
def test_unreadable_or_invalid_device_clocks_fall_back(epoch, uptime):
    assert not assess_location_status(**inputs(device_epoch=epoch, device_uptime=uptime))["verified"]


def test_output_is_metadata_only_and_does_not_contain_coordinates_or_raw_dump():
    result = assess_location_status(**inputs())
    text = json.dumps(result)
    assert "1.234567" not in text and "2.345678" not in text and "Location[" not in text
    assert not any(key in result for key in ["latitude", "longitude", "lat", "lon", "raw", "location_dump"])


class Base:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def shell(self, *args, **kwargs):
        self.calls.append(args)
        if self.fail: raise RuntimeError("a private system message which must not escape")
        commands = {
            ("am", "get-current-user"): "0",
            ("cmd", "location", "is-location-enabled", "--user", "0"): "true",
            ("dumpsys", "package", PACKAGE): permissions(),
            ("appops", "get", "--user", "0", PACKAGE, "FINE_LOCATION"): "FINE_LOCATION: allow; time=+1s ago",
            ("dumpsys", "location"): fix(),
            ("date", "+%s"): str(NOW),
            ("cat", "/proc/uptime"): str(UPTIME) + " 12345.0",
        }
        return commands[args]


def test_probe_reads_only_metadata_with_checkpoint_before_and_after_every_call():
    base = Base(); checkpoints = []
    result = read_location_status(base, lambda: checkpoints.append(True))
    assert result["verified"]
    assert len(checkpoints) == 2 * len(base.calls) == 14
    assert all(not any(word in call for word in ["set-location-enabled", "request", "add-test-provider", "grant", "set"]) for call in base.calls)
    assert ("appops", "get", "--user", "0", PACKAGE, "FINE_LOCATION") in base.calls


def test_probe_errors_are_sanitized_and_do_not_claim_permission_or_freshness():
    value = read_location_status(Base(fail=True))
    assert not value["verified"] and "private" not in json.dumps(value)


def test_cancel_after_blocking_shell_discards_return_and_stops_remaining_commands():
    base = Base(); count = [0]
    def checkpoint():
        count[0] += 1
        if count[0] == 2: raise Cancelled("cancelled")
    with pytest.raises(Cancelled): read_location_status(base, checkpoint)
    assert len(base.calls) == 1
