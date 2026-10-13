"""Loopback-only browser configuration, prompt editing, and background benchmarks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .adapters import ADAPTERS
from .adapters.demo import demo_transport
from .benchmark import run_benchmark, write_report
from .config import (
    DEFAULT_ENDPOINTS,
    PROVIDERS,
    ConfigurationError,
    ProviderConfig,
    model_default,
    provider_config,
)
from .models import Prompt
from .pricing import pricing_overrides
from .prompts import PromptError, load_prompts, validate_prompt
from .web_storage import (
    SAFE_ID,
    StorageError,
    atomic_json,
    atomic_text,
    checked_path,
    child_path,
    read_environment,
    update_environment,
)

DISPLAY_NAMES = {
    "jev": "JEV / TypeSafe",
    "openai": "OpenAI Decisions",
    "azure": "Microsoft Decision",
}
ProviderId = Literal["jev", "openai", "azure"]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderInput(InputModel):
    id: ProviderId
    name: str = Field(min_length=1, max_length=200)
    model: str = Field(max_length=500)
    endpoint: str = Field(max_length=2000)
    api_key: str | None = Field(default=None, max_length=16000)
    clear_key: bool = False
    input_usd_per_million: str | None = None
    output_usd_per_million: str | None = None

    @field_validator("input_usd_per_million", "output_usd_per_million", mode="before")
    @classmethod
    def decimal_text(cls, value: Any) -> Any:
        if type(value) in {int, float}:
            return str(value)
        return value


class ConfigInput(InputModel):
    providers: list[ProviderInput] = Field(min_length=1, max_length=3)


class ChoiceInput(InputModel):
    value: str
    description: str


class DatasetInput(InputModel):
    query: str
    expected: str


class PromptInput(InputModel):
    name: str
    type: Literal["choice"] = "choice"
    prompt: str
    choices: list[ChoiceInput]
    dataset: list[DatasetInput]


class RunInput(InputModel):
    prompt_id: str
    providers: list[ProviderId] = Field(min_length=1, max_length=3)
    repetitions: Annotated[StrictInt, Field(ge=1, le=10000)] = 1
    concurrency: Annotated[StrictInt, Field(ge=1, le=1000)] = 1
    timeout: float = Field(default=30, gt=0, le=3600)
    demo: bool = False

    @field_validator("timeout", mode="before")
    @classmethod
    def finite_timeout(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("timeout must be a positive finite number")
        if not math.isfinite(value):
            raise ValueError("timeout must be a positive finite number")
        return value


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def prompt_document(prompt: Prompt) -> dict[str, Any]:
    return {
        "id": prompt.source.name,
        "name": prompt.name,
        "type": prompt.type,
        "prompt": prompt.prompt,
        "choices": [{"value": c.value, "description": c.description} for c in prompt.choices],
        "dataset": [{"query": d.query, "expected": d.expected} for d in prompt.dataset],
    }


class WebState:
    def __init__(self, env_file: Path, prompt_dir: Path, output_dir: Path, demo: bool):
        self.env_file = checked_path(env_file)
        self.prompt_dir = checked_path(prompt_dir)
        self.output_dir = checked_path(output_dir)
        self.jobs_dir = checked_path(self.output_dir / "jobs")
        self.demo = demo
        self.jobs: dict[str, dict[str, Any]] = {}
        self.task: asyncio.Task[None] | None = None
        self.lock = asyncio.Lock()
        self.initialized = False

    def environment(self) -> dict[str, str]:
        return read_environment(self.env_file)

    def config_view(self) -> dict[str, Any]:
        environment = self.environment()
        providers = []
        for provider in PROVIDERS:
            prefix = provider.upper()
            key = environment.get(f"{prefix}_API_KEY", "")
            if provider == "jev" and not key:
                key = environment.get("TYPESAFE_API_KEY", "")
            providers.append(
                {
                    "id": provider,
                    "name": environment.get(f"{prefix}_NAME", "").strip()
                    or DISPLAY_NAMES[provider],
                    "model": model_default(provider, demo=self.demo, environment=environment),
                    "endpoint": environment.get(f"{prefix}_ENDPOINT", "").strip()
                    or DEFAULT_ENDPOINTS[provider],
                    "has_key": bool(key.strip()),
                    "input_usd_per_million": environment.get(f"{prefix}_INPUT_USD_PER_MILLION")
                    or None,
                    "output_usd_per_million": environment.get(f"{prefix}_OUTPUT_USD_PER_MILLION")
                    or None,
                }
            )
        return {"providers": providers}

    def prompts(self) -> list[Prompt]:
        checked_path(self.prompt_dir)
        if not self.prompt_dir.exists():
            self.prompt_dir.mkdir(parents=True)
        for path in self.prompt_dir.iterdir():
            if path.suffix.lower() in {".yaml", ".yml"}:
                checked_path(path)
        return load_prompts(self.prompt_dir)

    def persist(self, job: dict[str, Any]) -> None:
        atomic_json(child_path(self.jobs_dir, f"{job['id']}.json"), job)

    def initialize(self) -> None:
        if self.initialized:
            return
        checked_path(self.output_dir).mkdir(parents=True, exist_ok=True)
        checked_path(self.jobs_dir).mkdir(parents=True, exist_ok=True)
        for path in sorted(self.jobs_dir.glob("*.json")):
            checked_path(path)
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(job, dict):
                    continue
                job_id = job.get("id")
                if not isinstance(job_id, str) or not SAFE_ID.fullmatch(job_id):
                    continue
                if path.name != f"{job_id}.json":
                    continue
                if job.get("status") in {"pending", "running"}:
                    job.update(status="interrupted", finished_at=utc_now(), error="Server stopped.")
                    self.persist(job)
                if job.get("status") not in {"completed", "failed", "interrupted"}:
                    continue
                if not isinstance(job.get("started_at"), str):
                    continue
                report_file = job.get("report_file")
                if report_file is not None:
                    child_path(self.output_dir, report_file)
                self.jobs[job_id] = job
            except (OSError, ValueError, TypeError):
                continue
        self.import_reports()
        self.initialized = True

    def import_reports(self) -> None:
        known_files = {job.get("report_file") for job in self.jobs.values()}
        for path in sorted(self.output_dir.glob("*.json")):
            checked_path(path)
            if path.name in known_files:
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(report, dict) or not isinstance(
                    report.get("provider_results"), dict
                ):
                    continue
                if not isinstance(report.get("prompt"), dict):
                    continue
                run_id = report.get("run_id")
                if not isinstance(run_id, str) or not SAFE_ID.fullmatch(run_id):
                    continue
                if run_id in self.jobs:
                    run_id = "import-" + hashlib.sha256(path.name.encode()).hexdigest()[:24]
                config = report.get("configuration", {})
                providers = report.get("providers", {})
                if not isinstance(config, dict) or not isinstance(providers, dict):
                    continue
                started = report.get("started_at")
                if not isinstance(started, str):
                    started = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
                total = report.get("total_test_runs", len(report.get("per_request", [])))
                job = {
                    "id": run_id,
                    "status": "completed",
                    "started_at": started,
                    "finished_at": report.get("finished_at", started),
                    "completed": total,
                    "total": total,
                    "prompt_name": report["prompt"].get("name", "Imported benchmark"),
                    "providers": [
                        {"id": p, "name": DISPLAY_NAMES.get(p, p), "model": m}
                        for p, m in providers.items()
                    ],
                    "settings": {
                        "repetitions": config.get("repetitions", 1),
                        "concurrency": config.get("concurrency", 1),
                        "timeout": config.get("timeout_seconds", 30),
                        "demo": report.get("mode") == "demo",
                    },
                    "report_file": path.name,
                    "error": None,
                }
                self.persist(job)
                self.jobs[run_id] = job
                known_files.add(path.name)
            except (OSError, ValueError, TypeError):
                continue

    def runs_view(self) -> dict[str, Any]:
        self.initialize()
        runs = sorted(
            self.jobs.values(), key=lambda job: (job["started_at"], job["id"]), reverse=True
        )
        return {"runs": runs, "active": next((j for j in runs if j["status"] == "running"), None)}

    def job(self, run_id: str) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(run_id):
            raise HTTPException(404, "Run not found.")
        self.initialize()
        if run_id not in self.jobs:
            raise HTTPException(404, "Run not found.")
        return self.jobs[run_id]

    def report(self, job: dict[str, Any]) -> dict[str, Any] | None:
        if not job.get("report_file"):
            return None
        path = child_path(self.output_dir, job["report_file"])
        if not path.is_file():
            raise HTTPException(404, "Report not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    async def execute(
        self,
        job: dict[str, Any],
        prompt: Prompt,
        configs: list[ProviderConfig],
        prices: dict[str, Any],
    ) -> None:
        try:
            settings = job["settings"]
            async with AsyncExitStack() as stack:
                adapters = []
                for config in configs:
                    client = await stack.enter_async_context(
                        httpx.AsyncClient(
                            timeout=settings["timeout"],
                            follow_redirects=False,
                            transport=demo_transport(config.name) if settings["demo"] else None,
                            limits=httpx.Limits(max_connections=settings["concurrency"]),
                        )
                    )
                    adapters.append(ADAPTERS[config.name](config, client))

                def progress(completed: int, total: int) -> None:
                    job.update(completed=completed, total=total)

                report = await run_benchmark(
                    prompt,
                    adapters,
                    repetitions=settings["repetitions"],
                    concurrency=settings["concurrency"],
                    timeout_seconds=settings["timeout"],
                    on_progress=progress,
                    pricing=prices,
                )
            report["run_id"] = job["id"]
            report["started_at"] = job["started_at"]
            report["mode"] = "demo" if settings["demo"] else "live"
            report["provider_display_names"] = {p["id"]: p["name"] for p in job["providers"]}
            report["configuration"].update(
                latency_unit="milliseconds", percentile_method="nearest_rank", retries=0
            )
            checked_path(self.output_dir)
            path = write_report(report, self.output_dir)
            job.update(status="completed", completed=job["total"], report_file=path.name)
        except asyncio.CancelledError:
            job.update(status="interrupted", error="Server stopped.")
            raise
        except Exception:
            job.update(
                status="failed", error="Benchmark could not finish. Check configuration and files."
            )
        finally:
            job["finished_at"] = utc_now()
            try:
                self.persist(job)
            except (OSError, ValueError):
                job.update(
                    status="failed",
                    error="Unable to save run history. Check directory permissions.",
                )


def create_app(
    env_file: Path = Path(".env"),
    prompt_dir: Path = Path("prompt"),
    output_dir: Path = Path("results"),
    demo: bool = False,
) -> FastAPI:
    state = WebState(env_file, prompt_dir, output_dir, demo)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.initialize()
        try:
            yield
        finally:
            if state.task is not None and not state.task.done():
                state.task.cancel()
                try:
                    await state.task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Decision Benchmarks", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.web = state

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        def protect(response):
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            if request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            return response

        try:
            authority = urlsplit("http://" + request.headers.get("host", ""))
            host = authority.hostname
            if authority.username or authority.password or authority.path or authority.query:
                host = None
            if authority.port is not None and not 1 <= authority.port <= 65535:
                host = None
        except ValueError:
            host = None
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return protect(JSONResponse({"detail": "Local host required."}, status_code=403))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin is not None and origin != expected:
                return protect(
                    JSONResponse({"detail": "Same-origin request required."}, status_code=403)
                )
            if request.headers.get("sec-fetch-site") == "cross-site":
                return protect(
                    JSONResponse({"detail": "Same-origin request required."}, status_code=403)
                )
        return protect(await call_next(request))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default includes the rejected input, which might be an API key.
        errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(StorageError)
    async def storage_error(request: Request, exc: StorageError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(OSError)
    async def file_error(request: Request, exc: OSError):
        return JSONResponse(
            {"detail": "Unable to access application files. Check paths and permissions."},
            status_code=500,
        )

    @app.exception_handler(PromptError)
    async def prompt_error(request: Request, exc: PromptError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ConfigurationError)
    async def config_error(request: Request, exc: ConfigurationError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/config")
    async def get_config():
        return state.config_view()

    @app.put("/api/config")
    async def put_config(body: ConfigInput):
        ids = [p.id for p in body.providers]
        if len(ids) != len(set(ids)):
            raise HTTPException(400, "Each provider may be configured once.")
        changes: dict[str, str] = {}
        for provider in body.providers:
            prefix = provider.id.upper()
            if provider.api_key is not None and any(c in provider.api_key for c in "\r\n"):
                raise HTTPException(400, f"{prefix}_API_KEY must be a single line.")
            if not provider.name.strip():
                raise HTTPException(400, "Provider display name must not be blank.")
            endpoint = provider.endpoint.strip()
            if endpoint:
                # Reuse the CLI's HTTPS checks without requiring configured credentials.
                provider_config(
                    provider.id,
                    environment={
                        f"{prefix}_ENDPOINT": endpoint,
                        f"{prefix}_API_KEY": "validation-key",
                        f"{prefix}_MODEL": provider.model.strip() or "validation-model",
                    },
                )
            changes.update(
                {
                    f"{prefix}_NAME": provider.name.strip(),
                    f"{prefix}_MODEL": provider.model.strip(),
                    f"{prefix}_ENDPOINT": endpoint,
                    f"{prefix}_INPUT_USD_PER_MILLION": provider.input_usd_per_million or "",
                    f"{prefix}_OUTPUT_USD_PER_MILLION": provider.output_usd_per_million or "",
                }
            )
            if provider.clear_key:
                changes[f"{prefix}_API_KEY"] = ""
                if provider.id == "jev":
                    changes["TYPESAFE_API_KEY"] = ""
            elif provider.api_key is not None and provider.api_key.strip():
                changes[f"{prefix}_API_KEY"] = provider.api_key
        pricing_overrides(ids, environment=changes)
        update_environment(state.env_file, changes)
        return state.config_view()

    @app.get("/api/prompts")
    async def get_prompts():
        return {"prompts": [prompt_document(p) for p in state.prompts()]}

    def save_prompt(body: PromptInput, prompt_id: str) -> dict[str, Any]:
        path = child_path(state.prompt_dir, prompt_id)
        prompt = validate_prompt(body.model_dump(), path)
        for existing in state.prompts():
            if existing.source.name != prompt_id and existing.name == prompt.name:
                raise HTTPException(400, "Another prompt already uses this name.")
        data = prompt_document(prompt)
        data.pop("id")
        atomic_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        return prompt_document(prompt)

    @app.post("/api/prompts", status_code=201)
    async def create_prompt(body: PromptInput):
        return save_prompt(body, f"prompt-{uuid4().hex}.yaml")

    @app.put("/api/prompts/{prompt_id}")
    async def update_prompt(prompt_id: str, body: PromptInput):
        path = child_path(state.prompt_dir, prompt_id)
        if path.suffix.lower() not in {".yaml", ".yml"} or not path.is_file():
            raise HTTPException(404, "Prompt not found.")
        return save_prompt(body, prompt_id)

    @app.get("/api/runs")
    async def get_runs():
        return state.runs_view()

    @app.post("/api/runs", status_code=202)
    async def start_run(body: RunInput):
        async with state.lock:
            state.initialize()
            if state.task is not None and not state.task.done():
                raise HTTPException(409, "A benchmark is already running.")
            if len(body.providers) != len(set(body.providers)):
                raise HTTPException(400, "Select each provider once.")
            path = child_path(state.prompt_dir, body.prompt_id)
            prompt = next((p for p in state.prompts() if p.source == path), None)
            if prompt is None:
                raise HTTPException(404, "Prompt not found.")
            total = len(prompt.dataset) * body.repetitions * len(body.providers)
            if total > 100000:
                raise HTTPException(400, "Reduce the benchmark to at most 100,000 requests.")
            environment = state.environment()
            run_demo = state.demo or body.demo
            configs = [
                provider_config(p, demo=run_demo, environment=environment) for p in body.providers
            ]
            prices = pricing_overrides(body.providers, environment=environment)
            started_at = utc_now()
            run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ-") + uuid4().hex
            job = {
                "id": run_id,
                "status": "running",
                "started_at": started_at,
                "finished_at": None,
                "completed": 0,
                "total": total,
                "prompt_name": prompt.name,
                "providers": [
                    {
                        "id": c.name,
                        "name": environment.get(f"{c.name.upper()}_NAME", "").strip()
                        or DISPLAY_NAMES[c.name],
                        "model": c.model,
                    }
                    for c in configs
                ],
                "settings": {
                    "repetitions": body.repetitions,
                    "concurrency": body.concurrency,
                    "timeout": body.timeout,
                    "demo": run_demo,
                },
                "report_file": None,
                "error": None,
            }
            state.persist(job)
            state.jobs[run_id] = job
            state.task = asyncio.create_task(state.execute(job, prompt, configs, prices))
            return dict(job)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        job = state.job(run_id)
        return {"job": job, "report": state.report(job)}

    @app.get("/api/runs/{run_id}/download")
    async def download_run(run_id: str):
        job = state.job(run_id)
        if not job.get("report_file"):
            raise HTTPException(404, "Report is not available yet.")
        path = child_path(state.output_dir, job["report_file"])
        if not path.is_file():
            raise HTTPException(404, "Report not found.")
        return Response(
            path.read_bytes(),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
        )

    @app.get("/api/bootstrap")
    async def bootstrap():
        return {
            **state.config_view(),
            "prompts": [prompt_document(p) for p in state.prompts()],
            **state.runs_view(),
            "demo": state.demo,
        }

    static_dir = Path(__file__).parent / "static"

    @app.get("/static/{asset_name}")
    async def static_asset(asset_name: str):
        media_types = {
            "app.js": "text/javascript",
            "style.css": "text/css",
            "decision-api-office.webp": "image/webp",
        }
        if asset_name not in media_types:
            raise HTTPException(404, "Asset not found.")
        path = child_path(static_dir, asset_name)
        if not path.is_file():
            raise HTTPException(404, "Asset not found.")
        return Response(path.read_bytes(), media_type=media_types[asset_name])

    @app.get("/")
    async def index():
        index_file = child_path(static_dir, "index.html")
        if not index_file.is_file():
            raise HTTPException(503, "Browser assets are not installed.")
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Open local Decision Benchmarks in your browser.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--prompt-dir", type=Path, default=Path("prompt"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--demo", action="store_true", help="Offline synthetic benchmark responses")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    import uvicorn

    print(f"Decision Benchmarks: http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(
        create_app(args.env_file, args.prompt_dir, args.output_dir, args.demo),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
