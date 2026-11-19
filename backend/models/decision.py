"""Turn a model JSON object into a command the phone is allowed to run."""
from pydantic import ValidationError

from contract import parse_command


def normalize_decision(data) -> dict:
    if isinstance(data, dict) and "type" in data and "command" not in data:
        data = {"command": data, "progress": ""}
    if not isinstance(data, dict):
        raise ValueError("需要一个 JSON 对象")
    progress = str(data.get("progress") or "").strip()
    command = data.get("command")
    if command in (None, "", {}):
        return {"command": None, "progress": progress or None}
    try:
        parsed = parse_command(command)
    except ValidationError as error:
        raise ValueError(error.errors()[0]["msg"]) from None
    return {"command": parsed.model_dump(), "progress": progress or None}
