#!/usr/bin/env bash
# Installe les dépendances manquantes puis ouvre l'interface locale.
set -Eeuo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd -P)"
RUNTIME_DIR="$PROJECT_DIR/.runtime"
VENV_PY="$PROJECT_DIR/.venv/bin/python"
LOCAL_PORT="${LINKEDIN_PORT:-8765}"
INSTALL_ONLY=0
NO_BROWSER=0
SCHEDULE=0
UV_BIN=""
INSTALL_STEP="préparation"

installation_error() {
  local status="$?"
  printf '\nLe démarrage s’est arrêté pendant cette étape : %s.\n' "$INSTALL_STEP" >&2
  printf 'Le détail de l’erreur se trouve juste au-dessus.\n' >&2
  printf 'Vous pouvez relancer Démarrer.command ou ./install.sh après correction.\n' >&2
  printf 'Vos réglages et votre historique sont conservés.\n' >&2
  exit "$status"
}
trap installation_error ERR

usage() {
  cat <<'EOF'
Usage : ./install.sh [options]

Sans option : installe ce qui manque, ouvre l'interface locale et garde le serveur
actif dans ce terminal. Ctrl+C l'arrête. Aucune invitation n'est envoyée au démarrage.

  --port PORT       Port local (8765 par défaut)
  --no-browser      Démarre le serveur sans ouvrir un onglet
  --install-only    Installe les dépendances sans lancer le serveur
  --schedule        Active la tâche macOS avec les réglages enregistrés dans
                    l'interface (mots-clés obligatoires, aucun envoi immédiat)
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
fi

printf '\nLinkedIn Connector\n'
printf 'La première installation peut prendre plusieurs minutes. Gardez cette fenêtre ouverte.\n'
printf 'Aucune invitation n’est envoyée par ce démarrage.\n\n'
printf '[1/4] Préparation des outils nécessaires\n'
INSTALL_STEP="préparation des outils nécessaires"

python_usable() {
  # Le Python fourni comme raccourci par macOS peut ouvrir une demande d’outils
  # développeur. Sans ces outils, le Python local installé ci-dessous suffit.
  if [ "$PLATFORM" = Darwin ] && [ "$(command -v "$1" || true)" = /usr/bin/python3 ] \
    && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
    return 1
  fi
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

printf '\n[2/4] Vérification des composants de l’application\n'
INSTALL_STEP="installation des composants (vérifiez votre connexion Internet)"
if "$VENV_PY" scripts/check_install.py dependencies requirements.txt; then
  printf '✓ Dépendances Python déjà installées\n'
else
  printf '→ Installation des dépendances Python\n'
  "$VENV_PY" -m pip install --disable-pip-version-check -r requirements.txt
  "$VENV_PY" scripts/check_install.py dependencies requirements.txt \
    || fail "Les dépendances Python ne sont pas utilisables après installation."
fi

printf '\n[3/4] Vérification du navigateur utilisé pour LinkedIn\n'
INSTALL_STEP="installation du navigateur (vérifiez votre connexion Internet)"
if "$VENV_PY" scripts/check_install.py chromium; then
  printf '✓ Chromium déjà installé\n'
else
  printf '→ Installation du navigateur Chromium (Playwright)\n'
  "$VENV_PY" -m playwright install chromium
  "$VENV_PY" scripts/check_install.py chromium \
    || fail "Chromium reste introuvable. Vérifiez la sortie de Playwright puis relancez."
fi

if [ "$SCHEDULE" -eq 1 ]; then
  INSTALL_STEP="activation de l’automatisation avec vos réglages enregistrés"
  "$VENV_PY" scripts/check_install.py schedule "$PROJECT_DIR"
elif [ "$PLATFORM" = Darwin ]; then
  LABEL="com.$(whoami).linkedin-connector"
  if [ -f "$HOME/Library/LaunchAgents/$LABEL.plist" ]; then
    printf 'Note : une tâche planifiée existe. Son état et ses réglages sont disponibles dans l’interface.\n'
  fi
fi

printf '\n✓ Installation prête.\n'
if [ "$INSTALL_ONLY" -eq 1 ]; then
  printf '[4/4] Installation terminée. Ouvrez Démarrer.command ou lancez ./install.sh pour continuer.\n'
  exit 0
fi

printf '[4/4] Ouverture de l’application\n'
printf 'Si la page ne s’ouvre pas, copiez cette adresse dans votre navigateur :\n'
printf '  http://127.0.0.1:%s\n\n' "$LOCAL_PORT"
printf 'Les mots-clés, l’heure et la pause se règlent sur cette page.\n'
printf 'Gardez cette fenêtre ouverte tant que vous utilisez la page.\n\n'

SERVER_ARGS=(--port "$LOCAL_PORT")
[ "$NO_BROWSER" -eq 0 ] || SERVER_ARGS+=(--no-browser)
exec "$VENV_PY" "$PROJECT_DIR/web_app.py" "${SERVER_ARGS[@]}"
