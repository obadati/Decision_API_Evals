from __future__ import annotations

from typing import Any

from decision_tests.adapters.base import DecisionAdapter, ProviderError
from decision_tests.models import DecisionResult, Prompt


class OpenAIAdapter(DecisionAdapter):
    """Dedicated Decisions API; the questions and answers are arrays."""

    def build_payload(self, prompt: Prompt, query: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "input": query,
            "questions": [
                {
                    "type": "choice",
                    "name": "decision",
                    "instructions": prompt.prompt,
                    "choices": [
                        {"value": choice.value, "description": choice.description}
                        for choice in prompt.choices
                    ],
                }
            ],
        }

    def parse_response(self, data: dict[str, Any], prompt: Prompt) -> DecisionResult:
        answers = data.get("answers")
        if (
            not isinstance(answers, list)
            or len(answers) != 1
            or not isinstance(answers[0], dict)
            or answers[0].get("name") != "decision"
        ):
            raise ProviderError("invalid_response")
        return self.result(answers[0], data, prompt)
