"""Interfaces for order mirrors: places that receive a copy of every paper trade."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Signal
from .position import Position


class OrderMirror(ABC):
    """Receives entry/exit events from the paper ledger (e.g. to send them to a sandbox API)."""

    name: str = "mirror"

    def on_signal(self, signal: Signal) -> None:
        """Called for every signal, including NO_TRADE."""

    @abstractmethod
    def on_entry(self, position: Position, signal: Signal) -> None: ...

    @abstractmethod
    def on_exit(self, position: Position) -> None: ...
