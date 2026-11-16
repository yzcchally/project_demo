"""Conservative freshness for status bars, carets, loading and arrival icons.

This module never ignores an input field, keyboard, toolbar or map region.
Callers first require the exact epoch/package/geometry and full native/window
identity. Only independently proven small decorations may differ. A native
loading spinner is ignored solely when dismissing that same loading dialog.
One tiny, natively bound live-arrival icon may animate while entering the same
independently grounded route endpoint, applying/scrolling this task's native preference bar, or
selecting the same canonical route with this query's complete POI/filter proof.
The selected card's other pixels and every native attribute remain exact.
"""
from __future__ import annotations

import base64
import binascii
from collections import Counter
from functools import reduce
import io
import re
import time
import warnings
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops
from domain import WorkflowError

PACKAGE = 'com.autonavi.minimap'


def status_bar_frame(dump, width, height):
    """Read only Android's explicit, visible ITYPE_STATUS_BAR InsetsSource.

    An unknown/ambiguous/non-top/oversized source grants no pixel exception.
    Different visible displays/frames are rejected rather than choosing one.
    """
    if (not isinstance(dump, str) or type(width) is not int or type(height) is not int
            or width <= 0 or height <= 0):
        return None
    found = []
    for line in dump.splitlines():
        if not re.search(r'\bInsetsSource\b', line) or not re.search(r'\b(?:mType|type)\s*=\s*ITYPE_STATUS_BAR\b', line):
            continue
        visible = re.search(r'\b(?:mVisible|visible)\s*=\s*(true|false)\b', line)
        frame = re.search(r'\b(?:mFrame|frame)\s*=\s*\[(-?\d+),\s*(-?\d+)\]\[(-?\d+),\s*(-?\d+)\]', line)
        if visible is None or frame is None:
            return None
        if visible.group(1) != 'true':
            continue
        box = tuple(map(int, frame.groups()))
        if box[:3] != (0, 0, width) or not 1 <= box[3] <= min(200, int(height * .08)):
            return None
        found.append(box)
    return list(found[0]) if found and len(set(found)) == 1 else None


def _box(value, width, height):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(n) is not int for n in value)):
        return None
    x1, y1, x2, y2 = value
    return tuple(value) if 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height else None


def _native_focused_field(shot):
    """Match an executor-generated input permission to the actual native field.

    caret_context is populated only by safety.focused_input_context, never by
    the model. The full tree must still expose one visible focused EditText,
    with identical text and ancestry in the two snapshots.
    """
    permission = shot.get('caret_context')
    if (shot.get('current_app') != PACKAGE or not isinstance(permission, dict)
            or permission.get('role') not in {'query', 'origin', 'destination'}):
        return None
    try:
        root = ET.fromstring(shot.get('xml', ''))
    except (ET.ParseError, TypeError):
        return None
    parents = {child: parent for parent in root.iter() for child in parent}
    focused = []
    for node in root.iter('node'):
        if node.get('class') != 'android.widget.EditText' or node.get('focused') != 'true':
            continue
        current, valid = node, True
        while current is not None:
            if (current.get('visible-to-user') == 'false' or current.get('enabled') == 'false'
                    or current.get('package', PACKAGE) != PACKAGE):
                valid = False; break
            current = parents.get(current)
        if valid:
            focused.append(node)
    if len(focused) != 1:
        return None
    node = focused[0]
    if node.get('password') == 'true' or node.get('package') != PACKAGE:
        return None
    numbers = [int(n) for n in re.findall(r'-?\d+', node.get('bounds', ''))]
    rect = _box(numbers, shot['width'], shot['height'])
    if (rect is None or list(rect) != permission.get('bounds')
            or node.get('resource-id', '') != permission.get('resource_id', '')):
        return None
    return rect, tuple(sorted(node.attrib.items())), permission['role']


def _targets_outside_bar(action, bar, width, height):
    if action.kind not in {'Tap', 'Swipe'}:
        return True
    values = ([action.params.get('element')] if action.kind == 'Tap'
              else [action.params.get('start'), action.params.get('end')])
    for point in values:
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or any(type(n) is not int or not 0 <= n < 1000 for n in point)):
            return False
        x, y = int(point[0] * width / 1000), int(point[1] * height / 1000)
        # The bar spans the entire top edge, so two endpoints below it also
        # keep the whole straight swipe path outside the ignored region.
        if bar[0] <= x < bar[2] and bar[1] <= y < bar[3]:
            return False
    return True


