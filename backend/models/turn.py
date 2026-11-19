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
    memory: dict = field(default_factory=dict)

    def payload(self) -> dict:
        """This round, with earlier rounds inline. Screenshot bytes stay on the objects."""
        return {
            "task": self.task,
            "index": self.index,
            "image": self.image_file,
            "text": self.text,
            "info": self.info,
            "previous": [item.record() for item in self.previous],
            "memory": self.memory,
        }

    def record(self) -> dict:
        return {
            "index": self.index,
            "image": self.image_file,
            "text": self.text,
            "info": self.info,
        }
