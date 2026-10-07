# LinkedIn Connector

Prospection LinkedIn automatisée pour freelances : trouve les profils les plus pertinents
(fondateurs, dirigeants de PME/startups, sales), les note, leur envoie une demande de
connexion chaque jour ouvré et t'envoie un rapport par e-mail.

- 🎯 **Ciblage** : recherches LinkedIn prédéfinies + score de pertinence (0–100)
- 🟢 **Activité** : bonus pour les profils qui publient régulièrement
- 🛡️ **Prudent** : 20 invitations/jour, 80/semaine, pauses aléatoires, arrêt automatique si LinkedIn bloque
- 🔐 **Sans mot de passe** : tu te connectes toi-même une fois, la session reste sur ton Mac
- 📬 **Rapport e-mail** (via [Resend](https://resend.com)) après chaque exécution

> ⚠️ L'automatisation est contraire aux conditions d'utilisation de LinkedIn et peut
> entraîner une restriction du compte. Garde des volumes bas.

---

## Prérequis

- macOS
- Python 3.10+ : `brew install python`
- Un compte [Resend](https://resend.com) (gratuit) pour les rapports e-mail

## Installation

```bash
git clone git@github.com:wael-chatoui/Linkedin-connector.git
cd Linkedin-connector
./install.sh
```

Le script crée l'environnement Python, installe Chromium et planifie l'exécution
**du lundi au vendredi à 10h30, et au démarrage du Mac** (rattrapage, voir plus bas). Autre horaire : `HOUR=9 MINUTE=0 ./install.sh`.
Sans planification : `./install.sh --no-schedule`.

Ensuite :

1. **Configure les e-mails** dans `.env` :

   | Variable | Valeur |
   |---|---|
   | `RESEND_API_KEY` | clé créée sur [resend.com/api-keys](https://resend.com/api-keys) |
   | `NOTIFY_EMAIL` | adresse qui reçoit les rapports (sans domaine vérifié : l'adresse de ton compte Resend) |
   | `RESEND_FROM` | optionnel, expéditeur sur un domaine vérifié dans Resend |

2. **Connecte-toi à LinkedIn** (une seule fois, dans la fenêtre qui s'ouvre) :

   ```bash
   .venv/bin/python linkedin_connect.py login
   ```

3. **Teste** :

   ```bash
   .venv/bin/python linkedin_connect.py find --pages 1    # cherche et note des prospects
   .venv/bin/python linkedin_connect.py run --dry-run     # aperçu, n'envoie rien
   .venv/bin/python linkedin_connect.py run --limit 3     # 3 vraies invitations
   ```

## Fonctionnement automatique

Chaque jour ouvré, `linkedin_connect.py auto` :

1. lance 2 nouvelles recherches (rotation quotidienne) s'il reste moins de 30 prospects éligibles ;
2. vérifie l'activité récente des 20 meilleurs prospects ;
3. envoie jusqu'à 15 invitations, meilleurs scores d'abord ;
4. envoie un rapport e-mail (ou une alerte ⚠️ si la session a expiré ou si LinkedIn bloque).

Une fenêtre Chromium s'ouvre pendant ~30–45 min : ne la ferme pas.

**Aucun jour manqué** : la tâche se déclenche à 10h30 *et* à chaque démarrage du Mac
(ouverture de session). Elle vérifie d'abord si elle a déjà tourné aujourd'hui
(`linkedin_data/last_auto_run.txt`) :

| Situation | Comportement |
|---|---|
| Mac allumé à 10h30 | exécution à 10h30 |
| Mac éteint à 10h30, allumé à 14h | exécution au démarrage, à 14h |
| Mac démarré à 8h | exécution à 8h, celle de 10h30 est ignorée |
| Mac en veille à 10h30 | exécution au réveil |
| Week-end | rien |
| Pas de réseau au démarrage | attend jusqu'à 3 min, sinon réessaie au prochain déclenchement |
| Session LinkedIn expirée | e-mail d'alerte, réessaie au prochain déclenchement |

Pour forcer une exécution (même déjà faite aujourd'hui ou le week-end) :
`.venv/bin/python linkedin_connect.py auto --force`

| Action | Commande |
|---|---|
| Lancer maintenant | `launchctl kickstart gui/$(id -u)/com.$(whoami).linkedin-connector` |
| Voir le journal | `tail -f linkedin_data/auto.log` |
| Changer l'horaire | `HOUR=9 MINUTE=0 ./install.sh` |
| Arrêter l'automatisation | `./uninstall.sh` |

## Commandes

```bash
.venv/bin/python linkedin_connect.py <commande> [options]
```

| Commande | Rôle | Options utiles |
|---|---|---|
| `login` | Connexion manuelle à LinkedIn | |
| `find` | Recherche de personnes → `linkedin_data/prospects.csv` | `--segments founder,sales` · `--keywords "..."` · `--pages 2` · `--paris` · `--network S,O` |
| `enrich` | Vérifie la date du dernier post des meilleurs prospects | `--top 30` |
| `run` | Envoie les invitations, meilleurs scores d'abord | `--limit 15` · `--dry-run` · `--active-only` · `--note` · `--min-score 40` |
| `auto` | Pipeline complet + e-mail, une fois par jour ouvré (utilisé par la tâche planifiée) | `--force` · `--limit` · `--enrich` · `--note` |
| `rescore` | Recalcule les scores après modification des règles | |

Segments disponibles : `founder`, `pme`, `sales`, `growth`, `entrepreneur`.

## Score de pertinence

Calculé à partir du titre du profil :

| Signal | Points |
|---|---|
| Fondateur, CEO, gérant, dirigeant, président | +40 |
| Sales, business developer, growth | +40 |
| Produit / opérations · CTO | +25 · +15 |
| Startup, SaaS, PME, e-commerce, agence, B2B | +15 |
| Besoin (« on recrute », MVP, levée de fonds, automatisation…) | +10 |
| Relations en commun | jusqu'à +10 |
| A publié il y a < 7 j · < 30 j · aucun post depuis 90 j | +20 · +10 · −15 |
| Développeur/freelance · étudiant · recruteur · « open to work » · grand groupe | −50 · −50 · −40 · −30 · −20 |

Seuls les profils notés **≥ 40** reçoivent une invitation.

## Personnalisation

Tout se règle en haut de `linkedin_connect.py` :

| Constante | Rôle |
|---|---|
| `QUERIES` | recherches LinkedIn par segment |
| `SIGNALS` / `PENALTIES` | règles de score (puis `rescore`) |
| `NOTE_TEMPLATE` | note jointe avec `--note` (les comptes gratuits en ont très peu par mois) |
| `DAILY_CAP` / `WEEKLY_CAP` | plafonds d'invitations |
| `DELAY_RANGE` | pause entre deux invitations (secondes) |
| `MIN_SCORE` | score minimum pour inviter |

## Données

Tout reste en local dans `linkedin_data/` (ignoré par git) :

| Fichier | Contenu |
|---|---|
| `prospects.csv` | prospects, score, raison du score, statut d'envoi |
| `sent_log.csv` | historique des invitations (sert aux plafonds) |
| `auto.log` | journal des exécutions automatiques |
| `last_auto_run.txt` | date de la dernière exécution automatique réussie |
| `.profile/` | session du navigateur |

Colonne `status` : `sent`, `pending`, `no_connect_button` (déjà connecté ou profil
« Suivre »), `email_required`, `not_found`, `error_*`. Vide la case pour retenter un profil.

**Changer de Mac** : copie `prospects.csv` et `sent_log.csv` dans `linkedin_data/` pour
garder l'historique, puis refais `login` (la session ne se transfère pas).

## Dépannage

| Problème | Solution |
|---|---|
| E-mail « Session LinkedIn expirée » | relance `login` |
| « Limite ou vérification LinkedIn détectée » | LinkedIn a demandé une vérification ou la limite hebdo est atteinte : fais la vérification dans le navigateur et attends quelques jours |
| Beaucoup de `no_connect_button` / `error_*` | LinkedIn a probablement changé son interface : les sélecteurs sont dans `send_invite()` |
| Pas d'e-mail | vérifie `.env` et le journal `linkedin_data/auto.log` |
| Rien ne s'est lancé | `launchctl print gui/$(id -u)/com.$(whoami).linkedin-connector` |
