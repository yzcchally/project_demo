"""One round given to a model. It carries this step and every step before it."""
from dataclasses import dataclass, field


@dataclass
class Turn:
    index: int
    image_png: bytes
    image_file: str
    text: str
    info: dict = field(default_factory=dict)
    previous: list["Turn"] = field(default_factory=list)
    task: str = ""
    additions: list[dict] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    extra: list[dict] = field(default_factory=list)
    memory: dict = field(default_factory=dict)

    def prompt_lines(self) -> list[dict]:
        """Original prompt is step 0. Later lines use the backend round where each one was added."""
        lines = []
        text = self.task.strip()
        if text:
            lines.append({"step": 0, "text": text})
        for item in self.additions:
            line = str(item.get("text") or "").strip()
            if line:
                lines.append({"step": int(item.get("step") or 0), "text": line})
        return lines

    def payload(self) -> dict:
        """This round, with earlier rounds. prompt holds every instruction and the step it was entered."""
        return {
            "prompt": self.prompt_lines(),
            "index": self.index,
            "image": self.image_file,
            "text": self.text,
            "info": self.info,
            "previous": [item.record() for item in self.previous],
            "commands": self.commands,
            "extra": self.extra,
            "memory": self.memory,
        }

    def record(self) -> dict:
        return {
            "index": self.index,
            "image": self.image_file,
            "text": self.text,
            "info": self.info,
        }
