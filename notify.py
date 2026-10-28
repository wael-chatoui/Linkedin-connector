"""
Reports through the FluentFlow relay, with private per-installation credentials.
Legacy .env Resend settings apply only before central mail is configured.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from mail_client import MailClient

log = logging.getLogger(__name__)


def send_report(data_dir, summary, *, legacy_subject=None, legacy_html=None):
    """Use central mail first; evaluate optional legacy HTML only on fallback."""
    try:
        client = MailClient(data_dir)
        if client.config_path.exists():
            return client.deliver(summary)
        if legacy_subject is not None and legacy_html is not None:
            html = legacy_html() if callable(legacy_html) else legacy_html
            return send_email(legacy_subject, html)
    except Exception:
        # A report failure must never affect invitations or reveal a credential.
        log.warning("Le bilan par e-mail n’a pas pu être envoyé. Consultez l’application.")
    return False


def send_email(subject: str, html: str) -> bool:
    """Sends an HTML email through Resend. Returns False if not configured or failed."""
    api_key = os.getenv("RESEND_API_KEY")
    to = os.getenv("NOTIFY_EMAIL")
    if not api_key or not to:
        log.warning("Email not sent: RESEND_API_KEY / NOTIFY_EMAIL missing in .env")
        return False

    payload = json.dumps({
        # onboarding@resend.dev only delivers to the Resend account owner's address;
        # set RESEND_FROM to an address on a verified domain to send anywhere.
        "from": os.getenv("RESEND_FROM") or "LinkedIn auto <onboarding@resend.dev>",
        "to": [a.strip() for a in to.split(",")],
        "subject": subject,
        "html": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "User-Agent": "linkedin-connector/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            res.read(32768)
            log.info("Bilan par e-mail envoyé.")
        return True
    except urllib.error.HTTPError as e:
        log.warning("Le service e-mail a refusé le bilan (statut %s).", e.code)
        e.close()
    except Exception:
        log.warning("Le service e-mail n’a pas pu être joint. Consultez la configuration dans l’application.")
    return False
