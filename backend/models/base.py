"""Shared model entry. Subclasses only change how a Turn becomes a decision."""
import threading
from abc import ABC, abstractmethod

from memory_store import remember_input
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
        """Optional step before the model runs. RAG and other lookups go here."""
        return turn

    def decide(self, turn: Turn) -> dict:
        self._cancel.clear()
        prepared = self.prepare(turn)
        remember_input(prepared.index, prepared.payload())
        if self._cancel.is_set():
            raise ModelCancelled()
        return self.complete(prepared)

    @abstractmethod
    def complete(self, turn: Turn) -> dict:
        """Return {"command": dict | None, "progress": str | None}."""
