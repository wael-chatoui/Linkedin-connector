#!/usr/bin/env python3
"""
LinkedIn prospecting: find relevant people, score them, send connection requests.

Drives a real Chromium window (Playwright) with a persistent profile, so you log
in yourself once and the script never touches your password.

Pipeline:
    python linkedin_connect.py login                      # log in by hand once
    python linkedin_connect.py find                       # people search -> scored prospects.csv
    python linkedin_connect.py find --segments founder,sales --pages 3
    python linkedin_connect.py find --keywords "fondateur agence immobilière"
    python linkedin_connect.py find --keywords "startup founder" --international
    python linkedin_connect.py enrich --top 30            # check recent activity of best prospects
    python linkedin_connect.py run --dry-run              # preview, best scores first
    python linkedin_connect.py run --limit 15 --active-only
    python linkedin_connect.py run --limit 5 --note       # with personalised note
    python linkedin_connect.py auto                       # scheduled pipeline using interface settings

Targets: founders / CEOs / owners of startups & SMEs, sales & growth leaders —
people who could hire a freelance full-stack / automation / AI developer.
Competitors (developers, freelancers), students, recruiters are pushed down.
"""

import argparse
import csv
import json
import os
import random
import re
import socket
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout
from app_settings import SettingsError, read_settings
from runtime_lock import OperationBusy, OperationLock

load_dotenv(Path(__file__).parent / ".env")

DATA_DIR = Path(os.environ.get("LINKEDIN_DATA_DIR", Path(__file__).parent / "linkedin_data")).resolve()
PROFILE_DIR = DATA_DIR / ".profile"
PROSPECTS_CSV = DATA_DIR / "prospects.csv"
LOG_FILE = DATA_DIR / "sent_log.csv"
LAST_RUN_FILE = DATA_DIR / "last_auto_run.txt"

# LinkedIn tolerates roughly 100-200 invitations/week; stay well below.
DAILY_CAP = 20
WEEKLY_CAP = 80
DELAY_RANGE = (45, 120)  # seconds between two invitations
MIN_SCORE = 40

GEO_FRANCE = "105015875"
GEO_PARIS = "106383538"

# (segment, search keywords)
QUERIES = [
    ("founder", "fondateur startup"),
    ("founder", "co-founder SaaS"),
    ("founder", "CEO startup"),
    ("pme", "dirigeant PME"),
    ("pme", "gérant entreprise"),
    ("sales", "directeur commercial startup"),
    ("sales", "head of sales SaaS"),
    ("sales", "business developer startup"),
    ("growth", "head of growth"),
    ("entrepreneur", "entrepreneur e-commerce"),
]

# Free accounts get only a few personalised notes per month. Max 300 chars.
NOTE_TEMPLATE = (
    "Bonjour {first_name}, votre parcours m’intéresse. "
    "Au plaisir d’échanger avec vous{company_part} !"
)

FIELDS = ["url", "name", "headline", "location", "mutual", "segment",
          "score", "reasons", "last_post_days", "status", "updated_at"]


# ── scoring ───────────────────────────────────────────────────────────────────

# (weight, label, pattern) — only the best match of each group counts
SIGNALS = [
    [(40, "dirigeant", r"\b(fondat(eur|rice)|co-?fondat|founder|co-?founder|ceo|pdg|g[ée]rant|dirigeant|"
                       r"directeur g[ée]n[ée]ral|directrice g[ée]n[ée]rale|managing director|owner|pr[ée]sident)")],
    [(40, "sales", r"\b(head of sales|directeur commercial|directrice commerciale|sales director|vp sales|"
                   r"chief revenue|cro\b|cso\b|business develop|bizdev|biz dev|d[ée]veloppeu(r|se) d.affaires|head of growth|growth|"
                   r"responsable commercial|account executive|key account)"),
     (25, "produit", r"\b(cpo|head of product|product manager|chef de produit|product owner|coo\b|"
                     r"directeur des op[ée]rations|op[ée]rations)"),
     (15, "cto", r"\bcto\b")],
    [(15, "startup/pme", r"\b(startup|start-up|saas|pme|tpe|scale-?up|e-?commerce|agence|marketplace|"
                         r"proptech|fintech|legaltech|healthtech|edtech|b2b)")],
    [(10, "besoin", r"(on recrute|we.?re hiring|hiring|recrute|mvp|lev[ée]e de fonds|seed|"
                    r"digitalisation|automatisation|no-?code|lancement|en cr[ée]ation)")],
]
PENALTIES = [
    (-50, "concurrent", r"\b(d[ée]veloppeu(r|se)(?! d.affaires| commercial)|(?<!business )(?<!biz )developer|dev\b|freelance|ind[ée]pendant|software engineer|"
                        r"ing[ée]nieur logiciel|consultant (it|web|tech))"),
    (-40, "recruteur", r"\b(recruteu|recruiter|talent acquisition|chasseu|rh\b|human resources|ressources humaines)"),
    (-50, "étudiant", r"\b([ée]tudiant|student|alternan|stagiaire|intern\b|apprenti)"),
    (-30, "en recherche", r"(open to work|en recherche|looking for|disponible imm[ée]diatement)"),
    (-20, "grand groupe", r"\b(cac ?40|groupe|group\b|bnp|soci[ée]t[ée] g[ée]n[ée]rale|lvmh|orange|capgemini|accenture)"),
]


