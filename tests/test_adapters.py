import asyncio
import json
from pathlib import Path

import httpx
import pytest

from decision_tests.adapters import ADAPTERS
from decision_tests.adapters.base import ProviderError
from decision_tests.benchmark import run_benchmark, write_report
from decision_tests.config import ConfigurationError, ProviderConfig, provider_config
from decision_tests.models import Choice, DatasetItem, Prompt


@pytest.fixture
def prompt():
    return Prompt(
        name="Ticket routing",
        type="choice",
        prompt="Choose the responsible team.",
        choices=(Choice("billing", "Payments"), Choice("technical", "Bugs")),
        dataset=(DatasetItem("I was charged twice.", "billing"),),
        source=Path("arbitrary.yaml"),
    )


@pytest.mark.parametrize("provider", ["jev", "openai", "azure"])
def test_verified_wire_contract_and_authentication(provider, prompt):
    """Expectations follow vendor documentation, not adapter payload builders."""
    captured = []
    model = {"jev": "jev-1.13.0", "openai": "gpt-6-luna", "azure": "my-deployment"}[provider]
    endpoint = {
        "jev": "https://api.typesafe.ai/v1/systemone",
        "openai": "https://api.openai.com/v1/decisions",
        "azure": "https://example.services.ai.azure.com/providers/microsoft/v1/systemone",
    }[provider]
    answer = {"type": "choice", "choice": "billing"}
    if provider == "openai":
        answer.update(name="decision", probabilities=[{"value": "billing", "probability": 1.0}])
        response = {"model": model, "answers": [answer]}
        expected = {
            "model": model,
            "input": "I was charged twice.",
            "questions": [
                {
                    "type": "choice",
                    "name": "decision",
                    "instructions": "Choose the responsible team.",
                    "choices": [
                        {"value": "billing", "description": "Payments"},
                        {"value": "technical", "description": "Bugs"},
                    ],
                }
            ],
        }
    else:
        response = {"model": model, "answers": {"decision": answer}}
        expected = {
            "model": model,
            "state": "I was charged twice.",
            "questions": {
                "decision": {
                    "type": "choice",
                    "instructions": "Choose the responsible team.",
                    "criteria": {"billing": "Payments", "technical": "Bugs"},
                }
            },
        }

    def handle(request):
        captured.append(request)
        assert request.method == "POST"
        assert str(request.url) == endpoint
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == expected
        assert "expected" not in json.loads(request.content)
        if provider == "azure":
            assert request.headers["api-key"] == "test-secret"
            assert "authorization" not in request.headers
        else:
            assert request.headers["authorization"] == "Bearer test-secret"
            assert "api-key" not in request.headers
        return httpx.Response(200, json=response)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = ADAPTERS[provider](
                ProviderConfig(provider, endpoint, model, "test-secret"), client
            )
            result = await adapter.decide(prompt, prompt.dataset[0].query)
            assert result.choice == "billing"
            assert result.response == response
            assert result.model == model

    asyncio.run(run())
    assert len(captured) == 1


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(401, json={"message": "test-secret"}), "http_401"),
        (httpx.Response(429, json={"message": "test-secret"}), "http_429"),
        (httpx.Response(307, headers={"location": "https://elsewhere.invalid"}), "http_307"),
        (httpx.Response(200, text="not JSON"), "invalid_json"),
        (httpx.Response(200, text='{"answers": {}, "bad": NaN}'), "invalid_json"),
        (httpx.Response(200, json=[]), "invalid_response"),
        (httpx.Response(200, json={"answers": []}), "invalid_response"),
        (
            httpx.Response(
                200, json={"answers": [{"name": "wrong", "type": "choice", "choice": "billing"}]}
            ),
            "invalid_response",
        ),
        (
            httpx.Response(200, json={"answers": [{"name": "decision", "type": "refusal"}]}),
            "refusal",
        ),
        (
            httpx.Response(
                200, json={"answers": [{"name": "decision", "type": "choice", "choice": "unknown"}]}
            ),
            "invalid_choice",
        ),
        (
            httpx.Response(
                200, json={"answers": [{"name": "decision", "type": "choice", "choice": True}]}
            ),
            "invalid_choice",
        ),
    ],
)
def test_failures_are_counted_without_retries_or_secret_leaks(response, code, prompt):
    calls = []

    def handle(request):
        calls.append(request)
        return response

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), follow_redirects=False
        ) as client:
            adapter = ADAPTERS["openai"](
                ProviderConfig(
                    "openai", "https://example.invalid/v1/decisions", "gpt-6-luna", "test-secret"
                ),
                client,
            )
            report = await run_benchmark(prompt, [adapter])
            assert report["failed_requests"] == 1
            assert report["per_request"][0]["error"] == code
            assert "test-secret" not in json.dumps(report)

    asyncio.run(run())
    assert len(calls) == 1


def test_success_json_redacts_credentials_recursively(prompt):
    async def run():
        response = {
            "answers": {"decision": {"type": "choice", "choice": "billing"}},
            "model": "contains-test-secret",
            "test-secret": ["Bearer test-secret"],
        }
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response))
        ) as client:
            config = ProviderConfig("jev", "https://example.invalid", "jev-1.13.0", "test-secret")
            assert "test-secret" not in repr(config)
            report = await run_benchmark(prompt, [ADAPTERS["jev"](config, client)])
            assert report["successful_requests"] == 1
            assert "test-secret" not in json.dumps(report)

    asyncio.run(run())


def test_config_validates_selected_provider_and_supports_official_typesafe_variable(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")
    monkeypatch.setenv("JEV_ENDPOINT", "")
    config = provider_config("jev")
    assert config.api_key == "test-secret"
    assert config.endpoint == "https://api.typesafe.ai/v1/systemone"
    monkeypatch.setenv("AZURE_API_KEY", "")
    monkeypatch.setenv("AZURE_ENDPOINT", "")
    monkeypatch.setenv("AZURE_MODEL", "")
    with pytest.raises(ConfigurationError, match="AZURE_ENDPOINT, AZURE_API_KEY, AZURE_MODEL"):
        provider_config("azure")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.invalid",
        "https://user:test-secret@example.invalid",
        "https://example.invalid?api-key=test-secret",
        "https://example.invalid#secret",
    ],
)
def test_config_rejects_credentials_in_endpoints_without_echoing_them(endpoint, monkeypatch):
    monkeypatch.setenv("JEV_ENDPOINT", endpoint)
    monkeypatch.setenv("JEV_API_KEY", "test-secret")
    with pytest.raises(ConfigurationError) as caught:
        provider_config("jev")
    assert "test-secret" not in str(caught.value)


def test_untrusted_provider_error_text_is_not_a_report_code():
    assert str(ProviderError("Authorization: test-secret")) == "request_error"


@pytest.mark.parametrize("number", ["1e999", "-1e999", "NaN", "Infinity", "-Infinity"])
def test_nonfinite_vendor_data_becomes_failed_request_and_report_is_saved(number, prompt, tmp_path):
    async def run():
        content = (
            '{"answers":[{"name":"decision","type":"choice","choice":"billing"}],'
            '"usage":{"input_tokens":' + number + "}}"
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=content))
        ) as client:
            config = ProviderConfig(
                "openai", "https://example.invalid", "gpt-6-luna", "test-secret"
            )
            report = await run_benchmark(prompt, [ADAPTERS["openai"](config, client)])
        assert report["failed_requests"] == 1
        assert report["per_request"][0]["error"] == "invalid_json"
        path = write_report(report, tmp_path)
        assert json.loads(path.read_text())["failed_requests"] == 1

    asyncio.run(run())
