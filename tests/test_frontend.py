"""Optional Node checks of asynchronous frontend behavior, without browser/network."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Optional Node runtime is unavailable")
def test_frontend_navigation_and_demo_mode():
    script = Path(__file__).with_name("frontend_checks.cjs")
    result = subprocess.run(
        [shutil.which("node"), str(script)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
