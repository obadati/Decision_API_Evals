"""Common HTTP handling; provider modules own the wire contracts."""

from __future__ import annotations

import json
import math
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from decision_tests.config import ProviderConfig
from decision_tests.models import DecisionResult, Prompt
from decision_tests.pricing import token_usage


class ProviderError(Exception):
    """A safe, fixed diagnostic code; never contains vendor error bodies or credentials."""

    def __init__(self, code: str):
        allowed = {
            "invalid_response",
            "invalid_choice",
            "refusal",
            "unsupported_prompt",
            "invalid_json",
        }
        if code not in allowed and not re.fullmatch(r"http_[1-5][0-9]{2}", code):
            code = "request_error"
        super().__init__(code)
        self.usage = token_usage(None)
        self.reported_model: str | None = None


class DecisionAdapter(ABC):
    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient):
        self.config = config
        self.client = client
        self.name = config.name
        self.model = config.model

    @abstractmethod
    def build_payload(self, prompt: Prompt, query: str) -> dict[str, Any]:
        """Format a request without ground-truth labels."""

    @abstractmethod
    def parse_response(self, data: dict[str, Any], prompt: Prompt) -> DecisionResult:
        """Normalize the chosen label and keep the original JSON."""

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key}"}

    def sanitize(self, value: Any) -> Any:
        """Protect against a vendor echoing the configured credential in success JSON."""
        if isinstance(value, str):
            return value.replace(self.config.api_key, "[REDACTED]")
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, dict):
            return {self.sanitize(key): self.sanitize(item) for key, item in value.items()}
        return value

    def result(self, answer: Any, data: dict[str, Any], prompt: Prompt) -> DecisionResult:
        if not isinstance(answer, dict):
            raise ProviderError("invalid_response")
        if answer.get("type") == "refusal":
            raise ProviderError("refusal")
        if answer.get("type") != "choice":
            raise ProviderError("invalid_response")
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in {c.value for c in prompt.choices}:
            raise ProviderError("invalid_choice")
        reported_model = data.get("model")
        return DecisionResult(
            choice=choice,
            response=self.sanitize(data),
            model=self.sanitize(reported_model) if isinstance(reported_model, str) else None,
        )

    async def decide(self, prompt: Prompt, query: str) -> DecisionResult:
        if prompt.type != "choice" or not 2 <= len(prompt.choices) <= 255:
            raise ProviderError("unsupported_prompt")
        response = await self.client.post(
            self.config.endpoint,
            json=self.build_payload(prompt, query),
            headers=self.headers(),
        )
        if not response.is_success:
            raise ProviderError(f"http_{response.status_code}")
        try:

            def reject_constant(value: str) -> None:
                raise ValueError("non-finite JSON number")

            def finite_float(value: str) -> float:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError("non-finite JSON number")
                return number

            data = json.loads(
                response.content, parse_constant=reject_constant, parse_float=finite_float
            )
        except ValueError:
            raise ProviderError("invalid_json") from None
        if not isinstance(data, dict):
            raise ProviderError("invalid_response")
        try:
            return self.parse_response(data, prompt)
        except ProviderError as exc:
            # A refusal or invalid decision may still have billable usage. Keep only
            # validated counts and a sanitized model, never the rejected response body.
            exc.usage = token_usage(data)
            model = data.get("model")
            exc.reported_model = self.sanitize(model) if isinstance(model, str) else None
            raise
