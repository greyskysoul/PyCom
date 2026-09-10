"""Run the checks required before committing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


CHECKS = [
    ("Ruff lint", ["ruff", "check", "src", "tests"]),
    ("Ruff format", ["ruff", "format", "--check", "src", "tests"]),
    ("Mypy", ["mypy", "src/pycom"]),
    ("Unit tests", ["pytest", "tests/unit", "-q"]),
]


def main() -> int:
    for name, command in CHECKS:
        print(f"\n== {name} ==")
        result = subprocess.run([sys.executable, "-m", *command], cwd=ROOT)
        if result.returncode:
            return result.returncode
    print("\nAll pre-commit checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
