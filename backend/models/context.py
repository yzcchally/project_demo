"""Pack the new screenshot and every earlier round into the current turn."""
import base64
from pathlib import Path

from memory_store import additions_before, begin_round, earlier_commands, earlier_extras, earlier_turns, memory_notes
from models.turn import Turn

BACKEND = Path(__file__).resolve().parents[1]


def build_turn(task: str, screen: dict) -> Turn:
    png = base64.b64decode(screen["image_png_base64"])
    saved = begin_round(png)
    (BACKEND / "last_screen.png").write_bytes(png)
    previous: list[Turn] = []
    for item in earlier_turns(saved["index"]):
        previous.append(Turn(
            index=item["index"],
            image_png=item["image_png"],
            image_file=item["image_file"],
            text=item["text"],
            info=item["info"],
            previous=list(previous),
            task=task,
        ))
    return Turn(
        index=saved["index"],
        image_png=png,
        image_file=f"{saved['index']:03d}/screen.png",
        text="",
        info={
            "width": int(screen.get("width") or 0),
            "height": int(screen.get("height") or 0),
            "command": None,
            "latest": True,
        },
        previous=previous,
        task=task,
        additions=additions_before(saved["index"]),
        commands=earlier_commands(saved["index"]),
        extra=earlier_extras(saved["index"]),
        memory=memory_notes(),
    )
