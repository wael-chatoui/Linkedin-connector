# Notes de version

## 1.1.0, 28 octobre 2026

- Rapports e-mail centralisés via le domaine FluentFlow, avec une clé Resend conservée uniquement côté serveur.
- Adresse personnelle configurable dans l’interface, confirmation par lien reçu et désactivation indépendante des invitations.
- Bilan limité aux compteurs et au résultat du passage, sans transmission des profils ni de la session LinkedIn.
- Affichage du dernier résultat d’envoi et des erreurs de configuration ou de connexion au service.
- Bilan des passages sans invitation, notamment lorsqu’un quota est atteint ou qu’une reconnexion est nécessaire ; pas de doublon lors d’un passage déjà traité.
- Conservation des anciennes configurations `.env` jusqu’à l’activation ou la désactivation explicite du nouveau service sur ce Mac.

## 1.0.0, 28 octobre 2026

Première version identifiée pour le partage sur Mac.

- Démarrage par double-clic sur **Démarrer.command**, avec installation locale des outils manquants.
- Parcours en trois étapes : connexion LinkedIn, choix des personnes, programmation des invitations.
- Test de recherche sans envoi, avec résultats distincts de l’ancien historique, y compris lorsqu’aucun profil n’est trouvé.
- Résumé des réglages appliqués, prochain passage prévu, pause et reprise. Les modifications d’une automatisation active nécessitent un enregistrement explicite.
- Maximum quotidien réglable de 1 à 20, avec rappel du plafond de 80 invitations sur 7 jours. Les nouvelles installations démarrent avec un maximum de 3.
- Message d’invitation générique, consultable avant activation et désactivé par défaut.
- Journaux macOS placés hors de Documents pour éviter le blocage du démarrage par macOS. Les anciens journaux restent consultables.
- Comptage des invitations préservé lorsqu’une erreur survient après le clic d’envoi.
- Guide de mise à jour avec sauvegarde, conservation des données et consignes de première ouverture macOS.
- Tests automatiques de l’installation, du moteur, du planificateur et de l’interface sur des données fictives.

L’application est distribuée sous forme de dossier, sans signature Apple. Le Mac doit être allumé, éveillé et connecté à l’heure prévue. Les vérifications de cette version n’envoient aucune invitation réelle et ne certifient pas les sélecteurs de l’interface LinkedIn actuelle.
