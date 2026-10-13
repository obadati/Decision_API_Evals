from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .adapters.base import ProviderError
from .models import Adapter, DecisionResult, Prompt
from .pricing import TokenPricing, default_pricing, request_cost, token_usage, usage_cost_summary

ProgressCallback = Callable[[int, int], None]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _latency_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "avg": None, "max": None, "p90": None}
    ordered = sorted(values)
    nearest_rank_index = math.ceil(0.9 * len(ordered)) - 1
    return {
        "min": min(values),
        "avg": sum(values) / len(values),
        "max": max(values),
        "p90": ordered[nearest_rank_index],
    }


def _prompt_snapshot(prompt: Prompt) -> dict[str, Any]:
    return {
        "name": prompt.name,
        "type": prompt.type,
        "prompt": prompt.prompt,
        "choices": [
            {"value": choice.value, "description": choice.description} for choice in prompt.choices
        ],
        "dataset": [{"query": item.query, "expected": item.expected} for item in prompt.dataset],
        "source": str(prompt.source),
    }


def _error_result(
    *,
    provider: str,
    repetition: int,
    dataset_index: int,
    query: str,
    expected: str,
    predicted: str | None,
    latency_ms: float,
    error: str,
    reported_model: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "repetition": repetition,
        "dataset_index": dataset_index,
        "query": query,
        "expected": expected,
        "predicted": predicted,
        "correct": False,
        "latency_ms": latency_ms,
        "error": error,
        "response": None,
        "reported_model": reported_model,
    }


def _adapter_metadata(adapters: Sequence[Adapter]) -> dict[str, str]:
    providers: dict[str, str] = {}
    for adapter in adapters:
        try:
            name = adapter.name
            model = adapter.model
        except Exception as exc:
            raise ValueError(
                "each adapter must expose string 'name' and 'model' attributes"
            ) from exc
        if not isinstance(name, str) or not name.strip():
            raise ValueError("adapter names must be non-empty strings")
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"adapter {name!r} must expose a non-empty string model")
        if name in providers:
            raise ValueError(f"adapter names must be unique; duplicate name {name!r}")
        providers[name] = model
    return providers


