from __future__ import annotations

from typing import Any

from decision_tests.adapters.base import DecisionAdapter, ProviderError
from decision_tests.models import DecisionResult, Prompt


class JevAdapter(DecisionAdapter):
    """TypeSafe's official JEV System One endpoint."""

    def build_payload(self, prompt: Prompt, query: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "state": query,
            "questions": {
                "decision": {
                    "type": "choice",
                    "instructions": prompt.prompt,
                    "criteria": {choice.value: choice.description for choice in prompt.choices},
                }
            },
        }

    def parse_response(self, data: dict[str, Any], prompt: Prompt) -> DecisionResult:
        answers = data.get("answers")
        if not isinstance(answers, dict) or set(answers) != {"decision"}:
            raise ProviderError("invalid_response")
        return self.result(answers["decision"], data, prompt)
