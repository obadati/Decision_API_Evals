from __future__ import annotations

import asyncio
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

from decision_tests import cli
from decision_tests.adapters import ADAPTERS
from decision_tests.adapters.base import ProviderError
from decision_tests.benchmark import run_benchmark
from decision_tests.config import ConfigurationError, ProviderConfig
from decision_tests.models import Choice, DatasetItem, DecisionResult, Prompt
from decision_tests.pricing import TokenPricing


def make_prompt(queries: tuple[str, ...] = ("correct", "wrong", "failed")) -> Prompt:
    expected = {"correct": "yes", "wrong": "no", "failed": "yes"}
    return Prompt(
        name="cost-reporting",
        type="choice",
        prompt="Choose yes or no",
        choices=(Choice("yes", "Yes"), Choice("no", "No")),
        dataset=tuple(DatasetItem(query, expected[query]) for query in queries),
        source=Path("prompts/cost-reporting.yaml"),
    )


class UsageAdapter:
    def __init__(
        self, name: str, model: str, input_tokens: dict[str, int], output_tokens: dict[str, int]
    ):
        self.name = name
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    async def decide(self, prompt: Prompt, query: str) -> DecisionResult:
        if query == "failed":
            raise ProviderError("refusal")
        choice = "yes"
        return DecisionResult(
            choice=choice,
            response={
                "model": self.model,
                "usage": {
                    "input_tokens": self.input_tokens[query],
                    "output_tokens": self.output_tokens[query],
                },
            },
            model=self.model,
        )


def test_usage_and_cost_aggregate_across_providers_repetitions_wrong_answers_and_failures():
    prompt = make_prompt()
    jev = UsageAdapter(
        "jev",
        "jev-1.13.0",
        {"correct": 100, "wrong": 200},
        {"correct": 2, "wrong": 3},
    )
    openai = UsageAdapter(
        "openai",
        "gpt-6-luna",
        {"correct": 10, "wrong": 20},
        {"correct": 4, "wrong": 6},
    )

    report = asyncio.run(run_benchmark(prompt, [jev, openai], repetitions=2))

    jev_summary = report["provider_results"]["jev"]
    assert jev_summary["usage"] == {
        "input_tokens": 600,
        "output_tokens": 10,
        "total_tokens": 610,
    }
    assert jev_summary["usage_requests"] == {
        "input_tokens": 4,
        "output_tokens": 4,
        "total_tokens": 4,
    }
    assert jev_summary["estimated_cost_usd"] == pytest.approx(0.0000252)
    assert jev_summary["costed_requests"] == 4
    assert jev_summary["cost_complete"] is False
    assert jev_summary["correct_predictions"] == 2

    openai_summary = report["provider_results"]["openai"]
    assert openai_summary["usage"] == {
        "input_tokens": 60,
        "output_tokens": 20,
        "total_tokens": 80,
    }
    assert openai_summary["estimated_cost_usd"] == pytest.approx(0.000006)
    assert openai_summary["costed_requests"] == 4
    assert openai_summary["cost_complete"] is False
    assert openai_summary["correct_predictions"] == 2

    assert report["usage"] == {
        "input_tokens": 660,
        "output_tokens": 30,
        "total_tokens": 690,
    }
    assert report["estimated_cost_usd"] == pytest.approx(0.0000312)
    assert report["costed_requests"] == 8
    assert report["cost_complete"] is False
    assert report["failed_requests"] == 4

    wrong_rows = [row for row in report["per_request"] if row["query"] == "wrong"]
    assert len(wrong_rows) == 4
    assert all(row["correct"] is False for row in wrong_rows)
    assert all(row["estimated_cost_usd"] is not None for row in wrong_rows)
    failed_rows = [row for row in report["per_request"] if row["query"] == "failed"]
    assert all(
        row["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
        for row in failed_rows
    )
    assert all(row["estimated_cost_usd"] is None for row in failed_rows)


def test_unknown_model_keeps_usage_but_does_not_estimate_cost():
    adapter = UsageAdapter("openai", "unlisted-model", {"correct": 100}, {"correct": 5})
    report = asyncio.run(run_benchmark(make_prompt(("correct",)), [adapter]))
    request = report["per_request"][0]

    assert request["usage"] == {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105}
    assert request["pricing"] is None
    assert request["estimated_cost_usd"] is None
    assert report["provider_results"]["openai"]["estimated_cost_usd"] is None
    assert report["provider_results"]["openai"]["costed_requests"] == 0
    assert report["provider_results"]["openai"]["cost_complete"] is False


def test_azure_deployment_uses_reported_microsoft_model_for_price_snapshot():
    class AzureAdapter(UsageAdapter):
        async def decide(self, prompt: Prompt, query: str) -> DecisionResult:
            return DecisionResult(
                "yes",
                {
                    "model": "microsoft-decision-1",
                    "usage": {"input_tokens": 1_000_000, "output_tokens": 2},
                },
                model="microsoft-decision-1",
            )

    adapter = AzureAdapter("azure", "my-deployment", {"correct": 1}, {"correct": 1})
    report = asyncio.run(run_benchmark(make_prompt(("correct",)), [adapter]))
    request = report["per_request"][0]

    assert report["provider_results"]["azure"]["model"] == "my-deployment"
    assert request["reported_model"] == "microsoft-decision-1"
    assert request["pricing"]["model"] == "Microsoft-Decision-1"
    assert request["estimated_cost_usd"] == 0.042


@pytest.mark.parametrize("width", [80, 180])
def test_report_table_shows_cost_to_eight_decimal_places_and_marks_partial_totals(
    monkeypatch, width
):
    adapter = UsageAdapter(
        "jev",
        "jev-1.13.0",
        {"correct": 100},
        {"correct": 0},
    )
    report = asyncio.run(run_benchmark(make_prompt(("correct", "failed")), [adapter]))
    buffer = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buffer, width=width, color_system=None))

    cli.show_report(report)

    output = buffer.getvalue()
    assert "In tokens" in output
    assert "Out tokens" in output
    assert "Cost USD" in output
    assert "100*" in output
    assert "0*" in output
    assert "$0.00000420*" in output
    assert "partial total" in " ".join(output.split())


