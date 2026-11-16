"""Optional current native controls for visual targeting, never device actions.

The model still chooses its action and coordinates. Every proposed action is
re-observed and grounded again by the executor before dispatch.
"""
from device.amap import PACKAGE, bounds, label
from domain import WorkflowError
from .protocol import Action
from .safety import _candidates, resolve_action


def _normalized_bounds(rect, width, height):
    """Inclusive integer Tap ranges for an exclusive native pixel rectangle."""
    if (rect is None or len(rect) != 4 or any(type(v) is not int for v in rect)
            or not 0 <= rect[0] < rect[2] <= width
            or not 0 <= rect[1] < rect[3] <= height):
        return None
    # floor(n * dimension / 1000) must remain inside the native rectangle.
    # Integer ceiling preserves one-pixel controls without rounding outwards.
    low_x = (rect[0] * 1000 + width - 1) // width
    low_y = (rect[1] * 1000 + height - 1) // height
    high_x = min(999, (rect[2] * 1000 + width - 1) // width - 1)
    high_y = min(999, (rect[3] * 1000 + height - 1) // height - 1)
    if low_x > high_x or low_y > high_y:
        return None
    return [low_x, low_y, high_x, high_y]


def native_control_hints(shot, request, state):
    if shot.get("current_app", shot.get("current_package")) != PACKAGE:
        return []
    try:
        candidates = _candidates(shot, request, state)
    except WorkflowError:
        return []
    width, height = shot.get("width"), shot.get("height")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        return []
    result, seen = [], set()
    for candidate in candidates[:48]:
        rect = bounds(candidate.node)
        normalized = _normalized_bounds(rect, width, height)
        if normalized is None:
            continue
        # A private grounding probe only; no point or action is emitted.
        point = [(normalized[0] + normalized[2]) // 2,
                 (normalized[1] + normalized[3]) // 2]
        try:
            target = resolve_action(Action("Tap", {"element": point}), shot, request, state)
        except WorkflowError:
            continue
        rect = bounds(target.node)
        normalized = _normalized_bounds(rect, width, height)
        if normalized is None:
            continue
        text = label(target.node)[:96]
        key = (tuple(rect), target.role, text)
        if key in seen:
            continue
        seen.add(key)
        result.append({"text": text, "role": target.role, "bounds": list(rect),
                       "normalized_bounds": normalized})
        if len(result) == 16:
            break
    return result
