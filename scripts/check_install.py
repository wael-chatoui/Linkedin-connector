#!/usr/bin/env python3
"""Small installer checks, with no LinkedIn session or personal-data access."""

import importlib
import importlib.metadata
import os
from pathlib import Path
import plistlib
import sys


def dependencies(requirements: Path) -> bool:
    """Verify versions locally so an ordinary relaunch needs no package index."""
    try:
        from pip._vendor.packaging.requirements import Requirement

        for line in requirements.read_text(encoding="utf-8").splitlines():
            line = line.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            requirement = Requirement(line)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            installed = importlib.metadata.version(requirement.name)
            if installed not in requirement.specifier:
                return False
        importlib.import_module("dotenv")
        importlib.import_module("playwright.sync_api")
    except (ImportError, ValueError, OSError):
        return False
    return True


def chromium() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            return executable.is_file() and os.access(executable, os.X_OK)
    except Exception:
        return False


def schedule(project: Path, label: str, target: Path, hour: int, minute: int) -> None:
    """plistlib escapes paths and names, including spaces, & and non-ASCII."""
    job = {
        "Label": label,
        "ProgramArguments": [str(project / ".venv/bin/python"), str(project / "linkedin_connect.py"), "auto"],
        "WorkingDirectory": str(project),
        "RunAtLoad": False,
        "StartCalendarInterval": [
            {"Weekday": day, "Hour": hour, "Minute": minute}
            for day in range(1, 6)
        ],
        "StandardOutPath": str(project / "linkedin_data/auto.log"),
        "StandardErrorPath": str(project / "linkedin_data/auto.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    target.write_bytes(plistlib.dumps(job))


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "dependencies":
        sys.exit(0 if dependencies(Path(sys.argv[2])) else 1)
    elif action == "chromium":
        sys.exit(0 if chromium() else 1)
    elif action == "schedule":
        schedule(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]))
    else:
        raise SystemExit(f"Action inconnue : {action}")