def _decode(shot):
    raw = base64.b64decode(shot['screenshot_base64'], validate=True)
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as source:
            if source.format != 'PNG' or source.size != (shot['width'], shot['height']):
                return None
            source.load()
            return source.convert('RGBA')


def _loading_spinner(action, before, after):
    """Bound an animated spinner only for this dialog's Back/cancel action.

    Reconstruct native nodes from the XML whose full identity the caller
    compares. A model/metadata-supplied rectangle never grants an exception.
    """
    if action.kind not in {'Back', 'Tap'}:
        return None
    from autoglm.loading import loading_scene
    scenes = []
    for shot in (before, after):
        try:
            root = ET.fromstring(shot.get('xml', ''))
        except (ET.ParseError, TypeError):
            return None
        scene = loading_scene({**shot, 'nodes': list(root.iter('node'))})
        if scene is None:
            return None
        scenes.append({key: scene[key] for key in ('bounds', 'cancel_bounds', 'spinner_bounds')})
    if scenes[0] != scenes[1]:
        return None
    width, height = before['width'], before['height']
    spinner = _box(scenes[0]['spinner_bounds'], width, height)
    cancel = _box(scenes[0]['cancel_bounds'], width, height)
    if spinner is None or cancel is None:
        return None
    sw, sh = spinner[2] - spinner[0], spinner[3] - spinner[1]
    # The observed native lottie is 93x93 on a 1200px screen. Only a small
    # square qualifies, never the full dialog, its label or the underlying map.
    if not 16 <= sw <= min(128, width * .12) or abs(sw - sh) > 4:
        return None
    if action.kind == 'Tap':
        point = action.params.get('element')
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or any(type(n) is not int or not 0 <= n < 1000 for n in point)):
            return None
        x, y = int(point[0] * width / 1000), int(point[1] * height / 1000)
        if not (cancel[0] <= x < cancel[2] and cancel[1] <= y < cancel[3]):
            return None
    return spinner


def _route_card_context(shot, grounded, request, state):
    """Bind a selectable canonical card to this query's real POI/filter proof."""
    from autoglm.safety import _canonical_route_cards, _query_matches, _visible
    from device.amap import ID, bounds, label, place_key
    if not isinstance(state, dict):
        return None
    adapter = state.get('adapter')
    places, choices = state.get('places'), state.get('choices', {})
    if not isinstance(places, dict) or not isinstance(choices, dict):
        return None
    query_id = getattr(adapter, 'query_id', None)
    preference = {'recommended': '高德推荐', 'fewer_transfers': '换乘少', 'less_walking': '步行少'}.get(
        request.get('preference', 'recommended'))
    if (not isinstance(query_id, str) or not query_id or shot.get('evidence', {}).get('query_id') != query_id
            or preference is None or state.get('filter') != preference
            or grounded.node not in _canonical_route_cards(shot, adapter)
            or (grounded.binding or {}).get('route_summary') != label(grounded.node)):
        return None
    known_refs = {item.get('evidence_id') for item in getattr(adapter, 'observations', [])
                  if isinstance(item, dict) and item.get('query_id') == query_id
                  and item.get('page_kind') in {'place_detail', 'location'}}
    endpoints = []
    for role, suffix in (('origin', 'start'), ('destination', 'end')):
        place = places.get(role, {})
        if (not isinstance(place, dict) or not isinstance(place.get('name'), str) or not place['name']
                or not isinstance(place.get('city'), str) or not place['city']
                or request.get('city') and place['city'] != request['city']
                or not _query_matches(shot, role, request, state)):
            return None
        required = {'name', 'city'}
        choice = choices.get(role, {})
        if not isinstance(choice, dict):
            return None
        chosen_address = choice.get('address')
        if chosen_address or place.get('source') == 'current_location':
            required.add('address')
            if not place.get('address') or chosen_address and place['address'] != chosen_address:
                return None
        refs = place.get('evidence_refs', {})
        for field in required:
            values = refs.get(field) if isinstance(refs, dict) else None
            if not isinstance(values, list) or not values or any(type(ref) is not str or ref not in known_refs for ref in values):
                return None
        if role == 'origin' and not request.get('origin'):
            health = state.get('location_health', {}); checked = state.get('location_checked_at')
            age = health.get('age_seconds') if isinstance(health, dict) else None
            if (place.get('source') != 'current_location' or state.get('location_verified') is not True
                    or not isinstance(health, dict) or health.get('verified') is not True
                    or type(age) not in {int, float} or type(checked) not in {int, float}):
                return None
            elapsed = time.monotonic() - checked
            if not 0 <= age <= 120 or not 0 <= elapsed <= 120 - age:
                return None
        fields = [n for n in _visible(shot) if n.get('resource-id') == ID + 'route_edit_summary_' + suffix
                  and n.get('class') == 'android.widget.EditText']
        expected = place['name'] if request.get(role) else '我的位置'
        if len(fields) != 1 or place_key(fields[0].get('text', ''), request.get('city', '')) != place_key(expected, request.get('city', '')):
            return None
        endpoints.append((tuple(sorted(fields[0].attrib.items())), place['name'], place['city'],
                          tuple((field, tuple(refs[field])) for field in sorted(required))))
    return query_id, preference, tuple(endpoints)