def test_explicit_provider_pricing_is_applied_instead_of_default_rates():
    adapter = UsageAdapter("jev", "jev-1.13.0", {"correct": 100_000}, {"correct": 200_000})
    price = TokenPricing(Decimal("1"), Decimal("2"), "test override", "jev-1.13.0")

    report = asyncio.run(
        run_benchmark(make_prompt(("correct",)), [adapter], pricing={"jev": price})
    )

    request = report["per_request"][0]
    assert request["pricing"]["source"] == "test override"
    assert request["estimated_cost_usd"] == 0.5


def test_refusal_response_usage_is_counted_without_saving_rejected_body():
    response_data = {
        "model": "gpt-6-luna",
        "answers": [{"name": "decision", "type": "refusal"}],
        "usage": {"input_tokens": 100, "output_tokens": 2, "total_tokens": 102},
    }

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response_data))
        ) as client:
            config = ProviderConfig(
                "openai",
                "https://example.invalid/v1/decisions",
                "gpt-6-luna",
                "test-key",
            )
            adapter = ADAPTERS["openai"](config, client)
            return await run_benchmark(make_prompt(("correct",)), [adapter])

    report = asyncio.run(run())
    request = report["per_request"][0]

    assert request["error"] == "refusal"
    assert request["response"] is None
    assert request["reported_model"] == "gpt-6-luna"
    assert request["usage"] == {
        "input_tokens": 100,
        "output_tokens": 2,
        "total_tokens": 102,
    }
    assert request["estimated_cost_usd"] == pytest.approx(0.00001)
    assert report["provider_results"]["openai"]["failed_requests"] == 1
    assert report["provider_results"]["openai"]["cost_complete"] is True
    assert "refusal" not in str(request["response"])


def test_invalid_price_override_fails_before_http_client_is_created(monkeypatch):
    monkeypatch.setenv("JEV_INPUT_USD_PER_MILLION", "NaN")
    monkeypatch.setenv("JEV_OUTPUT_USD_PER_MILLION", "0")
    monkeypatch.setattr(
        cli,
        "provider_config",
        lambda provider, model=None, demo=False: ProviderConfig(
            provider,
            "https://example.invalid",
            "jev-1.13.0",
            "test-key",
        ),
    )

    def unexpected_client(*args, **kwargs):
        raise AssertionError("HTTP client must not be created for invalid pricing")

    monkeypatch.setattr(cli.httpx, "AsyncClient", unexpected_client)
    args = SimpleNamespace(timeout=30.0, concurrency=1, demo=False, repetitions=1)

    with pytest.raises(ConfigurationError, match="non-negative finite USD rate"):
        asyncio.run(cli.execute(args, make_prompt(("correct",)), ["jev"], {}))
