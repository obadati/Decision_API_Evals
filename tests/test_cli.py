import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_offline_end_to_end_report_and_repeated_runs(tmp_path):
    args = (
        "--demo",
        "--non-interactive",
        "--prompt",
        "Customer support routing",
        "--providers",
        "jev",
        "openai",
        "azure",
        "--repetitions",
        "2",
        "--concurrency",
        "3",
        "--output-dir",
        str(tmp_path),
    )
    for _ in range(2):
        process = run_cli(*args)
        assert process.returncode == 0, process.stdout + process.stderr
        assert "DEMO" in process.stdout
        assert "Saved:" in process.stdout
    paths = list(tmp_path.glob("*.json"))
    assert len(paths) == 2
    report = json.loads(paths[0].read_text())
    assert report["mode"] == "demo"
    assert report["total_test_runs"] == 96
    assert report["successful_requests"] == 96
    assert report["success_rate"] == 25.0
    for provider in ("jev", "openai", "azure"):
        assert report["provider_results"][provider]["total_test_runs"] == 32
        assert report["provider_results"][provider]["success_rate"] == 25.0


def test_dry_run_has_three_wire_formats_without_credentials():
    process = run_cli(
        "--dry-run",
        "--non-interactive",
        "--prompt",
        "Customer support routing",
        "--providers",
        "jev",
        "openai",
        "azure",
    )
    assert process.returncode == 0, process.stdout + process.stderr
    payloads = json.loads(process.stdout)
    assert payloads["jev"]["state"] == payloads["azure"]["state"] == payloads["openai"]["input"]
    assert isinstance(payloads["jev"]["questions"], dict)
    assert isinstance(payloads["openai"]["questions"], list)
    assert "API_KEY" not in process.stdout


def test_missing_credentials_fail_before_creating_report(tmp_path):
    empty_env = tmp_path / ".env"
    empty_env.write_text("OPENAI_API_KEY=\n")
    # Clear ambient key so this check never makes a live request.
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "main.py"),
            "--env-file",
            str(empty_env),
            "--non-interactive",
            "--prompt",
            "Customer support routing",
            "--providers",
            "openai",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 2
    assert "OPENAI_API_KEY" in process.stdout
    assert not (tmp_path / "results").exists()


def test_noninteractive_missing_arguments_and_invalid_numeric_input():
    process = run_cli("--demo", "--non-interactive")
    assert process.returncode == 2
    assert "requires --prompt and --providers" in process.stdout
    process = run_cli("--timeout", "nan")
    assert process.returncode == 2
    assert "positive finite" in process.stderr


def test_prompt_listing_uses_yaml_names():
    process = run_cli("--list-prompts")
    assert process.returncode == 0
    assert "Customer support routing (16 rows)" in process.stdout
    assert "Product review sentiment (8 rows)" in process.stdout


def test_dashboard_saved_dotenv_values_are_literal_in_the_cli(tmp_path):
    env_file = tmp_path / ".env"
    literal_model = "model-${DECISION_TEST_LITERAL_VARIABLE}-tail"
    env_file.write_text(f"OPENAI_MODEL='{literal_model}'\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPENAI_MODEL", "DECISION_TEST_LITERAL_VARIABLE"}
    }
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "main.py"),
            "--env-file",
            str(env_file),
            "--dry-run",
            "--non-interactive",
            "--prompt",
            "Customer support routing",
            "--providers",
            "openai",
        ],
        env={**env, "PYTHONPATH": str(ROOT / "src")},
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout)["openai"]["model"] == literal_model