def _arrival_icons_for_entry(action, before, after, request, state):
    """Prove a tiny arrival icon for one grounded endpoint/preference target.

    This never exempts a map/card/ETA text. A card target additionally needs
    this query's canonical itinerary and independently resolved POI/filter proof.
    Both native trees and both independently grounded entry targets stay exact.
    """
    if (action.kind not in {'Tap', 'Swipe'} or not isinstance(request, dict) or not request.get('destination')
            or not (request.get('origin') or request.get('use_current_location'))):
        return []
    from autoglm.safety import resolve_action, page_kind, _PREFERENCES
    from device.amap import ID, bounds, label
    shots, native, targets, selected_cards = [], [], [], []
    try:
        for shot in (before, after):
            root = ET.fromstring(shot.get('xml', ''))
            if not root.get('window-id'):
                return []
            nodes = list(root.iter('node'))
            native.append((sorted((k, v) for k, v in root.attrib.items() if k not in {'nonce', 'captured-at'}),
                           [(sorted(n.attrib.items()), len(n)) for n in nodes]))
            current = {**shot, 'nodes': nodes}
            if page_kind(current) != 'route_list':
                return []
            grounded = resolve_action(action, current, request, state)
            role = grounded.role
            node = grounded.node
            route_context = None
            gesture = None
            gesture_region = None
            if grounded.kind == 'Swipe' and role == 'preference_scroll' and grounded.page == 'route_list':
                from autoglm.safety import preference_scroll_context
                context = preference_scroll_context(current, request, state)
                binding = grounded.binding or {}
                if (not isinstance(context, dict) or context.get('axis') != 'horizontal'
                        or binding.get('preference_bar') is not True
                        or binding.get('bounds') != context.get('bounds')
                        or binding.get('gesture_bounds') != context.get('gesture_bounds')):
                    return []
                bar = _box(context.get('bounds'), current['width'], current['height'])
                gesture_region = _box(context.get('gesture_bounds'), current['width'], current['height'])
                if (bar is None or gesture_region is None
                        or not (gesture_region[0] <= bar[0] < bar[2] <= gesture_region[2]
                                and gesture_region[1] == bar[1] and gesture_region[3] == bar[3])):
                    return []
                bars = [n for n in nodes if bounds(n) == list(bar)
                        and n.get('class', '').endswith('SpringHorizontalScrollView')]
                if len(bars) != 1 or (node is not None and node is not bars[0]):
                    return []
                node = bars[0]
                gesture = (grounded.start, grounded.end)
                for point in gesture:
                    if (not isinstance(point, tuple) or len(point) != 2 or any(type(v) is not int for v in point)
                            or not (gesture_region[0] <= point[0] < gesture_region[2]
                                    and gesture_region[1] <= point[1] < gesture_region[3])):
                        return []
                start, end = gesture
                if not (bar[0] <= start[0] < bar[2] and bar[1] <= start[1] < bar[3]):
                    return []
                if not bar[0] <= end[0] < bar[2] and not (end[0] < bar[0] and end[0] < start[0]):
                    return []  # Only the same leftward drag may leave its child bar.
            elif grounded.kind != 'Tap' or node is None:
                return []
            elif role == 'transit' and grounded.page == 'route_input':
                # A rule-driven assist has independently rechecked its exact
                # task endpoints and page before this comparison. Never grant
                # this exemption to the autonomous agent or an unbound query.
                adapter = state.get('adapter') if isinstance(state, dict) else None
                if (not isinstance(state, dict) or state.get('assist_transit_precondition') is not True
                        or not getattr(adapter, 'query_id', None)
                        or shot.get('evidence', {}).get('query_id') != adapter.query_id
                        or node.get('class') != 'android.widget.TextView'
                        or node.get('resource-id') != ID + 'route_input_conbine_text'
                        or label(node) != '公共交通' or node.get('selected') != 'true'):
                    return []
            elif role in {'origin', 'destination'}:
                if (node.get('class') != 'android.widget.EditText'
                        or node.get('resource-id') != ID + 'route_edit_summary_' + ('start' if role == 'origin' else 'end')):
                    return []
            elif role == 'preference':
                if (node.get('class') not in {'android.view.View', 'android.widget.TextView', 'android.view.ViewGroup'}
                        or label(node) not in _PREFERENCES.get(request.get('preference'), set())
                        or (grounded.binding or {}).get('filter') != label(node)):
                    return []
            elif role == 'route' and grounded.page == 'route_list':
                route_context = _route_card_context(current, grounded, request, state)
                if route_context is None:
                    return []
            else:
                return []
            parents = {child: parent for parent in root.iter() for child in parent}
            path, cursor = [], node
            while cursor is not root:
                parent = parents.get(cursor)
                if parent is None:
                    return []
                path.append(list(parent).index(cursor)); cursor = parent
            targets.append((grounded.kind, role, tuple(sorted(node.attrib.items())), tuple(bounds(node)),
                            grounded.point, tuple(reversed(path)), route_context, gesture, gesture_region))
            shots.append(current)
            selected_cards.append(node if route_context is not None else None)
        if native[0] != native[1] or targets[0] != targets[1]:
            return []
    except (WorkflowError, ET.ParseError, TypeError, ValueError, KeyError):
        return []

    # Full native equality above also binds the ETA, icon ancestry, visibility
    # and every other window/control. Inspect that shared tree independently.
    shot = shots[0]
    width, height = shot['width'], shot['height']
    nodes = shot['nodes']; parents = {c: p for p in nodes for c in p}
    def visible(node):
        if _box(bounds(node), width, height) is None:
            return False
        while node is not None:
            if (node.get('package', PACKAGE) != PACKAGE or node.get('enabled') == 'false'
                    or node.get('visible-to-user') == 'false'):
                return False
            node = parents.get(node)
        return True
    def blank_group(node):
        return (node is not None and visible(node) and node.get('class') == 'android.view.ViewGroup'
                and not node.get('resource-id') and not label(node) and node.get('clickable') == 'true')
    def contained(a, b):
        return a and b and b[0] <= a[0] < a[2] <= b[2] and b[1] <= a[1] < a[3] <= b[3]
    transit = [n for n in nodes if visible(n) and n.get('text') == '公共交通'
               and n.get('resource-id') == ID + 'route_input_conbine_text' and n.get('selected') == 'true']
    if len(transit) != 1:
        return []
    icons = []
    for icon in nodes:
        if (not visible(icon) or icon.get('class') != 'android.widget.ImageView' or list(icon)
                or icon.get('text') or icon.get('content-desc') != '图标' or icon.get('resource-id')
                or icon.get('clickable') != 'false'):
            continue
        rect = bounds(icon); iw, ih = rect[2] - rect[0], rect[3] - rect[1]
        if not 12 <= iw <= min(64, width * .06) or abs(iw - ih) > 2:
            continue
        if targets[0][7] is not None:
            start, end = targets[0][7]
            # A conservative full path box, not just its endpoints, must avoid
            # the ignored icon. The real near-horizontal path lies inside it.
            path_box = (min(start[0], end[0]), min(start[1], end[1]),
                        max(start[0], end[0]) + 1, max(start[1], end[1]) + 1)
            if (max(path_box[0], rect[0]) < min(path_box[2], rect[2])
                    and max(path_box[1], rect[1]) < min(path_box[3], rect[3])):
                continue
        point = targets[0][4]
        if point is not None and rect[0] <= point[0] < rect[2] and rect[1] <= point[1] < rect[3]:
            continue  # No tap may land inside an ignored animation.
        if selected_cards[0] is not None:
            if (icon not in set(selected_cards[0].iter('node'))
                    or rect[0] <= point[0] < rect[2] and rect[1] <= point[1] < rect[3]):
                continue
        owner = parents.get(icon); row = parents.get(owner)
        if (not blank_group(owner) or bounds(owner) != rect or list(owner) != [icon]
                or not blank_group(row) or len(row) != 2 or list(row)[0] is not owner):
            continue
        caption_owner = list(row)[1]
        if not blank_group(caption_owner) or len(caption_owner) != 1:
            continue
        caption = list(caption_owner)[0]; eta = caption.get('text', '')
        if (not visible(caption) or caption.get('class') != 'android.view.View' or list(caption)
                or caption.get('resource-id') or caption.get('content-desc') or caption.get('clickable') != 'false'
                or not re.fullmatch(r'(?:预计(?:[01]\d|2[0-3]):[0-5]\d进站|即将进站)', eta)
                or bounds(caption) != bounds(caption_owner)
                or not rect[2] <= bounds(caption)[0] <= rect[2] + max(12, width * .02)
                or not contained(rect, bounds(row)) or not contained(bounds(caption), bounds(row))
                or bounds(row)[3] - bounds(row)[1] > min(128, height * .06)):
            continue
        # A real broad route-card ancestor contains both transit description
        # and this exact arrival label. A generic icon next to arbitrary text
        # cannot acquire an ignored rectangle.
        card = parents.get(row)
        for _ in range(6):
            if card is None:
                break
            box = bounds(card); text = label(card)
            if (visible(card) and card.get('class') == 'android.view.ViewGroup'
                    and card.get('clickable') == 'true' and re.match(r'\d+(?:小时|分钟)', text)
                    and eta in text and re.search(r'\d+[A-Za-z]?号线|\d+[A-Za-z]?路|APM', text)
                    and box[2] - box[0] >= width * .85
                    and 80 <= box[3] - box[1] <= height * .4 and box[1] >= height * .18
                    and contained(bounds(row), box)):
                icons.append(tuple(rect)); break
            card = parents.get(card)
    return icons[:16]


