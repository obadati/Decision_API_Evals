"""Read credentials without placing them in reprs or reports."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    endpoint: str
    model: str
    api_key: str = field(repr=False)


PROVIDERS = ("jev", "openai", "azure")
DEFAULT_MODELS = {"jev": "jev-1.13.0", "openai": "gpt-6-luna", "azure": ""}
DEFAULT_ENDPOINTS = {
    "jev": "https://api.typesafe.ai/v1/systemone",
    "openai": "https://api.openai.com/v1/decisions",
    "azure": "",
}


def model_default(
    provider: str, demo: bool = False, environment: Mapping[str, str] | None = None
) -> str:
    environment = os.environ if environment is None else environment
    value = environment.get(f"{provider.upper()}_MODEL", "").strip() or DEFAULT_MODELS[provider]
    return value or ("demo-microsoft-decision-1" if demo else "")


def provider_config(
    provider: str,
    model: str | None = None,
    demo: bool = False,
    environment: Mapping[str, str] | None = None,
) -> ProviderConfig:
    if provider not in PROVIDERS:
        raise ConfigurationError("Unknown provider; use jev, openai, or azure.")
    prefix = provider.upper()
    environment = os.environ if environment is None else environment
    if demo:
        return ProviderConfig(
            provider,
            f"https://{provider}.demo.invalid/decision",
            model or model_default(provider, demo=True, environment=environment),
            "demo-key",
        )
    endpoint = environment.get(f"{prefix}_ENDPOINT", "").strip() or DEFAULT_ENDPOINTS[provider]
    key = environment.get(f"{prefix}_API_KEY", "").strip()
    # TypeSafe's official variable is also supported for direct JEV access.
    if provider == "jev" and not key:
        key = environment.get("TYPESAFE_API_KEY", "").strip()
    selected_model = (model or model_default(provider, environment=environment)).strip()
    missing = [
        variable
        for variable, value in (
            (f"{prefix}_ENDPOINT", endpoint),
            (f"{prefix}_API_KEY", key),
            (f"{prefix}_MODEL", selected_model),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError("Set " + ", ".join(missing) + " in .env.")
    try:
        parsed = urlsplit(endpoint)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.port != 0
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError(
            f"{prefix}_ENDPOINT must be a full HTTPS URL without credentials, query, or fragment."
        )
    if any(c in key for c in "\r\n"):
        raise ConfigurationError(f"{prefix}_API_KEY must be a single line.")
    return ProviderConfig(provider, endpoint, selected_model, key)
