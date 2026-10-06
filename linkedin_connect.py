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
    python linkedin_connect.py enrich --top 30            # check recent activity of best prospects
    python linkedin_connect.py run --dry-run              # preview, best scores first
    python linkedin_connect.py run --limit 15 --active-only
    python linkedin_connect.py run --limit 5 --note       # with personalised note
    python linkedin_connect.py auto                       # daily pipeline + email report (launchd)

Targets: founders / CEOs / owners of startups & SMEs, sales & growth leaders —
people who could hire a freelance full-stack / automation / AI developer.
Competitors (developers, freelancers), students, recruiters are pushed down.
"""

import argparse
import csv
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout

load_dotenv(Path(__file__).parent / ".env")

DATA_DIR = Path(__file__).parent / "linkedin_data"
PROFILE_DIR = DATA_DIR / ".profile"
PROSPECTS_CSV = DATA_DIR / "prospects.csv"
LOG_FILE = DATA_DIR / "sent_log.csv"

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
    "Bonjour {first_name}, je suis freelance full-stack (Next.js, Python, automatisation, IA) "
    "et j'aide les startups et PME à livrer vite leurs outils et produits. "
    "Ravi d'échanger avec vous{company_part} !"
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
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="") as f:
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
    return "/feed" in page.url


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
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if not is_logged_in(page):
        ctx.close()
        raise NotLoggedIn("Session LinkedIn expirée — relance `python linkedin_connect.py login`.")
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


def search_url(keywords: str, page_no: int, network: str, geo: str) -> str:
    nets = quote("[" + ",".join(f'"{n}"' for n in network.split(",")) + "]")
    geos = quote(f'["{geo}"]')
    return ("https://www.linkedin.com/search/results/people/"
            f"?keywords={quote(keywords)}&network={nets}&geoUrn={geos}"
            f"&origin=FACETED_SEARCH&page={page_no}")


def find_prospects(page, rows: list[dict], queries, pages: int, network: str, geo: str) -> int:
    """Appends new scored prospects to rows (saved as it goes). Returns how many were added."""
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
        queries = [("custom", args.keywords)]
    else:
        wanted = set(args.segments.split(",")) if args.segments else None
        queries = [q for q in QUERIES if not wanted or q[0] in wanted]

    rows = load_rows(PROSPECTS_CSV)
    geo = GEO_PARIS if args.paris else GEO_FRANCE
    with sync_playwright() as pw:
        ctx, page = start(pw)
        try:
            added = find_prospects(page, rows, queries, args.pages, args.network, geo)
        except Blocked as e:
            print(f"⚠️  {e} — arrêt.")
            added = None
        ctx.close()

    rescore(rows)
    save_rows(PROSPECTS_CSV, rows)
    print(f"\n+{added} nouveaux prospects ({len(rows)} au total) → {PROSPECTS_CSV}\n")
    print_top([r for r in rows if not r["status"]])


# ── enrich: recent activity ───────────────────────────────────────────────────

AGO_RE = re.compile(r"(?<!\w)(\d{1,2})\s?(min|h|j|sem|mois|ans?|d|w|mo|yrs?|y)\s?•", re.I)
UNIT_DAYS = {"min": 0, "h": 0, "j": 1, "d": 1, "sem": 7, "w": 7, "mois": 30, "mo": 30,
             "an": 365, "ans": 365, "y": 365, "yr": 365, "yrs": 365}


def last_post_days(page, url: str) -> int:
    goto(page, url + "recent-activity/all/")
    pause(3, 5)
    page.mouse.wheel(0, 800)
    pause(1, 2)
    text = page.locator("main").inner_text(timeout=10000)
    days = [int(n) * UNIT_DAYS[u.lower()] for n, u in AGO_RE.findall(text) if u.lower() in UNIT_DAYS]
    return min(days) if days else 999


def enrich_rows(page, rows: list[dict], top: int, min_score: int) -> int:
    """Fills last_post_days for the best unchecked prospects. Returns how many were checked."""
    todo = sorted((r for r in rows if not r["status"] and r["last_post_days"] == ""
                   and int(r["score"] or 0) >= min_score),
                  key=lambda r: -int(r["score"] or 0))[:top]
    print(f"Vérification de l'activité de {len(todo)} profils…")
    done = 0
    for r in todo:
        try:
            days = last_post_days(page, r["url"])
        except Exception as e:
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
        except Blocked as e:
            print(f"⚠️  {e} — arrêt.")
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
               results: list[dict]) -> None:
    """Sends up to `budget` invitations; appends every processed row to `results`."""
    sent = 0
    for r in todo:
        if sent >= budget:
            break
        try:
            status = send_invite(page, r, note)
        except Exception as e:  # keep going on UI surprises
            status = f"error_{type(e).__name__}"
        check_blocked(page)

        r["status"] = status
        r["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_rows(PROSPECTS_CSV, rows)
        results.append(r)
        print(f"[{status}] {r['score']:>3}  {r['name']} — {r['headline'][:60]}")

        if status == "sent":
            sent += 1
            log_sent(r["url"])
            time.sleep(random.uniform(*DELAY_RANGE))
        else:
            pause(5, 10)


def cmd_run(args) -> None:
    rows = load_rows(PROSPECTS_CSV)
    todo = eligible(rows, args.min_score, args.active_only)
    budget = remaining_budget(args.limit)
    print(f"{len(todo)} prospects éligibles (score ≥ {args.min_score}) · budget restant : {budget}")
    if budget <= 0:
        print("Quota jour/semaine atteint. Réessaie plus tard.")
        return

    if args.dry_run:
        print_top(todo, budget)
        return

    results: list[dict] = []
    with sync_playwright() as pw:
        ctx, page = start(pw)
        try:
            send_batch(page, rows, todo, budget, args.note, results)
        except Blocked as e:
            print(f"⚠️  {e} — arrêt.")
        ctx.close()
    print(f"Terminé : {sum(r['status'] == 'sent' for r in results)} invitation(s) envoyée(s).")


# ── auto: daily pipeline + email report ───────────────────────────────────────

REFILL_BELOW = 30      # search for new prospects when fewer eligible ones remain
QUERIES_PER_RUN = 2    # rotate through QUERIES, 2 per day


def todays_queries() -> list[tuple[str, str]]:
    start_idx = (date.today().toordinal() * QUERIES_PER_RUN) % len(QUERIES)
    return [QUERIES[(start_idx + i) % len(QUERIES)] for i in range(QUERIES_PER_RUN)]


def cmd_auto(args) -> None:
    from notify import send_email

    rows = load_rows(PROSPECTS_CSV)
    report = {"added": 0, "checked": 0, "results": [], "error": None, "queries": []}
    budget = remaining_budget(args.limit)

    try:
        with sync_playwright() as pw:
            ctx, page = start(pw)
            try:
                if len(eligible(rows, args.min_score)) < REFILL_BELOW:
                    report["queries"] = todays_queries()
                    report["added"] = find_prospects(page, rows, report["queries"], 2, "S", GEO_FRANCE)
                report["checked"] = enrich_rows(page, rows, args.enrich, args.min_score)
                if budget:
                    send_batch(page, rows, eligible(rows, args.min_score), budget, args.note,
                               report["results"])
            finally:
                ctx.close()
    except (Blocked, NotLoggedIn) as e:
        report["error"] = str(e)
    except Exception as e:
        report["error"] = f"{type(e).__name__}: {e}"

    sent = [r for r in report["results"] if r["status"] == "sent"]
    subject = (f"⚠️ LinkedIn auto : {report['error'][:60]}" if report["error"] and not sent
               else f"LinkedIn : {len(sent)} invitation(s) envoyée(s)")
    html = render_report(report, rows, budget, args.min_score)
    print(subject)
    send_email(subject, html)


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

def cmd_login(_args) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = open_context(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.linkedin.com/login")
        input("Connecte-toi dans la fenêtre, puis appuie sur Entrée ici… ")
        print("Session OK" if is_logged_in(page) else "Pas connecté — relance `login`.")
        ctx.close()


def cmd_rescore(_args) -> None:
    rows = load_rows(PROSPECTS_CSV)
    rescore(rows)
    save_rows(PROSPECTS_CSV, rows)
    print_top([r for r in rows if not r["status"]], 30)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login").set_defaults(func=cmd_login)

    f = sub.add_parser("find", help="recherche de personnes → prospects.csv")
    f.add_argument("--segments", help=f"parmi : {', '.join(sorted({q[0] for q in QUERIES}))}")
    f.add_argument("--keywords", help="recherche libre à la place des requêtes prédéfinies")
    f.add_argument("--pages", type=int, default=2, help="pages de résultats par requête (10/page)")
    f.add_argument("--network", default="S", help="S=2e degré, O=3e+ (ex: S,O)")
    f.add_argument("--paris", action="store_true", help="région parisienne au lieu de toute la France")
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
    r.set_defaults(func=cmd_run)

    a = sub.add_parser("auto", help="pipeline quotidien (find si besoin → enrich → run) + e-mail")
    a.add_argument("--limit", type=int, default=15)
    a.add_argument("--min-score", type=int, default=MIN_SCORE)
    a.add_argument("--enrich", type=int, default=20, help="profils dont on vérifie l'activité")
    a.add_argument("--note", action="store_true")
    a.set_defaults(func=cmd_auto)

    args = p.parse_args()
    try:
        args.func(args)
    except NotLoggedIn as e:
        sys.exit(str(e))
    args.func(args)


if __name__ == "__main__":
    main()