def score_row(r: dict) -> tuple[int, str]:
    text = f"{r['headline']}".lower()
    score, reasons = 0, []
    for group in SIGNALS:
        for weight, label, pat in group:
            if re.search(pat, text):
                score += weight
                reasons.append(label)
                break
    for weight, label, pat in PENALTIES:
        if re.search(pat, text):
            score += weight
            reasons.append(label)

    mutual = int(r["mutual"] or 0)
    if mutual:
        score += min(mutual * 2, 10)
        reasons.append(f"{mutual} en commun")

    if r["last_post_days"] != "":
        days = int(r["last_post_days"])
        if days <= 7:
            score += 20; reasons.append("actif <7j")
        elif days <= 30:
            score += 10; reasons.append("actif <30j")
        elif days > 90:
            score -= 15; reasons.append("inactif")
    return max(min(score, 100), 0), ", ".join(reasons)


def rescore(rows: list[dict]) -> None:
    for r in rows:
        s, why = score_row(r)
        r["score"], r["reasons"] = str(s), why


# ── csv / log ─────────────────────────────────────────────────────────────────

def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{k: (r.get(k) or "").strip() for k in FIELDS} for r in csv.DictReader(f)]


def save_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def sent_since(days: int) -> int:
    if not LOG_FILE.exists():
        return 0
    cutoff = date.today() - timedelta(days=days - 1)
    with LOG_FILE.open() as f:
        return sum(1 for r in csv.DictReader(f) if date.fromisoformat(r["date"]) >= cutoff)


def log_sent(url: str) -> None:
    new = not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0
    with LOG_FILE.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "url"])
        w.writerow([date.today().isoformat(), url])


def print_top(rows: list[dict], n: int = 15) -> None:
    for r in sorted(rows, key=lambda r: -int(r["score"] or 0))[:n]:
        print(f"{r['score']:>3}  {r['name'][:28]:<28} {r['headline'][:70]:<70}  [{r['reasons']}]")


# ── browser helpers ───────────────────────────────────────────────────────────

def open_context(pw):
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=False,
        viewport={"width": 1280, "height": 900},
        locale="fr-FR",
    )


def goto(page, url: str) -> None:
    """LinkedIn's SPA often aborts the initial navigation (client-side redirect)."""
    try:
        page.goto(url, wait_until="domcontentloaded")
    except PWError as e:
        if "ERR_ABORTED" not in str(e):
            raise
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PWTimeout:
        pass


def pause(a: float = 1.0, b: float = 2.5) -> None:
    time.sleep(random.uniform(a, b))


def is_logged_in(page) -> bool:
    goto(page, "https://www.linkedin.com/feed/")
    pause(2, 3)
    parsed = urlsplit(page.url)
    return (parsed.scheme == "https" and parsed.hostname == "www.linkedin.com"
            and parsed.path.rstrip("/") == "/feed")


def blocked(page) -> bool:
    return "checkpoint" in page.url or "authwall" in page.url or page.get_by_text(
        re.compile(r"limite hebdomadaire|weekly invitation limit|limite de recherche|commercial use limit",
                   re.I)).count() > 0


class Blocked(Exception):
    """LinkedIn security check, weekly limit or search limit."""


class NotLoggedIn(Exception):
    pass


def check_blocked(page) -> None:
    if blocked(page):
        raise Blocked(f"Limite ou vérification LinkedIn détectée ({page.url})")


def start(pw):
    ctx = open_context(pw)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        logged_in = is_logged_in(page)
        check_blocked(page)
        if not logged_in:
            raise NotLoggedIn("Session LinkedIn expirée, relance `python linkedin_connect.py login`.")
    except BaseException:
        ctx.close()
        raise
    return ctx, page


# ── find: people search ───────────────────────────────────────────────────────

EXTRACT_JS = """
() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('main a[href*="/in/"]')) {
    const li = a.closest('li, [role="listitem"]');
    if (!li || seen.has(li)) continue;
    const m = a.href.match(/linkedin\\.com\\/in\\/([^/?#]+)/);
    if (!m) continue;
    seen.add(li);
    out.push({slug: decodeURIComponent(m[1]), text: li.innerText});
  }
  return out;
}
"""

NOISE_RE = re.compile(
    r"^[•·]|^(1er|2e|3e\+?|1st|2nd|3rd\+?)$|relation de|degree connection|Voir le profil|View .*profile|"
    r"^(Se connecter|Connect|Suivre|Follow|Message|En attente|Pending|Statut.*|Status.*)$", re.I)
MUTUAL_RE = re.compile(r"en commun|mutual", re.I)


