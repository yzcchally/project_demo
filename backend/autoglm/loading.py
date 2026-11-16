"""Pure recognition of Amap's observed cancellable loading dialog.

No image/model claim supplies the dialog or its cancel target. All returned
coordinates are original native bounds; node objects are internal only.
"""
from device.amap import ID, PACKAGE, bounds


def loading_scene(shot):
    if (shot.get("current_app", shot.get("current_package")) != PACKAGE
            or shot.get("current_package", shot.get("current_app")) != PACKAGE):
        return None
    width, height = shot.get("width"), shot.get("height")
    if any(type(value) is not int or not 100 <= value <= 16384 for value in (width, height)):
        return None
    nodes = list(shot.get("nodes", []))
    parents = {child: parent for parent in nodes for child in parent}
    def inside(inner, outer):
        return (inner is not None and outer is not None and
                outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3])
    def visible(node):
        if not inside(bounds(node), [0, 0, width, height]):
            return False
        current = node
        while current is not None:
            if (current.get("package") != PACKAGE or current.get("visible-to-user") == "false"
                    or current.get("enabled") == "false"):
                return False
            current = parents.get(current)
        return True
    expected = {"loading_view": "android.widget.LinearLayout", "id_root": "android.view.ViewGroup",
                "lottie_view": "android.widget.ImageView", "loading_text": "android.widget.TextView",
                "close": "android.widget.ImageView"}
    found = {}
    for key, cls in expected.items():
        matches = [n for n in nodes if n.get("resource-id") == ID + key and visible(n)]
        if len(matches) != 1 or matches[0].get("class") != cls:
            return None
        found[key] = matches[0]
    view, root, spinner, text, close = (found[key] for key in ("loading_view", "id_root", "lottie_view", "loading_text", "close"))
    box = bounds(root)
    if (parents.get(root) is not view or set(view) != {root} or set(root) != {spinner, text, close}
            or bounds(view) != box or any(list(n) for n in (spinner, text, close))
            or any(n.get("clickable") != "false" for n in (view, root, spinner, text))
            or close.get("clickable") != "true" or close.get("text", "") or close.get("content-desc", "")
            or text.get("text") not in {"正在加载...", "正在加载…"}
            or text.get("content-desc", "") not in {"", text.get("text")}
            or any(n.get("text", "") or n.get("content-desc", "") for n in (view, root, spinner))
            or not width * .25 <= box[2] - box[0] <= width * .75
            or not 80 <= box[3] - box[1] <= height * .2
            or not height * .2 <= box[1] < box[3] <= height * .85):
        return None
    sb, tb, cb = (bounds(n) for n in (spinner, text, close))
    if (not all(inside(rect, box) for rect in (sb, tb, cb))
            or not sb[2] <= tb[0] < tb[2] <= cb[0]
            or abs((sb[2] - sb[0]) - (sb[3] - sb[1])) > 4
            or not 30 <= sb[2] - sb[0] <= min(160, width * .15)
            or abs((cb[2] - cb[0]) - (cb[3] - cb[1])) > 4
            or not 30 <= cb[2] - cb[0] <= min(160, width * .15)):
        return None
    return {"bounds": list(box), "cancel_bounds": list(cb), "spinner_bounds": list(sb),
            "cancel_node": close, "loading_node": text, "root_node": root,
            "view_node": view, "spinner_node": spinner}
