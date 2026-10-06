#!/usr/bin/env bash
# Supprime la tâche planifiée. Les données (linkedin_data/) et le .venv sont conservés.
set -euo pipefail

LABEL="com.$(whoami).linkedin-connector"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "✅ Tâche planifiée supprimée ($LABEL)."
