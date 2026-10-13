from __future__ import annotations

from decimal import Decimal

import pytest

from decision_tests.config import ConfigurationError
from decision_tests.pricing import (
    TokenPricing,
    default_pricing,
    pricing_overrides,
    request_cost,
    token_usage,
    usage_cost_summary,
)


@pytest.mark.parametrize(
    ("provider", "model", "input_rate", "output_rate", "price_model"),
    [
        ("jev", "jev-1.13.0", "0.042", "0", "jev-1.13.0"),
        ("jev", "jev-latest", "0.042", "0", "jev-1.13.0"),
        ("jev", "jev-preview", "0.042", "0", "jev-1.13.0"),
        ("openai", "gpt-6-luna", "0.10", "0", "gpt-6-luna"),
        ("azure", "user-deployment", "0.042", "0", "Microsoft-Decision-1"),
    ],
)
def test_default_model_pricing(provider, model, input_rate, output_rate, price_model):
    price = default_pricing(provider, model)

    assert price is not None
    assert price.input_usd_per_million == Decimal(input_rate)
    assert price.output_usd_per_million == Decimal(output_rate)
    assert price.model == price_model
    assert price.checked_at is not None
    assert price.source.startswith("https://")


@pytest.mark.parametrize(
    ("provider", "model"),
    [("jev", "other-model"), ("openai", "gpt-6-luna-latest")],
)
def test_unknown_models_have_no_default_price(provider, model):
    assert default_pricing(provider, model) is None


def test_explicit_rates_override_only_selected_providers(monkeypatch):
    monkeypatch.setenv("JEV_INPUT_USD_PER_MILLION", "0.25")
    monkeypatch.setenv("JEV_OUTPUT_USD_PER_MILLION", "0.75")
    monkeypatch.setenv("OPENAI_INPUT_USD_PER_MILLION", "invalid-but-unselected")

    prices = pricing_overrides(["jev"])

    assert list(prices) == ["jev"]
    assert prices["jev"].input_usd_per_million == Decimal("0.25")
    assert prices["jev"].output_usd_per_million == Decimal("0.75")
    assert prices["jev"].source == "environment override"
    assert prices["jev"].model == "selected model/deployment"
    assert prices["jev"].checked_at is None


@pytest.mark.parametrize(
    "environment",
    [
        {"JEV_INPUT_USD_PER_MILLION": "0.1"},
        {"JEV_OUTPUT_USD_PER_MILLION": "0.1"},
        {"JEV_INPUT_USD_PER_MILLION": "bad", "JEV_OUTPUT_USD_PER_MILLION": "0"},
        {"JEV_INPUT_USD_PER_MILLION": "NaN", "JEV_OUTPUT_USD_PER_MILLION": "0"},
        {"JEV_INPUT_USD_PER_MILLION": "Infinity", "JEV_OUTPUT_USD_PER_MILLION": "0"},
        {"JEV_INPUT_USD_PER_MILLION": "-Infinity", "JEV_OUTPUT_USD_PER_MILLION": "0"},
        {"JEV_INPUT_USD_PER_MILLION": "1e309", "JEV_OUTPUT_USD_PER_MILLION": "0"},
        {"JEV_INPUT_USD_PER_MILLION": "1e1000000", "JEV_OUTPUT_USD_PER_MILLION": "0"},
        {"JEV_INPUT_USD_PER_MILLION": "1e-1000000", "JEV_OUTPUT_USD_PER_MILLION": "0"},
        {"JEV_INPUT_USD_PER_MILLION": "-0.01", "JEV_OUTPUT_USD_PER_MILLION": "0"},
        {"JEV_INPUT_USD_PER_MILLION": "0", "JEV_OUTPUT_USD_PER_MILLION": "-1"},
    ],
)
def test_invalid_or_incomplete_overrides_are_rejected(monkeypatch, environment):
    monkeypatch.delenv("JEV_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("JEV_OUTPUT_USD_PER_MILLION", raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ConfigurationError, match="non-negative finite USD rate"):
        pricing_overrides(["jev"])


def test_no_override_returns_empty_mapping(monkeypatch):
    monkeypatch.delenv("JEV_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("JEV_OUTPUT_USD_PER_MILLION", raising=False)

    assert pricing_overrides(["jev"]) == {}


@pytest.mark.parametrize(
    "response",
    [None, {}, {"usage": None}, {"usage": []}, {"usage": "invalid"}],
)
def test_missing_or_malformed_usage_is_unavailable(response):
    assert token_usage(response) == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


@pytest.mark.parametrize("value", [True, -1, 1.5, "4"])
def test_usage_counts_must_be_nonnegative_exact_integers(value):
    assert token_usage({"usage": {"input_tokens": value, "output_tokens": 2}}) == {
        "input_tokens": None,
        "output_tokens": 2,
        "total_tokens": None,
    }


def test_zero_usage_is_distinct_from_unavailable_and_total_is_recomputed():
    assert token_usage({"usage": {"input_tokens": 0, "output_tokens": 0}}) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    assert token_usage({"usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 100}}) == {
        "input_tokens": 4,
        "output_tokens": 3,
        "total_tokens": 7,
    }


def test_vendor_total_is_kept_when_component_counts_are_partial():
    assert token_usage({"usage": {"input_tokens": 4, "total_tokens": 6}}) == {
        "input_tokens": 4,
        "output_tokens": None,
        "total_tokens": 6,
    }


def test_input_only_cost_is_available_when_output_rate_is_zero():
    price = TokenPricing(Decimal("0.04"), Decimal(0), "test", "test-model")

    assert request_cost(
        {"input_tokens": 250_000, "output_tokens": None, "total_tokens": None}, price
    ) == pytest.approx(0.01)


def test_input_and_output_rates_are_applied_to_their_own_token_counts():
    price = TokenPricing(Decimal("0.4"), Decimal("0.8"), "test", "test-model")

    assert request_cost(
        {"input_tokens": 250_000, "output_tokens": 500_000, "total_tokens": 750_000},
        price,
    ) == pytest.approx(0.5)


def test_missing_billed_token_count_or_price_makes_cost_unavailable():
    price = TokenPricing(Decimal("0.4"), Decimal("0.8"), "test", "test-model")
    usage = {"input_tokens": 10, "output_tokens": None, "total_tokens": None}

    assert request_cost(usage, price) is None
    assert request_cost(usage, None) is None


def test_unrepresentable_cost_is_unavailable_instead_of_breaking_json_report():
    price = TokenPricing(Decimal("1e308"), Decimal(0), "test", "test-model")
    usage = {"input_tokens": 10_000_000, "output_tokens": 0, "total_tokens": 10_000_000}
    assert request_cost(usage, price) is None
    summary = usage_cost_summary([{"usage": usage, "estimated_cost_usd": 1e308}] * 2)
    assert summary["estimated_cost_usd"] is None
    assert summary["cost_complete"] is False
