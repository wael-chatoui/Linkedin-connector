#!/usr/bin/env python3
"""Local installation checks and opt-in scheduling, without opening LinkedIn."""

import importlib
import importlib.metadata
import os
from pathlib import Path
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


def schedule(project: Path) -> None:
    """Use exactly the same saved settings and scheduler as the web interface."""
    sys.path.insert(0, str(project.resolve()))
    from app_settings import read_settings, write_settings
    from automation_schedule import ScheduleManager
    from runtime_lock import OperationLock

    manager = ScheduleManager(project)
    settings_path = manager.data_dir / "settings.json"
    with OperationLock(manager.data_dir / ".operation.lock"):
        try:
            previous = read_settings(settings_path)
        except FileNotFoundError as exc:
            raise ValueError("Enregistre d'abord des mots-clés dans l'interface avant d'utiliser --schedule.") from exc
        if not previous["keywords"]:
            raise ValueError("Enregistre d'abord des mots-clés dans l'interface avant d'utiliser --schedule.")
        settings = {**previous, "automation_enabled": True}
        # Leave sends disabled if installation is interrupted before completion.
        write_settings(settings_path, {**settings, "automation_enabled": False})
        manager.install(settings)
        write_settings(settings_path, settings)
    days = "tous les jours" if settings["schedule_days"] == "everyday" else "du lundi au vendredi"
    print(f"✓ Automatisation activée {days} à {settings['schedule_time']} (sans envoi immédiat).")


def unschedule(project: Path) -> None:
    """Pause this data directory and remove only a verified, owned schedule."""
    sys.path.insert(0, str(project.resolve()))
    from app_settings import SettingsError, read_settings, write_settings
    from automation_schedule import ScheduleError, ScheduleManager
    from runtime_lock import OperationBusy, OperationLock

    manager = ScheduleManager(project)
    if not manager.supported:
        raise ScheduleError("La suppression de la planification nécessite macOS.")
    state = manager.status()
    if state.get("foreign") or state.get("error"):
        raise ScheduleError(state.get("error") or "Cette tâche appartient à une autre installation.")
    settings_path = manager.data_dir / "settings.json"
    try:
        previous = read_settings(settings_path)
    except (FileNotFoundError, SettingsError):
        # Missing or invalid settings already prevent automatic sends. Keep
        # the file intact so removing a schedule never repairs or erases data.
        previous = None
    if previous is not None:
        # A running automation checks this switch before each invitation.
        write_settings(settings_path, {**previous, "automation_enabled": False})
    try:
        with OperationLock(manager.data_dir / ".operation.lock"):
            manager.uninstall()
    except OperationBusy as exc:
        raise ScheduleError("Les envois sont en pause. Une opération est encore en cours ; "
                            "relancez uninstall.sh après sa fin pour retirer le calendrier.") from exc
    print("✓ Automatisation en pause et calendrier supprimé. Vos réglages, votre connexion LinkedIn et votre historique sont conservés.")


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "dependencies":
        sys.exit(0 if dependencies(Path(sys.argv[2])) else 1)
    elif action == "chromium":
        sys.exit(0 if chromium() else 1)
    elif action in ("schedule", "unschedule"):
        try:
            {"schedule": schedule, "unschedule": unschedule}[action](Path(sys.argv[2]))
        except (ValueError, RuntimeError, OSError) as exc:
            raise SystemExit(f"Erreur : {exc}") from exc
    else:
        raise SystemExit(f"Action inconnue : {action}")
