"""
Email reports through Resend (https://resend.com).
Set RESEND_API_KEY + NOTIFY_EMAIL (and optionally RESEND_FROM) in .env.
"""

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


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
            log.info("Email sent to %s (%s)", to, json.loads(res.read()).get("id"))
        return True
    except urllib.error.HTTPError as e:
        log.warning("Email failed: %s %s", e.code, e.read().decode(errors="replace")[:300])
    except Exception as e:
        log.warning("Email failed: %s", e)
    return False
