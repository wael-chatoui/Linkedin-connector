# LinkedIn Connector

Choisissez les personnes à rechercher sur LinkedIn et l’heure d’envoi. L’application peut ensuite leur envoyer des invitations automatiquement. Vous pouvez modifier vos choix ou mettre les envois en pause depuis une page dans votre navigateur.

**Pour commencer : un Mac avec macOS 14 ou plus récent, une connexion Internet et votre compte LinkedIn.** Aucun logiciel de programmation à installer vous-même. La planification automatique fonctionne sur Mac ; Windows n’est pas pris en charge.

## Première ouverture sur Mac

1. [Téléchargez la version 1.1.0 pour Mac](https://github.com/wael-chatoui/Linkedin-connector/archive/refs/tags/v1.1.0.zip). Vous pouvez aussi [ouvrir le projet sur GitHub](https://github.com/wael-chatoui/Linkedin-connector), puis choisir **Code > Download ZIP** pour obtenir la version en cours de développement.
2. Dans Téléchargements, ouvrez le fichier ZIP. Déplacez le dossier obtenu dans **Documents**, ou un autre endroit où vous souhaitez le garder. Ne le déplacez plus après avoir activé l’automatisation.
3. Dans ce dossier, double-cliquez sur **Démarrer.command**. Une fenêtre nommée Terminal s’ouvre : c’est normal. Vous n’avez rien à y taper.
4. Attendez la fin des quatre étapes. Au premier démarrage, les outils nécessaires sont téléchargés ; cela peut prendre plusieurs minutes. Une page s’ouvre ensuite dans votre navigateur.

**Pour rouvrir une installation existante, commencez directement à l’étape 3.** Le même double-clic conserve vos réglages et votre historique. Pour installer une nouvelle version, suivez plutôt [la mise à jour ci-dessous](#mettre-à-jour-sans-perdre-vos-données). Gardez la fenêtre Terminal ouverte pendant que vous utilisez la page.

Si macOS bloque l’ouverture, consultez [Besoin d’aide ?](#besoin-daide). Ce projet est distribué sous forme de dossier, pas encore comme une application Mac signée.

## Vos premiers réglages

1. Cliquez sur **Se connecter à LinkedIn** sur la page. Une autre fenêtre de navigateur, appelée Chromium, s’ouvre. Connectez-vous sur LinkedIn dans cette fenêtre, même si vous l’êtes déjà dans Safari ou Chrome. Effectuez les éventuelles vérifications demandées par LinkedIn. Votre connexion sera mémorisée pour les prochaines fois.
2. **Choisir les personnes à rechercher.** Écrivez une recherche par ligne, jusqu’à dix lignes. Par exemple :

   ```text
   fondateur SaaS
   dirigeant PME
   ```

3. **Où rechercher ?** Choisissez : France, ou International pour tous les pays.
4. Réglez **Maximum d’invitations par jour**, **Quels jours ?** et **À quelle heure ?**. Au départ : au plus 3 invitations, tous les jours à 10 h 30, week-end compris. L’heure est celle du Mac.
5. Cliquez sur **Activer l’automatisation**. L’application enregistre vos choix. Le premier envoi attend l’heure prévue : cliquer sur Activer ne lance pas d’invitation immédiatement.

Vous pouvez d’abord **Tester la recherche sans envoyer** pour voir les résultats. Ce test ne programme pas les envois : l’activation reste nécessaire.

**Le maximum est partagé entre toutes vos recherches.** Avec 5 invitations maximum et 3 lignes de mots-clés, il y aura au plus 5 invitations au total pendant le passage, pas 15. Il peut y en avoir moins si les résultats ou les filtres ne permettent pas d’atteindre ce nombre.

L’application applique aussi un plafond de **80 invitations sur les 7 derniers jours**. Avec un maximum de 15 par jour, certains jours peuvent donc compter moins d’envois. Le test de recherche affiche ses propres résultats ; choisissez **Tous les profils enregistrés** pour retrouver l’historique complet.

La version installée est indiquée tout en bas de la page. Consultez les [notes de version](CHANGELOG.md) pour voir les changements.

## Recevoir votre bilan par e-mail

Cette option est indépendante des invitations. Chaque utilisateur choisit son propre destinataire depuis l’application : aucun compte Resend ni clé API n’est nécessaire sur son Mac.

1. Dans **Recevoir les bilans par e-mail**, renseignez **Votre adresse e-mail**.
2. Cliquez sur **Recevoir le lien de confirmation**.
3. Ouvrez le message de **LinkedIn Connector**, envoyé depuis `rapports@fluent-flow.fr`, puis le lien de confirmation. Consultez les courriers indésirables si nécessaire.
4. Cliquez sur **Confirmer mon adresse** dans la page ouverte, puis revenez au connecteur. Le statut se met à jour automatiquement.

Les prochains passages programmés auront un bilan, y compris lorsqu’aucune invitation ne peut être envoyée à cause du quota ou d’une connexion LinkedIn à renouveler. Le résumé contient les compteurs et le motif du résultat. Les noms, URL et données des profils LinkedIn restent sur votre ordinateur.

Pour arrêter les rapports, cliquez sur **Désactiver les bilans par e-mail**. Cette action ne met pas les invitations en pause. Un passage non lancé parce que le Mac est éteint ne peut pas produire de rapport ; une absence de réseau peut aussi empêcher sa transmission.

## Au quotidien

| Je veux… | Ce que je fais |
|---|---|
| Rouvrir mes réglages | Je double-clique sur **Démarrer.command**, dans le même dossier. |
| Changer les mots-clés, le pays, le maximum ou l’heure | Je modifie la page puis clique sur **Enregistrer les modifications**. Les prochains passages utilisent ces choix. |
| Arrêter les invitations | Je clique sur **Mettre en pause**. Les invitations déjà envoyées restent envoyées. |
| Reprendre les invitations | Je vérifie mes réglages puis j’active à nouveau l’automatisation. |
| Voir ce qui s’est passé | Je consulte le dernier passage et les résultats sur la page. |

Une fois l’automatisation activée, vous pouvez fermer la page et le Terminal. **Pour fonctionner à l’heure choisie, le Mac doit rester allumé, éveillé, connecté à Internet et avec votre session macOS ouverte.** Une fenêtre Chromium pourra apparaître pendant le travail. Si le Mac est éteint ou en veille à l’heure prévue, le rattrapage n’est pas garanti.

Fermer la page ou le Terminal **ne met pas les envois en pause**. Utilisez le bouton **Mettre en pause**. Si un passage a commencé, la pause empêche l’invitation suivante ; elle n’annule pas un clic d’envoi déjà en cours.

## Mettre à jour sans perdre vos données

Gardez le dossier installé à son emplacement actuel : il contient vos réglages, votre historique et votre connexion LinkedIn. **Ne remplacez pas ce dossier entier par le nouveau téléchargement.**

1. Dans l’application, cliquez sur **Mettre en pause**. Attendez la fin de toute opération en cours, puis fermez sa fenêtre Terminal.
2. Dans le Finder, sélectionnez votre dossier installé et choisissez **Fichier > Dupliquer**. Cette copie est votre sauvegarde ; gardez-la sur votre Mac sans l’ouvrir pendant la mise à jour.
3. Téléchargez la nouvelle version depuis le même projet GitHub et ouvrez le ZIP dans **Téléchargements**. Laissez ce nouveau dossier à part.
4. Ouvrez le nouveau dossier, sélectionnez son **contenu** avec Commande + A, puis copiez-le avec Commande + C. Ouvrez ensuite votre dossier installé d’origine et collez avec Commande + V. Acceptez le remplacement des fichiers du programme et la fusion des sous-dossiers si le Finder la propose.
5. Double-cliquez sur **Démarrer.command dans le dossier d’origine**. Le lanceur vérifie les dépendances de cette version. Vérifiez vos mots-clés, votre maximum et l’heure, puis cliquez sur **Activer l’automatisation** lorsque vous souhaitez reprendre.

Le ZIP du projet ne contient ni `linkedin_data/`, ni `.env`, ni `.venv/`, ni `.runtime/`. Ces éléments de votre installation restent en place. Le dossier `linkedin_data/` conserve notamment les cookies de connexion et le journal utilisé pour les plafonds d’envoi. LinkedIn peut malgré tout demander une nouvelle connexion si la session a expiré.

Pour partager l’application avec un ami, envoyez le lien de téléchargement GitHub. Votre dossier installé et sa sauvegarde contiennent des données personnelles, dont votre session LinkedIn.

## Besoin d’aide ?

| Ce que vous voyez | Que faire |
|---|---|
| macOS bloque le premier lancement | Si le message concerne un développeur non identifié et que vous faites confiance à cette copie, ouvrez **Réglages Système > Confidentialité et sécurité**, puis **Ouvrir quand même** après la tentative d’ouverture. Suivez [les instructions Apple](https://support.apple.com/fr-fr/102445). Si l’alerte indique un fichier malveillant ou endommagé, arrêtez-vous et téléchargez à nouveau depuis le projet officiel. |
| « Permission refusée » ou fichier impossible à exécuter | L’archive a peut-être perdu le droit de lancer le fichier. La méthode de secours avec Terminal est décrite juste après ce tableau. |
| Le téléchargement s’arrête | Rétablissez Internet, puis double-cliquez à nouveau sur **Démarrer.command**. Vos réglages sont conservés. |
| Rien ne s’ouvre dans le navigateur | Laissez Terminal ouvert et ouvrez [la page locale](http://127.0.0.1:8765). Si elle reste inaccessible, lisez le dernier message de Terminal. |
| LinkedIn demande de se reconnecter | Utilisez la connexion LinkedIn sur la page et terminez la connexion dans la fenêtre Chromium. |
| Aucun envoi | Vérifiez que l’automatisation est active, que l’heure choisie est passée et que le Mac était disponible. Consultez le dernier passage : il indique le résultat ou le problème à résoudre. |
| La recherche ne donne rien | Essayez des mots-clés plus simples, ou International, puis testez de nouveau la recherche. Vérifiez aussi **Options de recherche et message joint**. |
| Les invitations partent encore après fermeture de la page | C’est normal si l’automatisation est active. Rouvrez l’application et cliquez sur **Mettre en pause**. |

<details>
<summary>Ouvrir avec Terminal si le double-clic ne fonctionne pas</summary>

1. Ouvrez **Terminal** avec la recherche Spotlight du Mac (Commande + Espace, puis « Terminal »).
2. Tapez `bash` suivi d’un espace.
3. Glissez le fichier **install.sh** depuis le dossier du projet vers cette fenêtre. Le Mac écrit son chemin à votre place.
4. Appuyez sur Entrée. L’installation et l’ouverture de la page commencent.

Cette méthode évite de taper le chemin du dossier, même si son nom contient des espaces. Elle ne doit pas servir à ignorer une alerte indiquant qu’un fichier est malveillant ou endommagé.

</details>

## Pour les personnes qui utilisent le terminal

Depuis le dossier du projet, une seule commande installe les éléments manquants et ouvre la page :

```bash
./install.sh
```

Python est installé localement si nécessaire. Vous n’avez besoin ni de Homebrew, ni de Node.js, ni d’un service d’e-mail. **Ctrl+C** arrête le serveur local, mais ne suspend pas une automatisation déjà activée.

<details>
<summary>Options, données, fonctionnement et vérifications techniques</summary>

## Options du lanceur

| Besoin | Commande |
|---|---|
| Installer et ouvrir l'application | `./install.sh` |
| Utiliser un autre port | `./install.sh --port 8877` |
| Garder le serveur sans ouverture automatique d'onglet | `./install.sh --no-browser` |
| Installer seulement, sans lancer le serveur | `./install.sh --install-only` |
| Activer avec les réglages déjà enregistrés, sous macOS | `./install.sh --schedule` |
| Consulter l'aide | `./install.sh --help` |

`--no-schedule` reste accepté pour les anciennes commandes et correspond au comportement par défaut. Il ne supprime pas une tâche existante. `PYTHON=/chemin/vers/python3 ./install.sh` permet de choisir l'interpréteur lors de la création de l'environnement ; un environnement `.venv` déjà fonctionnel est réutilisé.

## Installation et données locales

Le lanceur crée un environnement isolé `.venv/`, y installe les paquets de `requirements.txt` et télécharge Chromium dans le cache utilisateur de Playwright. Il vérifie localement les versions des paquets et la présence du navigateur avant de télécharger quoi que ce soit. Les versions majeures des dépendances sont bornées ; les versions mineures ne sont pas verrouillées.

Si aucun Python compatible n'est trouvé, le script utilise l'installateur officiel de **uv** puis installe Python 3.12 dans `.runtime/`. Il ne modifie ni les profils du shell ni le Python système. Un ancien `.venv` inutilisable est conservé dans `.runtime/venv-backup.*/` avant sa reconstruction. Voir les documentations officielles : [installation locale de uv](https://docs.astral.sh/uv/configuration/installer/), [gestion de Python](https://docs.astral.sh/uv/concepts/python-versions/) et [navigateurs Playwright](https://playwright.dev/python/docs/browsers).

L'interface écoute uniquement sur `127.0.0.1`. Les actions sont protégées par un jeton anti-CSRF et une vérification de l'origine. Elle est conçue pour un usage local ; ne l'exposez pas directement sur Internet.

Les fichiers personnels sont ignorés par Git :

| Emplacement | Contenu |
|---|---|
| `linkedin_data/prospects.csv` | Profils trouvés, score, raison du score et statut |
| `linkedin_data/sent_log.csv` | Historique utilisé pour les plafonds d'envoi |
| `linkedin_data/settings.json` | Ciblage, maximum par exécution, horaire, fréquence et état actif ou en pause |
| `linkedin_data/.profile/` | Session Chromium, notamment les cookies LinkedIn |
| `~/Library/Logs/Linkedin-connector/<identifiant>/auto.log` | Journal détaillé de l’automatisation macOS, propre à ce dossier de données |
| `linkedin_data/auto.log` | Ancien journal, conservé et encore visible dans l’interface |
| `linkedin_data/automation_status.json` | Dernière exécution, réglages utilisés, résultats et erreurs |
| `linkedin_data/last_auto_run.txt` | Jour déjà traité, pour éviter de répéter les envois |
| `linkedin_data/last_search.json` | Résultats et critères du dernier test de recherche, distincts de l’historique |
| `linkedin_data/mail_config.json` | Adresse, état et jeton privé de cette installation pour les rapports |
| `linkedin_data/mail_delivery.json` | Résultat du dernier envoi de bilan |
| `.env` | Configuration optionnelle des e-mails |
| `.venv/`, `.runtime/` | Environnement Python et outils d'installation |

Protégez le dossier `.profile/` comme une session de compte. Pour changer d'ordinateur, copiez les CSV afin de conserver les prospects et l'historique, puis connectez-vous à nouveau à LinkedIn. Ne supprimez pas l'historique pour contourner les plafonds.

## Configuration du service e-mail (maintenance)

Les utilisateurs du connecteur passent par le service central `https://fluent-flow.fr/api/linkedin-reports`. Ce service vérifie leur adresse et utilise un modèle de rapport fixe. Les jetons privés sont propres à chaque installation et révocables. Une copie installée ne contient aucune clé Resend commune.

La clé **`RESEND_API_KEY` doit être installée uniquement dans les variables privées du serveur FluentFlow**, sur Vercel, avant d’activer ce service en production. L’expéditeur par défaut est `LinkedIn Connector <rapports@fluent-flow.fr>` ; `LINKEDIN_REPORTS_FROM` permet à l’exploitant de le modifier. Le domaine d’envoi doit être vérifié dans Resend. Les quotas du [forfait Resend](https://resend.com/docs/knowledge-base/account-quotas-and-limits) restent applicables.

Le serveur utilise des tables de rapports isolées des comptes et cours PhraseLane. Une adresse confirmée reçoit au plus une nouvelle réservation de rapport par jour UTC ; les nouvelles tentatives du même rapport utilisent la clé d’idempotence du prestataire. L’adresse et les compteurs nécessaires au bilan sont transmis au service central, pas les cookies ni les profils LinkedIn.

### Compatibilité avec les anciennes installations

Si aucun `mail_config.json` n’existe encore, une ancienne configuration `.env` continue d’être reconnue. Dès que les rapports centraux sont configurés ou explicitement désactivés, elle ne sert plus de solution de repli. Les anciennes variables sont :

| Variable | Valeur |
|---|---|
| `RESEND_API_KEY` | Clé API Resend |
| `NOTIFY_EMAIL` | Adresse de destination du rapport |
| `RESEND_FROM` | Expéditeur facultatif sur un domaine vérifié dans Resend |

Le lanceur ne crée ni ne modifie `.env`. Ce fichier reste privé et n’est pas distribué aux amis avec le programme.

## Fonctionnement de la planification macOS

`./install.sh` ouvre l’interface ; l’automatisation s’active depuis cette interface après saisie des mots-clés. La tâche macOS utilise les mêmes réglages que la page, conservés dans `linkedin_data/settings.json`. Il n’existe plus de recherches prédéfinies utilisées en secours dans le mode `auto`. Sans configuration valide, sans mots-clés ou lorsque l’automatisation est en pause, ce mode n’envoie rien.

L’activation crée ou remplace la tâche launchd `com.<utilisateur>.linkedin-connector`, à l’heure et aux jours choisis. Elle ne démarre pas le pipeline à l’activation ni à chaque ouverture de session. Les réglages initiaux sont **tous les jours à 10h30** ; l’horaire enregistré est conservé tant que vous ne le modifiez pas dans l’interface.

Le navigateur de configuration et le terminal peuvent être fermés. Pour une exécution à l’heure prévue, le Mac doit être allumé, disposer d’une connexion Internet et avoir une session macOS ouverte permettant à Chromium de s’afficher. La session LinkedIn mémorisée doit rester valide. Le projet ne garantit pas le rattrapage des exécutions manquées pendant l’arrêt ou la veille du Mac. Une exécution réussie ou bloquée par LinkedIn n’est pas répétée le même jour.

Une ancienne tâche est détectée dans l’interface. Il faut activer la nouvelle configuration pour la remplacer ; sa simple détection ne change pas le système. En cas d’échec d’installation de la nouvelle tâche, l’application tente de restaurer le fichier et l’état précédents, sans déclencher l’ancien comportement d’envoi au chargement.

Les nouvelles tâches écrivent leur journal dans un sous-dossier de `~/Library/Logs/Linkedin-connector/`, identifié à partir du chemin du dossier de données. macOS peut refuser à son lanceur l’ouverture d’un journal situé dans `Documents`, avant même que Python démarre. Réactiver l’automatisation remplace cette ancienne configuration ; l’historique déjà présent dans `linkedin_data/auto.log` reste consultable dans l’interface.

Si la tâche appartient à une autre copie du projet ou utilise un autre dossier de données, l’interface le signale et bloque son remplacement. Ouvrez cette autre copie pour gérer sa planification.

L’option avancée suivante active la même planification avec les réglages déjà enregistrés :

```bash
./install.sh --schedule
```

Elle refuse une configuration absente ou des mots-clés vides. Si l’activation échoue, les envois restent en pause jusqu’à une activation réussie. Les anciennes variables `HOUR` et `MINUTE` ne pilotent plus l’horaire : modifiez-le dans l’interface. Cette option nécessite macOS et ne crée pas une configuration parallèle.

| Action avancée | Commande |
|---|---|
| Voir les journaux automatiques | `tail -f ~/Library/Logs/Linkedin-connector/*/auto.log` |
| Inspecter la tâche | `launchctl print gui/$(id -u)/com.$(whoami).linkedin-connector` |
| Supprimer la tâche en conservant les données | `./uninstall.sh` |

`uninstall.sh` met les réglages en pause puis retire uniquement la tâche vérifiée de cette installation. Il refuse de modifier une tâche appartenant à une autre copie. Si une opération utilise encore LinkedIn, les envois passent en pause et le script demande d’attendre sa fin avant de retirer le calendrier.

Sous Linux, le lanceur et les tests de recherche restent disponibles, mais l’activation de la planification macOS est indisponible. Aucun service système n’y est installé automatiquement.

## Commandes avancées

L'interface remplace les commandes pour l'utilisation courante. La CLI reste disponible :

```bash
.venv/bin/python linkedin_connect.py --help
```

| Commande | Rôle | Options utiles |
|---|---|---|
| `login` | Connexion manuelle à LinkedIn | |
| `find` | Recherche et enregistrement des prospects | `--keywords "..."`, `--segments founder,sales`, `--pages 2`, `--international`, `--network S,O` |
| `enrich` | Vérification de l'activité récente | `--top 30`, `--min-score 40` |
| `run` | Invitations aux prospects éligibles | `--dry-run`, `--limit 3`, `--active-only`, `--min-score 40`, `--note` |
| `auto` | Recherche et envoi selon les réglages enregistrés dans l’interface | `--force`, `--enrich` |
| `rescore` | Recalcul des scores | |

Exemple sans envoi :

```bash
.venv/bin/python linkedin_connect.py run --dry-run
```

`run` sans `--dry-run` effectue de vrais envois avec ses options CLI. `auto` effectue de vrais envois uniquement lorsque l’automatisation enregistrée est active. `auto --force` ignore la restriction aux jours ouvrés et le contrôle d’exécution quotidienne, mais conserve le ciblage enregistré, la pause, les mots-clés obligatoires et les plafonds d’invitations. Les options `--limit` et `--note` sont réservées au mode manuel `run` ; le mode `auto` lit ces valeurs dans les réglages de l’interface.

La recherche CLI cible la France par défaut. `find --international` retire le filtre géographique. L'ancienne option `--paris` reste disponible en CLI, mais ne peut pas être combinée avec `--international`. Les anciens réglages France ou Paris de l'interface deviennent France, en conservant les autres paramètres.

## Ciblage et score

Le score, entre 0 et 100, correspond au ciblage initial du projet : décideurs de startups et PME susceptibles de recourir à un freelance en développement, automatisation ou IA. Il ne mesure pas universellement la pertinence pour n'importe quels mots-clés.

| Signal | Points |
|---|---|
| Fondateur, CEO, gérant, dirigeant, président | +40 |
| Sales, business developer, growth | +40 |
| Produit ou opérations / CTO | +25 / +15 |
| Startup, SaaS, PME, e-commerce, agence, B2B | +15 |
| Indice de besoin dans le titre | +10 |
| Relations en commun | Jusqu'à +10 |
| Publication récente, 7 jours / 30 jours | +20 / +10 |
| Aucune publication depuis plus de 90 jours | −15 |
| Développeur ou freelance / étudiant | −50 / −50 |
| Recruteur / recherche d'emploi / grand groupe | −40 / −30 / −20 |

Le seuil par défaut est **40**, modifiable dans les réglages avancés de l’interface. Les segments prédéfinis `founder`, `pme`, `sales`, `growth` et `entrepreneur` restent disponibles uniquement pour les recherches CLI qui les demandent.

Les règles avancées restent en haut de `linkedin_connect.py` : `QUERIES`, `SIGNALS`, `PENALTIES`, `NOTE_TEMPLATE`, `DAILY_CAP`, `WEEKLY_CAP`, `DELAY_RANGE` et `MIN_SCORE`. Après une modification du score, utilisez l'action de recalcul. Le texte de la note reste défini par `NOTE_TEMPLATE` ; l’interface permet de le consulter et de choisir de le joindre ou non.

## Dépannage

| Problème | Solution |
|---|---|
| Le navigateur ne s'ouvre pas automatiquement | Ouvrez l'adresse affichée dans le terminal, normalement `http://127.0.0.1:8765`. |
| Le port est déjà occupé | Arrêtez l'ancienne instance ou utilisez `./install.sh --port 8877`. |
| Téléchargement interrompu ou réseau indisponible | Rétablissez la connexion puis relancez `./install.sh`. Les dépendances présentes sont conservées. |
| Permission refusée pour `./install.sh` après extraction d'une archive | Utilisez `bash install.sh`, ou restaurez le droit d'exécution avec `chmod +x install.sh`. |
| Session LinkedIn expirée | Reconnectez LinkedIn depuis l’interface ; les prochaines exécutions pourront réutiliser la session. |
| Aucun envoi automatique | Vérifiez l’état actif, les mots-clés, les jours et l’heure, la dernière exécution et le journal `auto.log`. Le Mac et la session macOS doivent être disponibles. |
| Activation impossible | Consultez le message de l’interface. La planification exige macOS, des mots-clés et aucun autre travail LinkedIn en cours. |
| Limite ou vérification LinkedIn détectée | Arrêtez les actions et effectuez manuellement la vérification demandée par LinkedIn. |
| Recherche vide ou nombreux statuts `no_connect_button` / `error_*` | Vérifiez les mots-clés, les filtres et la session ; les sélecteurs peuvent aussi nécessiter une adaptation à LinkedIn. |
| Aucun rapport e-mail | Vérifiez la configuration facultative `.env` et le journal du mode `auto`. |
| Bibliothèques Chromium manquantes sous Linux | Sur une distribution prise en charge, exécutez `.venv/bin/python -m playwright install-deps chromium`, puis relancez. Cette étape peut nécessiter les droits administrateur. Le lanceur n'utilise pas `sudo` automatiquement. |
| Linux sans affichage graphique | Utilisez une session de bureau : la connexion manuelle nécessite une fenêtre Chromium. |

## Vérifications et améliorations restantes

La [revue qualité de la version 1.0.0](docs/QUALITE.md) décrit les corrections UX, les parcours vérifiés et les limites de ces essais.

Les tests isolés s'exécutent avec :

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Ils vérifient les comportements locaux avec des données temporaires et des processus simulés. Ils n'utilisent pas votre session LinkedIn et n'envoient aucune invitation. Le succès des tests ne valide pas le fonctionnement des sélecteurs sur le site LinkedIn actuel.

Pour vérifier aussi l'interface dans Chromium avec des profils fictifs et sans accès externe :

```bash
.venv/bin/python scripts/smoke_ui.py
```

Ce contrôle utilise une planification simulée et des profils fictifs pour vérifier la configuration, le test de recherche sans envoi et l’affichage mobile. Les tests du planificateur vérifient les horaires, les sept jours ou les jours ouvrés, la pause et la restauration après échec. Aucune tâche launchd réelle ni invitation LinkedIn n’est déclenchée par les tests.

Pour inspecter les mêmes données fictives dans un navigateur déjà ouvert, utilisez `.venv/bin/python scripts/smoke_ui.py --serve`, puis ouvrez `http://127.0.0.1:8878`. Le bandeau « Mode démonstration » confirme que cette page ne pilote pas votre compte. Les tests GitHub Actions exécutent aussi le parcours de l’interface dans un navigateur de test isolé.

Les principales améliorations apportées sont le démarrage en une commande, l’installation de Python si nécessaire, la configuration centralisée dans l’interface et l’envoi quotidien limité aux recherches actuelles. Le contrôle de concurrence empêche la CLI, l’interface et l’automatisation d’utiliser simultanément la même session.

Les limites qui restent à traiter sont la fragilité des sélecteurs LinkedIn, les règles de score et le texte de la note encore définis dans le code, et la reproductibilité des versions mineures des dépendances.

Le statut `sent` est enregistré après le clic d'envoi, sans confirmation indépendante du serveur LinkedIn. Une interruption au moment du clic peut donc laisser un résultat ambigu : vérifiez sur LinkedIn avant de retenter. Les plafonds se fondent sur le journal local et ne comptabilisent pas toutes les invitations envoyées manuellement ailleurs.

Le fonctionnement dépend d'une session LinkedIn valide et de son interface, qui peut changer. LinkedIn interdit les outils d'automatisation non autorisés et peut restreindre le compte ; les pauses et plafonds du projet ne constituent pas une garantie. Voir [l'aide officielle sur les logiciels interdits](https://www.linkedin.com/help/linkedin/answer/a1341387).

</details>