async def run_benchmark(
    prompt: Prompt,
    adapters: Sequence[Adapter],
    repetitions: int = 1,
    concurrency: int = 1,
    timeout_seconds: float = 30.0,
    on_progress: ProgressCallback | None = None,
    pricing: dict[str, TokenPricing] | None = None,
) -> dict[str, Any]:
    """Run every dataset row for every adapter and return an ordered report.

    The semaphore bounds active adapter requests globally. A request's latency
    starts after it acquires a semaphore slot and includes response validation.
    """
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise ValueError("repetitions must be a positive integer")
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError("concurrency must be a positive integer")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a positive finite number")

    adapter_list = list(adapters)
    providers = _adapter_metadata(adapter_list)
    pricing = pricing or {}
    total_test_runs = len(adapter_list) * len(prompt.dataset) * repetitions
    started_at = _utc_now()
    run_id = uuid4().hex
    semaphore = asyncio.Semaphore(concurrency)
    valid_choices = {choice.value for choice in prompt.choices}
    completed = 0

    async def execute(adapter: Adapter, repetition: int, dataset_index: int) -> dict[str, Any]:
        nonlocal completed
        item = prompt.dataset[dataset_index]
        async with semaphore:
            started = time.perf_counter()
            reported_model: str | None = None
            predicted: str | None = None
            usage = token_usage(None)
            try:
                async with asyncio.timeout(float(timeout_seconds)):
                    result = await adapter.decide(prompt, item.query)
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    if not isinstance(result, DecisionResult):
                        request = _error_result(
                            provider=adapter.name,
                            repetition=repetition,
                            dataset_index=dataset_index,
                            query=item.query,
                            expected=item.expected,
                            predicted=None,
                            latency_ms=elapsed_ms,
                            error="invalid_response",
                        )
                    else:
                        usage = token_usage(result.response)
                        predicted = result.choice if isinstance(result.choice, str) else None
                        if isinstance(result.model, str) and result.model.strip():
                            reported_model = result.model
                        else:
                            reported_model = adapter.model
                        if (
                            not isinstance(result.choice, str)
                            or not isinstance(result.response, dict)
                            or (result.model is not None and not isinstance(result.model, str))
                        ):
                            request = _error_result(
                                provider=adapter.name,
                                repetition=repetition,
                                dataset_index=dataset_index,
                                query=item.query,
                                expected=item.expected,
                                predicted=predicted,
                                latency_ms=elapsed_ms,
                                error="invalid_response",
                                reported_model=reported_model,
                            )
                        elif result.choice not in valid_choices:
                            request = _error_result(
                                provider=adapter.name,
                                repetition=repetition,
                                dataset_index=dataset_index,
                                query=item.query,
                                expected=item.expected,
                                predicted=result.choice,
                                latency_ms=elapsed_ms,
                                error="invalid_choice",
                                reported_model=reported_model,
                            )
                        else:
                            request = {
                                "provider": adapter.name,
                                "repetition": repetition,
                                "dataset_index": dataset_index,
                                "query": item.query,
                                "expected": item.expected,
                                "predicted": result.choice,
                                "correct": result.choice == item.expected,
                                "latency_ms": elapsed_ms,
                                "error": None,
                                "response": result.response,
                                "reported_model": reported_model,
                            }
                    request["latency_ms"] = (time.perf_counter() - started) * 1000.0
            except TimeoutError:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                request = _error_result(
                    provider=adapter.name,
                    repetition=repetition,
                    dataset_index=dataset_index,
                    query=item.query,
                    expected=item.expected,
                    predicted=None,
                    latency_ms=elapsed_ms,
                    error="timeout",
                )
            except httpx.TimeoutException:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                request = _error_result(
                    provider=adapter.name,
                    repetition=repetition,
                    dataset_index=dataset_index,
                    query=item.query,
                    expected=item.expected,
                    predicted=None,
                    latency_ms=elapsed_ms,
                    error="timeout",
                )
            except ProviderError as exc:
                usage = exc.usage
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                request = _error_result(
                    provider=adapter.name,
                    repetition=repetition,
                    dataset_index=dataset_index,
                    query=item.query,
                    expected=item.expected,
                    predicted=None,
                    latency_ms=elapsed_ms,
                    error=str(exc),
                    reported_model=exc.reported_model,
                )
            except httpx.RequestError:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                request = _error_result(
                    provider=adapter.name,
                    repetition=repetition,
                    dataset_index=dataset_index,
                    query=item.query,
                    expected=item.expected,
                    predicted=None,
                    latency_ms=elapsed_ms,
                    error="network_error",
                )
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                request = _error_result(
                    provider=adapter.name,
                    repetition=repetition,
                    dataset_index=dataset_index,
                    query=item.query,
                    expected=item.expected,
                    predicted=None,
                    latency_ms=elapsed_ms,
                    error="request_error",
                )

        request["usage"] = usage
        price = pricing.get(adapter.name) or default_pricing(
            adapter.name, request["reported_model"] or adapter.model
        )
        request["pricing"] = price.snapshot() if price else None
        request["estimated_cost_usd"] = request_cost(request["usage"], price)
        completed += 1
        if on_progress is not None:
            on_progress(completed, total_test_runs)
        return request

    tasks = [
        execute(adapter, repetition, dataset_index)
        for repetition in range(1, repetitions + 1)
        for dataset_index in range(len(prompt.dataset))
        for adapter in adapter_list
    ]
    per_request = await asyncio.gather(*tasks)
    finished_at = _utc_now()

    attempted_latencies = [request["latency_ms"] for request in per_request]
    successful = [request for request in per_request if request["error"] is None]
    successful_latencies = [request["latency_ms"] for request in successful]
    successful_requests = len(successful)
    failed_requests = total_test_runs - successful_requests
    correct_predictions = sum(request["correct"] for request in per_request)

    def percentage(numerator: int, denominator: int) -> float:
        return (numerator / denominator * 100.0) if denominator else 0.0

    provider_results: dict[str, dict[str, Any]] = {}
    for provider_name, model in providers.items():
        provider_requests = [
            request for request in per_request if request["provider"] == provider_name
        ]
        provider_successful = [request for request in provider_requests if request["error"] is None]
        provider_correct = sum(request["correct"] for request in provider_requests)
        provider_results[provider_name] = {
            "model": model,
            **usage_cost_summary(provider_requests),
            "total_test_runs": len(provider_requests),
            "successful_requests": len(provider_successful),
            "failed_requests": len(provider_requests) - len(provider_successful),
            "correct_predictions": provider_correct,
            "success_rate": percentage(provider_correct, len(provider_requests)),
            "accuracy_on_successful_requests": percentage(
                provider_correct, len(provider_successful)
            ),
            "request_success_rate": percentage(len(provider_successful), len(provider_requests)),
            "latency_ms": {
                "attempted": _latency_summary(
                    [request["latency_ms"] for request in provider_requests]
                ),
                "successful": _latency_summary(
                    [request["latency_ms"] for request in provider_successful]
                ),
            },
        }

    return {
        "schema_version": 1,
        "run_id": run_id,
        "provider_results": provider_results,
        "started_at": started_at,
        "finished_at": finished_at,
        "prompt": _prompt_snapshot(prompt),
        "configuration": {
            "repetitions": repetitions,
            "concurrency": concurrency,
            "timeout_seconds": float(timeout_seconds),
        },
        "providers": providers,
        **usage_cost_summary(per_request),
        "total_test_runs": total_test_runs,
        "successful_requests": successful_requests,
        "failed_requests": failed_requests,
        "correct_predictions": correct_predictions,
        "success_rate": percentage(correct_predictions, total_test_runs),
        "accuracy_on_successful_requests": percentage(correct_predictions, successful_requests),
        "request_success_rate": percentage(successful_requests, total_test_runs),
        "latency_ms": {
            "attempted": _latency_summary(attempted_latencies),
            "successful": _latency_summary(successful_latencies),
        },
        "per_request": per_request,
    }


def write_report(report: dict[str, Any], output_dir: Path) -> Path:
    """Write one JSON report atomically without replacing an existing report."""
    run_id = report.get("run_id") if isinstance(report, dict) else None
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
        raise ValueError("report must contain a filesystem-safe string run_id")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_dir,
            prefix=".decision-test-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = stream.name
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        published_path: Path | None = None
        for suffix in range(1, 100_000):
            suffix_text = "" if suffix == 1 else f"_{suffix}"
            candidate = output_dir / f"decision-test-{run_id}{suffix_text}.json"
            try:
                # Hard-link publication is atomic and fails if another run has
                # already claimed this name, so no reader sees a partial file.
                os.link(temporary_path, candidate)
                published_path = candidate
                break
            except FileExistsError:
                continue
        if published_path is None:
            raise FileExistsError(f"could not reserve a unique report name for run {run_id!r}")
        Path(temporary_path).unlink()
        temporary_path = None
        return published_path
    finally:
        if temporary_path is not None:
            Path(temporary_path).unlink(missing_ok=True)
