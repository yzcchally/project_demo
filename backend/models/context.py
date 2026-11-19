"""Save this step's screenshot and pack it with the memory so far."""
import base64
from pathlib import Path

from memory_store import begin_round, earlier_rounds, memory_notes
from models.turn import Turn

BACKEND = Path(__file__).resolve().parents[1]


def build_turn(task: str, screen: dict) -> Turn:
    png = base64.b64decode(screen["image_png_base64"])
    saved = begin_round(png)
    (BACKEND / "last_screen.png").write_bytes(png)
    turn = Turn(
        task=task,
        index=saved["index"],
        width=int(screen.get("width") or 0),
        height=int(screen.get("height") or 0),
        image_png=png,
        folder=saved["folder"],
        history=earlier_rounds(saved["index"]),
        memory=memory_notes(),
    )
    print(f"\n任务: {task}")
    print(f"本轮: 第 {turn.index} 轮  {turn.folder}")
    return turn
