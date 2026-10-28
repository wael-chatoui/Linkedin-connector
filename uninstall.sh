#!/usr/bin/env bash
# Met en pause et retire uniquement la tâche appartenant à cette installation.
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd -P)"
VENV_PY="$PROJECT_DIR/.venv/bin/python"

if [ "$(uname -s)" != Darwin ]; then
  printf 'La planification automatique utilise macOS. Aucun service n’a été supprimé.\n' >&2
  exit 1
fi
if [ ! -x "$VENV_PY" ]; then
  printf 'Ouvrez Démarrer.command, puis cliquez sur « Mettre en pause » dans l’application.\n' >&2
  printf 'Le Python de cette installation est absent ; aucune autre tâche n’a été modifiée.\n' >&2
  exit 1
fi
exec "$VENV_PY" "$PROJECT_DIR/scripts/check_install.py" unschedule "$PROJECT_DIR"
