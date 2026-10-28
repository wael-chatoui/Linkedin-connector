"""Private installation credentials and fixed, anonymous mail-relay requests."""

from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.request


RELAY_URL = "https://fluent-flow.fr/api/linkedin-reports"
REASONS = {"configuration", "login", "linkedin_check", "network", "quota",
           "no_matches", "no_eligible", "error"}
MESSAGES = {
    "unconfigured": "Ajoutez votre adresse pour recevoir le bilan des passages automatiques.",
    "pending": "Un lien de confirmation a été envoyé. Ouvrez-le pour activer les rapports.",
    "verified": "Votre adresse est confirmée. Les prochains passages automatiques auront un bilan par e-mail.",
    "disabled": "Les rapports par e-mail sont désactivés sur ce Mac.",
    "error": "Les rapports par e-mail ne peuvent pas être configurés. Enregistrez à nouveau votre adresse.",
}
ERROR_MESSAGES = {
    "email_not_configured": "Le service de rapports par e-mail n’est pas encore configuré. Réessayez plus tard.",
    "email_unavailable": "Le service de rapports par e-mail est temporairement indisponible. Réessayez plus tard.",
    "rate_limited": "Trop de demandes de rapports par e-mail. Patientez quelques minutes avant de réessayer.",
    "unauthorized": "Cette inscription aux rapports n’est plus reconnue. Enregistrez à nouveau votre adresse.",
    "verification_required": "Confirmez votre adresse avec le lien reçu par e-mail avant le prochain passage.",
    "revoked": "Cette inscription aux rapports a été révoquée. Enregistrez à nouveau votre adresse pour reprendre.",
    "invalid_request": "Le service n’a pas accepté la demande de rapport. Vérifiez votre adresse puis réessayez.",
    "report_conflict": "Ce bilan ne correspond pas au rapport déjà enregistré. Les invitations ne sont pas affectées.",
    "report_processing": "Ce bilan est déjà en cours de traitement par le service mail.",
}


class MailError(RuntimeError):
    def __init__(self, message, *, status=503, code="email_unavailable"):
        super().__init__(message)
        self.status = status
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Installation tokens must never be forwarded to another URL/host.
        return None


def _stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.stem}-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_email(value):
    if not isinstance(value, str):
        raise ValueError("Saisissez une adresse e-mail valide.")
    email = value.strip()
    if (len(email) > 254 or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}", email)
            or email.startswith(".") or ".." in email.split("@", 1)[0] or ".@" in email):
        raise ValueError("Saisissez une adresse e-mail valide, par exemple vous@exemple.fr.")
    # The relay identifies installations by the normalized mailbox address.
    return email.lower()


def relay_summary(value):
    """Only fixed counters/reasons leave this Mac, never LinkedIn profile data."""
    report_id = value.get("report_id")
    if not isinstance(report_id, str) or not 1 <= len(report_id) <= 100:
        raise ValueError("Identifiant de bilan invalide.")
    day = value.get("date")
    from datetime import date
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError("Date de bilan invalide.")
    if not isinstance(value.get("status"), str) or value["status"] not in {"success", "error", "skipped"}:
        raise ValueError("Statut de bilan invalide.")
    reason = value.get("reason")
    if reason is not None and (not isinstance(reason, str) or reason not in REASONS):
        raise ValueError("Motif de bilan invalide.")
    result = {"report_id": report_id, "date": day, "status": value["status"], "reason": reason}
    for key, maximum in (("sent", 20), ("added", 10000), ("checked", 10000),
                         ("matched", 10000), ("eligible", 10000)):
        count = value.get(key)
        if key in {"matched", "eligible"} and count is None:
            result[key] = None
        elif type(count) is not int or not 0 <= count <= maximum:
            raise ValueError("Compteur de bilan invalide.")
        else:
            result[key] = count
    return result


