from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Choice:
    value: str
    description: str


@dataclass(frozen=True, slots=True)
class DatasetItem:
    query: str
    expected: str


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    type: str
    prompt: str
    choices: tuple[Choice, ...]
    dataset: tuple[DatasetItem, ...]
    source: Path


@dataclass(frozen=True, slots=True)
class DecisionResult:
    choice: str
    response: dict[str, Any]
    model: str | None = None


class Adapter(Protocol):
    name: str
    model: str

    async def decide(self, prompt: Prompt, query: str) -> DecisionResult:
        """Return a choice and the provider's raw JSON response for a query."""
