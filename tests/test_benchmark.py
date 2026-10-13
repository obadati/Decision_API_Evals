from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from decision_tests.adapters.base import ProviderError
from decision_tests.benchmark import _latency_summary, run_benchmark, write_report
from decision_tests.models import Choice, DatasetItem, DecisionResult, Prompt


def make_prompt(queries: tuple[str, ...] = ("first", "second")) -> Prompt:
    expected = ("yes", "no")
    return Prompt(
        name="classification",
        type="choice",
        prompt="Choose yes or no",
        choices=(Choice("yes", "Yes"), Choice("no", "No")),
        dataset=tuple(
            DatasetItem(query=query, expected=expected[index % len(expected)])
            for index, query in enumerate(queries)
        ),
        source=Path("prompts/classification.yaml"),
    )


class MatchingAdapter:
    def __init__(
        self,
        name: str,
        model: str = "test-model",
        shared_counter: dict[str, int] | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.active = 0
        self.max_active = 0
        self.shared_counter = shared_counter

    async def decide(self, prompt: Prompt, query: str) -> DecisionResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.shared_counter is not None:
            self.shared_counter["active"] += 1
            self.shared_counter["max"] = max(
                self.shared_counter["max"], self.shared_counter["active"]
            )
        try:
            await asyncio.sleep(0.005)
            choice = "yes" if query == "first" else "no"
            return DecisionResult(
                choice=choice,
                response={"raw": {"query": query}},
                model=f"reported-{self.name}",
            )
        finally:
            self.active -= 1
            if self.shared_counter is not None:
                self.shared_counter["active"] -= 1


def test_runs_every_row_and_repetition_with_global_concurrency_and_stable_order() -> None:
    prompt = make_prompt()
    shared_counter = {"active": 0, "max": 0}
    first = MatchingAdapter("first-provider", shared_counter=shared_counter)
    second = MatchingAdapter("second-provider", shared_counter=shared_counter)
    progress: list[tuple[int, int]] = []

    report = asyncio.run(
        run_benchmark(
            prompt,
            [first, second],
            repetitions=2,
            concurrency=2,
            on_progress=lambda completed, total: progress.append((completed, total)),
        )
    )

    assert report["total_test_runs"] == 8
    assert report["successful_requests"] == 8
    assert report["failed_requests"] == 0
    assert report["correct_predictions"] == 8
    assert report["success_rate"] == 100.0
    assert report["accuracy_on_successful_requests"] == 100.0
    assert report["request_success_rate"] == 100.0
    assert report["providers"] == {
        "first-provider": "test-model",
        "second-provider": "test-model",
    }
    assert list(report)[:3] == ["schema_version", "run_id", "provider_results"]
    assert report["provider_results"]["first-provider"]["model"] == "test-model"
    assert report["provider_results"]["first-provider"]["total_test_runs"] == 4
    assert report["provider_results"]["first-provider"]["successful_requests"] == 4
    assert report["provider_results"]["first-provider"]["correct_predictions"] == 4
    assert report["provider_results"]["first-provider"]["request_success_rate"] == 100.0
    request_keys = [
        (row["provider"], row["repetition"], row["dataset_index"]) for row in report["per_request"]
    ]
    assert request_keys == [
        (provider, repetition, dataset_index)
        for repetition in (1, 2)
        for dataset_index in (0, 1)
        for provider in ("first-provider", "second-provider")
    ]
    assert all(row["response"] == {"raw": {"query": row["query"]}} for row in report["per_request"])
    assert all(row["reported_model"].startswith("reported-") for row in report["per_request"])
    assert len(progress) == 8
    assert progress[-1] == (8, 8)
    assert shared_counter["max"] == 2
    assert shared_counter["active"] == 0
    assert report["latency_ms"]["attempted"]["p90"] is not None
    assert report["latency_ms"]["successful"]["avg"] is not None


class ErrorAdapter:
    name = "mixed-provider"
    model = "configured-model"

    async def decide(self, prompt: Prompt, query: str) -> DecisionResult:
        if query == "timeout":
            await asyncio.sleep(0.05)
        if query == "provider_timeout":
            raise httpx.ReadTimeout("Authorization: bearer-secret")
        if query == "exception":
            raise RuntimeError("Authorization: bearer-secret")
        if query == "provider_error":
            raise ProviderError("refusal")
        if query == "network_error":
            raise httpx.RequestError("Authorization: bearer-secret")
        if query == "invalid":
            return DecisionResult("unexpected", {"token": "never reported"})
        if query == "malformed":
            return "not a decision result"  # type: ignore[return-value]
        expected = next(item.expected for item in prompt.dataset if item.query == query)
        return DecisionResult(expected, {"ok": True})


def test_request_failures_are_contained_and_do_not_leak_exception_or_raw_response() -> None:
    report = asyncio.run(
        run_benchmark(
            make_prompt(
                (
                    "good",
                    "timeout",
                    "exception",
                    "invalid",
                    "malformed",
                    "provider_error",
                    "network_error",
                    "provider_timeout",
                )
            ),
            [ErrorAdapter()],
            timeout_seconds=0.01,
            concurrency=8,
        )
    )

    assert report["total_test_runs"] == 8
    assert report["successful_requests"] == 1
    assert report["failed_requests"] == 7
    assert report["correct_predictions"] == 1
    assert report["success_rate"] == 12.5
    assert report["accuracy_on_successful_requests"] == 100.0
    assert report["request_success_rate"] == 12.5
    assert [row["error"] for row in report["per_request"]] == [
        None,
        "timeout",
        "request_error",
        "invalid_choice",
        "invalid_response",
        "refusal",
        "network_error",
        "timeout",
    ]
    assert all("bearer-secret" not in str(row) for row in report["per_request"])
    assert report["per_request"][3]["response"] is None
    assert report["per_request"][4]["response"] is None
    assert report["latency_ms"]["attempted"]["min"] is not None
    assert report["latency_ms"]["successful"]["min"] == report["per_request"][0]["latency_ms"]


class AllCorrectAdapter:
    name = "all-correct"
    model = "clean-model"

    async def decide(self, prompt: Prompt, query: str) -> DecisionResult:
        expected = next(item.expected for item in prompt.dataset if item.query == query)
        return DecisionResult(expected, {"ok": True})


def test_provider_results_keep_independent_success_and_accuracy_denominators() -> None:
    report = asyncio.run(
        run_benchmark(
            make_prompt(("good", "timeout", "exception", "invalid", "malformed")),
            [ErrorAdapter(), AllCorrectAdapter()],
            timeout_seconds=0.01,
            concurrency=2,
        )
    )

    mixed = report["provider_results"]["mixed-provider"]
    clean = report["provider_results"]["all-correct"]
    assert mixed["model"] == "configured-model"
    assert mixed["total_test_runs"] == 5
    assert mixed["successful_requests"] == 1
    assert mixed["failed_requests"] == 4
    assert mixed["correct_predictions"] == 1
    assert mixed["success_rate"] == 20.0
    assert mixed["accuracy_on_successful_requests"] == 100.0
    assert mixed["request_success_rate"] == 20.0
    assert mixed["latency_ms"]["attempted"]["min"] is not None
    assert mixed["latency_ms"]["successful"]["min"] is not None
    assert clean["model"] == "clean-model"
    assert clean["total_test_runs"] == 5
    assert clean["successful_requests"] == 5
    assert clean["failed_requests"] == 0
    assert clean["correct_predictions"] == 5
    assert clean["success_rate"] == 100.0
    assert clean["accuracy_on_successful_requests"] == 100.0
    assert clean["request_success_rate"] == 100.0
    assert clean["latency_ms"]["attempted"] == clean["latency_ms"]["successful"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"repetitions": 0}, "repetitions"),
        ({"repetitions": True}, "repetitions"),
        ({"concurrency": 0}, "concurrency"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": float("inf")}, "timeout_seconds"),
    ],
)
def test_rejects_invalid_configuration(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        asyncio.run(run_benchmark(make_prompt(), [MatchingAdapter("provider")], **kwargs))


def test_rejects_duplicate_adapter_names() -> None:
    with pytest.raises(ValueError, match="adapter names must be unique"):
        asyncio.run(
            run_benchmark(
                make_prompt(),
                [MatchingAdapter("duplicate"), MatchingAdapter("duplicate")],
            )
        )


def test_write_report_creates_unique_atomic_json_files(tmp_path: Path) -> None:
    report = asyncio.run(run_benchmark(make_prompt(), [MatchingAdapter("provider")]))

    first = write_report(report, tmp_path)
    second = write_report(report, tmp_path)

    assert first != second
    assert first.suffix == second.suffix == ".json"
    assert json.loads(first.read_text(encoding="utf-8")) == report
    assert json.loads(second.read_text(encoding="utf-8")) == report


def test_latency_percentile_uses_nearest_rank() -> None:
    assert _latency_summary([10.0, 1.0, 9.0, 2.0, 8.0, 3.0, 7.0, 4.0, 6.0, 5.0]) == {
        "min": 1.0,
        "avg": 5.5,
        "max": 10.0,
        "p90": 9.0,
    }
    assert _latency_summary([]) == {
        "min": None,
        "avg": None,
        "max": None,
        "p90": None,
    }
