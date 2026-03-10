from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT_DIR / "scripts" / "check_limits.py"


def test_check_limits_script_does_not_crash_on_gbk_stdout() -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "gbk"
    env.pop("PYTHONUTF8", None)

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        errors="ignore",
        env=env,
    )

    assert "UnicodeEncodeError" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.returncode in (0, 1)
