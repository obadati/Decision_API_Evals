from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from dotenv import dotenv_values

from decision_tests.benchmark import run_benchmark, write_report
from decision_tests.models import Choice, DatasetItem, DecisionResult, Prompt
from decision_tests.web import create_app


def prompt_payload(
    name: str = "Web fixture prompt",
    query: str = "Does this item pass?",
    expected: str = "yes",
) -> dict[str, Any]:
    return {
        "name": name,
        "type": "choice",
        "prompt": "Choose whether the item passes.",
        "choices": [
            {"value": "yes", "description": "Passes review"},
            {"value": "no", "description": "Fails review"},
        ],
        "dataset": [{"query": query, "expected": expected}],
    }


def write_prompt(directory: Path, payload: dict[str, Any], filename: str = "fixture.yaml") -> None:
    import yaml

    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def build_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JEV_MODEL=jev-test\n"
        "JEV_ENDPOINT=https://jev.example.test/decision\n"
        "JEV_API_KEY=jev-existing-secret\n"
        "OPENAI_MODEL=openai-before\n"
        "OPENAI_ENDPOINT=https://openai.example.test/decision\n"
        "OPENAI_API_KEY=openai-existing-secret\n"
        "AZURE_MODEL=azure-test\n"
        "AZURE_ENDPOINT=https://azure.example.test/decision\n"
        "AZURE_API_KEY=azure-existing-secret\n",
        encoding="utf-8",
    )
    prompt_dir = tmp_path / "prompts"
    write_prompt(prompt_dir, prompt_payload())
    output_dir = tmp_path / "results"
    return env_file, prompt_dir, output_dir


def build_app(tmp_path: Path):
    env_file, prompt_dir, output_dir = build_files(tmp_path)
    app = create_app(
        env_file=env_file,
        prompt_dir=prompt_dir,
        output_dir=output_dir,
        demo=True,
    )
    return app, env_file, prompt_dir, output_dir


