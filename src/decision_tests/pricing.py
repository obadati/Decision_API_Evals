"""Decision API list prices and validated, provider-reported token usage."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Any

from .config import ConfigurationError

PRICING_CHECKED_AT = "2026-10-12"
MICROSOFT_PRICING_SOURCE = (
    "https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/"
    "introducing-microsoft-decision-1-in-microsoft-foundry-for-decision-and-classific/4562742"
)


@dataclass(frozen=True)
class TokenPricing:
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    source: str
    model: str
    checked_at: str | None = PRICING_CHECKED_AT

    def snapshot(self) -> dict[str, Any]:
        data = asdict(self)
        # Preserve decimal rates exactly in the report.
        for key in ("input_usd_per_million", "output_usd_per_million"):
            data[key] = str(data[key])
        return data


def default_pricing(provider: str, model: str) -> TokenPricing | None:
    if provider == "jev" and model in {"jev-1.13.0", "jev-latest", "jev-preview"}:
        return TokenPricing(
            Decimal("0.042"), Decimal(0), "https://docs.typesafe.ai/models", "jev-1.13.0"
        )
    if provider == "openai" and model == "gpt-6-luna":
        return TokenPricing(
            Decimal("0.10"),
            Decimal(0),
            "https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability",
            "gpt-6-luna",
        )
    if provider == "azure":
        # This adapter exclusively targets Microsoft-Decision-1; model is a deployment name.
        return TokenPricing(
            Decimal("0.042"), Decimal(0), MICROSOFT_PRICING_SOURCE, "Microsoft-Decision-1"
        )
    return None


def pricing_overrides(
    providers: list[str], environment: Mapping[str, str] | None = None
) -> dict[str, TokenPricing]:
    """Explicit rates apply to the selected model/deployment for this run."""
    prices = {}
    environment = os.environ if environment is None else environment
    for provider in providers:
        prefix = provider.upper()
        keys = (f"{prefix}_INPUT_USD_PER_MILLION", f"{prefix}_OUTPUT_USD_PER_MILLION")
        values = [environment.get(key, "").strip() for key in keys]
        if not any(values):
            continue
        rates = []
        for key, value in zip(keys, values, strict=True):
            try:
                rate = Decimal(value)
            except InvalidOperation:
                raise ConfigurationError(f"Set {key} to a non-negative finite USD rate.") from None
            if (
                not rate.is_finite()
                or rate < 0
                or not math.isfinite(float(rate))
                or (rate != 0 and float(rate) == 0)
            ):
                raise ConfigurationError(f"Set {key} to a non-negative finite USD rate.")
            rates.append(rate)
        prices[provider] = TokenPricing(
            *rates,
            source="environment override",
            model="selected model/deployment",
            checked_at=None,
        )
    return prices


def token_usage(response: Any) -> dict[str, int | None]:
    raw = response.get("usage") if isinstance(response, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    def count(key: str) -> int | None:
        value = raw.get(key)
        return value if type(value) is int and value >= 0 else None

    input_tokens, output_tokens = count("input_tokens"), count("output_tokens")
    total = count("total_tokens")
    if input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total}


def request_cost(usage: dict[str, int | None], price: TokenPricing | None) -> float | None:
    if price is None or all(value is None for value in usage.values()):
        return None
    cost = Decimal(0)
    for key, rate in (
        ("input_tokens", price.input_usd_per_million),
        ("output_tokens", price.output_usd_per_million),
    ):
        if rate == 0:
            continue
        if usage[key] is None:
            return None
        try:
            cost += Decimal(usage[key]) * rate / Decimal(1_000_000)
        except DecimalException:
            return None
    # Keep small per-request charges; round only when displaying them.
    amount = float(cost)
    return amount if math.isfinite(amount) else None


def usage_cost_summary(requests: list[dict[str, Any]]) -> dict[str, Any]:
    usage, coverage = {}, {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        values = [r["usage"][key] for r in requests if r["usage"][key] is not None]
        usage[key] = sum(values) if values else None
        coverage[key] = len(values)
    costs = [r["estimated_cost_usd"] for r in requests if r["estimated_cost_usd"] is not None]
    total_cost = float(sum((Decimal(str(c)) for c in costs), Decimal(0))) if costs else None
    if total_cost is not None and not math.isfinite(total_cost):
        total_cost = None
    return {
        "usage": usage,
        "usage_requests": coverage,
        "estimated_cost_usd": total_cost,
        "costed_requests": len(costs),
        "cost_complete": total_cost is not None and len(costs) == len(requests),
    }
