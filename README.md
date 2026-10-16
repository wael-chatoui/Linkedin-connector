# LinkedIn Connector

Une interface locale pour rechercher des profils LinkedIn par mots-clés, consulter leur score et choisir les personnes à inviter. La session LinkedIn et les prospects restent sur votre ordinateur.

## Démarrer avec une seule commande

Dans le dossier du projet, exécutez :

```bash
./install.sh
```

Le script installe les dépendances manquantes, prépare Chromium puis ouvre votre navigateur sur [http://127.0.0.1:8765](http://127.0.0.1:8765). Si Python 3.10 ou plus récent n'est pas disponible, il installe un Python local au projet. Vous n'avez pas besoin de Homebrew, de Node.js ou de configurer un service d'e-mail.

Gardez le terminal ouvert pendant l'utilisation. **Ctrl+C** arrête le serveur. La même commande relance l'application les fois suivantes, sans mettre systématiquement à jour les dépendances ni les retélécharger.

Si le projet n'est pas encore sur votre ordinateur, téléchargez-le depuis GitHub ou clonez-le une fois :

```bash
git clone https://github.com/wael-chatoui/Linkedin-connector.git
cd Linkedin-connector
./install.sh
```

Le parcours principal vise **macOS 14 ou plus récent**, avec une connexion Internet pour la première installation et les actions LinkedIn. Une session graphique est nécessaire. Le lanceur fonctionne aussi sous Linux, avec les réserves indiquées dans « Dépannage ». Windows natif n'est pas pris en charge par ce script.

## Utiliser l'interface

1. **Connectez LinkedIn** depuis la page locale. Une fenêtre Chromium s'ouvre : connectez-vous vous-même et effectuez les éventuelles vérifications demandées par LinkedIn. L'application ne demande jamais votre mot de passe.
2. **Saisissez vos mots-clés**, par exemple `fondateur agence immobilière`, choisissez les options de recherche puis lancez la recherche.
3. **Consultez les résultats** : identité, titre, score et statut. Vous pouvez vérifier l'activité récente et recalculer les scores.
4. **Sélectionnez les profils** à inviter et consultez l'aperçu. L'aperçu n'envoie aucune invitation.
5. **Lancez explicitement l'envoi** après avoir vérifié votre sélection et la limite affichée. Le journal permet de suivre la progression.

L'installation, l'ouverture de la page et une recherche ne déclenchent aucun envoi. La recherche utilise LinkedIn dans une fenêtre Chromium distincte de l'onglet de contrôle. L'authentification LinkedIn reste une étape manuelle.

Les invitations sont soumises au score minimum, aux statuts déjà enregistrés et aux plafonds du projet : **20 par jour et 80 sur sept jours glissants**, avec des pauses entre les actions. Ces limites locales ne garantissent pas l'absence de restriction par LinkedIn. LinkedIn peut demander une vérification, faire expirer la session ou modifier son interface.

## Options du lanceur

| Besoin | Commande |
|---|---|
| Installer et ouvrir l'application | `./install.sh` |
| Utiliser un autre port | `./install.sh --port 8877` |
| Garder le serveur sans ouverture automatique d'onglet | `./install.sh --no-browser` |
| Installer seulement, sans lancer le serveur | `./install.sh --install-only` |
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
| `linkedin_data/settings.json` | Mots-clés et réglages de l'interface locale |
| `linkedin_data/.profile/` | Session Chromium, notamment les cookies LinkedIn |
| `linkedin_data/auto.log` | Journal de l'automatisation optionnelle |
| `linkedin_data/last_auto_run.txt` | Dernière exécution automatique réussie |
| `.env` | Configuration optionnelle des e-mails |
| `.venv/`, `.runtime/` | Environnement Python et outils d'installation |

Protégez le dossier `.profile/` comme une session de compte. Pour changer d'ordinateur, copiez les CSV afin de conserver les prospects et l'historique, puis connectez-vous à nouveau à LinkedIn. Ne supprimez pas l'historique pour contourner les plafonds.

## Rapports e-mail facultatifs

L'application fonctionne sans `.env` et sans compte Resend. Pour les rapports du mode `auto`, créez `.env` à partir de `.env.example` et renseignez :

| Variable | Valeur |
|---|---|
| `RESEND_API_KEY` | Clé API Resend |
| `NOTIFY_EMAIL` | Adresse de destination du rapport |
| `RESEND_FROM` | Expéditeur facultatif sur un domaine vérifié dans Resend |

Le lanceur ne crée ni ne modifie `.env`. Consultez [Resend](https://resend.com) pour configurer l'envoi et les restrictions de votre compte.

## Planification quotidienne, uniquement sur demande

**Aucune nouvelle tâche planifiée n'est activée par `./install.sh`.** Si une ancienne version a déjà installé une tâche launchd, elle continue de fonctionner : le lanceur et l'interface vous le signalent. Pour la retirer tout en conservant vos données :

```bash
./uninstall.sh
```

Pour activer volontairement le pipeline quotidien sous macOS :

```bash
./install.sh --schedule
```

La tâche se lance du lundi au vendredi à **10h30**, sans exécution immédiate lors de l'installation ni lancement à chaque ouverture de session. Pour un autre horaire :

```bash
HOUR=9 MINUTE=0 ./install.sh --schedule
```

Cette option utilise le mode historique `auto` : recherches prédéfinies en rotation si le stock est bas, vérification d'activité, puis jusqu'à 15 invitations aux profils éligibles et rapport e-mail si configuré. **Ce mode n'utilise pas la sélection manuelle ni les mots-clés saisis dans l'interface.** Il peut envoyer des invitations sans validation quotidienne. Le terminal du serveur web peut être fermé ; le compte doit rester connecté dans Chromium et la session macOS doit pouvoir ouvrir une fenêtre graphique.

La tâche conserve le contrôle « une exécution réussie par jour ouvré ». Elle n'assure pas le rattrapage des jours où l'ordinateur était éteint. Les anciennes tâches peuvent encore être configurées pour se lancer à l'ouverture de session jusqu'à leur suppression ou leur remplacement explicite avec `--schedule`.

| Action avancée | Commande |
|---|---|
| Voir le journal automatique | `tail -f linkedin_data/auto.log` |
| Inspecter la tâche | `launchctl print gui/$(id -u)/com.$(whoami).linkedin-connector` |
| Supprimer la tâche | `./uninstall.sh` |

## Commandes avancées

L'interface remplace les commandes pour l'utilisation courante. La CLI reste disponible :

```bash
.venv/bin/python linkedin_connect.py --help
```

| Commande | Rôle | Options utiles |
|---|---|---|
| `login` | Connexion manuelle à LinkedIn | |
| `find` | Recherche et enregistrement des prospects | `--keywords "..."`, `--segments founder,sales`, `--pages 2`, `--paris`, `--network S,O` |
| `enrich` | Vérification de l'activité récente | `--top 30`, `--min-score 40` |
| `run` | Invitations aux prospects éligibles | `--dry-run`, `--limit 3`, `--active-only`, `--min-score 40`, `--note` |
| `auto` | Pipeline quotidien avec envoi, sans sélection manuelle | `--force`, `--limit`, `--enrich`, `--note` |
| `rescore` | Recalcul des scores | |

Exemple sans envoi :

```bash
.venv/bin/python linkedin_connect.py run --dry-run
```

`run` sans `--dry-run` et `auto` effectuent de vrais envois. `auto --force` ignore le contrôle du jour ouvré et de l'exécution quotidienne, mais pas les plafonds d'invitations.

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

Le seuil par défaut est **40**. Les segments prédéfinis sont `founder`, `pme`, `sales`, `growth` et `entrepreneur`.

Les règles avancées restent en haut de `linkedin_connect.py` : `QUERIES`, `SIGNALS`, `PENALTIES`, `NOTE_TEMPLATE`, `DAILY_CAP`, `WEEKLY_CAP`, `DELAY_RANGE` et `MIN_SCORE`. Après une modification du score, utilisez l'action de recalcul. La note personnalisée du mode CLI nécessite aussi d'adapter son texte à votre activité.

## Dépannage

| Problème | Solution |
|---|---|
| Le navigateur ne s'ouvre pas automatiquement | Ouvrez l'adresse affichée dans le terminal, normalement `http://127.0.0.1:8765`. |
| Le port est déjà occupé | Arrêtez l'ancienne instance ou utilisez `./install.sh --port 8877`. |
| Téléchargement interrompu ou réseau indisponible | Rétablissez la connexion puis relancez `./install.sh`. Les dépendances présentes sont conservées. |
| Permission refusée pour `./install.sh` après extraction d'une archive | Utilisez `bash install.sh`, ou restaurez le droit d'exécution avec `chmod +x install.sh`. |
| Session LinkedIn expirée | Reconnectez LinkedIn depuis l'interface. |
| Limite ou vérification LinkedIn détectée | Arrêtez les actions et effectuez manuellement la vérification demandée par LinkedIn. |
| Recherche vide ou nombreux statuts `no_connect_button` / `error_*` | Vérifiez les mots-clés, les filtres et la session ; les sélecteurs peuvent aussi nécessiter une adaptation à LinkedIn. |
| Aucun rapport e-mail | Vérifiez la configuration facultative `.env` et le journal du mode `auto`. |
| Bibliothèques Chromium manquantes sous Linux | Sur une distribution prise en charge, exécutez `.venv/bin/python -m playwright install-deps chromium`, puis relancez. Cette étape peut nécessiter les droits administrateur. Le lanceur n'utilise pas `sudo` automatiquement. |
| Linux sans affichage graphique | Utilisez une session de bureau : la connexion manuelle nécessite une fenêtre Chromium. |

## Vérifications et améliorations restantes

Les tests isolés s'exécutent avec :

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Ils vérifient les comportements locaux avec des données temporaires et des processus simulés. Ils n'utilisent pas votre session LinkedIn et n'envoient aucune invitation. Le succès des tests ne valide pas le fonctionnement des sélecteurs sur le site LinkedIn actuel.

Pour vérifier aussi l'interface dans Chromium avec des profils fictifs et sans accès externe :

```bash
.venv/bin/python scripts/smoke_ui.py
```

Ce contrôle couvre l'état vide, la sauvegarde des réglages, la sélection, un aperçu réel sans envoi, l'annulation de la confirmation et l'affichage mobile. Il écrit deux captures de test dans `/tmp/`.

Les principales améliorations apportées sont le démarrage en une commande, l'installation de Python si nécessaire, l'interface locale, la sélection explicite avant envoi, la gestion des actions longues et la désactivation de toute nouvelle planification par défaut.

Les limites qui restent à traiter sont la fragilité des sélecteurs LinkedIn, le score et la note encore définis dans le code, et la reproductibilité des versions mineures des dépendances. Les recherches enrichissent un CSV commun, ce qui rend nécessaire la sélection explicite des profils à chaque envoi.

Le statut `sent` est enregistré après le clic d'envoi, sans confirmation indépendante du serveur LinkedIn. Une interruption au moment du clic peut donc laisser un résultat ambigu : vérifiez sur LinkedIn avant de retenter. Les plafonds se fondent sur le journal local et ne comptabilisent pas toutes les invitations envoyées manuellement ailleurs.

Le fonctionnement dépend d'une session LinkedIn valide et de son interface, qui peut changer. LinkedIn interdit les outils d'automatisation non autorisés et peut restreindre le compte ; les pauses et plafonds du projet ne constituent pas une garantie. Voir [l'aide officielle sur les logiciels interdits](https://www.linkedin.com/help/linkedin/answer/a1341387).
