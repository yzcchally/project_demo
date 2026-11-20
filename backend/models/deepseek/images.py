"""Shrink a screenshot before it is sent to the vision model."""
import base64
from io import BytesIO

from PIL import Image

LIMIT = 1280


def prepare_image(png: bytes) -> tuple[str, int, int, float, float]:
    """Return a jpeg data URL, the size the model sees, and how to scale coordinates back."""
    raw = Image.open(BytesIO(png))
    origin_w, origin_h = raw.size
    image = raw.convert("RGB")
    image.thumbnail((LIMIT, LIMIT), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    seen_w = max(image.width, 1)
    seen_h = max(image.height, 1)
    return f"data:image/jpeg;base64,{encoded}", seen_w, seen_h, origin_w / seen_w, origin_h / seen_h


def scale_command(command: dict | None, scale_x: float, scale_y: float) -> dict | None:
    """Map pixels on the image the model saw back to the original screenshot."""
    if not isinstance(command, dict):
        return command
    scaled = dict(command)
    for key in ("x", "x1", "x2"):
        if key in scaled:
            scaled[key] = int(round(float(scaled[key]) * scale_x))
    for key in ("y", "y1", "y2"):
        if key in scaled:
            scaled[key] = int(round(float(scaled[key]) * scale_y))
    if "distance" in scaled:
        scaled["distance"] = int(round(float(scaled["distance"]) * ((scale_x + scale_y) / 2)))
    return scaled
