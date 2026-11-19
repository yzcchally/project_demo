"""The single packet every model receives for one step."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Turn:
    task: str
    index: int
    width: int
    height: int
    image_png: bytes
    folder: Path
    history: list[dict]
    memory: dict
    extra: dict = field(default_factory=dict)

    def payload(self) -> dict:
        return {
            "task": self.task,
            "step": self.index,
            "screen": {
                "width": self.width,
                "height": self.height,
                "file": f"{self.index:03d}/screen.png",
            },
            "history": self.history,
            "memory": self.memory,
            "extra": self.extra,
        }
