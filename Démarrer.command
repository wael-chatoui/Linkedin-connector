#!/bin/bash
# Lanceur Finder : aucun chemin ni commande à saisir.
set -u

finish() {
  local status="$?"
  printf '\n'
  if [ "$status" -eq 0 ]; then
    printf 'Lancement terminé. Vous pouvez fermer cette fenêtre.\n'
  elif [ "$status" -eq 130 ]; then
    printf 'Le démarrage ou la page locale a été arrêté.\n'
  else
    printf 'L’application n’a pas pu démarrer (code %s).\n' "$status"
    printf 'Lisez le message juste au-dessus, puis relancez Démarrer.command.\n'
    printf 'Vos réglages et votre historique ne sont pas effacés.\n'
    printf 'La rubrique « Besoin d’aide ? » du README explique les problèmes courants.\n'
  fi
  printf 'Fermer cette fenêtre ne met pas en pause une automatisation déjà activée.\n'
  printf 'Pour suspendre les invitations, utilisez « Mettre en pause » sur la page.\n'
  if [ -t 0 ]; then
    printf '\nAppuyez sur Entrée pour terminer. '
    read -r _ || true
  fi
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT

cd "$(dirname "$0")" || exit 1
printf '\nBienvenue dans LinkedIn Connector.\n'
printf 'Cette fenêtre prépare l’application, puis ouvre votre navigateur.\n'
/bin/bash ./install.sh "$@"
