#!/usr/bin/env bash
# Installe linkedin-connector sur macOS et planifie l'exécution quotidienne.
#
#   ./install.sh                    # installation + tâche du lundi au vendredi à 10h30
#   HOUR=9 MINUTE=0 ./install.sh    # autre horaire
#   ./install.sh --no-schedule      # installation sans tâche planifiée
set -euo pipefail

cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="com.$(whoami).linkedin-connector"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HOUR="${HOUR:-10}"
MINUTE="${MINUTE:-30}"

# ── Python ≥ 3.10 ──────────────────────────────────────────────────────────────
PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "❌ Python 3.10+ requis (trouvé : $("$PY" --version 2>&1 || echo aucun))."
  echo "   Installe-le avec : brew install python"
  exit 1
fi

echo "→ Environnement Python (.venv)"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "→ Navigateur Chromium (Playwright)"
.venv/bin/playwright install chromium

mkdir -p linkedin_data
if [ ! -f .env ]; then
  cp .env.example .env
  echo "→ .env créé à partir de .env.example (à remplir)"
fi

# ── Tâche planifiée (launchd) ─────────────────────────────────────────────────
if [ "${1:-}" != "--no-schedule" ]; then
  echo "→ Tâche planifiée : lundi–vendredi à ${HOUR}h$(printf %02d "$MINUTE") + au démarrage du Mac"
  mkdir -p "$HOME/Library/LaunchAgents"
  {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/linkedin_connect.py</string>
    <string>auto</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <!-- Au démarrage / à l'ouverture de session : rattrape la journée si besoin -->
  <key>RunAtLoad</key><true/>
  <key>StartCalendarInterval</key>
  <array>
EOF
    for day in 1 2 3 4 5; do
      echo "    <dict><key>Weekday</key><integer>$day</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MINUTE</integer></dict>"
    done
    cat <<EOF
  </array>
  <key>StandardOutPath</key><string>$DIR/linkedin_data/auto.log</string>
  <key>StandardErrorPath</key><string>$DIR/linkedin_data/auto.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict>
</plist>
EOF
  } > "$PLIST"
  plutil -lint "$PLIST" >/dev/null
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
fi

cat <<EOF

✅ Installation terminée.

Étapes suivantes :
  1. Remplis .env (clé Resend + e-mail de destination)
  2. Connecte-toi à LinkedIn :  .venv/bin/python linkedin_connect.py login
  3. Premier test :             .venv/bin/python linkedin_connect.py find --pages 1
                                .venv/bin/python linkedin_connect.py run --dry-run
EOF