class MailClient:
    def __init__(self, data_dir, *, opener=None, monotonic=None):
        self.data_dir = Path(data_dir).resolve()
        self.config_path = self.data_dir / "mail_config.json"
        self.delivery_path = self.data_dir / "mail_delivery.json"
        self.opener = opener
        self.monotonic = monotonic or time.monotonic
        self.lock = threading.RLock()
        self.last_poll = float("-inf")

    def _read(self):
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise MailError(MESSAGES["error"]) from None
        if (not isinstance(value, dict) or type(value.get("enabled")) is not bool
                or not isinstance(value.get("email"), str)
                or not isinstance(value.get("status"), str)
                or value["status"] not in {"pending", "verified", "disabled", "error"}
                or (value["enabled"] and (not isinstance(value.get("token"), str)
                                         or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value["token"])))):
            raise MailError(MESSAGES["error"])
        return value

    def _request(self, method, path, *, token=None, payload=None, timeout=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "linkedin-connector/1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(RELAY_URL + path, method=method, headers=headers,
                                         data=None if payload is None else json.dumps(payload).encode("utf-8"))
        opener = self.opener or urllib.request.build_opener(_NoRedirect()).open
        try:
            with opener(request, timeout=timeout if timeout is not None else (5 if method == "GET" else 20)) as response:
                raw = response.read(32769)
                if len(raw) > 32768:
                    raise ValueError("response too large")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("invalid response")
                return result
        except urllib.error.HTTPError as error:
            code = "email_unavailable"
            try:
                payload = json.loads(error.read(32768))
                if (isinstance(payload, dict) and isinstance(payload.get("code"), str)
                        and payload["code"] in ERROR_MESSAGES):
                    code = payload["code"]
            except (OSError, ValueError):
                pass
            finally:
                error.close()
            status = error.code if error.code in {400, 401, 403, 409, 429, 503} else 503
            raise MailError(ERROR_MESSAGES[code], status=status, code=code) from None
        except (OSError, ValueError):
            raise MailError(ERROR_MESSAGES["email_unavailable"]) from None

    def _delivery(self):
        try:
            value = json.loads(self.delivery_path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or not isinstance(value.get("status"), str)
                    or value["status"] not in {"sent", "duplicate", "pending", "disabled", "error"}):
                return None
            return {key: value[key] for key in ("status", "message", "attempted_at") if key in value}
        except (OSError, ValueError):
            return None

    def _public(self, config):
        if config is None:
            return {"enabled": False, "email": "", "status": "unconfigured",
                    "message": MESSAGES["unconfigured"], "last_delivery": self._delivery()}
        return {"enabled": config["enabled"], "email": config["email"], "status": config["status"],
                "message": config.get("message") or MESSAGES[config["status"]],
                "last_delivery": self._delivery()}

    def state(self, *, refresh=True):
        with self.lock:
            try:
                config = self._read()
                interval = 60 if config and config["status"] == "verified" else 5
                if (refresh and config and config["enabled"]
                        and self.monotonic() - self.last_poll >= interval):
                    self.last_poll = self.monotonic()
                    try:
                        remote = self._request("GET", "/status", token=config["token"])
                        if (not isinstance(remote.get("status"), str)
                                or remote["status"] not in {"pending", "verified", "revoked"}
                                or remote.get("email") != config["email"]):
                            raise MailError(ERROR_MESSAGES["email_unavailable"])
                        status = "disabled" if remote["status"] == "revoked" else remote["status"]
                        config = {**config, "enabled": status != "disabled", "status": status,
                                  "message": MESSAGES[status]}
                        _write_private(self.config_path, config)
                    except MailError as error:
                        config = {**config, "status": "error", "message": str(error)}
                        _write_private(self.config_path, config)
                return self._public(config)
            except (MailError, OSError):
                return {"enabled": False, "email": "", "status": "error",
                        "message": MESSAGES["error"], "last_delivery": self._delivery()}

    def configure(self, value):
        if (not isinstance(value, dict) or value.keys() - {"enabled", "email"}
                or type(value.get("enabled")) is not bool):
            raise ValueError("Indiquez si les rapports par e-mail doivent être activés.")
        with self.lock:
            try:
                previous = self._read()
            except MailError:
                previous = None
            if not value["enabled"]:
                config = {"enabled": False, "email": previous["email"] if previous else "",
                          "status": "disabled", "message": MESSAGES["disabled"]}
                token = previous.get("token") if previous else None
                if token:
                    config["token"] = token
                _write_private(self.config_path, config)
                if token:
                    try:
                        self._request("DELETE", "/installation", token=token)
                        config.pop("token", None)
                    except MailError:
                        config["message"] = (MESSAGES["disabled"] + " Le service distant n’a pas pu être joint ; "
                                             "ce Mac n’enverra plus de bilan.")
                    _write_private(self.config_path, config)
                return self._public(config)
            email = validate_email(value.get("email", previous["email"] if previous else ""))
            if previous and previous["enabled"] and previous["email"] == email and previous["status"] == "verified":
                return self._public(previous)
            reuse = previous and previous["enabled"] and previous["email"] == email
            token = previous["token"] if reuse else secrets.token_urlsafe(32)
            config = {"enabled": True, "email": email, "token": token,
                      "status": "pending", "message": MESSAGES["pending"]}
            _write_private(self.config_path, config)
            self.last_poll = self.monotonic()
            if previous and previous.get("token") and previous["token"] != token:
                # Replacing the mailbox must not leave its former credential
                # authorized remotely. Keep this cleanup short so enrollment
                # still fits inside the local interface's request timeout.
                try:
                    self._request("DELETE", "/installation", token=previous["token"], timeout=5)
                except MailError:
                    pass
            try:
                remote = self._request("POST", "/enroll", payload={"email": email, "token": token})
                if remote.get("status") != "pending":
                    raise MailError(ERROR_MESSAGES["email_unavailable"])
            except MailError as error:
                _write_private(self.config_path, {**config, "status": "error", "message": str(error)})
                raise
            return self._public(config)

    def deliver(self, summary):
        """Never write mail_config here: a running job must not undo a UI pause."""
        attempted_at = _stamp()
        try:
            config = self._read()
            if not config or not config["enabled"]:
                return False
            payload = relay_summary(summary)
            remote = self._request("POST", "/report", token=config["token"], payload=payload)
            status = remote.get("status")
            if not isinstance(status, str) or status not in {"sent", "duplicate"}:
                raise MailError(ERROR_MESSAGES["email_unavailable"])
            delivery = {"status": status, "message": "Bilan envoyé par e-mail." if status == "sent"
                        else "Ce bilan a déjà été envoyé par e-mail.", "attempted_at": attempted_at}
            _write_private(self.delivery_path, delivery)
            return True
        except (MailError, OSError, ValueError) as error:
            pending = isinstance(error, MailError) and error.code == "verification_required"
            delivery = {"status": "pending" if pending else "error", "attempted_at": attempted_at,
                        "message": str(error) if isinstance(error, MailError)
                        else "Le bilan par e-mail n’a pas pu être envoyé. Les invitations ne sont pas affectées."}
            try:
                _write_private(self.delivery_path, delivery)
            except OSError:
                pass
            return False