def parse_card(text: str) -> dict | None:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    mutual = 0
    kept = []
    for l in lines:
        if MUTUAL_RE.search(l):
            # "A, B et 4 autres relations…" / "A et B sont des relations…" / "A est une relation…"
            n = re.search(r"(\d+)\s+autres?|(\d+)\s+other", l)
            named = l.count(",") + (1 if re.search(r"\s(et|and)\s", l) else 0) + 1
            mutual = int(n.group(1) or n.group(2)) + named - 1 if n else named
        elif not NOISE_RE.search(l):
            kept.append(l)
    if len(kept) < 2 or kept[0].lower().startswith(("membre linkedin", "linkedin member")):
        return None
    return {
        "name": re.sub(r"\s*[•·].*$", "", kept[0]).strip(),
        "headline": kept[1],
        "location": kept[2] if len(kept) > 2 else "",
        "mutual": str(mutual),
    }


def search_url(keywords: str, page_no: int, network: str, geo: str | None) -> str:
    nets = quote("[" + ",".join(f'"{n}"' for n in network.split(",")) + "]")
    geo_filter = "&geoUrn=" + quote(f'["{geo}"]') if geo else ""
    return ("https://www.linkedin.com/search/results/people/"
            f"?keywords={quote(keywords)}&network={nets}{geo_filter}"
            f"&origin=FACETED_SEARCH&page={page_no}")


def find_prospects(page, rows: list[dict], queries, pages: int, network: str, geo: str | None,
                   found_urls: set[str] | None = None) -> int:
    """Append prospects and optionally collect all URLs matching this search, including known ones."""
    known = {r["url"] for r in rows}
    added = 0
    for segment, kw in queries:
        for p in range(1, pages + 1):
            goto(page, search_url(kw, p, network, geo))
            pause(3, 5)
            for _ in range(4):  # lazy-loaded results
                page.mouse.wheel(0, 900)
                pause(0.6, 1.2)
            check_blocked(page)
            cards = page.evaluate(EXTRACT_JS)
            if not cards:
                break
            for c in cards:
                url = f"https://www.linkedin.com/in/{c['slug']}/"
                if found_urls is not None:
                    found_urls.add(url)
                info = parse_card(c["text"])
                if not info or url in known:
                    continue
                known.add(url)
                row = {"url": url, "segment": segment, "score": "", "reasons": "",
                       "last_post_days": "", "status": "", "updated_at": "", **info}
                rescore([row])
                rows.append(row)
                added += 1
            save_rows(PROSPECTS_CSV, rows)
            print(f"[{segment}] « {kw} » p{p} : {len(cards)} résultats")
            pause(4, 8)
    return added


