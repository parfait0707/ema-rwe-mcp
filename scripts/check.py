"""The checks a change must pass before it is pushed: one command, no hosted CI.

    uv run python scripts/check.py            # every step
    uv run python scripts/check.py --no-build # without the build and wheel steps (quicker while editing)

Steps, in order; the first failure stops the run and is named in the last line:

    versions  the release version is the same everywhere it is written (docs/release.md, step 1)
    lock      uv.lock matches pyproject.toml
    lint      ruff check src tests scripts
    format    ruff format --check src tests scripts
    tests     pytest (includes the real stdio MCP tests)
    gold      scripts/extraction_eval.py check --require: every reference criterion of the four reference
              protocols is still found, served to the reader and accepted as evidence. The protocols are the
              user's own copies (`uv run python scripts/extraction_eval.py fetch`, once per clone);
              --allow-missing-gold checks only those that are there.
    build     uv build into a temporary folder
    wheel     the wheel holds every module and the bundled data the server reads at run time

`.githooks/pre-push` runs this before every push once `git config core.hooksPath .githooks` is set for the
clone (README_DEV.md). Nothing here needs the network once the environment is installed and the reference
protocols are fetched.
"""

import argparse
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATHS = ["src", "tests", "scripts"]
# Files of the wheel the server needs beside its modules (pyproject.toml force-include).
BUNDLED = ("ema_rwe/data/ema.sqlite3", "ema_rwe/data/ema-medicines.json", "ema_rwe/docs/mcp-workflow.md")
# Where the release version is written, each with the text that carries it.
VERSIONED = {
    "uv.lock": 'name = "ema-rwe-mcp"\nversion = "{v}"',
    "README.md": "**バージョン {v}**",
    "README_DEV.md": "公開版は{v}",
    "src/ema_rwe/config.py": '"ema-rwe-mcp/{v}"',
    ".env.example": "EMA_USER_AGENT=ema-rwe-mcp/{v}",
    ".mcp.json.sample": "ema-rwe-mcp@v{v}",
    ".codex/config.toml.sample": "ema-rwe-mcp@v{v}",
}


def run(*command: str) -> None:
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def versions() -> None:
    version = project_version()
    wrong = [
        f"{name}: expected {text.format(v=version)!r}"
        for name, text in VERSIONED.items()
        if text.format(v=version) not in (ROOT / name).read_text(encoding="utf-8")
    ]
    # The newest dated heading; an 'Unreleased' section may stand above it.
    released = re.search(
        r"^## (\d+\.\d+\.\d+) ", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.MULTILINE
    )
    if not released or released[1] != version:
        wrong.append(
            f"CHANGELOG.md: newest release heading is {released[1] if released else None}, not {version}"
        )
    if wrong:
        raise SystemExit("version " + version + " is not written everywhere:\n  " + "\n  ".join(wrong))


def wheel(folder: Path) -> None:
    (built,) = folder.glob("*.whl")
    names = set(zipfile.ZipFile(built).namelist())
    modules = {
        "ema_rwe/" + path.relative_to(ROOT / "src" / "ema_rwe").as_posix()
        for path in (ROOT / "src" / "ema_rwe").rglob("*.py")
    }
    missing = sorted((modules | set(BUNDLED)) - names)
    if missing:
        raise SystemExit(f"{built.name} lacks: " + ", ".join(missing))
    if project_version() not in built.name:
        raise SystemExit(f"{built.name} is not version {project_version()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--no-build", action="store_true", help="skip the build and wheel steps")
    parser.add_argument(
        "--allow-missing-gold",
        action="store_true",
        help="pass when reference protocols of the extraction check are not in the archive (they are skipped)",
    )
    args = parser.parse_args()
    gold = ["uv", "run", "python", "scripts/extraction_eval.py", "check"]
    steps = [
        ("versions", versions),
        ("lock", lambda: run("uv", "lock", "--check")),
        ("lint", lambda: run("uv", "run", "ruff", "check", *PATHS)),
        ("format", lambda: run("uv", "run", "ruff", "format", "--check", *PATHS)),
        ("tests", lambda: run("uv", "run", "pytest", "-q")),
        ("gold", lambda: run(*gold, *([] if args.allow_missing_gold else ["--require"]))),
    ]
    with tempfile.TemporaryDirectory() as folder:
        if not args.no_build:
            steps += [
                ("build", lambda: run("uv", "build", "--out-dir", folder)),
                ("wheel", lambda: wheel(Path(folder))),
            ]
        started = time.perf_counter()
        for name, step in steps:
            print(f"== {name}", flush=True)
            try:
                step()
            except SystemExit as failure:
                if failure.code not in (0, None) and not isinstance(failure.code, int):
                    print(failure.code, file=sys.stderr)
                print(f"FAILED at {name}", file=sys.stderr)
                raise SystemExit(1) from None
    print(f"OK: {', '.join(name for name, _ in steps)} ({time.perf_counter() - started:.0f} s)")


if __name__ == "__main__":
    main()
