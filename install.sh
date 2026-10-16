#!/usr/bin/env bash
# Installe les dépendances manquantes puis ouvre l'interface locale.
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd -P)"
RUNTIME_DIR="$PROJECT_DIR/.runtime"
VENV_PY="$PROJECT_DIR/.venv/bin/python"
LOCAL_PORT="${LINKEDIN_PORT:-8765}"
INSTALL_ONLY=0
NO_BROWSER=0
SCHEDULE=0
UV_BIN=""

usage() {
  cat <<'EOF'
Usage : ./install.sh [options]

Sans option : installe ce qui manque, ouvre l'interface locale et garde le serveur
actif dans ce terminal. Ctrl+C l'arrête. Aucune invitation n'est envoyée au démarrage.

  --port PORT       Port local (8765 par défaut)
  --no-browser      Démarre le serveur sans ouvrir un onglet
  --install-only    Installe les dépendances sans lancer le serveur
  --schedule        Active explicitement la tâche macOS du lundi au vendredi
                    (HOUR=10 MINUTE=30 par défaut, aucun lancement immédiat)
  --no-schedule     Compatibilité : aucun changement de planification
  -h, --help        Affiche cette aide
EOF
}

fail() { printf '\nErreur : %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --port)
      [ "$#" -ge 2 ] || fail "--port attend un numéro."
      LOCAL_PORT="$2"; shift 2 ;;
    --no-browser) NO_BROWSER=1; shift ;;
    --install-only) INSTALL_ONLY=1; shift ;;
    --schedule) SCHEDULE=1; shift ;;
    --no-schedule) shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Option inconnue : $1. Consultez ./install.sh --help." ;;
  esac
done

[[ "$LOCAL_PORT" =~ ^[0-9]{1,5}$ ]] || fail "Le port doit être un entier entre 1 et 65535."
LOCAL_PORT=$((10#$LOCAL_PORT))
((LOCAL_PORT >= 1 && LOCAL_PORT <= 65535)) || fail "Le port doit être entre 1 et 65535."

PLATFORM="$(uname -s)"
case "$PLATFORM" in
  Darwin|Linux) ;;
  *) fail "Ce lanceur prend en charge macOS et Linux. Windows natif n'est pas pris en charge." ;;
esac

