# Decision API tests

A local browser workspace and terminal benchmark for **JEV (TypeSafe)**,
**OpenAI Decisions**, and **Azure Microsoft-Decision-1**. Choose a prompt and
providers, run a labeled dataset, and compare accuracy, request latency, token
usage, and estimated cost in US dollars.

## Start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).
Run these commands from the repository root:

```bash
uv sync --group dev
make run
```

Open **http://127.0.0.1:8765** in your browser. The launcher prints the URL.
`uv run python main.py` and `uv run decision-web` launch the same dashboard.
Use `make run PORT=9000` to change the port. Ctrl+C stops the server.

Try the whole browser workflow without credentials:

```bash
make demo
```

This starts a demo-only server; every run uses mock transports and makes no API
calls. In a normal dashboard, the **Offline demo** switch chooses demo or live
mode for each benchmark. Demo results are synthetic and are not provider measurements.

The dashboard includes:

* **Run benchmark:** select provider checkboxes and a prompt, then set repetitions,
  concurrency, and timeout. The request preview updates as you change the setup.
* **Configuration:** edit provider display names, model IDs/deployments, complete
  endpoints, keys, and optional input/output pricing. Save updates `.env` atomically.
  Leave a key blank to retain it; use **Clear saved key** to remove it. The browser
  sees only whether a stored key exists.
* **Prompt library:** create or edit YAML prompts using instruction fields,
  **Add choice**, and **Add example** buttons. Expected answers use choice dropdowns.
  Saving validates labels and duplicate names before updating the original file.
* **Run history:** newest runs first, with date, models, mode, and status. Open a
  run for provider comparisons, individual decisions, prompt/settings snapshots,
  and a downloadable JSON report. Existing terminal reports appear here too.

A persistent banner shows request progress across tabs and browser reloads.
Completion creates an in-app notification; **Notifications** optionally enables
browser notifications. One benchmark runs at a time. Runs execute in the server,
so switching pages or closing the browser does not cancel them. Stopping the
server interrupts active runs, and that status is preserved on restart.
Configuration and prompt edits apply to subsequent runs; active runs keep their
initial snapshots. Unsaved editor changes prompt before leaving.

Browser results use dated filenames such as
`results/decision-test-20261012T150000000000Z-<id>.json`; run metadata, including
failed/interrupted runs, lives in `results/jobs/`. These files are gitignored.
The server binds to loopback and checks request host/origin. No frontend build,
Node.js install, database, or external font service is required.

The dashboard reads `.env` on each configuration access/run, with saved file
values taking precedence over ambient variables. Values are literal, including
`${...}`. The terminal runner preserves ambient environment precedence.
Use `uv run decision-web --env-file PATH --prompt-dir PATH --output-dir PATH`
for other directories. Keys are excluded from reports and `.env` is gitignored.

The Makefile also provides `make install`, `make cli`, `make benchmark`,
`make test` (automated tests), and `make check` (tests, lint, formatting).
Pass terminal flags with `make benchmark ARGS='--demo --non-interactive ...'`.

## Terminal benchmark

This repository includes a local `.env` with empty credentials and an
`.env.example` template. Configure credentials through the dashboard or edit
the file before a live run. On a fresh clone, create it:

```bash
cp .env.example .env
make cli
```

Arrow keys move through the menus. **Space** toggles providers; **Enter** submits.
The script asks for the prompt, selected providers' model IDs (Azure deployment
name), repetitions per dataset row, concurrency, and timeout. Ctrl+C cancels.
Only selected providers require credentials. Process environment variables take
precedence over `.env`. Keys are excluded from reports and `.env` is gitignored.

Try the complete flow without credentials:

```bash
uv run python main.py --demo
```

Demo mode uses HTTPX mock transports through the real adapters. Its synthetic
responses always choose the first option: it demonstrates correct and incorrect
answers, but its accuracy and timings are **not provider measurements**.