@asynccontextmanager
async def lifespan_client(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            yield client


@pytest.fixture
async def web_context(tmp_path: Path):
    app, env_file, prompt_dir, output_dir = build_app(tmp_path)
    async with lifespan_client(app) as client:
        yield client, env_file, prompt_dir, output_dir


async def test_dashboard_assets_include_decision_api_hero(web_context):
    client, *_ = web_context
    for path, media_type in (
        ("/", "text/html"),
        ("/static/app.js", "text/javascript"),
        ("/static/style.css", "text/css"),
        ("/static/decision-api-office.webp", "image/webp"),
    ):
        response = await client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(media_type)
        assert response.content
        if media_type == "image/webp":
            assert response.content[:4] == b"RIFF"
            assert response.content[8:12] == b"WEBP"

    # Adding an image must preserve the explicit static-asset allowlist.
    assert (await client.get("/static/web.py")).status_code == 404


async def provider_payloads(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get("/api/config")
    assert response.status_code == 200, response.text
    providers = response.json()["providers"]
    return [
        {
            key: provider.get(key)
            for key in (
                "id",
                "name",
                "model",
                "endpoint",
                "input_usd_per_million",
                "output_usd_per_million",
            )
        }
        for provider in providers
    ]


async def test_browser_frames_and_api_caching_are_disabled(web_context):
    client, *_ = web_context
    page = await client.get("/")
    assert page.status_code == 200
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    config = await client.get("/api/config")
    assert config.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("key", ["secret\nsecond-line", "secret\rsecond-line"])
async def test_multiline_credentials_fail_before_saving(web_context, key):
    client, env_file, *_ = web_context
    original = env_file.read_text()
    response = await put_provider(client, "openai", api_key=key)
    assert response.status_code == 400
    assert key not in response.text
    assert env_file.read_text() == original


async def test_active_run_is_recorded_as_interrupted_after_server_restart(tmp_path):
    app, env_file, prompt_dir, output_dir = build_app(tmp_path)
    async with lifespan_client(app) as client:
        response = await client.post(
            "/api/runs",
            json={
                "prompt_id": "fixture.yaml",
                "providers": ["openai"],
                "repetitions": 100,
                "concurrency": 1,
                "demo": True,
            },
        )
        assert response.status_code == 202
        run_id = response.json()["id"]
    restarted = create_app(env_file, prompt_dir, output_dir, demo=True)
    async with lifespan_client(restarted) as client:
        history = (await client.get("/api/runs")).json()
        assert history["active"] is None
        job = next(job for job in history["runs"] if job["id"] == run_id)
        assert job["status"] == "interrupted"
        assert job["finished_at"] is not None
        assert (output_dir / "jobs" / f"{run_id}.json").is_file()


async def put_provider(client: httpx.AsyncClient, provider_id: str, **changes: Any):
    providers = await provider_payloads(client)
    selected = next(provider for provider in providers if provider["id"] == provider_id)
    selected.update(changes)
    return await client.put("/api/config", json={"providers": providers})


class ReportAdapter:
    name = "openai"
    model = "cli-model"

    async def decide(self, prompt: Prompt, query: str) -> DecisionResult:
        return DecisionResult("yes", {"synthetic": True}, model=self.model)


async def create_cli_report(output_dir: Path, run_id: str, started_at: str) -> Path:
    prompt = Prompt(
        name="CLI imported prompt",
        type="choice",
        prompt="Choose yes or no.",
        choices=(Choice("yes", "Yes"), Choice("no", "No")),
        dataset=(DatasetItem("old CLI row", "yes"),),
        source=Path("prompt/cli.yaml"),
    )
    report = await run_benchmark(prompt, [ReportAdapter()])
    report["run_id"] = run_id
    report["started_at"] = started_at
    report["finished_at"] = started_at
    return write_report(report, output_dir)


async def test_config_saves_values_and_round_trips_dotenv_literals_without_echoing_key(
    web_context,
) -> None:
    client, env_file, _, _ = web_context
    secret = 'value with $DOLLAR and "double quotes" plus an apostrophe \' safely'

    response = await put_provider(
        client,
        "openai",
        name="Edited OpenAI label",
        model="edited-model",
        endpoint="https://edited.example.test/decision",
        api_key=secret,
        input_usd_per_million="1.25",
        output_usd_per_million="4.5",
    )

    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert "openai-existing-secret" not in response.text
    assert all("api_key" not in provider for provider in response.json()["providers"])
    saved = dotenv_values(env_file, interpolate=False)
    assert saved["OPENAI_API_KEY"] == secret
    assert saved["OPENAI_NAME"] == "Edited OpenAI label"
    assert saved["OPENAI_MODEL"] == "edited-model"
    assert saved["OPENAI_ENDPOINT"] == "https://edited.example.test/decision"
    row = next(row for row in response.json()["providers"] if row["id"] == "openai")
    assert row["model"] == "edited-model"
    assert row["name"] == "Edited OpenAI label"
    assert row["endpoint"] == "https://edited.example.test/decision"
    assert row["has_key"] is True
    assert row["input_usd_per_million"] == "1.25"
    assert row["output_usd_per_million"] == "4.5"
    bootstrap = await client.get("/api/bootstrap")
    assert bootstrap.status_code == 200, bootstrap.text
    assert secret not in bootstrap.text

    reloaded_app = create_app(
        env_file=env_file,
        prompt_dir=web_context[2],
        output_dir=web_context[3],
        demo=True,
    )
    async with lifespan_client(reloaded_app) as reloaded:
        persisted = await reloaded.get("/api/config")
    assert persisted.status_code == 200
    reloaded_row = next(row for row in persisted.json()["providers"] if row["id"] == "openai")
    assert reloaded_row["model"] == "edited-model"
    assert reloaded_row["name"] == "Edited OpenAI label"
    assert reloaded_row["endpoint"] == "https://edited.example.test/decision"
    assert reloaded_row["has_key"] is True
    assert secret not in persisted.text


@pytest.mark.parametrize("empty_value", [None, ""])
async def test_empty_api_key_retains_saved_secret(web_context, empty_value: str | None) -> None:
    client, env_file, _, _ = web_context
    secret = "retained-$-secret-'quoted'"
    first = await put_provider(client, "openai", api_key=secret)
    assert first.status_code == 200, first.text

    response = await put_provider(client, "openai", api_key=empty_value)

    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert dotenv_values(env_file, interpolate=False)["OPENAI_API_KEY"] == secret
    row = next(row for row in response.json()["providers"] if row["id"] == "openai")
    assert row["has_key"] is True


async def test_clear_key_is_explicit_and_clears_saved_secret(web_context) -> None:
    client, env_file, _, _ = web_context
    secret = "remove-this-secret"
    assert (await put_provider(client, "openai", api_key=secret)).status_code == 200

    response = await put_provider(client, "openai", api_key=None, clear_key=True)

    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert dotenv_values(env_file, interpolate=False)["OPENAI_API_KEY"] == ""
    row = next(row for row in response.json()["providers"] if row["id"] == "openai")
    assert row["has_key"] is False


async def test_prompt_post_and_put_return_editor_labels_and_reject_invalid_or_duplicate_data(
    web_context,
) -> None:
    client, _, _, _ = web_context
    payload = prompt_payload("API-created prompt", "First query")

    created = await client.post("/api/prompts", json=payload)

    assert created.status_code == 201, created.text
    prompt = created.json()
    assert prompt["name"] == "API-created prompt"
    assert prompt["type"] == "choice"
    assert prompt["choices"] == [
        {"value": "yes", "description": "Passes review"},
        {"value": "no", "description": "Fails review"},
    ]
    assert prompt["dataset"] == [{"query": "First query", "expected": "yes"}]
    assert prompt.get("id") or prompt.get("filename")
    prompt_id = prompt.get("id", prompt.get("filename"))

    updated_payload = prompt_payload("API-updated prompt", "Second query", "no")
    updated = await client.put(f"/api/prompts/{prompt_id}", json=updated_payload)
    assert updated.status_code == 200, updated.text
    assert updated.json().get("id", updated.json().get("filename")) == prompt_id
    assert updated.json()["name"] == "API-updated prompt"
    assert updated.json()["dataset"] == [{"query": "Second query", "expected": "no"}]

    duplicate = await client.post("/api/prompts", json=updated_payload)
    assert duplicate.status_code >= 400
    invalid = prompt_payload("Invalid expected label", expected="maybe")
    rejected = await client.post("/api/prompts", json=invalid)
    assert rejected.status_code >= 400


async def test_prompt_paths_cannot_escape_prompt_directory_or_follow_a_symlink(
    web_context, tmp_path
):
    client, _, prompt_dir, _ = web_context
    outside = tmp_path / "outside.yaml"
    outside_payload = prompt_payload("outside-secret-marker")
    write_prompt(tmp_path, outside_payload, "outside.yaml")
    outside_original = outside.read_text(encoding="utf-8")
    link = prompt_dir / "linked.yaml"
    link.symlink_to(outside)

    listing = await client.get("/api/prompts")

    assert listing.status_code == 400
    assert "outside-secret-marker" not in listing.text
    attempted = await client.put("/api/prompts/linked.yaml", json=prompt_payload("Tampered link"))
    assert attempted.status_code == 400
    assert outside.read_text(encoding="utf-8") == outside_original
    link.unlink()

    # Names containing path components must never cause files to be created outside the root.
    traversal = prompt_payload("../outside-created", "attempted escape")
    result = await client.post("/api/prompts", json=traversal)
    if result.status_code == 201:
        created_id = result.json().get("id", result.json().get("filename"))
        stored_path = (prompt_dir / created_id).resolve()
        assert stored_path.is_relative_to(prompt_dir.resolve())
    else:
        assert result.status_code >= 400
    traversal_update = await client.put(
        "/api/prompts/%2E%2E%2Foutside.yaml", json=prompt_payload("attempted overwrite")
    )
    assert traversal_update.status_code >= 400
    traversal_run = await client.post(
        "/api/runs",
        json={
            "prompt_id": "../outside.yaml",
            "providers": ["openai"],
            "repetitions": 1,
            "concurrency": 1,
            "timeout": 1,
            "demo": True,
        },
    )
    assert traversal_run.status_code >= 400
    assert not (tmp_path / "outside-created.yaml").exists()
    assert outside.read_text(encoding="utf-8") == outside_original


async def test_mutating_api_rejects_foreign_origins_without_changing_files(web_context) -> None:
    client, env_file, prompt_dir, _ = web_context
    original_env = env_file.read_text(encoding="utf-8")
    original_prompts = {
        path.name: path.read_text(encoding="utf-8") for path in prompt_dir.iterdir()
    }

    config = await client.put(
        "/api/config",
        json={"providers": await provider_payloads(client)},
        headers={"Origin": "https://attacker.example"},
    )
    prompt = await client.post(
        "/api/prompts",
        json=prompt_payload("Cross-origin write"),
        headers={"Origin": "https://attacker.example"},
    )
    prompt_listing = await client.get("/api/prompts")
    fixture_prompt = next(
        item for item in prompt_listing.json()["prompts"] if item["name"] == "Web fixture prompt"
    )
    run = await client.post(
        "/api/runs",
        json={
            "prompt_id": fixture_prompt.get("id", fixture_prompt.get("filename")),
            "providers": ["openai"],
            "repetitions": 1,
            "concurrency": 1,
            "timeout": 2,
            "demo": True,
        },
        headers={"Origin": "https://attacker.example"},
    )

    assert config.status_code == 403
    assert prompt.status_code == 403
    assert run.status_code == 403
    assert (await client.get("/api/runs")).json()["active"] is None
    assert env_file.read_text(encoding="utf-8") == original_env
    assert {path.name: path.read_text(encoding="utf-8") for path in prompt_dir.iterdir()} == (
        original_prompts
    )


async def test_demo_run_is_background_snapshotting_and_keeps_progress_during_edits(
    tmp_path: Path,
):
    app, env_file, prompt_dir, output_dir = build_app(tmp_path)
    rows = [
        {"query": f"original query {index}", "expected": "yes" if index % 2 == 0 else "no"}
        for index in range(320)
    ]
    original_prompt = prompt_payload("Long synthetic run", "original query 0")
    original_prompt["dataset"] = rows
    write_prompt(prompt_dir, original_prompt, "long-run.yaml")

    async with lifespan_client(app) as client:
        prompt_listing = await client.get("/api/prompts")
        created_prompt = next(
            prompt
            for prompt in prompt_listing.json()["prompts"]
            if prompt["name"] == "Long synthetic run"
        )
        prompt_id = created_prompt.get("id", created_prompt.get("filename"))
        started = await client.post(
            "/api/runs",
            json={
                "prompt_id": prompt_id,
                "providers": ["openai"],
                "repetitions": 1,
                "concurrency": 1,
                "timeout": 2,
                "demo": True,
            },
        )
        assert started.status_code == 202, started.text
        job = started.json()
        run_id = job["id"]
        assert job["status"] in {"queued", "running"}
        assert job["total"] == 320
        assert job["prompt_name"] == "Long synthetic run"
        assert job["providers"][0]["model"] == "openai-before"

        duplicate = await client.post(
            "/api/runs",
            json={
                "prompt_id": prompt_id,
                "providers": ["openai"],
                "repetitions": 1,
                "concurrency": 1,
                "timeout": 2,
                "demo": True,
            },
        )
        assert duplicate.status_code == 409

        during = await client.get("/api/runs")
        assert during.status_code == 200, during.text
        active = during.json()["active"]
        assert active is not None and active["id"] == run_id
        assert 0 <= active["completed"] < active["total"]

        # Browser navigation and edits must remain available while the demo runs.
        assert (await client.get("/api/bootstrap")).status_code == 200
        assert (await client.get("/api/prompts")).status_code == 200
        changed = await put_provider(client, "openai", model="openai-after")
        assert changed.status_code == 200, changed.text
        edited = await client.put(
            f"/api/prompts/{prompt_id}",
            json=prompt_payload("Edited during run", "replacement query"),
        )
        assert edited.status_code == 200, edited.text

        still_running = await client.get(f"/api/runs/{run_id}")
        assert still_running.status_code == 200, still_running.text
        state = still_running.json()["job"]
        assert state["id"] == run_id
        assert state["providers"][0]["model"] == "openai-before"
        assert state["status"] in {"queued", "running"}

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            finished = await client.get(f"/api/runs/{run_id}")
            assert finished.status_code == 200, finished.text
            result = finished.json()
            if result["job"]["status"] == "completed":
                break
            assert result["job"]["status"] not in {"failed", "cancelled"}, result
            await asyncio.sleep(0.05)
        else:
            pytest.fail("synthetic background run did not finish within 20 seconds")

        final_job = result["job"]
        report = result["report"]
        assert final_job["completed"] == final_job["total"] == 320
        assert final_job["report_file"]
        assert datetime.fromisoformat(final_job["started_at"].replace("Z", "+00:00"))
        assert datetime.fromisoformat(final_job["finished_at"].replace("Z", "+00:00"))
        assert report["prompt"]["name"] == "Long synthetic run"
        assert report["prompt"]["dataset"][0]["query"] == "original query 0"
        assert report["prompt"]["dataset"][-1]["query"] == "original query 319"
        assert report["provider_results"]["openai"]["model"] == "openai-before"
        saved_path = output_dir / final_job["report_file"]
        assert saved_path.is_file()
        saved_report = json.loads(saved_path.read_text(encoding="utf-8"))
        assert saved_report["prompt"]["name"] == "Long synthetic run"
        assert saved_report["providers"]["openai"] == "openai-before"

        history = await client.get("/api/runs")
        assert history.status_code == 200
        listed = history.json()["runs"]
        completed_job = next(item for item in listed if item["id"] == run_id)
        assert completed_job["status"] == "completed"

        download = await client.get(f"/api/runs/{run_id}/download")
        assert download.status_code == 200
        assert "attachment" in download.headers.get("content-disposition", "").lower()
        assert json.loads(download.content)["prompt"]["name"] == "Long synthetic run"


async def test_run_history_imports_cli_reports_and_orders_by_start_time(tmp_path: Path) -> None:
    env_file, prompt_dir, output_dir = build_files(tmp_path)
    await create_cli_report(output_dir, "older-cli-run", "2024-03-01T12:00:00Z")
    await create_cli_report(output_dir, "newer-cli-run", "2025-07-15T09:30:00Z")
    app = create_app(
        env_file=env_file,
        prompt_dir=prompt_dir,
        output_dir=output_dir,
        demo=True,
    )

    async with lifespan_client(app) as client:
        response = await client.get("/api/runs")

    assert response.status_code == 200, response.text
    runs = response.json()["runs"]
    imported = [run for run in runs if run["id"] in {"older-cli-run", "newer-cli-run"}]
    assert [run["id"] for run in imported] == ["newer-cli-run", "older-cli-run"]
    assert all(run["status"] == "completed" for run in imported)
    assert response.json()["active"] is None