if [ "$SCHEDULE" -eq 1 ]; then
  [ "$PLATFORM" = Darwin ] || fail "--schedule utilise launchd et nécessite macOS."
  SCHEDULE_HOUR="${HOUR:-10}"
  SCHEDULE_MINUTE="${MINUTE:-30}"
  [[ "$SCHEDULE_HOUR" =~ ^[0-9]{1,2}$ ]] || fail "HOUR doit être entre 0 et 23."
  [[ "$SCHEDULE_MINUTE" =~ ^[0-9]{1,2}$ ]] || fail "MINUTE doit être entre 0 et 59."
  SCHEDULE_HOUR=$((10#$SCHEDULE_HOUR))
  SCHEDULE_MINUTE=$((10#$SCHEDULE_MINUTE))
  ((SCHEDULE_HOUR <= 23 && SCHEDULE_MINUTE <= 59)) || fail "Horaire invalide (HOUR 0..23, MINUTE 0..59)."
fi

python_usable() {
  "$1" -c 'import sys, venv; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

bootstrap_uv() {
  mkdir -p "$RUNTIME_DIR/bin"
  export UV_UNMANAGED_INSTALL="$RUNTIME_DIR/bin"
  export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
  export UV_CACHE_DIR="$RUNTIME_DIR/cache"
  export UV_PYTHON_INSTALL_BIN=0
  UV_BIN="$RUNTIME_DIR/bin/uv"
  if [ ! -x "$UV_BIN" ]; then
    printf '→ Installation locale de uv (source officielle : astral.sh)\n'
    local installer
    installer="$(mktemp "$RUNTIME_DIR/uv-installer.XXXXXX")"
    if command -v curl >/dev/null 2>&1; then
      if ! curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --retry 2 \
        https://astral.sh/uv/install.sh --output "$installer"; then
        rm -f "$installer"
        fail "Téléchargement de uv impossible. Vérifiez la connexion puis relancez ./install.sh."
      fi
    elif command -v wget >/dev/null 2>&1; then
      if ! wget --https-only --quiet https://astral.sh/uv/install.sh -O "$installer"; then
        rm -f "$installer"
        fail "Téléchargement de uv impossible. Vérifiez la connexion puis relancez ./install.sh."
      fi
    else
      rm -f "$installer"
      fail "curl ou wget est requis pour installer automatiquement Python."
    fi
    if ! sh "$installer"; then
      rm -f "$installer"
      fail "Installation de uv impossible. Relancez ./install.sh après correction de l'erreur ci-dessus."
    fi
    rm -f "$installer"
  fi
}

if ! python_usable "$VENV_PY"; then
  PYTHON_BIN=""
  if [ -n "${PYTHON:-}" ]; then
    python_usable "$PYTHON" || fail "PYTHON=$PYTHON doit désigner un Python 3.10+ fonctionnel."
    PYTHON_BIN="$PYTHON"
  else
    for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
      if command -v "$candidate" >/dev/null 2>&1 && python_usable "$candidate"; then
        PYTHON_BIN="$(command -v "$candidate")"
        break
      fi
    done
  fi

  if [ -z "$PYTHON_BIN" ]; then
    bootstrap_uv
    printf '→ Installation de Python 3.12 dans .runtime/\n'
    "$UV_BIN" python install --no-bin 3.12
    PYTHON_BIN="$("$UV_BIN" python find --managed-python 3.12)"
  fi

  if [ -e "$PROJECT_DIR/.venv" ]; then
    mkdir -p "$RUNTIME_DIR"
    backup="$(mktemp -d "$RUNTIME_DIR/venv-backup.XXXXXX")"
    mv "$PROJECT_DIR/.venv" "$backup/venv"
    printf '→ Ancien environnement inutilisable conservé dans %s/venv\n' "$backup"
  fi
  printf '→ Création de l’environnement Python (.venv)\n'
  if ! "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv"; then
    # Certaines distributions Linux séparent venv/ensurepip du paquet Python.
    bootstrap_uv
    "$UV_BIN" venv --allow-existing --seed --python "$PYTHON_BIN" "$PROJECT_DIR/.venv"
  fi
fi

if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
  if ! "$VENV_PY" -m ensurepip --default-pip; then
    bootstrap_uv
    "$UV_BIN" pip install --python "$VENV_PY" pip
  fi
fi

if "$VENV_PY" scripts/check_install.py dependencies requirements.txt; then
  printf '✓ Dépendances Python déjà installées\n'
else
  printf '→ Installation des dépendances Python\n'
  "$VENV_PY" -m pip install --disable-pip-version-check -r requirements.txt
  "$VENV_PY" scripts/check_install.py dependencies requirements.txt \
    || fail "Les dépendances Python ne sont pas utilisables après installation."
fi

if "$VENV_PY" scripts/check_install.py chromium; then
  printf '✓ Chromium déjà installé\n'
else
  printf '→ Installation du navigateur Chromium (Playwright)\n'
  "$VENV_PY" -m playwright install chromium
  "$VENV_PY" scripts/check_install.py chromium \
    || fail "Chromium reste introuvable. Vérifiez la sortie de Playwright puis relancez."
fi

if [ "$SCHEDULE" -eq 1 ]; then
  LABEL="com.$(whoami).linkedin-connector"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/linkedin_data"
  "$VENV_PY" scripts/check_install.py schedule "$PROJECT_DIR" "$LABEL" "$PLIST" "$SCHEDULE_HOUR" "$SCHEDULE_MINUTE"
  plutil -lint "$PLIST" >/dev/null
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  printf '✓ Tâche activée du lundi au vendredi à %02dh%02d (sans lancement immédiat)\n' "$SCHEDULE_HOUR" "$SCHEDULE_MINUTE"
elif [ "$PLATFORM" = Darwin ]; then
  LABEL="com.$(whoami).linkedin-connector"
  if [ -f "$HOME/Library/LaunchAgents/$LABEL.plist" ]; then
    printf 'Note : une ancienne tâche planifiée existe ; elle reste inchangée.\n'
    printf 'Pour la retirer : ./uninstall.sh\n'
  fi
fi

printf '\n✓ Installation prête.\n'
[ "$INSTALL_ONLY" -eq 0 ] || exit 0

SERVER_ARGS=(--port "$LOCAL_PORT")
[ "$NO_BROWSER" -eq 0 ] || SERVER_ARGS+=(--no-browser)
exec "$VENV_PY" "$PROJECT_DIR/web_app.py" "${SERVER_ARGS[@]}"
