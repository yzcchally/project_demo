"""Native hints are optional current observations, not execution permissions."""
from unittest.mock import Mock

import pytest

from autoglm.hints import native_control_hints
from autoglm.safety import normalized_point
from device.amap import ID
from test_autoglm_safety import node, shot


REQUEST = {"origin": "甲地铁站", "destination": "乙地铁站", "city": "广州市"}


def field_frame():
    return shot(node("起点", cls="android.widget.EditText", **{
        "resource-id": ID + "route_edit_summary_start"}))


def test_hints_describe_current_grounded_field_without_device_side_effects():
    phone = Mock()
    frame = field_frame()
    hints = native_control_hints(frame, REQUEST, {"phone": phone})
    assert hints == [{"text": "起点", "role": "origin", "bounds": [100, 100, 1100, 250],
                      "normalized_bounds": [84, 42, 916, 104]}]
    assert not phone.mock_calls


def test_unknown_or_foreign_overlay_does_not_become_a_hint():
    frame = field_frame()
    frame["nodes"].append(node("", rect="[100,100][1100,250]", **{"package": "other.app"}))
    assert native_control_hints(frame, REQUEST, {}) == []


def test_foreign_app_and_invalid_geometry_never_expose_click_hints():
    frame = field_frame()
    frame["current_app"] = "com.example.demo"
    assert native_control_hints(frame, REQUEST, {}) == []
    frame.pop("current_app")
    frame["screen_width"] = 720
    assert native_control_hints(frame, REQUEST, {}) == []


def test_hints_follow_new_observation_and_do_not_invent_missing_controls():
    assert native_control_hints(shot(node("未知图形")), REQUEST, {}) == []
    assert native_control_hints(field_frame(), REQUEST, {})
    assert native_control_hints(shot(), REQUEST, {}) == []


def bounded_field_frame(width, height, rect):
    return shot(node("起点", cls="android.widget.EditText", rect=f"[{rect[0]},{rect[1]}][{rect[2]},{rect[3]}]", **{
        "resource-id": ID + "route_edit_summary_start"}),
        width=width, height=height, screen_width=width, screen_height=height)


@pytest.mark.parametrize("width,height,rect,expected", [
    (1200, 2640, [1103, 592, 1200, 669], [920, 225, 999, 253]),
    (360, 640, [0, 0, 360, 640], [0, 0, 999, 999]),
    (1080, 2340, [17, 101, 311, 177], [16, 44, 287, 75]),
    (400, 800, [399, 799, 400, 800], [998, 999, 999, 999]),
    (1200, 2640, [1198, 264, 1199, 265], [999, 100, 999, 100]),
    (800, 400, [1, 0, 2, 1], [2, 0, 2, 2]),
])
def test_hint_normalized_bounds_are_inclusive_and_map_inside_actual_pixels(width, height, rect, expected):
    frame = bounded_field_frame(width, height, rect)
    hints = native_control_hints(frame, REQUEST, {})
    assert len(hints) == 1
    assert hints[0]["bounds"] == rect
    assert hints[0]["normalized_bounds"] == expected
    # Every emitted integer in either axis maps to a real pixel in the control.
    for value in range(expected[0], expected[2] + 1):
        x, _ = normalized_point([value, expected[1]], frame)
        assert rect[0] <= x < rect[2]
    for value in range(expected[1], expected[3] + 1):
        _, y = normalized_point([expected[0], value], frame)
        assert rect[1] <= y < rect[3]
    # The ranges include all representable positions, with no outward rounding.
    if expected[0] > 0:
        assert normalized_point([expected[0] - 1, expected[1]], frame)[0] < rect[0]
    if expected[2] < 999:
        assert normalized_point([expected[2] + 1, expected[1]], frame)[0] >= rect[2]
    if expected[1] > 0:
        assert normalized_point([expected[0], expected[1] - 1], frame)[1] < rect[1]
    if expected[3] < 999:
        assert normalized_point([expected[0], expected[3] + 1], frame)[1] >= rect[3]
    assert set(hints[0]) == {"text", "role", "bounds", "normalized_bounds"}


@pytest.mark.parametrize("rect", [
    [1199, 100, 1200, 120],  # The last pixel is unreachable at 1200px width.
    [100, 2639, 120, 2640],  # The last pixel is unreachable at 2640px height.
    [5, 100, 6, 120],       # A skipped pixel in the interior is also unreachable.
])
def test_unrepresentable_tiny_native_control_does_not_become_a_hint(rect):
    assert native_control_hints(bounded_field_frame(1200, 2640, rect), REQUEST, {}) == []
