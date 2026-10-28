# Vérifications des versions

## Version 1.1.0, 28 octobre 2026

La revue porte sur les bilans e-mail facultatifs et le relais central FluentFlow.

- 172 tests Python du connecteur passent, sans compte LinkedIn ni envoi réel dans les tests automatisés.
- 708 tests du serveur FluentFlow et son build Next.js passent. Le commit serveur `c2b7f7d` est déployé en production sur `fluent-flow.fr`, statut Vercel Ready.
- La migration et les assertions SQL ont d’abord été exécutées dans une transaction annulée, puis revérifiées sur le schéma installé. Les trois tables ont RLS activé, sans accès pour `anon` ou `authenticated`. Les données des assertions ont été annulées.
- Recette du vrai relais avec l’[adresse de test officielle Resend](https://resend.com/changelog/sending-test-emails) : inscription, confirmation explicite, détection dans le connecteur et bilan fictif marqué Delivered. Le réessai du même bilan retourne `duplicate` sans nouvel e-mail ; un second bilan distinct le même jour est refusé avec HTTP 429. L’installation de recette a ensuite été révoquée.
- Revue dans Chrome Codex Agents : adresse invalide et focus, normalisation de l’adresse, attente de confirmation, adresse confirmée, désactivation, nettoyage des erreurs et rendu dans les cadres de 320 et 390 pixels. La taille globale du navigateur partagé n’a pas été changée.
- Page de confirmation française, jeton retiré de l’URL affichée et activation uniquement après un clic. Une indisponibilité du service laisse une possibilité de réessayer.
- Régressions couvertes : erreur fournisseur suivie d’une nouvelle tentative de confirmation, changement d’adresse avec révocation de l’ancien jeton, et erreur de CSV qui ne doit pas bloquer le bilan central.

Les essais Resend utilisent des compteurs fictifs. Ils ne prouvent pas la réception dans toutes les messageries. Un Mac éteint ne produit pas de bilan, et une coupure réseau peut empêcher l’envoi. Les quotas du forfait Resend restent applicables.

## Version 1.0.0

Revue du 28 octobre 2026, centrée sur une installation Mac et une utilisation sans connaissances de programmation. Les contrôles techniques de l’interface s’appuient notamment sur les [Web Interface Guidelines](https://github.com/vercel-labs/web-interface-guidelines/blob/main/command.md).

### Problèmes corrigés

| Problème observé | Comportement livré |
|---|---|
| Le ZIP publié ne proposait pas le lanceur par double-clic disponible localement. | `Démarrer.command` fait partie de la version publiée et le README décrit la première ouverture. |
| Une mise à jour pouvait être comprise comme le remplacement du dossier personnel entier. | Guide de sauvegarde et de copie des fichiers du programme dans le dossier existant. |
| Le test de recherche affichait aussi d’anciens profils sans distinguer les correspondances actuelles. | Vue du dernier test, avec critères, date et état vide propre. L’historique reste accessible. |
| La note présentait tous les utilisateurs comme freelances en développement. | Note générique visible avant activation, désactivée par défaut. |
| Le plafond de 80 sur 7 jours était caché dans des rubriques repliées. | Rappel à côté du maximum quotidien et dans le récapitulatif d’activation. |
| La connexion pouvait interrompre l’enregistrement du brouillon. | Sauvegarde reprise après la fin d’une connexion asynchrone. |
| Une réponse de mutation bloquée pouvait laisser tous les boutons désactivés. | Délai de réponse borné et récupération sans réessai automatique de la mutation. |
| Le démarrage macOS pouvait échouer avant que le programme écrive son état. | Journaux hors de Documents et indication du dernier code d’échec launchd. |
| Une erreur après le clic d’envoi pouvait faire perdre son comptage. | Enregistrement de l’envoi avant les vérifications et écritures suivantes. |
| Le second double-clic pouvait refuser une interface déjà ouverte. | Réutilisation de l’instance du même projet, même si sa réponse contient une version. |

### Contrôles effectués

- 145 tests unitaires et d’intégration locaux, avec fichiers temporaires, processus simulés et aucun envoi réel.
- Parcours rendu dans Chrome, profil réservé aux agents : première ouverture, connexion fictive, saisie invalide et focus du champ, activation différée, recherche avec résultat et sans résultat, accès à l’historique, aperçu de la note et navigation clavier.
- Largeur bureau mesurée à 1 470 pixels et cadres de prévisualisation à 390 et 320 pixels, sans débordement horizontal. Ces cadres vérifient le responsive dans Chrome ; ils ne simulent pas un appareil physique.
- Connexion fictive de trois secondes : les champs saisis avant la connexion sont enregistrés après celle-ci et conservés au rechargement.
- Syntaxe JavaScript et Bash, contrôle des espaces Git, vérification des fichiers inclus dans la distribution.
- Pipeline GitHub Actions : tests isolés sur Linux et macOS, puis parcours de l’interface avec profils fictifs dans un navigateur de CI.

### Limites

Les essais n’utilisent aucun compte LinkedIn et ne valident pas les sélecteurs du site actuel. L’installation sur un Mac vierge est couverte par simulation, pas par un essai matériel. L’application n’est pas signée par Apple. Les contrôles du navigateur ne constituent pas un audit complet avec lecteur d’écran ou sur Safari.