def _solid_caret(image, box):
    """Recognize a narrow solid accent line, with at most 3-row rounded caps.

    One frame must actually contain this signature; a narrow difference alone
    is not enough. Glyphs, outlines, stripes, faded overlays and broad regions
    do not have a uniform full-width core with this shape.
    """
    crop = image.crop(box).convert('RGB')
    width, height = crop.size
    pixels = list(crop.get_flattened_data())
    color, count = Counter(pixels).most_common(1)[0]
    if max(color) - min(color) < 70 or count < width * height * .90:
        return False
    if len(set(crop.crop((0, 3, width, height - 3)).get_flattened_data())) != 1:
        return False
    if crop.getpixel((width // 2, height // 2)) != color:
        return False
    # At the caps accept only opaque antialias blends of the same accent with
    # a near-white field background. This deliberately supports the observed
    # native Amap caret, not arbitrary theme/selection/highlight transitions.
    for pixel in set(pixels):
        if pixel == color:
            continue
        if not all(c <= p <= 255 for c, p in zip(color, pixel)):
            return False
        weights = [(255 - p) / (255 - c) for c, p in zip(color, pixel) if c < 245]
        if weights and max(weights) - min(weights) > .08:
            return False
    return True


def _detail_arrival_icons(action, before, after, request, state):
    """Prove tiny native train-arrival icons on the same selected detail."""
    if action.kind not in {'Swipe', 'Finish'} or not isinstance(state, dict):
        return []
    from autoglm.safety import resolve_action, page_kind
    from device.amap import bounds, label
    adapter = state.get('adapter'); selected = state.get('selected_list')
    if (not adapter or not isinstance(selected, dict) or not getattr(adapter, 'route_id', None)
            or selected.get('route_id') != adapter.route_id or selected.get('query_id') != getattr(adapter, 'query_id', None)):
        return []
    try:
        roots = [ET.fromstring(shot.get('xml', '')) for shot in (before, after)]
        if any(not root.get('window-id') for root in roots):
            return []
        identity = lambda root: (sorted((k,v) for k,v in root.attrib.items() if k not in {'nonce','captured-at'}),
                                 [(sorted(n.attrib.items()),len(n)) for n in root.iter('node')])
        if identity(roots[0]) != identity(roots[1]):
            return []
        targets = []
        for root, shot in zip(roots, (before, after)):
            current = {**shot, 'nodes': list(root.iter('node'))}
            if (shot.get('evidence', {}).get('query_id') != adapter.query_id
                    or page_kind(current) != 'route_detail'):
                return []
            grounded = resolve_action(action, current, request, state)
            if (grounded.page != 'route_detail' or grounded.kind != action.kind
                    or action.kind == 'Swipe' and grounded.role != 'scroll'):
                return []
            targets.append((grounded.start, grounded.end,
                            tuple(sorted(grounded.node.attrib.items())) if grounded.node is not None else None))
        if targets[0] != targets[1]:
            return []
        nodes = list(roots[0].iter('node')); parents = {c:p for p in nodes for c in p}
        def visible(n):
            while n is not None:
                if (n.get('package', PACKAGE) != PACKAGE or n.get('visible-to-user') == 'false'
                        or n.get('enabled') == 'false'):
                    return False
                n = parents.get(n)
            return True
        icons = []
        start, end, _ = targets[0]
        for n in nodes:
            r = _box(bounds(n), before['width'], before['height'])
            if (not r or not visible(n) or n.get('class') != 'android.widget.ImageView' or list(n)
                    or n.get('text') or n.get('content-desc') or n.get('resource-id')
                    or n.get('clickable') != 'false' or not 12 <= r[2]-r[0] <= 64
                    or abs((r[2]-r[0])-(r[3]-r[1])) > 2):
                continue
            owner = parents.get(n); group = parents.get(owner); scroller = parents.get(group)
            if (owner is None or group is None or scroller is None or list(owner)[-1:] != [n]
                    or len(owner) != 2 or owner.get('class') != 'android.view.ViewGroup'
                    or not scroller.get('class','').endswith('SpringHorizontalScrollView')):
                continue
            texts = [label(x) for x in owner.iter('node') if x is not owner and not list(x) and visible(x)]
            if (len(texts) != 3 or texts[-1] != '' or texts[0] not in {'第一班','第二班'}
                    or not re.fullmatch(r'(?:即将进站|预计(?:[01]\d|2[0-3]):[0-5]\d进站)', texts[1])):
                continue
            # The full swipe path must avoid every ignored icon.
            if action.kind == 'Swipe':
                path = (min(start[0],end[0]),min(start[1],end[1]),max(start[0],end[0])+1,max(start[1],end[1])+1)
                if max(path[0],r[0]) < min(path[2],r[2]) and max(path[1],r[1]) < min(path[3],r[3]):
                    return []
            icons.append(r)
        return icons if 1 <= len(icons) <= 2 else []
    except (WorkflowError, ET.ParseError, TypeError, ValueError, KeyError):
        return []


def visual_decorations_only(action, before, after, *, request=None, state=None):
    """True only for exact content plus independently bounded decorations.

    Does not grant permission to execute: the caller must still freshly ground
    the action against the current native candidates immediately afterwards.
    """
    keys = ('epoch', 'width', 'height', 'rotation', 'current_app')
    if any(before.get(key) != after.get(key) for key in keys) or before.get('current_app') != PACKAGE:
        return False
    width, height = before.get('width'), before.get('height')
    if type(width) is not int or type(height) is not int:
        return False
    first = _box(before.get('status_bar_frame'), width, height)
    fresh = _box(after.get('status_bar_frame'), width, height)
    if (first is None or first != fresh or first[:3] != (0, 0, width)
            or first[3] > min(200, int(height * .08))
            or not _targets_outside_bar(action, first, width, height)):
        return False
    try:
        a, b = _decode(before), _decode(after)
        if a is None or b is None:
            return False
        diff = ImageChops.difference(a, b)
        mask = reduce(ImageChops.lighter, diff.split()).point(lambda n: 255 if n else 0)
        mask.paste(0, first)
        box = mask.getbbox()
        if box is None:
            return True  # All application/keyboard pixels remain identical.
        spinner = _loading_spinner(action, before, after)
        if spinner is not None:
            mask.paste(0, spinner)
            return mask.getbbox() is None  # Every non-spinner pixel stays exact.
        for icon in _arrival_icons_for_entry(action, before, after, request, state):
            outside = mask.copy(); outside.paste(0, icon)
            if outside.getbbox() is None:
                return True  # Only this proven small arrival icon changed.
        detail_icons = _detail_arrival_icons(action, before, after, request, state)
        if detail_icons:
            outside = mask.copy()
            for icon in detail_icons:
                outside.paste(0, icon)
            if outside.getbbox() is None:
                return True
        field_a, field_b = _native_focused_field(before), _native_focused_field(after)
        if field_a is None or field_a != field_b:
            return False
        rect = field_a[0]
        x1, y1, x2, y2 = box; w, h = x2 - x1, y2 - y1
        if (not (rect[0] <= x1 < x2 <= rect[2] and rect[1] <= y1 < y2 <= rect[3])
                or not 1 <= w <= 8 or not max(12, w * 4) <= h <= min(96, int((rect[3] - rect[1]) * .85))
                or w * h > 800 or mask.crop(box).histogram()[255] < w * h * .75):
            return False
        return _solid_caret(a, box) or _solid_caret(b, box)
    except (ValueError, TypeError, KeyError, OSError, binascii.Error, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return False
