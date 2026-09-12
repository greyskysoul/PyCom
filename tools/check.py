"""Run the checks required before committing.

Covers the CI *test* job (ruff / format / mypy / pytest) plus the packaging
mistakes the CI *build* job would otherwise only catch after a push: version
drift between ``pyproject.toml`` and ``pycom.__version__``, and a wheel that
fails to build or ships without the resources the app loads at runtime.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
RESOURCES = SRC / "pycom" / "resources"

# Resource files loaded through importlib.resources at runtime; a wheel missing
# one of them installs cleanly but the app cannot start.
REQUIRED_RESOURCES = ("app.tcss",)

MODULE_CHECKS = [
    ("Ruff lint", ["ruff", "check", "src", "tests"]),
    ("Ruff format", ["ruff", "format", "--check", "src", "tests"]),
    ("Mypy", ["mypy", "src/pycom"]),
    ("Unit tests", ["pytest", "tests/unit", "-q"]),
]


def _project_version() -> str | None:
    """``version`` from the ``[project]`` table.

    Parsed by hand rather than with ``tomllib`` because the test matrix still
    includes Python 3.9, where that module does not exist.
    """
    section: list[str] = []
    inside = False
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped == "[project]"
            continue
        if inside:
            section.append(line)
    match = re.search(r'^version\s*=\s*"([^"]+)"', "\n".join(section), re.MULTILINE)
    return match.group(1) if match else None


def check_version() -> bool:
    """``pyproject.toml`` and ``pycom.__version__`` must not drift apart."""
    print("\n== Version ==")
    sys.path.insert(0, str(SRC))
    from pycom import __version__

    declared = _project_version()
    if declared != __version__:
        print(f"Mismatch: pyproject.toml has {declared!r}, pycom.__version__ is {__version__!r}")
        return False
    print(f"OK ({__version__})")
    return True


def _required_resources() -> dict[str, Path]:
    """Wheel path -> source file for every resource the runtime has to find."""
    wanted = {name: RESOURCES / name for name in REQUIRED_RESOURCES}
    for path in sorted((RESOURCES / "themes").glob("*.json")):
        wanted[f"themes/{path.name}"] = path
    return {f"pycom/resources/{rel}": src for rel, src in wanted.items()}


def check_packaging() -> bool:
    """Build the wheel like CI does and confirm it ships the resources."""
    print("\n== Packaging ==")
    for module in ("build", "hatchling"):
        if importlib.util.find_spec(module) is None:
            print(f"Missing {module!r}: run `pip install -e \".[dev]\"` or pass --skip-build")
            return False

    required = _required_resources()
    absent = [str(src) for src in required.values() if not src.is_file()]
    if absent:
        print("Missing source resources:\n  " + "\n  ".join(absent))
        return False

    with tempfile.TemporaryDirectory() as tmp:
        outdir = Path(tmp)
        command = [sys.executable, "-m", "build", "--wheel", "--no-isolation"]
        command += ["--outdir", str(outdir)]
        if subprocess.run(command, cwd=ROOT).returncode:
            print("Wheel build failed (see above).")
            return False
        wheels = sorted(outdir.glob("*.whl"))
        if not wheels:
            print("The build produced no wheel.")
            return False
        with zipfile.ZipFile(wheels[0]) as archive:
            names = set(archive.namelist())

    absent = [name for name in required if name not in names]
    if absent:
        print("The wheel is missing runtime resources:\n  " + "\n  ".join(absent))
        return False
    print(f"OK ({wheels[0].name})")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checks to run before committing.")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="skip the wheel/packaging checks (faster, but no parity with the CI build job)",
    )
    args = parser.parse_args(argv)

    if not check_version():
        return 1
    for name, command in MODULE_CHECKS:
        print(f"\n== {name} ==")
        if subprocess.run([sys.executable, "-m", *command], cwd=ROOT).returncode:
            return 1
    if not args.skip_build and not check_packaging():
        return 1

    print("\nAll pre-commit checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