You can also use `uv run decision-tests` or `uv run python -m decision_tests`.
With pip: `python -m venv .venv`, activate the environment, `pip install -e .`,
then `python main.py` for the dashboard or `decision-tests` for terminal menus.
Passing benchmark flags to `main.py` retains the terminal runner; use
`python main.py --web --port 9000` for dashboard flags.

## Provider configuration and verified contracts

Checked against official documentation on **2026-10-12**. These are three separate
decision services, each making one choice per dataset row. No chat completion
fallbacks or generated-text parsing are involved.

| Provider | Endpoint in `.env` | Key and model |
| --- | --- | --- |
| JEV | `JEV_ENDPOINT=https://api.typesafe.ai/v1/systemone` | `JEV_API_KEY`; `JEV_MODEL=jev-1.13.0` |
| OpenAI | `OPENAI_ENDPOINT=https://api.openai.com/v1/decisions` | `OPENAI_API_KEY`; `OPENAI_MODEL=gpt-6-luna` |
| Azure | `AZURE_ENDPOINT=https://YOUR-RESOURCE.services.ai.azure.com/providers/microsoft/v1/systemone` | `AZURE_API_KEY`; `AZURE_MODEL=YOUR-DEPLOYMENT-NAME` |

Supply **complete inference URLs**, including the paths shown above. JEV and
OpenAI have defaults; Azure requires your resource URL and deployed
Microsoft-Decision-1 model's deployment name. Copy the resource endpoint from
Foundry and append `/providers/microsoft/v1/systemone`; use the endpoint host your
deployment supplies. Deployment creation is outside this benchmark.

