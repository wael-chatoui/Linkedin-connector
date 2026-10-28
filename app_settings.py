"""Shared, validated settings for the local interface and scheduled runs."""

import json
import re
import tempfile
from pathlib import Path


DEFAULTS = {
    "keywords": "", "pages": 2, "network": "S", "geography": "france",
    "min_score": 40, "limit": 3, "active_only": False, "note": False,
    "automation_enabled": False, "schedule_time": "10:30", "schedule_days": "everyday",
}
MAX_INVITATIONS = 20


class SettingsError(ValueError):
    pass


def validate_settings(value, base=None):
    if not isinstance(value, dict) or (base is not None and not isinstance(base, dict)):
        raise SettingsError("Réglages invalides.")
    value = dict(value)
    if "paris" in value:
        # An old regional search remains in France, never expands worldwide.
        if type(value.pop("paris")) is not bool:
            raise SettingsError("L'ancien réglage paris doit être un booléen.")
        value.setdefault("geography", "france")
    if value.keys() - DEFAULTS.keys() or (base and base.keys() - DEFAULTS.keys()):
        raise SettingsError("Réglages invalides.")
    settings = {**DEFAULTS, **(base or {}), **value}
    if not isinstance(settings["keywords"], str) or len(settings["keywords"]) > 500:
        raise SettingsError("Les mots-clés doivent contenir au maximum 500 caractères.")
    settings["keywords"] = settings["keywords"].strip()
    if len([line for line in settings["keywords"].splitlines() if line.strip()]) > 10:
        raise SettingsError("Entre au maximum 10 recherches, une par ligne.")
    for field, minimum, maximum in (("pages", 1, 10), ("min_score", 0, 100),
                                     ("limit", 1, MAX_INVITATIONS)):
        if type(settings[field]) is not int or not minimum <= settings[field] <= maximum:
            label = {"pages": "Nombre de pages", "min_score": "Score minimum", "limit": "Maximum d’invitations"}[field]
            raise SettingsError(f"{label} : choisissez un nombre entier de {minimum} à {maximum}.")
    if settings["network"] not in ("S", "O", "S,O"):
        raise SettingsError("Choisis un réseau valide : 2e degré, 3e degré ou les deux.")
    if settings["geography"] not in ("france", "international"):
        raise SettingsError("Choisis une localisation valide : France ou International.")
    for field in ("active_only", "note", "automation_enabled"):
        if type(settings[field]) is not bool:
            raise SettingsError(f"{field} doit être un booléen.")
    if (not isinstance(settings["schedule_time"], str)
            or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", settings["schedule_time"])):
        raise SettingsError("L'heure doit être au format HH:MM, entre 00:00 et 23:59.")
    if settings["schedule_days"] not in ("weekdays", "everyday"):
        raise SettingsError("Choisis les jours ouvrés ou tous les jours.")
    return settings


def read_settings(path: Path) -> dict:
    """Fail closed on absent or invalid files; never invent an active schedule."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SettingsError("Le fichier des réglages est illisible.") from error
    return validate_settings(value)


def write_settings(path: Path, settings) -> dict:
    settings = validate_settings(settings)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".settings-",
                                         suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return settings
