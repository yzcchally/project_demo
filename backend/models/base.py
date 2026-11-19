"""Shared model entry. Subclasses only change how one round becomes one command."""
import threading
from abc import ABC, abstractmethod

from memory_store import remember_input, remember_output
from models.turn import Turn


class ModelCancelled(Exception):
    """Phone paused or stopped while this model was still deciding."""


class AgentModel(ABC):
    kind = ""

    def __init__(self, options: dict):
        self.options = options
        self.name = str(options.get("name") or self.kind)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def prepare(self, turn: Turn) -> Turn:
        """Subclass hook for extra lookup before the model answers. Default keeps the round unchanged."""
        return turn

    def decide(self, turn: Turn) -> dict:
        """Input is this round, including every earlier round. Output is one command for the phone."""
        self._cancel.clear()
        prepared = self.prepare(turn)
        remember_input(prepared.index, prepared.payload())
        if self._cancel.is_set():
            raise ModelCancelled()
        decision = self.complete(prepared)
        remember_output(prepared.index, decision)
        return decision

    @abstractmethod
    def complete(self, turn: Turn) -> dict:
        """Return {"command": dict | None, "progress": str | None}."""