* **JEV:** `Authorization: Bearer`. `TYPESAFE_API_KEY` is also accepted when
  `JEV_API_KEY` is empty. Sends `state`, `model`, and a `questions` map; the choice
  rubric is a `criteria` map. Reads `answers.decision.choice`. See the
  [official TypeSafe API reference](https://docs.typesafe.ai/api) and
  [model IDs](https://docs.typesafe.ai/models). This adapter targets TypeSafe,
  rather than independent services with JEV in their names.
* **OpenAI:** `Authorization: Bearer`. Sends `input`, `model`, and a `questions`
  array, with `choices` containing `value` and `description`. Reads the named
  choice in the `answers` array; refusals are recorded as failures. See the
  [Decisions guide](https://developers.openai.com/api/docs/guides/decisions),
  [API reference](https://developers.openai.com/api/reference/resources/decisions/methods/create),
  and [authentication reference](https://developers.openai.com/api/reference/overview).
* **Azure:** `api-key` header. Sends the same `state`/`questions`/`criteria` shape
  as JEV, with the deployment name in `model`. Reads `answers.decision.choice`.
  This adapter targets **Microsoft-Decision-1**, Microsoft's own model; see
  [Microsoft's deployment and API guide](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision).
  The documented guide does not establish an exact probability field encoding;
  the report preserves whatever response JSON Azure returns. Entra-token auth is
  supported by the service but is not implemented in this key-based runner.

The runner supports text `choice` tasks with string labels. It applies a shared
2–255-choice benchmark limit; TypeSafe and OpenAI document this bound, while Azure's
guide does not specify a choice-count limit. Provider context, deployment access,
and quota restrictions still apply. Authentication failures, rate limits, invalid
responses, and timeouts are recorded per request. Redirects and retries are disabled.

## Prompt files

Place one prompt in each `.yaml` or `.yml` file in `prompt/`. **The YAML `name`
identifies the prompt; the filename is arbitrary.** Names and choice values must
be unique. The loader rejects duplicate YAML keys, unknown expected labels, and
malformed rows before making requests.

```yaml
name: Support ticket routing
type: choice
prompt: |
  Choose the team responsible for this customer's request.
choices:
  - value: billing
    description: Payments, invoices, and refunds
  - value: technical
    description: Software bugs and outages
dataset:
  - "I was charged twice. | billing"
  - "The app crashes when I open it. | technical"
```

Choices can also be plain strings, one per line. Dataset rows can be quoted list
entries, a YAML `dataset: |` block with one `query | expected` per line, or mappings:

```yaml
dataset:
  - query: |
      The report says "status | unknown".
      The app then crashes.
    expected: technical
```

Pipe rows split on the **last** `|`, allowing pipes in the query. Use mappings for
multiline queries or choice labels that contain `|`. Comparison is case-sensitive
and exact after trimming YAML field/row boundaries. Gold labels are used locally
for grading and are never included in provider requests. There is no 100-row cap:
add as many labeled examples as you need. The included routing and sentiment
prompts contain 16 and 8 illustrative rows, respectively.

## Automation and payload preview

Noninteractive runs require the exact prompt name and selected providers:

```bash
uv run python main.py --non-interactive \
  --prompt "Customer support routing" \
  --providers jev openai azure \
  --repetitions 5 --concurrency 3 --timeout 30
```

Defaults in noninteractive mode: one repetition, concurrency one, timeout 30
seconds. Use `--model jev=jev-latest`, `--model openai=gpt-6-luna`, or
`--model azure=your-deployment` to override `.env` for the selected provider.
One model per provider is supported in each run; use separate runs to compare
additional model versions or deployments.

```bash
uv run python main.py --list-prompts
uv run python main.py --dry-run --non-interactive \
  --prompt "Customer support routing" --providers jev openai azure
uv run python main.py --demo --non-interactive \
  --prompt "Customer support routing" --providers jev openai azure
```

`--dry-run` prints the first row's provider request bodies without authentication
or HTTP requests; configure Azure's deployment name for a representative preview.
`--prompt-dir`, `--env-file`, and `--output-dir` accept alternative paths, resolved
relative to your working directory. Live runs send the selected dataset to the
selected providers and can incur their API charges.

## Reports and timing

Each completed run writes a unique `results/decision-test-<run-id>.json` atomically.
Existing reports are never overwritten. A report starts with `provider_results`,
then includes timestamps, the complete prompt/dataset snapshot, settings, aggregate
metrics, and every request's expected/predicted labels, correctness, duration,
diagnostic code, original vendor JSON (with credential redaction), and reported model.

Each provider summary includes:

* `total_test_runs`: dataset size × repetitions.
* `success_rate`: **correct predictions / all test runs × 100**; failures count
  as incorrect.
* `request_success_rate`: valid decisions / all test runs × 100.
* `accuracy_on_successful_requests`: correct predictions / valid decisions × 100.
* `successful_requests`, `failed_requests`, and `correct_predictions`.
* `latency_ms.successful` and `latency_ms.attempted`, each with `min`, `avg`,
  `max`, and `p90`. An empty latency set yields JSON `null` values.
* `usage`: summed provider-reported `input_tokens`, `output_tokens`, and
  `total_tokens`; `usage_requests` gives the number of requests contributing to
  each sum. Missing or invalid counts are unavailable, never inferred from text.
* `estimated_cost_usd`: sum of known request costs, with `costed_requests` and
  `cost_complete` recording coverage. If no request can be priced, cost is `null`.

The console adds **In tokens**, **Out tokens**, and **Cost USD** columns for each
provider/model across all rows and repetitions. A `*` marks a partial total;
`—` means unavailable. JSON includes the same run totals and each request's
normalized usage, estimated cost, and applied pricing snapshot (rates, model,
source, and verification date). Costs retain precision in JSON and display eight
decimal places in the console. Demo usage and costs are synthetic zeros.

Decision API list prices checked on **2026-10-12**, in USD per million tokens:

| Provider / model | Input | Output | Source |
| --- | --- | --- | --- |
| JEV `jev-1.13.0` (also current `jev-latest` / `jev-preview`) | $0.042 | Free | [TypeSafe models](https://docs.typesafe.ai/models) |
| OpenAI Decisions `gpt-6-luna` | $0.10 | Free | [Decisions pricing](https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability) |
| Azure Microsoft-Decision-1 (US/EU Datazone deployments) | $0.042 | No output charge | [Microsoft pricing](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-microsoft-decision-1-in-microsoft-foundry-for-decision-and-classific/4562742) |

Cost is `(input_tokens × input_rate + output_tokens × output_rate) / 1,000,000`.
Output counts are not needed to calculate cost when output is free. Cached input
is included in input usage; the Decisions API has no separate cache charges.
The OpenAI rate is specific to `/v1/decisions`. Azure's adapter exclusively targets
Microsoft-Decision-1 and applies its rate to your deployment name. JEV/OpenAI use
the reported model (or configured model if absent); unknown model prices are
unavailable unless overridden.

To use new prices, account-specific rates, or regional/long-context premiums,
set both `<PROVIDER>_INPUT_USD_PER_MILLION` and
`<PROVIDER>_OUTPUT_USD_PER_MILLION` in `.env`, e.g.:

```dotenv
OPENAI_INPUT_USD_PER_MILLION=0.10
OPENAI_OUTPUT_USD_PER_MILLION=0
```

Overrides apply to that provider's selected model/deployment for the run and are
saved in the pricing snapshot. Rates must be finite and non-negative. Estimates
use the listed base rates unless overridden and exclude taxes or other fees.
Usage reported on a refusal or invalid decision still contributes to costs;
timeouts and responses without usable usage make totals partial. A provider bill
may include charges that the API response did not expose.

All rates use percentages from 0–100. P90 uses **nearest rank**: sort the measured
durations and take rank `ceil(0.9 × n)`, counting from one. For fewer than ten
samples, P90 can equal the maximum.

The console compares latencies of completed, valid decisions. JSON additionally
reports all attempted-request latencies, including timeouts and failures. Durations
use a monotonic clock and cover request construction, transport, response parsing,
and validation after acquiring a concurrency slot; queue time is excluded.
Connection setup and cold requests are included. Provider submissions are
interleaved by dataset row. The concurrency limit is global across all providers.
These are client-observed timings, not server-side inference measurements.

There are no hidden retries or warm-up calls. Every attempt contributes once.
Exit status: `0` for a completed benchmark with no request failures (wrong choices
still return `0`), `1` if any request failed, `2` for configuration/usage errors,
and `130` for cancellation. Cancellation stops the run without a completed report.
Reports include dataset text and are gitignored by default.

## Layout and development

```text
main.py                         # dashboard by default, terminal when given CLI flags
Makefile                        # launch, demo, benchmark, tests, checks
src/decision_tests/
  cli.py                        # menus, flags, summary table
  config.py                     # endpoint, model, and authentication configuration
  models.py                     # shared prompt and decision types
  prompts.py                    # validated YAML loading
  benchmark.py                  # scheduling, grading, timing, JSON output
  pricing.py                    # decision API prices, token counts, cost estimates
  web.py                        # local HTTP API, background runs, browser launcher
  web_storage.py                # atomic dotenv/history persistence and file boundaries
  static/{index.html,style.css,app.js} # responsive browser dashboard
  adapters/{jev,openai,azure}.py # provider syntax and authentication
  adapters/demo.py              # offline mock transport
prompt/*.yaml                   # one named prompt and dataset per file
results/                        # JSON reports and jobs/ run metadata
tests/                          # protocol, grading, timeout, concurrency, CLI checks
.env.example                    # committed configuration template
uv.lock                         # reproducible dependency versions
```

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use simulated HTTP responses and do not spend API credits. They cover provider
payloads and headers, response validation, safe errors, YAML validation, percentile
calculation, failure denominators, bounded concurrency, and complete CLI reports.
Live provider behavior must be validated with your credentials and deployment.