def cmd_find(args) -> None:
    if args.keywords:
        queries = [("custom", line.strip()) for line in args.keywords.splitlines() if line.strip()]
    else:
        wanted = set(args.segments.split(",")) if args.segments else None
        queries = [q for q in QUERIES if not wanted or q[0] in wanted]

    rows = load_rows(PROSPECTS_CSV)
    geo = None if getattr(args, "international", False) else (GEO_PARIS if args.paris else GEO_FRANCE)
    found_urls = set()
    with sync_playwright() as pw:
        ctx, page = start(pw)
        try:
            added = find_prospects(page, rows, queries, args.pages, args.network, geo,
                                   found_urls=found_urls)
        finally:
            ctx.close()

    rescore(rows)
    save_rows(PROSPECTS_CSV, rows)
    snapshot = {
        "keywords": [keywords for _segment, keywords in queries],
        "geography": "international" if geo is None else ("paris" if geo == GEO_PARIS else "france"),
        "urls": sorted(found_urls),
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".last-search-",
                                         suffix=".tmp", dir=DATA_DIR, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(snapshot, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(DATA_DIR / "last_search.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"\n+{added} nouveaux prospects ({len(rows)} au total) → {PROSPECTS_CSV}\n")
    print_top([r for r in rows if r["url"] in found_urls and not r["status"]])


# ── enrich: recent activity ───────────────────────────────────────────────────

AGO_RE = re.compile(r"(?<!\w)(\d{1,2})\s?(min|h|j|sem|mois|ans?|d|w|mo|yrs?|y)\s?•", re.I)
UNIT_DAYS = {"min": 0, "h": 0, "j": 1, "d": 1, "sem": 7, "w": 7, "mois": 30, "mo": 30,
             "an": 365, "ans": 365, "y": 365, "yr": 365, "yrs": 365}


def last_post_days(page, url: str) -> int:
    goto(page, url + "recent-activity/all/")
    pause(3, 5)
    check_blocked(page)
    page.mouse.wheel(0, 800)
    pause(1, 2)
    text = page.locator("main").inner_text(timeout=10000)
    days = [int(n) * UNIT_DAYS[u.lower()] for n, u in AGO_RE.findall(text) if u.lower() in UNIT_DAYS]
    return min(days) if days else 999


def enrich_rows(page, rows: list[dict], top: int, min_score: int,
                candidates: list[dict] | None = None) -> int:
    """Fills last_post_days for the best unchecked prospects. Returns how many were checked."""
    source = rows if candidates is None else candidates
    todo = sorted((r for r in source if not r["status"] and r["last_post_days"] == ""
                   and int(r["score"] or 0) >= min_score),
                  key=lambda r: -int(r["score"] or 0))[:top]
    print(f"Vérification de l'activité de {len(todo)} profils…")
    done = 0
    for r in todo:
        try:
            days = last_post_days(page, r["url"])
        except Blocked:
            raise
        except Exception as e:
            check_blocked(page)
            print(f"  erreur {type(e).__name__} : {r['url']}")
            continue
        check_blocked(page)
        r["last_post_days"] = str(days)
        rescore([r])
        save_rows(PROSPECTS_CSV, rows)
        done += 1
        print(f"  {r['last_post_days']:>3} j  {r['score']:>3}  {r['name']}")
        pause(6, 15)
    return done


def cmd_enrich(args) -> None:
    rows = load_rows(PROSPECTS_CSV)
    with sync_playwright() as pw:
        ctx, page = start(pw)
        try:
            enrich_rows(page, rows, args.top, args.min_score)
        finally:
            ctx.close()
    print()
    print_top([r for r in rows if not r["status"]])


# ── run: send invitations ─────────────────────────────────────────────────────

CONNECT_RE = re.compile(r"^(Se connecter|Connect)$", re.I)
MORE_RE = re.compile(r"^(Plus|More)$|Plus d.actions|More actions", re.I)
PENDING_RE = re.compile(r"(En attente|Pending|retirer l.invitation|withdraw invitation)", re.I)
ADD_NOTE_RE = re.compile(r"(Ajouter une note|Add a note)", re.I)
SEND_NO_NOTE_RE = re.compile(r"(Envoyer sans note|Send without a note)", re.I)
SEND_RE = re.compile(r"^(Envoyer|Send)$|Envoyer l.invitation|Send invitation", re.I)


def company_from(headline: str) -> str:
    m = re.search(r"(?:\bchez\b|\bat\b|@)\s*([^|,•·–\-]+)", headline, re.I)
    return m.group(1).strip() if m else ""


def any_role(scope, name_re):
    return (scope.get_by_role("button", name=name_re)
            .or_(scope.get_by_role("link", name=name_re))
            .or_(scope.get_by_role("menuitem", name=name_re)))


def first_visible(loc):
    for i in range(min(loc.count(), 8)):
        if loc.nth(i).is_visible():
            return loc.nth(i)
    return None


def send_invite(page, row: dict, with_note: bool) -> str:
    """Returns the new status for the row."""
    goto(page, row["url"])
    pause(3, 5)
    check_blocked(page)
    if "/in/" not in page.url:
        return "not_found"

    top = page.locator("main section").first  # profile top card, not sidebar suggestions

    if any_role(top, PENDING_RE).count():
        return "pending"

    # "Inviter <Nom> à rejoindre votre réseau" — named after the prospect, so
    # sidebar suggestions ("Les membres ont aussi consulté") are never matched.
    name = re.escape(row["name"].split(",")[0].strip())
    invite_re = re.compile(rf"Inviter {name} à rejoindre|Invite {name} to connect", re.I)

    btn = first_visible(any_role(page, invite_re)) or first_visible(any_role(top, CONNECT_RE))
    if not btn:
        more = first_visible(top.get_by_role("button", name=MORE_RE))
        if not more:
            return "no_connect_button"
        more.click()
        pause()
        btn = first_visible(any_role(page, invite_re))
        if not btn:
            page.keyboard.press("Escape")
            return "no_connect_button"  # already connected, or follow-only profile
    btn.click()
    pause()

    dialog = page.get_by_role("dialog").last
    try:
        dialog.wait_for(timeout=8000)
    except PWTimeout:
        return "error_no_dialog"

    if dialog.locator("input[type=email], input[name=email]").count():
        page.keyboard.press("Escape")
        return "email_required"

    if with_note and dialog.get_by_role("button", name=ADD_NOTE_RE).count():
        dialog.get_by_role("button", name=ADD_NOTE_RE).first.click()
        pause()
        company = company_from(row["headline"])
        note = NOTE_TEMPLATE.format(
            first_name=row["name"].split()[0] if row["name"] else "",
            company_part=f" et d'en savoir plus sur {company}" if company else "",
        )[:300]
        box = dialog.locator("textarea").first
        box.click()
        box.type(note, delay=random.randint(25, 60))
        pause()
        send = dialog.get_by_role("button", name=SEND_RE).first
    else:
        send = dialog.get_by_role("button", name=SEND_NO_NOTE_RE).first
        if not send.count():
            send = dialog.get_by_role("button", name=SEND_RE).first

    if not send.count() or send.is_disabled():
        # note quota reached on free accounts -> "Send" stays disabled
        page.keyboard.press("Escape")
        return "error_send_disabled"
    send.click()
    pause(2, 3)
    return "sent"


def eligible(rows: list[dict], min_score: int, active_only: bool = False) -> list[dict]:
    todo = [r for r in rows if not r["status"] and int(r["score"] or 0) >= min_score]
    if active_only:
        todo = [r for r in todo if r["last_post_days"] != "" and int(r["last_post_days"]) <= 30]
    return sorted(todo, key=lambda r: -int(r["score"] or 0))


def remaining_budget(limit: int) -> int:
    return max(min(limit, DAILY_CAP - sent_since(1), WEEKLY_CAP - sent_since(7)), 0)


def send_batch(page, rows: list[dict], todo: list[dict], budget: int, note: bool,
               results: list[dict], before_send=None) -> None:
    """Sends up to `budget` invitations; appends every processed row to `results`."""
    sent = 0
    for r in todo:
        if sent >= budget:
            break
        if before_send is not None:
            before_send()
        try:
            status = send_invite(page, r, note)
        except Blocked:
            raise
        except Exception as e:  # keep going on UI surprises
            status = f"error_{type(e).__name__}"

        r["status"] = status
        r["updated_at"] = datetime.now().isoformat(timespec="seconds")
        results.append(r)
        if status == "sent":
            sent += 1
            # Record the invitation before another page check or CSV write can
            # fail. A later security check must not erase it from the caps.
            log_sent(r["url"])
        save_rows(PROSPECTS_CSV, rows)
        check_blocked(page)
        print(f"[{status}] {r['score']:>3}  {r['name']} — {r['headline'][:60]}")

        if status == "sent":
            time.sleep(random.uniform(*DELAY_RANGE))
        else:
            pause(5, 10)


def selected_rows(path: Path, rows: list[dict]) -> list[dict]:
    """Validate an explicit snapshot before opening LinkedIn or sending anything."""
    try:
        urls = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Impossible de lire la sélection de profils : {exc}") from exc
    if not isinstance(urls, list) or not urls or any(not isinstance(url, str) for url in urls):
        raise ValueError("La sélection doit être une liste JSON non vide d'URL de profils LinkedIn.")
    if len(urls) != len(set(urls)):
        raise ValueError("La sélection contient des profils en double.")
    known = {}
    for row in rows:
        known.setdefault(row["url"], []).append(row)
    chosen = []
    for url in urls:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "www.linkedin.com"
                or not re.fullmatch(r"/in/[^/?#]+/", parsed.path)
                or parsed.query or parsed.fragment):
            raise ValueError(f"URL de profil LinkedIn invalide : {url}")
        matches = known.get(url, [])
        if not matches:
            raise ValueError(f"Profil sélectionné inconnu : {url}")
        if len(matches) != 1:
            raise ValueError(f"Profil présent plusieurs fois dans les données : {url}")
        chosen.append(matches[0])
    return chosen


def cmd_run(args) -> None:
    rows = load_rows(PROSPECTS_CSV)
    selection = getattr(args, "selected_file", None)
    candidates = selected_rows(selection, rows) if selection is not None else rows
    todo = eligible(candidates, args.min_score, args.active_only)
    budget = remaining_budget(args.limit)
    print(f"{len(todo)} prospects éligibles (score ≥ {args.min_score}) · budget restant : {budget}")
    if budget <= 0:
        print("Quota jour/semaine atteint. Réessaie plus tard.")
        return

    if args.dry_run:
        print_top(todo, budget)
        return
    if not todo:
        print("Aucun profil éligible dans la sélection.")
        return

    results: list[dict] = []
    with sync_playwright() as pw:
        ctx, page = start(pw)
        try:
            send_batch(page, rows, todo, budget, args.note, results)
        finally:
            ctx.close()
    print(f"Terminé : {sum(r['status'] == 'sent' for r in results)} invitation(s) envoyée(s).")


# ── auto: daily pipeline + email report ───────────────────────────────────────

class AutomationPaused(Exception):
    """The interface disabled the automation while a run was in progress."""


def ran_today() -> bool:
    return LAST_RUN_FILE.exists() and LAST_RUN_FILE.read_text().strip() == date.today().isoformat()


def mark_ran_today() -> None:
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(date.today().isoformat())


def wait_for_network(timeout: int = 180) -> bool:
    """At boot/login Wi-Fi is often not up yet."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("www.linkedin.com", 443), timeout=5).close()
            return True
        except OSError:
            time.sleep(10)
    return False


def write_auto_status(status: str, message: str, started_at: str,
                      settings: dict | None, report: dict, *,
                      reason: str | None = None, summary: str | None = None) -> None:
    """Publish a complete snapshot; readers never see half-written JSON."""
    path = DATA_DIR / "automation_status.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    last_success = None
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(previous, dict):
            last_success = previous.get("last_success_at")
    except (OSError, ValueError):
        pass
    finished_at = None if status == "running" else datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {
        "status": status, "message": message, "date": date.today().isoformat(),
        "summary": summary or {
            "running": "Recherche et invitations en cours.",
            "success": "Le passage automatique est terminé.",
            "skipped": "Aucune nouvelle invitation envoyée pendant ce passage.",
        }.get(status, "Le passage automatique a rencontré un problème. Consultez les détails."),
        "pid": os.getpid(),
        "started_at": started_at, "finished_at": finished_at,
        "last_success_at": finished_at if status == "success" else last_success,
        "added": report["added"], "checked": report["checked"],
        "matched": report.get("matched"), "eligible": report.get("eligible"),
        "sent": sum(row["status"] == "sent" for row in report["results"]),
        "settings": ({key: settings[key] for key in ("keywords", "geography", "limit")}
                     if settings is not None else None),
    }
    if reason is not None:
        payload["reason"] = reason
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".auto-status-",
                                         suffix=".tmp", dir=DATA_DIR, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def auto_failure_details(error: Exception) -> tuple[str, str]:
    """Public guidance stays separate from exception details and URLs in logs."""
    if isinstance(error, NotLoggedIn):
        return "login", "Reconnectez votre compte LinkedIn depuis l’application."
    if isinstance(error, Blocked):
        return "linkedin_check", (
            "LinkedIn demande une vérification ou applique une limite. "
            "Ouvrez LinkedIn pour consulter ses indications avant le prochain passage."
        )
    if isinstance(error, SettingsError):
        return "configuration", "Vérifiez les réglages dans l’application, puis enregistrez-les à nouveau."
    network_error = isinstance(error, (ConnectionError, socket.gaierror)) or (
        isinstance(error, PWError) and re.search(
            r"net::ERR_(?:INTERNET_DISCONNECTED|NAME_NOT_RESOLVED|CONNECTION_[A-Z_]+|"
            r"NETWORK_CHANGED|ADDRESS_UNREACHABLE|TIMED_OUT)\b", str(error)
        )
    )
    if network_error:
        return "network", "LinkedIn est inaccessible. Vérifiez la connexion Internet avant le prochain passage."
    return "error", "Le passage automatique n’a pas pu se terminer. Consultez les détails pour identifier le problème."


def cmd_auto(args) -> None:
    from notify import send_report

    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    settings = None
    report = {"added": 0, "checked": 0, "matched": None, "eligible": None,
              "results": [], "error": None, "queries": []}
    report_id = "auto-" + uuid.uuid4().hex
    mailed = False
    candidates, budget = [], 0

    def finish(status, message, *, reason=None, summary=None, notify_mail=True, legacy=False):
        nonlocal mailed
        print(f"[{started_at}] {message}")
        write_auto_status(status, message, started_at, settings, report, reason=reason, summary=summary)
        if status == "running" or not notify_mail or mailed:
            return
        mailed = True
        sent = sum(row["status"] == "sent" for row in report["results"])
        public_report = {"report_id": report_id, "date": date.today().isoformat(),
                         "status": status, "reason": reason, "sent": sent,
                         **{key: report[key] for key in ("added", "checked", "matched", "eligible")}}
        try:
            subject = html = None
            if legacy:
                subject = (f"⚠️ LinkedIn auto : {report['error'][:60]}" if report["error"] and not sent
                           else f"LinkedIn : {sent} invitation(s) envoyée(s)")
                html = lambda: render_report(report, candidates, budget, settings["min_score"])
            send_report(DATA_DIR, public_report, legacy_subject=subject, legacy_html=html)
        except Exception:
            print("Le bilan par e-mail n’a pas pu être envoyé. Les invitations ne sont pas affectées.")

    try:
        # Never reuse the web server's memory or fall back to predefined searches.
        settings = read_settings(DATA_DIR / "settings.json")
    except FileNotFoundError:
        finish("skipped", "Aucun réglage enregistré. Configure l'automatisation dans l'interface.",
               reason="configuration", summary="Choisissez vos critères dans l’application, puis activez l’automatisation.")
        return
    except (OSError, ValueError) as error:
        finish("error", f"Impossible de lire les réglages : {error}", reason="configuration",
               summary="Les réglages ne peuvent pas être lus. Ouvrez l’application pour les vérifier.")
        raise
    if not settings["automation_enabled"]:
        finish("skipped", "L'automatisation est en pause.",
               summary="L’automatisation est en pause. Aucun envoi automatique n’est prévu.", notify_mail=False)
        return
    if not getattr(args, "force", False):
        if settings["schedule_days"] == "weekdays" and date.today().weekday() >= 5:
            finish("skipped", "Week-end : le programme est limité aux jours ouvrés.",
                   summary="Aucun envoi prévu le week-end avec les jours choisis.", notify_mail=False)
            return
        if ran_today():
            finish("skipped", "L'automatisation a déjà été exécutée aujourd'hui.",
                   summary="Le passage d’aujourd’hui a déjà eu lieu. Aucun nouvel envoi ne sera lancé aujourd’hui.", notify_mail=False)
            return
    if not settings["keywords"]:
        finish("skipped", "Aucun mot-clé configuré. Aucune invitation envoyée.", reason="configuration",
               summary="Ajoutez au moins une recherche dans l’application pour choisir qui inviter.")
        return
    if not PROFILE_DIR.exists():
        finish("skipped", "Connecte d'abord ton compte LinkedIn dans l'interface.", reason="login",
               summary="Connectez votre compte LinkedIn depuis l’application avant le prochain passage.")
        return

    failure = None
    rows, candidates = [], []
    budget = 0
    failure_reason = None

    def ensure_enabled():
        nonlocal failure_reason
        # A pause takes effect between invitations, never halfway through a send.
        try:
            enabled = read_settings(DATA_DIR / "settings.json")["automation_enabled"]
        except (OSError, ValueError):
            failure_reason = "configuration"
            raise
        if not enabled:
            raise AutomationPaused("Automatisation mise en pause. Aucune nouvelle invitation ne sera envoyée.")

    try:
        budget = remaining_budget(settings["limit"])
        if not budget:
            finish("skipped", "Le plafond quotidien ou hebdomadaire est atteint.", reason="quota",
                   summary="Le plafond d’invitations est atteint. Le prochain passage vérifiera à nouveau la limite disponible.")
            return
        finish("running", "Automatisation en cours avec les réglages enregistrés.")
        if not wait_for_network():
            finish("skipped", "Pas de réseau. Nouvel essai au prochain déclenchement.", reason="network",
                   summary="LinkedIn est inaccessible. Vérifiez la connexion Internet avant le prochain passage.")
            return
        ensure_enabled()
        rows = load_rows(PROSPECTS_CSV)
        report["queries"] = [("custom", line.strip()) for line in settings["keywords"].splitlines()
                             if line.strip()]
        geo = GEO_FRANCE if settings["geography"] == "france" else None
        found_urls = set()
        with sync_playwright() as pw:
            ctx, page = start(pw)
            try:
                report["added"] = find_prospects(page, rows, report["queries"], settings["pages"],
                                                  settings["network"], geo, found_urls=found_urls)
                # Old CSV rows are retained, but only today's matching profiles can be sent.
                candidates = [row for row in rows if row["url"] in found_urls]
                report["matched"] = len(candidates)
                ensure_enabled()
                report["checked"] = enrich_rows(page, rows, getattr(args, "enrich", 20),
                                                  settings["min_score"], candidates=candidates)
                ensure_enabled()
                todo = eligible(candidates, settings["min_score"], settings["active_only"])
                report["eligible"] = len(todo)
                send_batch(page, rows, todo,
                           budget, settings["note"], report["results"], before_send=ensure_enabled)
            finally:
                ctx.close()
        mark_ran_today()
    except AutomationPaused as error:
        mark_ran_today()
        finish("skipped", str(error), summary="La pause a été prise en compte. Aucun nouvel envoi ne commencera.")
        return
    except Blocked as error:
        failure = error
        report["error"] = str(error)
        mark_ran_today()  # Do not retry a LinkedIn limit or security check the same day.
    except Exception as error:
        failure = error
        report["error"] = f"{type(error).__name__}: {error}"

    sent = sum(row["status"] == "sent" for row in report["results"])
    reason = None
    summary = f"{sent} invitation{'s' if sent != 1 else ''} envoyée{'s' if sent != 1 else ''}."
    if failure:
        reason, summary = auto_failure_details(failure)
        if failure_reason == "configuration":
            reason = failure_reason
            summary = "Les réglages ne peuvent plus être lus. Ouvrez l’application pour les vérifier."
        if sent:
            summary = f"{sent} invitation{'s' if sent != 1 else ''} déjà envoyée{'s' if sent != 1 else ''}. {summary}"
    elif report["matched"] == 0:
        reason = "no_matches"
        summary = "Aucun profil trouvé avec ces recherches. Vérifiez les mots-clés et la localisation."
    elif report["eligible"] == 0:
        reason = "no_eligible"
        summary = "Des profils ont été trouvés, mais aucun ne passe les filtres ou ne reste à inviter. Vérifiez les options de sélection."
    elif any(str(row.get("status", "")).startswith("error") for row in report["results"]):
        reason = "error"
        summary += " Certaines invitations n’ont pas abouti. Consultez les détails avant de réessayer."
    elif not sent:
        reason = "no_eligible"
        summary = "Aucune nouvelle invitation envoyée : les profils retenus n’ont pas pu être invités sur LinkedIn. Consultez leurs statuts."
    finish("error" if failure else "success", report["error"] or f"{sent} invitation(s) envoyée(s).",
           reason=reason, summary=summary, legacy=True)
    if failure is not None:
        raise failure


def render_report(report: dict, rows: list[dict], budget: int, min_score: int) -> str:
    from html import escape as e

    sent = [r for r in report["results"] if r["status"] == "sent"]
    other = [r for r in report["results"] if r["status"] != "sent"]
    parts = [f"<h2 style='margin:0 0 12px'>LinkedIn — {date.today():%d/%m/%Y}</h2>"]
    if report["error"]:
        parts.append(f"<p style='color:#b42318'><b>Arrêt :</b> {e(report['error'])}</p>")

    parts.append(
        f"<p><b>{len(sent)}</b> invitation(s) envoyée(s) sur un budget de {budget} · "
        f"{sent_since(7)}/{WEEKLY_CAP} cette semaine<br>"
        f"{report['added']} nouveaux prospects trouvés · {report['checked']} profils vérifiés · "
        f"{len(eligible(rows, min_score))} prospects en réserve (score ≥ {min_score})</p>")
    if report["queries"]:
        parts.append("<p style='color:#667085'>Recherches du jour : "
                     + ", ".join(f"« {e(kw)} »" for _, kw in report["queries"]) + "</p>")

    def table(items):
        trs = "".join(
            f"<tr><td style='padding:4px 8px'>{e(r['score'])}</td>"
            f"<td style='padding:4px 8px'><a href='{e(r['url'])}'>{e(r['name'])}</a></td>"
            f"<td style='padding:4px 8px'>{e(r['headline'][:90])}</td>"
            f"<td style='padding:4px 8px;color:#667085'>{e(r['status'] if r['status'] != 'sent' else r['reasons'])}</td></tr>"
            for r in items)
        return f"<table style='border-collapse:collapse;font-size:14px'>{trs}</table>"

    if sent:
        parts.append("<h3>Invitations envoyées</h3>" + table(sent))
    if other:
        parts.append("<h3>Ignorés</h3>" + table(other))
    return "<div style='font-family:-apple-system,Segoe UI,sans-serif;color:#101828'>" + "".join(parts) + "</div>"


# ── login / cli ───────────────────────────────────────────────────────────────

def cmd_login(args) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = open_context(pw)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.linkedin.com/login")
            if getattr(args, "wait", False):
                print("Connecte-toi dans la fenêtre LinkedIn. Détection automatique de la session…")
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    if page.is_closed():
                        raise NotLoggedIn("La fenêtre LinkedIn a été fermée avant la connexion.")
                    parsed = urlsplit(page.url)
                    if parsed.hostname == "www.linkedin.com" and parsed.path.rstrip("/") == "/feed":
                        print("Session OK")
                        return
                    page.wait_for_timeout(500)
                raise NotLoggedIn("Délai de connexion dépassé. Relance la connexion pour réessayer.")
            input("Connecte-toi dans la fenêtre, puis appuie sur Entrée ici… ")
            if not is_logged_in(page):
                raise NotLoggedIn("Pas connecté, relance `login`.")
            print("Session OK")
        finally:
            ctx.close()


def cmd_rescore(_args) -> None:
    rows = load_rows(PROSPECTS_CSV)
    rescore(rows)
    save_rows(PROSPECTS_CSV, rows)
    print_top([r for r in rows if not r["status"]], 30)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    login = sub.add_parser("login")
    login.add_argument("--wait", action="store_true", help="détecte la connexion sans entrée au terminal")
    login.add_argument("--timeout", type=int, default=300, help="délai maximal de connexion en secondes")
    login.set_defaults(func=cmd_login)

    f = sub.add_parser("find", help="recherche de personnes → prospects.csv")
    f.add_argument("--segments", help=f"parmi : {', '.join(sorted({q[0] for q in QUERIES}))}")
    f.add_argument("--keywords", help="recherche libre à la place des requêtes prédéfinies")
    f.add_argument("--pages", type=int, default=2, help="pages de résultats par requête (10/page)")
    f.add_argument("--network", default="S", help="S=2e degré, O=3e+ (ex: S,O)")
    geography = f.add_mutually_exclusive_group()
    geography.add_argument("--paris", action="store_true", help="région parisienne au lieu de toute la France")
    geography.add_argument("--international", action="store_true", help="monde entier, sans filtre géographique")
    f.set_defaults(func=cmd_find)

    e = sub.add_parser("enrich", help="vérifie l'activité récente des meilleurs prospects")
    e.add_argument("--top", type=int, default=30)
    e.add_argument("--min-score", type=int, default=MIN_SCORE)
    e.set_defaults(func=cmd_enrich)

    sub.add_parser("rescore", help="recalcule les scores après modification des règles").set_defaults(
        func=cmd_rescore)

    r = sub.add_parser("run", help="envoie les invitations, meilleurs scores d'abord")
    r.add_argument("--limit", type=int, default=15)
    r.add_argument("--min-score", type=int, default=MIN_SCORE)
    r.add_argument("--active-only", action="store_true", help="seulement les profils actifs < 30 j")
    r.add_argument("--note", action="store_true", help="ajouter une note personnalisée")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--selected-file", type=Path, help="liste JSON des seules URL de profils autorisées")
    r.set_defaults(func=cmd_run)

    a = sub.add_parser("auto", help="recherche et envoi automatiques selon les réglages de l'interface")
    a.add_argument("--enrich", type=int, default=20, help="profils dont on vérifie l'activité")
    a.add_argument("--force", action="store_true", help="ignore week-end et « déjà exécuté aujourd'hui »")
    a.set_defaults(func=cmd_auto)

    args = p.parse_args()
    if args.cmd == "login" and args.timeout <= 0:
        p.error("--timeout doit être strictement positif")
    try:
        with OperationLock(DATA_DIR / ".operation.lock"):
            args.func(args)
    except (NotLoggedIn, Blocked, OperationBusy, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        exit_codes = {NotLoggedIn: 3, Blocked: 4, OperationBusy: 5, ValueError: 2}
        code = next(code for exception, code in exit_codes.items() if isinstance(exc, exception))
        sys.exit(code)


if __name__ == "__main__":
    main()
