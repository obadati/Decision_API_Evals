"""Interactive launch and automation-friendly command-line options."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
import questionary
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from decision_tests.adapters import ADAPTERS
from decision_tests.adapters.demo import demo_transport
from decision_tests.benchmark import run_benchmark, write_report
from decision_tests.config import (
    PROVIDERS,
    ConfigurationError,
    model_default,
    provider_config,
)
from decision_tests.models import Prompt
from decision_tests.pricing import pricing_overrides
from decision_tests.prompts import PromptError, load_prompts

console = Console()


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive finite number") from None
    if number <= 0 or not math.isfinite(number):
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Compare JEV, OpenAI, and Azure decision APIs.")
    result.add_argument("--prompt-dir", type=Path, default=Path("prompt"))
    result.add_argument("--env-file", type=Path, default=Path(".env"))
    result.add_argument("--output-dir", type=Path, default=Path("results"))
    result.add_argument("--prompt", help="Exact YAML name, independent of filename")
    result.add_argument("--providers", nargs="+", choices=PROVIDERS)
    result.add_argument("--model", action="append", default=[], metavar="PROVIDER=MODEL")
    result.add_argument("--repetitions", type=positive_int)
    result.add_argument("--concurrency", type=positive_int)
    result.add_argument("--timeout", type=positive_float)
    result.add_argument(
        "--non-interactive", action="store_true", help="Require prompt and providers"
    )
    result.add_argument(
        "--demo", action="store_true", help="Offline synthetic responses; no API calls"
    )
    result.add_argument(
        "--dry-run", action="store_true", help="Show first-row payloads; no API calls"
    )
    result.add_argument("--list-prompts", action="store_true")
    return result


def ask(question: Any) -> Any:
    answer = question.ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def ask_number(message: str, default: str, number_type: Any) -> Any:
    def validate(value: str) -> bool | str:
        try:
            number_type(value)
        except argparse.ArgumentTypeError as exc:
            return str(exc)
        return True

    return number_type(ask(questionary.text(message, default=default, validate=validate)))


def choose_options(
    args: argparse.Namespace, prompts: list[Prompt]
) -> tuple[Prompt, list[str], dict]:
    models: dict[str, str] = {}
    for override in args.model:
        provider, separator, model = override.partition("=")
        if not separator or provider not in PROVIDERS or not model.strip():
            raise ConfigurationError("--model must be PROVIDER=MODEL (jev, openai, or azure).")
        if provider in models:
            raise ConfigurationError("Supply at most one model override for each provider.")
        models[provider] = model.strip()
    if args.non_interactive and (not args.prompt or not args.providers):
        raise ConfigurationError("--non-interactive requires --prompt and --providers.")
    if not args.non_interactive and not sys.stdin.isatty():
        raise ConfigurationError(
            "Interactive mode needs a terminal. Use --non-interactive --prompt NAME --providers ..."
        )
    name = args.prompt
    if name is None:
        name = ask(questionary.select("Choose a prompt:", choices=[p.name for p in prompts]))
    prompt = next((p for p in prompts if p.name == name), None)
    if prompt is None:
        raise ConfigurationError(
            "Unknown YAML prompt name. Use --list-prompts to see available names."
        )
    providers = args.providers
    if providers is None:
        labels = {
            "jev": "JEV / TypeSafe",
            "openai": "OpenAI Decisions",
            "azure": "Azure Microsoft-Decision-1",
        }
        providers = ask(
            questionary.checkbox(
                "Select providers (Space to toggle, Enter to continue):",
                choices=[questionary.Choice(labels[p], value=p) for p in PROVIDERS],
                validate=lambda selected: bool(selected) or "Select at least one provider.",
            )
        )
    if len(providers) != len(set(providers)):
        raise ConfigurationError("Each provider may be selected once per run.")
    if set(models) - set(providers):
        raise ConfigurationError("A --model override refers to a provider that was not selected.")
    if not args.non_interactive:
        for provider in providers:
            if provider not in models:
                models[provider] = ask(
                    questionary.text(
                        f"{provider}: model{' deployment name' if provider == 'azure' else ''}:",
                        default=model_default(provider, demo=args.demo or args.dry_run),
                        validate=lambda text: (
                            bool(text.strip()) or "Enter a model or deployment name."
                        ),
                    )
                ).strip()
        if args.repetitions is None:
            args.repetitions = ask_number("Repetitions of every dataset row:", "1", positive_int)
        if args.concurrency is None:
            args.concurrency = ask_number(
                "Maximum simultaneous requests (all providers):", "1", positive_int
            )
        if args.timeout is None:
            args.timeout = ask_number("Request timeout in seconds:", "30", positive_float)
    args.repetitions = args.repetitions or 1
    args.concurrency = args.concurrency or 1
    args.timeout = args.timeout or 30.0
    return prompt, providers, models


def show_report(report: dict[str, Any]) -> None:
    def token_cell(result: dict[str, Any], key: str) -> str:
        value = result["usage"][key]
        if value is None:
            return "—"
        suffix = "*" if result["usage_requests"][key] < result["total_test_runs"] else ""
        return f"{value:,}{suffix}"

    table = Table(title=f"Results: {report['prompt']['name']}")
    separate_latency = console.width < 120
    if separate_latency:
        table.padding = (0, 0)
    headings = (
        "Provider / model",
        "Runs",
        "Correct %",
        "Requests OK %",
        "In tokens",
        "Out tokens",
        "Cost USD",
    )
    latency_headings = ("Min ms", "Avg ms", "Max ms", "P90 ms")
    if not separate_latency:
        headings += latency_headings
    for heading in headings:
        table.add_column(heading, min_width=12 if heading == "Cost USD" else None)
    latency_table = Table(title="Latency of valid responses")
    for heading in ("Provider / model", *latency_headings):
        latency_table.add_column(heading)
    for provider, result in report["provider_results"].items():
        # Only completed valid decisions inform the headline latency comparison.
        latency = result["latency_ms"]["successful"]

        cost = result["estimated_cost_usd"]
        cost_cell = "—" if cost is None else f"${cost:.8f}"
        if cost is not None and not result["cost_complete"]:
            cost_cell += "*"
        identity = f"{provider} / {result['model']}"
        latency_cells = [
            "—" if latency[k] is None else f"{latency[k]:.2f}" for k in ("min", "avg", "max", "p90")
        ]
        row = [
            identity,
            str(result["total_test_runs"]),
            f"{result['success_rate']:.2f}",
            f"{result['request_success_rate']:.2f}",
            token_cell(result, "input_tokens"),
            token_cell(result, "output_tokens"),
            cost_cell,
        ]
        if not separate_latency:
            row.extend(latency_cells)
        table.add_row(*row)
        latency_table.add_row(identity, *latency_cells)
    console.print(table)
    if separate_latency:
        console.print(latency_table)
    console.print(
        "Correct % includes failed requests. Latencies show valid responses; "
        "JSON also includes all attempts."
    )
    console.print(
        "Tokens come from API usage. Cost estimates use decision API rates. "
        "* = partial total; — = unavailable. Demo usage is synthetic."
    )
    for provider, result in report["provider_results"].items():
        if result["failed_requests"]:
            console.print(
                f"{provider}: {result['failed_requests']} failed requests; see JSON error codes."
            )


async def execute(
    args: argparse.Namespace, prompt: Prompt, providers: list[str], models: dict
) -> dict:
    # Validate every selected credential before making the first paid request.
    configs = [provider_config(p, model=models.get(p), demo=args.demo) for p in providers]
    prices = pricing_overrides(providers)
    async with AsyncExitStack() as stack:
        adapters = []
        for config in configs:
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    timeout=args.timeout,
                    follow_redirects=False,
                    transport=demo_transport(config.name) if args.demo else None,
                    limits=httpx.Limits(max_connections=args.concurrency),
                )
            )
            adapters.append(ADAPTERS[config.name](config, client))
        total = len(prompt.dataset) * args.repetitions * len(providers)
        console.print(
            f"Running {total} requests: {len(prompt.dataset)} rows × "
            f"{args.repetitions} repetitions × {len(providers)} providers."
        )
        with Progress(console=console, disable=not sys.stdout.isatty()) as progress:
            task = progress.add_task("Decisions", total=total)
            report = await run_benchmark(
                prompt,
                adapters,
                repetitions=args.repetitions,
                concurrency=args.concurrency,
                timeout_seconds=args.timeout,
                on_progress=lambda done, count: progress.update(task, completed=done),
                pricing=prices,
            )
    report["mode"] = "demo" if args.demo else "live"
    report["configuration"]["latency_unit"] = "milliseconds"
    report["configuration"]["percentile_method"] = "nearest_rank"
    report["configuration"]["retries"] = 0
    return report


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        load_dotenv(args.env_file, override=False, interpolate=False)
        prompts = load_prompts(args.prompt_dir)
        if not prompts:
            raise PromptError("No YAML files found in the prompt directory.")
        if args.list_prompts:
            for prompt in prompts:
                console.print(f"{prompt.name} ({len(prompt.dataset)} rows)", markup=False)
            return
        if args.demo and not args.dry_run:
            console.print(
                "DEMO: synthetic responses and timings; no provider calls or performance claims."
            )
        prompt, providers, models = choose_options(args, prompts)
        if args.dry_run:
            payloads = {}
            for provider in providers:
                config = provider_config(provider, models.get(provider), demo=True)
                # No network or credentials needed to preview wire-format JSON.
                payloads[provider] = ADAPTERS[provider](config, None).build_payload(
                    prompt, prompt.dataset[0].query
                )
            print(json.dumps(payloads, ensure_ascii=False, indent=2))
            return
        report = asyncio.run(execute(args, prompt, providers, models))
        path = write_report(report, args.output_dir)
        show_report(report)
        console.print(f"Saved: {path}", markup=False)
        if report["failed_requests"]:
            raise SystemExit(1)
    except KeyboardInterrupt:
        console.print("Cancelled.")
        raise SystemExit(130) from None
    except (PromptError, ConfigurationError) as exc:
        console.print(f"Error: {exc}", markup=False)
        raise SystemExit(2) from None
    except OSError:
        console.print(
            "Unable to read configuration or save the report; check the file paths and permissions."
        )
        raise SystemExit(2) from None
