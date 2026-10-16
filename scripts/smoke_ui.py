#!/usr/bin/env python3
"""Vérifie le parcours local avec des profils fictifs, sans accès à LinkedIn."""

import csv
import json
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright, expect
import web_app


def main():
    with tempfile.TemporaryDirectory(prefix="linkedin-ui-test-") as directory:
        app = web_app.Application(directory)
        server = web_app.LocalServer(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1100})
                # Prevent every external request, even if a regression creates one.
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(server.origin + "/") else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(server.origin)
                expect(page.locator("#empty-heading")).to_have_text("Votre recherche commence ici")
                expect(page.locator("#send-button")).to_be_disabled()
                page.locator("#keywords").fill("fondateur santé")
                expect(page.locator("#settings-status")).to_have_text("Réglages enregistrés sur cet ordinateur.")
                assert json.loads(app.settings_path.read_text())["keywords"] == "fondateur santé"

                rows = [
                    {**dict.fromkeys(web_app.engine.FIELDS, ""), "url": "https://www.linkedin.com/in/camille-exemple/",
                     "name": "Camille Exemple", "headline": "Fondatrice SaaS santé", "location": "Paris",
                     "score": "80", "reasons": "dirigeant, startup/pme"},
                    {**dict.fromkeys(web_app.engine.FIELDS, ""), "url": "https://www.linkedin.com/in/alex-demo/",
                     "name": "Alex Démo", "headline": "Directeur commercial", "location": "Lyon",
                     "score": "65", "reasons": "sales"},
                ]
                with (Path(directory) / "prospects.csv").open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=web_app.engine.FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)
                page.reload()
                expect(page.locator(".profile-checkbox")).to_have_count(2)
                expect(page.locator("#keywords")).to_have_value("fondateur santé")
                expect(page.locator(".profile-checkbox:checked")).to_have_count(0)
                page.locator(".profile-checkbox").first.check()
                page.locator("#preview-button").click()
                expect(page.locator("#job-status")).to_contain_text("terminé", timeout=15000)
                expect(page.locator("#activity-log")).to_contain_text("Camille Exemple")
                expect(page.locator("#activity-log")).not_to_contain_text("Alex Démo")
                assert not (Path(directory) / "sent_log.csv").exists()
                before = app.job["id"]
                page.once("dialog", lambda dialog: dialog.dismiss())
                page.locator("#send-button").click()
                assert app.job["id"] == before
                page.screenshot(path="/tmp/linkedin-connector-desktop.png", full_page=True)

                page.locator(".advanced summary").click()
                page.locator("#active-only").check()
                expect(page.locator("#enrich-button")).to_be_enabled()
                expect(page.locator("#send-button")).to_be_disabled()
                page.locator("#active-only").uncheck()
                page.locator(".advanced summary").click()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Débordement mobile"
                page.screenshot(path="/tmp/linkedin-connector-mobile.png", full_page=True)
                assert not errors, errors
                browser.close()
                print("UI validée : état vide, réglages persistants, sélection explicite, aperçu réel sans envoi, confirmation annulée, enrichissement et mobile 390 px.")
                print("Captures : /tmp/linkedin-connector-desktop.png et /tmp/linkedin-connector-mobile.png")
        finally:
            app.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
