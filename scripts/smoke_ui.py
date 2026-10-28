#!/usr/bin/env python3
"""Vérifie l’automatisation dans l’interface, sans LinkedIn ni tâche macOS réelle."""

import argparse
import csv
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright, expect
import web_app


class FakeSchedule:
    def __init__(self):
        self.installed = False
        self.loaded = False
        self.settings = {}
        self.installations = 0
        self.overrides = {}

    def status(self):
        return {"supported": True, "installed": self.installed, "loaded": self.loaded,
                "schedule_time": self.settings.get("schedule_time"),
                "schedule_days": self.settings.get("schedule_days"), **self.overrides}

    def install(self, settings):
        self.settings = dict(settings)
        self.installed = self.loaded = True
        self.installations += 1

    def uninstall(self):
        self.installed = self.loaded = False


class FixtureApplication(web_app.Application):
    """Every UI action is local and simulated, even if a test forgets a route."""

    def __init__(self, *args, login_delay=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.actions = []
        self.login_delay = login_delay
        self._login_timer = None

    def state(self):
        return {**super().state(), "demo": True}

    def start_job(self, payload):
        action = payload.get("action")
        if action not in ("login", "find", "enrich", "rescore"):
            raise AssertionError(f"Action interdite dans les vérifications UI : {action}")
        self.actions.append(dict(payload))
        if action == "login" and self.login_delay:
            now = datetime.now().astimezone().isoformat()
            job = {"id": "fixture-delayed-login", "action": "login", "status": "running",
                   "message": "Connexion simulée en cours…",
                   "logs": ["Données fictives, aucune action LinkedIn."],
                   "started_at": now, "finished_at": None}
            self.job = job

            def finish_login():
                with self.lock:
                    if self.stopping or job.get("cancel_requested"):
                        job.update(status="cancelled", message="Connexion simulée arrêtée.",
                                   finished_at=datetime.now().astimezone().isoformat())
                        return
                    (self.data_dir / ".profile").mkdir(exist_ok=True)
                    self._record_session("connected")
                    job.update(status="success", message="Connexion vérifiée.",
                               finished_at=datetime.now().astimezone().isoformat())

            self._login_timer = threading.Timer(self.login_delay, finish_login)
            self._login_timer.daemon = True
            self._login_timer.start()
            return
        if action == "login":
            (self.data_dir / ".profile").mkdir(exist_ok=True)
            self._record_session("connected")
        elif action == "find":
            values = payload.get("settings", self.settings)
            if not self.settings["automation_enabled"]:
                self._persist_settings(web_app.validate_settings(values, self.settings))
            rows = self.rows()
            if not rows:
                rows = [{**dict.fromkeys(web_app.engine.FIELDS, ""),
                         "url": "https://www.linkedin.com/in/camille-exemple/", "name": "Camille Exemple",
                         "headline": "Fondatrice SaaS santé", "location": "Paris", "score": "80"}]
                web_app.engine.save_rows(self.data_dir / "prospects.csv", rows)
            report = {"keywords": values["keywords"].splitlines(), "geography": values["geography"],
                      "urls": [] if "aucun profil" in values["keywords"].lower() else [rows[0]["url"]],
                      "finished_at": datetime.now().astimezone().isoformat()}
            (self.data_dir / "last_search.json").write_text(json.dumps(report), encoding="utf-8")
        now = datetime.now().astimezone().isoformat()
        self.job = {"id": "fixture", "action": action, "status": "success",
                    "message": "Connexion vérifiée." if action == "login" else "Recherche terminée. Aucune invitation n’a été envoyée.",
                    "logs": ["Données fictives, aucune action LinkedIn."], "started_at": now,
                    "finished_at": now}

    def _run(self, *_args):
        raise AssertionError("Un test UI ne doit jamais lancer de commande LinkedIn.")

    def close(self):
        with self.lock:
            self.stopping = True
            if self._login_timer:
                self._login_timer.cancel()
        super().close()


class FixtureHandler(web_app.Handler):
    """Narrow test frames avoid resizing the agents' shared Chrome session."""

    def send_header(self, keyword, value):
        if keyword == "X-Frame-Options":
            value = "SAMEORIGIN"
        elif keyword == "Content-Security-Policy":
            value = value.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        super().send_header(keyword, value)

    def do_GET(self):
        if self.path == "/?mobile-preview":
            if not self.allowed():
                return
            html = ('<!doctype html><html lang="fr"><meta charset="utf-8">'
                    '<title>Vérification responsive : données fictives</title>'
                    '<h1>Largeurs de 390 et 320 pixels</h1>'
                    '<p>Prévisualisations isolées, sans redimensionner les autres onglets.</p>'
                    '<iframe title="Interface 390 pixels" src="/" width="390" height="1400"></iframe>'
                    '<iframe title="Interface 320 pixels" src="/" width="320" height="1400"></iframe>'
                    '</html>')
            self.reply(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        super().do_GET()


def assert_no_page_overflow(page, label):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"Débordement : {label}"


def report_run(app, **values):
    moment = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat()
    (app.data_dir / "automation_status.json").write_text(json.dumps({
        "status": "success", "started_at": moment, "finished_at": moment,
        "sent": 0, **values,
    }), encoding="utf-8")


def serve_fixture(port, slow_login=False):
    """Expose disposable fixtures for testing in the shared agents browser."""
    with tempfile.TemporaryDirectory(prefix="linkedin-ui-preview-") as directory:
        app = FixtureApplication(directory, scheduler=FakeSchedule(), login_delay=3 if slow_login else 0)
        server = web_app.LocalServer(("127.0.0.1", port), app)
        server.RequestHandlerClass = FixtureHandler
        print(f"Interface de test : {server.origin}", flush=True)
        print("Données fictives. Aucune connexion LinkedIn, invitation ou tâche macOS.", flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            app.close()
            server.server_close()


def main():
    with tempfile.TemporaryDirectory(prefix="linkedin-ui-test-") as directory:
        scheduler = FakeSchedule()
        app = FixtureApplication(directory, scheduler=scheduler)
        server = web_app.LocalServer(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1100}, locale="fr-FR")
                # No browser request may leave this temporary local application.
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(server.origin + "/") else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(server.origin)

                # A beginner can identify the first step and cannot enable an
                # automation while the account has never been connected.
                expect(page.locator("#empty-heading")).to_have_text("Vos futurs profils apparaîtront ici")
                expect(page.locator("#automation-status")).to_have_text("À configurer")
                expect(page.locator("#automation-next-run")).to_contain_text("Aucune invitation")
                expect(page.locator("#schedule-days")).to_have_value("everyday")
                expect(page.locator("#login-button")).to_be_enabled()
                expect(page.locator("#automation-button")).to_be_disabled()
                expect(page.locator("#activation-help")).to_contain_text("étape 1")
                expect(page.locator("#find-button")).to_be_disabled()
                expect(page.locator("#send-button, #preview-button, .profile-checkbox")).to_have_count(0)
                page.screenshot(path="/tmp/Linkedin-connector-first-open.png", full_page=True)

                # Keyboard-only connection: the backend fixture marks a session
                # connected without launching Chromium or touching LinkedIn.
                page.locator("#login-button").focus()
                page.keyboard.press("Enter")
                expect(page.locator("#session-status")).to_contain_text("vérifiée")
                expect(page.locator("#automation-button")).to_be_enabled()
                assert app.actions[-1]["action"] == "login"
                page.locator("#automation-button").click()
                expect(page.locator("#keywords-error")).to_contain_text("au moins")
                expect(page.locator("#keywords")).to_be_focused()
                assert not scheduler.loaded

                page.locator("#keywords").fill("fondateur santé\ndirigeant PME")
                page.locator("#limit").fill("21")
                page.locator("#automation-button").click()
                expect(page.locator("#limit-error")).to_contain_text("entre 1 et 20")
                expect(page.locator("#limit")).to_be_focused()
                assert not scheduler.loaded
                page.locator("#limit").fill("7")
                page.locator("#geography").select_option("international")
                page.locator("#schedule-time").fill("09:15")
                expect(page.locator("#settings-status")).to_contain_text("Brouillon enregistré")
                draft = json.loads(app.settings_path.read_text())
                assert (draft["keywords"], draft["geography"], draft["limit"], draft["schedule_time"]) == ("fondateur santé\ndirigeant PME", "international", 7, "09:15")
                assert not scheduler.loaded
                expect(page.locator("#activation-preview")).to_contain_text("7 invitations")
                page.locator("#automation-button").click()
                expect(page.locator("#automation-status")).to_have_text("Active")
                expect(page.locator("#automation-next-run")).to_contain_text("Prochain passage prévu")
                expect(page.locator("#automation-schedule")).to_contain_text("Tous les jours à 09:15")
                expect(page.locator("#automation-criteria")).to_contain_text("International · 2 recherches · Jusqu’à 7 invitations")
                expect(page.locator("#automation-criteria")).to_contain_text("fondateur santé")
                assert scheduler.loaded and scheduler.settings["limit"] == 7
                assert [item["action"] for item in app.actions] == ["login"], "Activer ne doit pas lancer de recherche ni d’envoi immédiat."

                # Editing an active automation must leave the old values visible
                # in the applied summary until the user explicitly saves.
                page.locator("#keywords").fill("co-fondateur industrie")
                page.locator("#geography").select_option("france")
                page.locator("#limit").fill("4")
                page.locator("#schedule-days").select_option("weekdays")
                expect(page.locator("#settings-status")).to_contain_text("Modifications non appliquées")
                expect(page.locator("#reset-settings-button")).to_be_visible()
                page.wait_for_timeout(1200)
                assert json.loads(app.settings_path.read_text())["limit"] == 7
                expect(page.locator("#automation-criteria")).to_contain_text("International · 2 recherches · Jusqu’à 7 invitations")
                page.locator("#reset-settings-button").click()
                expect(page.locator("#limit")).to_have_value("7")
                expect(page.locator("#keywords")).to_have_value("fondateur santé\ndirigeant PME")
                expect(page.locator("#keywords")).to_be_focused()
                expect(page.locator("#reset-settings-button")).to_be_hidden()
                page.locator("#keywords").fill("co-fondateur industrie")
                page.locator("#geography").select_option("france")
                page.locator("#limit").fill("4")
                page.locator("#schedule-days").select_option("weekdays")
                page.locator("#automation-button").click()
                expect(page.locator("#settings-status")).to_contain_text("Automatisation enregistrée")
                assert scheduler.settings["limit"] == 4
                expect(page.locator("#automation-criteria")).to_contain_text("France · 1 recherche · Jusqu’à 4 invitations")
                expect(page.locator("#automation-schedule")).to_contain_text("Du lundi au vendredi")

                scheduler.loaded = False
                app.schedule_cache = None
                page.reload()
                expect(page.locator("#automation-status")).to_have_text("Planification à vérifier")
                expect(page.locator("#schedule-warning")).to_be_visible()
                page.locator("#automation-button").click()
                expect(page.locator("#automation-status")).to_have_text("Active")
                expect(page.locator("#schedule-warning")).to_be_hidden()
                scheduler.overrides = {"runs": 1, "last_exit_code": 78}
                app.schedule_cache = None
                page.reload()
                expect(page.locator("#automation-status")).to_have_text("Dernier passage en échec")
                expect(page.locator("#schedule-warning")).to_contain_text("code 78")
                scheduler.overrides = {}
                app.schedule_cache = None
                page.reload()
                page.locator("#pause-button").click()
                expect(page.locator("#automation-status")).to_have_text("En pause")
                expect(page.locator("#automation-next-run")).to_contain_text("Aucune invitation")
                assert not scheduler.loaded and not json.loads(app.settings_path.read_text())["automation_enabled"]
                page.locator("#automation-button").click()
                expect(page.locator("#automation-status")).to_have_text("Active")
                assert scheduler.settings["limit"] == 4

                rows = [
                    {**dict.fromkeys(web_app.engine.FIELDS, ""), "url": "https://www.linkedin.com/in/camille-exemple/",
                     "name": "Camille Exemple", "headline": "Fondatrice SaaS santé", "location": "Paris",
                     "score": "80", "reasons": "dirigeant, startup/pme"},
                    {**dict.fromkeys(web_app.engine.FIELDS, ""), "url": "https://www.linkedin.com/in/alex-demo/",
                     "name": "Alex Démo", "headline": "Directeur commercial", "location": "Lyon",
                     "score": "65", "reasons": "sales", "status": "sent"},
                ]
                with (Path(directory) / "prospects.csv").open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=web_app.engine.FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)
                page.reload()
                expect(page.locator("#profiles-body tr")).to_have_count(2)
                page.locator("#profile-filter").select_option("sent")
                expect(page.locator("#profiles-body tr")).to_have_count(1)
                expect(page.locator("#profiles-body")).to_contain_text("Alex Démo")
                page.locator("#profile-filter").select_option("all")
                page.locator("#find-button").click()
                expect(page.locator("#job-status")).to_contain_text("Aucune invitation")
                expect(page.locator("#profile-filter")).to_have_value("latest")
                expect(page.locator("#profiles-body tr")).to_have_count(1)
                expect(page.locator("#results-description")).to_contain_text("co-fondateur industrie")
                page.locator("#keywords").fill("aucun profil")
                page.locator("#find-button").click()
                expect(page.locator("#empty-heading")).to_have_text("Aucun profil trouvé pour ce test")
                page.locator("#profile-filter").select_option("all")
                expect(page.locator("#profiles-body tr")).to_have_count(2)
                page.locator("#reset-settings-button").click()
                assert app.actions[-1]["action"] == "find"
                assert all(item["action"] in ("login", "find") for item in app.actions)
                page.locator("#keywords").focus()
                page.keyboard.press("Tab")
                expect(page.locator("#geography")).to_be_focused()
                page.keyboard.press("Tab")
                expect(page.locator("#score-filter-link")).to_be_focused()
                page.keyboard.press("Tab")
                expect(page.locator("#find-button")).to_be_focused(timeout=1000)
                page.screenshot(path="/tmp/Linkedin-connector-desktop.png", full_page=True)
                for width in (390, 320):
                    page.set_viewport_size({"width": width, "height": 844})
                    assert_no_page_overflow(page, f"mobile {width} px")
                    for control in ("#keywords", "#limit", "#automation-button", "#pause-button"):
                        bounds = page.locator(control).bounding_box()
                        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1, control
                    page.screenshot(path=f"/tmp/Linkedin-connector-mobile-{width}.png", full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1100})
                # CSS zoom exercises actual 200% layout reflow; the 320px viewport
                # above additionally checks the narrow reading width explicitly.
                page.evaluate("document.documentElement.style.zoom = '2'")
                assert_no_page_overflow(page, "agrandissement 200 %")
                expect(page.locator("#automation-button")).to_be_enabled()
                page.screenshot(path="/tmp/Linkedin-connector-zoom-200.png", full_page=True)
                page.evaluate("document.documentElement.style.zoom = ''")

                # Even during a scheduled run, the user must be able to pause.
                def running_state(route):
                    payload = route.fetch().json()
                    payload["automation"]["running"] = True
                    route.fulfill(json=payload)
                context.route("**/api/state", running_state)
                page.reload()
                expect(page.locator("#automation-status")).to_have_text("Passage en cours")
                expect(page.locator("#automation-button")).to_be_disabled()
                expect(page.locator("#pause-button")).to_be_enabled()
                page.locator("#pause-button").click()
                expect(page.locator("#automation-status")).to_have_text("Pause demandée")
                assert not json.loads(app.settings_path.read_text())["automation_enabled"]
                context.unroute("**/api/state", running_state)
                page.reload()
                page.locator("#automation-button").click()
                expect(page.locator("#automation-status")).to_have_text("Active")

                app._record_session("disconnected")
                page.reload()
                expect(page.locator("#session-status")).to_contain_text("Reconnectez")
                expect(page.locator("#automation-attention")).to_contain_text("connexion")
                expect(page.locator("#automation-button")).to_be_disabled()
                expect(page.locator("#pause-button")).to_be_enabled()
                page.locator("#login-button").click()
                expect(page.locator("#automation-status")).to_have_text("Active")
                expect(page.locator("#automation-attention")).to_be_hidden()
                report_run(app, status="error", reason="network", summary="La connexion Internet était indisponible.")
                page.reload()
                expect(page.locator("#automation-attention")).to_contain_text("Internet")
                expect(page.locator("#automation-attention")).to_contain_text("prochain passage")
                expect(page.locator("#automation-status")).not_to_have_text("Active")
                (app.data_dir / "automation_status.json").unlink()

                # Loss of the local server must not present stale controls as
                # usable. Restoring it should recover without another setup.
                unavailable = lambda route: route.fulfill(status=503, body="Service indisponible")
                context.route("**/api/state", unavailable)
                page.reload()
                expect(page.locator("#connection-error")).to_be_visible()
                expect(page.locator("#automation-button")).to_be_disabled()
                expect(page.locator("#pause-button")).to_be_disabled()
                context.unroute("**/api/state", unavailable)
                expect(page.locator("#connection-error")).to_be_hidden(timeout=7000)
                expect(page.locator("#automation-status")).to_have_text("Active")
                expect(page.locator("#keywords")).to_have_value("co-fondateur industrie")

                # The login lasts longer than the draft debounce. A pending
                # draft must be saved after the running job becomes idle.
                page.locator("#pause-button").click()
                expect(page.locator("#automation-status")).to_have_text("En pause")
                app.login_delay = 3
                page.locator("#keywords").fill("brouillon après connexion différée")
                page.locator("#login-button").click()
                expect(page.locator("#job-status")).to_contain_text("en cours")
                expect(page.locator("#login-button")).to_be_disabled()
                assert json.loads(app.settings_path.read_text())["keywords"] == "co-fondateur industrie"
                expect(page.locator("#job-status")).to_contain_text("Connexion vérifiée", timeout=7000)
                expect(page.locator("#settings-status")).to_contain_text("Brouillon enregistré")
                assert json.loads(app.settings_path.read_text())["keywords"] == "brouillon après connexion différée"
                app.login_delay = 0
                page.locator("#keywords").fill("co-fondateur industrie")
                page.locator("#automation-button").click()
                expect(page.locator("#automation-status")).to_have_text("Active")

                # An unrelated installed copy cannot be silently replaced.
                installations = scheduler.installations
                scheduler.overrides = {"foreign": True, "error": "Cette planification appartient à une autre copie du projet."}
                app.schedule_cache = None
                page.reload()
                expect(page.locator("#automation-status")).to_have_text("Autre copie du projet")
                expect(page.locator("#schedule-warning")).to_contain_text("autre copie")
                expect(page.locator("#automation-schedule")).to_contain_text("Ses envois sont indépendants")
                expect(page.locator("#automation-button")).to_be_disabled()
                assert scheduler.installations == installations
                assert not (Path(directory) / "sent_log.csv").exists()
                assert app.worker is None and app.process is None
                assert not errors, errors
                browser.close()
                print("UI validée : premier démarrage, connexion simulée, erreurs de champs, brouillon après connexion différée, activation différée, prochain passage, modification/annulation, pause/reprise, historique, reconnexion LinkedIn, erreur Internet, serveur indisponible, autre copie, clavier, mobile 320/390 px et agrandissement 200 %.")
                print("Aucune connexion LinkedIn réelle, aucune invitation, aucune tâche macOS. Captures : /tmp/Linkedin-connector-*.png")
        finally:
            app.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="ouvrir une interface fictive dans votre navigateur existant")
    parser.add_argument("--port", type=int, default=8878)
    parser.add_argument("--slow-login", action="store_true", help="simuler une connexion de 3 secondes dans l’interface fictive")
    args = parser.parse_args()
    if args.serve:
        serve_fixture(args.port, slow_login=args.slow_login)
    else:
        main()
