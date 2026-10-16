"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("search-form");
  const selected = new Set();
  const numberFormat = new Intl.NumberFormat("fr-FR");
  const defaults = { keywords: "", pages: 2, network: "S", paris: false, min_score: 40, limit: 3, active_only: false, note: false };
  let state = null;
  let initialized = false;
  let online = false;
  let submitting = false;
  let dirty = false;
  let saveTimer;
  let pollTimer;
  let refreshRequest = null;
  let requestVersion = 0;
  let tableSignature = "";
  let jobSignature = "";

  const settings = () => ({
    keywords: $("keywords").value.trim(),
    pages: Number($("pages").value),
    network: $("network").value,
    paris: $("geography").value === "paris",
    min_score: Number($("min-score").value),
    limit: Number($("limit").value),
    active_only: $("active-only").checked,
    note: $("note").checked,
  });
  const isRunning = () => state?.job?.status === "running";
  const isBusy = () => submitting || isRunning();
  const rows = () => Array.isArray(state?.prospects) ? state.prospects : [];
  const num = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const format = (value) => numberFormat.format(num(value));
  const setText = (id, text) => { if ($(id).textContent !== text) $(id).textContent = text; };

  function showError(message, focus = true) {
    $("action-error").textContent = message;
    $("action-error").hidden = false;
    if (focus) $("action-error").focus();
  }

  function safeProfileUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && (url.hostname === "linkedin.com" || url.hostname.endsWith(".linkedin.com")) ? url.href : null;
    } catch (_) { return null; }
  }

  function isEligible(row) {
    const current = settings();
    const age = Number(row.last_post_days);
    return !row.status && num(row.score) >= current.min_score && (!current.active_only || (row.last_post_days !== "" && row.last_post_days != null && Number.isFinite(age) && age >= 0 && age <= 30));
  }

  function availableBudget() {
    const stats = state?.stats || {};
    return Math.max(0, Math.min(settings().limit, num(stats.daily_cap ?? 20) - num(stats.sent_today), num(stats.weekly_cap ?? 80) - num(stats.sent_week)));
  }

  function fillSettings(values) {
    const current = { ...defaults, ...values };
    $("keywords").value = current.keywords;
    $("pages").value = current.pages;
    $("network").value = current.network;
    $("geography").value = current.paris ? "paris" : "france";
    $("min-score").value = current.min_score;
    $("limit").value = current.limit;
    $("active-only").checked = current.active_only;
    $("note").checked = current.note;
  }

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": state?.csrf_token || "" },
      body: JSON.stringify(body),
      credentials: "same-origin",
    });
    let payload;
    try { payload = await response.json(); } catch (_) { throw new Error("Réponse illisible du serveur local. Rechargez la page puis réessayez."); }
    if (!response.ok || payload.error) throw new Error(payload.error || "L’action n’a pas pu démarrer. Réessayez.");
    return payload;
  }

  function validateSettings(requireKeywords = false, report = true) {
    for (const id of ["pages", "min-score", "limit"]) {
      if (!$(id).checkValidity()) {
        if (report) {
          $(id).closest("details").open = true;
          $(id).reportValidity();
        }
        return false;
      }
    }
    if (requireKeywords && !settings().keywords) {
      $("keywords").setCustomValidity("Entrez les mots-clés des profils que vous recherchez.");
      $("keywords").reportValidity();
      return false;
    }
    return true;
  }

  async function saveSettings() {
    if (!initialized || !online || isBusy()) return;
    if (!validateSettings(false, false)) {
      setText("settings-status", "Vérifiez les valeurs dans les réglages avancés.");
      return;
    }
    const version = requestVersion;
    try {
      await post("/api/settings", settings());
      if (version === requestVersion) {
        dirty = false;
        setText("settings-status", "Réglages enregistrés sur cet ordinateur.");
      }
    } catch (error) {
      setText("settings-status", "Réglages non enregistrés. Ils seront repris au lancement.");
      showError(error.message, false);
    }
  }

  function renderSession() {
    const session = state?.session || {};
    const connected = session.status === "connected";
    const label = connected ? "Connexion vérifiée" : session.status === "disconnected" ? "Connexion nécessaire" : session.profile_exists ? "Session enregistrée, à vérifier" : "Pas encore connecté";
    setText("session-status", online ? label : "Serveur local indisponible");
    $("session-status").classList.toggle("connected", connected && online);
    $("login-button").firstChild.textContent = connected ? "Vérifier la session " : "Se connecter ";
    setText("session-help", isRunning() && state.job.action === "login" ? "Terminez votre connexion dans Chromium. Cette page se mettra à jour automatiquement." : "La connexion se fait directement dans la fenêtre LinkedIn.");
  }

  function renderControls() {
    const busy = isBusy();
    const unavailable = !online || busy;
    const count = selected.size;
    const budget = availableBudget();
    for (const element of form.elements) element.disabled = unavailable;
    $("login-button").disabled = unavailable;
    $("find-button").disabled = unavailable;
    $("preview-button").disabled = unavailable || !count;
    $("send-button").disabled = unavailable || !count || !budget || count > budget;
    $("enrich-button").disabled = unavailable || !rows().some((row) => !row.status && num(row.score) >= settings().min_score && (row.last_post_days === "" || row.last_post_days == null));
    $("rescore-button").disabled = unavailable || !rows().length;
    $("cancel-button").hidden = !isRunning();
    $("cancel-button").disabled = submitting;
    $("profile-filter").disabled = !initialized;
    $("note-preview").hidden = !settings().note;
    setText("note-template", state?.note_template || "Le modèle de note n’est pas disponible. Rechargez la page avant d’envoyer avec une note.");
    if (settings().note && !state?.note_template) $("send-button").disabled = true;
    document.querySelectorAll(".profile-checkbox").forEach((checkbox) => {
      const row = rows().find((profile) => profile.url === checkbox.value);
      checkbox.disabled = unavailable || !row || !isEligible(row);
    });
    setText("selection-count", count ? `${format(count)} profil${count > 1 ? "s" : ""} sélectionné${count > 1 ? "s" : ""}` : "Aucun profil sélectionné");
    setText("selection-help", !budget ? "Plafond local atteint. Aucun envoi disponible pour le moment." : count > budget ? `Votre sélection dépasse le maximum actuel de ${format(budget)} invitation(s). Décochez des profils ou ajustez le maximum par envoi.` : `Cochez jusqu’à ${format(budget)} profils. Aucun envoi sans votre confirmation.`);
    $("find-button").lastChild.textContent = isRunning() && state.job.action === "find" ? "Recherche en cours…" : "Rechercher des profils";
  }

  function statusLabel(row) {
    const labels = { sent: "Invitation envoyée", pending: "Déjà en attente", no_connect_button: "Connexion indisponible", email_required: "E-mail requis", not_found: "Profil introuvable" };
    if (row.status) return labels[row.status] || (String(row.status).startsWith("error") ? "Erreur d’envoi" : String(row.status));
    if (num(row.score) < settings().min_score) return "Score insuffisant";
    if (!isEligible(row)) return "Activité à vérifier";
    return "Éligible";
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderRows(force = false) {
    for (const url of selected) if (!rows().some((row) => row.url === url && isEligible(row))) selected.delete(url);
    const filter = $("profile-filter").value;
    const visible = rows().filter((row) => filter === "eligible" ? isEligible(row) : filter === "selected" ? selected.has(row.url) : filter === "sent" ? row.status === "sent" : true);
    const signature = JSON.stringify([visible, settings().min_score, settings().active_only, online, isBusy(), filter]);
    if (!force && signature === tableSignature) return;
    tableSignature = signature;
    $("profiles-body").replaceChildren();
    setText("result-count", `${format(visible.length)} profil${visible.length > 1 ? "s" : ""}`);
    $("empty-state").hidden = visible.length > 0;
    $("table-wrapper").hidden = !visible.length;
    if (!visible.length) {
      if (!online) {
        setText("empty-heading", "L’espace local est indisponible");
        setText("empty-copy", "Gardez le terminal ouvert. La page se reconnectera dès que le serveur répondra.");
      } else if (rows().length) {
        setText("empty-heading", "Aucun profil dans cette vue");
        setText("empty-copy", "Changez le filtre pour retrouver les autres profils enregistrés.");
      } else {
        setText("empty-heading", "Votre recherche commence ici");
        setText("empty-copy", "Connectez votre compte, entrez vos mots-clés puis lancez une recherche. Les profils apparaîtront ici.");
      }
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const row of visible) {
      const tr = element("tr", selected.has(row.url) ? "selected" : "");
      const checkCell = element("td");
      const checkLabel = element("label", "profile-select");
      const check = element("input", "profile-checkbox");
      check.type = "checkbox";
      check.value = row.url;
      check.checked = selected.has(row.url);
      check.disabled = !online || isBusy() || !isEligible(row);
      check.setAttribute("aria-label", `Sélectionner ${row.name || "ce profil"}`);
      check.addEventListener("change", () => {
        if (check.checked) selected.add(row.url); else selected.delete(row.url);
        tr.classList.toggle("selected", check.checked);
        if ($("profile-filter").value === "selected") renderRows();
        renderControls();
      });
      checkLabel.append(check);
      checkCell.append(checkLabel);
      const profileCell = element("td");
      const url = safeProfileUrl(row.url);
      const name = element(url ? "a" : "span", "profile-name", row.name || "Profil LinkedIn");
      if (url) {
        name.href = url;
        name.target = "_blank";
        name.rel = "noopener noreferrer";
        name.setAttribute("aria-label", `${row.name || "Profil LinkedIn"}, ouvrir dans un nouvel onglet`);
        const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        icon.setAttribute("class", "icon");
        icon.setAttribute("aria-hidden", "true");
        const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        use.setAttribute("href", "#icon-external");
        icon.append(use);
        name.append(icon);
      }
      profileCell.append(name, element("p", "profile-headline", row.headline || "Titre non renseigné"), element("p", "profile-location", row.location || "Localisation non renseignée"));
      const scoreCell = element("td", "score-cell");
      scoreCell.append(element("span", "score-value", format(row.score)));
      if (row.reasons && (Array.isArray(row.reasons) ? row.reasons.length : String(row.reasons).trim())) {
        const details = element("details", "score-reasons");
        details.append(element("summary", "", "Détail"), element("p", "", Array.isArray(row.reasons) ? row.reasons.join(", ") : row.reasons));
        scoreCell.append(details);
      }
      const statusCell = element("td");
      const status = element("span", `profile-status${row.status === "sent" ? " sent" : String(row.status || "").startsWith("error") ? " error" : ""}`);
      const dot = element("span", "status-dot");
      dot.setAttribute("aria-hidden", "true");
      status.append(dot, document.createTextNode(statusLabel(row)));
      const age = row.last_post_days === "" || row.last_post_days == null ? "Activité non vérifiée" : `Publication : ${format(row.last_post_days)} j`;
      statusCell.append(status, element("p", "activity-age", age));
      tr.append(checkCell, profileCell, scoreCell, statusCell);
      fragment.append(tr);
    }
    $("profiles-body").append(fragment);
  }

  function renderJob() {
    const job = state?.job;
    if (!job) return;
    const titles = { login: "Connexion LinkedIn", find: "Recherche de profils", preview: "Aperçu des invitations", send: "Envoi des invitations", enrich: "Vérification de l’activité", rescore: "Recalcul des scores" };
    const statuses = { running: "en cours", success: "terminé", error: "échec", cancelled: "arrêté" };
    setText("job-status", `${titles[job.action] || "Tâche"} : ${statuses[job.status] || job.status}${job.message ? `. ${job.message}` : "."}`);
    $("job-status").classList.toggle("running", job.status === "running");
    const signature = JSON.stringify([job.id, job.status, job.logs]);
    if (signature === jobSignature) return;
    jobSignature = signature;
    const log = $("activity-log");
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    const lines = Array.isArray(job.logs) ? job.logs.map((line) => typeof line === "string" ? line : line.message || JSON.stringify(line)) : [String(job.logs || "")];
    log.textContent = lines.join("\n") || "La tâche démarre…";
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  function render() {
    const stats = state?.stats || {};
    setText("stat-total", format(stats.total ?? rows().length));
    setText("stat-eligible", format(rows().filter(isEligible).length));
    setText("stat-today", format(stats.sent_today));
    setText("stat-week", format(stats.sent_week));
    setText("daily-cap", format(stats.daily_cap ?? 20));
    setText("weekly-cap", format(stats.weekly_cap ?? 80));
    $("legacy-warning").hidden = !state?.legacy_schedule;
    renderSession();
    renderRows();
    renderControls();
    renderJob();
  }

  async function refresh(force = false) {
    clearTimeout(pollTimer);
    if (refreshRequest) {
      await refreshRequest;
      if (force) return refresh(true);
      return;
    }
    refreshRequest = loadState();
    await refreshRequest;
    refreshRequest = null;
    clearTimeout(pollTimer);
    pollTimer = setTimeout(() => refresh(), isRunning() ? 1000 : 4000);
  }

  async function loadState() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/api/state", { cache: "no-store", credentials: "same-origin", signal: controller.signal });
      if (!response.ok) throw new Error(`Serveur local : erreur ${response.status}.`);
      const next = await response.json();
      if (!next.csrf_token || !next.settings) throw new Error("Réponse du serveur local incomplète.");
      state = next;
      online = true;
      $("connection-error").hidden = true;
      if (!initialized) { fillSettings(state.settings); initialized = true; }
      render();
    } catch (error) {
      online = false;
      $("connection-error").textContent = "Impossible de joindre le serveur local. Gardez le terminal ouvert ou relancez la commande de démarrage. Reconnexion automatique en cours.";
      $("connection-error").hidden = false;
      renderSession();
      renderRows(true);
      renderControls();
    } finally {
      clearTimeout(timeout);
    }
  }

  async function startJob(action) {
    if (isBusy() || !online || !validateSettings(action === "find")) return;
    const current = settings();
    const urls = [...selected];
    if ((action === "send" || action === "preview") && !urls.length) {
      showError("Cochez au moins un profil avant de préparer les invitations.");
      return;
    }
    if (action === "send") {
      if (current.note && !state?.note_template) {
        showError("Le modèle de note n’est pas disponible. Rechargez la page avant d’envoyer avec une note.");
        return;
      }
      const budget = availableBudget();
      if (urls.length > budget || !budget) {
        showError(`Vous pouvez envoyer au maximum ${format(budget)} invitation(s) avec les réglages et plafonds actuels.`);
        return;
      }
      const stats = state.stats || {};
      const noteDescription = current.note ? `Note utilisée :\n${state.note_template}\n\nLe prénom et l’entreprise sont adaptés à chaque profil.` : "Les invitations seront envoyées sans note.";
      const confirmed = window.confirm(`Envoyer ${urls.length} invitation(s) LinkedIn aux profils sélectionnés ?\n\nPlafonds locaux : ${stats.daily_cap ?? 20} par jour et ${stats.weekly_cap ?? 80} sur 7 jours.\n\n${noteDescription}\n\nCette action envoie de vraies invitations depuis votre compte.`);
      if (!confirmed) return;
    }
    clearTimeout(saveTimer);
    submitting = true;
    $("action-error").hidden = true;
    renderControls();
    try {
      await post("/api/jobs", { action, settings: current, selected_urls: urls, ...(action === "send" ? { confirmed: true } : {}) });
      dirty = false;
      setText("settings-status", "Réglages enregistrés sur cet ordinateur.");
      $("activity-panel")?.scrollIntoView({ block: "nearest" });
      await refresh(true);
    } catch (error) {
      showError(error.message);
    } finally {
      submitting = false;
      renderControls();
    }
  }

  form.addEventListener("submit", (event) => { event.preventDefault(); startJob("find"); });
  form.addEventListener("input", () => {
    $("keywords").setCustomValidity("");
    dirty = true;
    requestVersion += 1;
    setText("settings-status", "Enregistrement des réglages…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveSettings, 700);
    render();
  });
  $("profile-filter").addEventListener("change", () => { renderRows(); renderControls(); });
  for (const [id, action] of [["login-button", "login"], ["preview-button", "preview"], ["send-button", "send"], ["enrich-button", "enrich"], ["rescore-button", "rescore"]]) {
    $(id).addEventListener("click", () => startJob(action));
  }
  $("cancel-button").addEventListener("click", async () => {
    if (submitting || !isRunning()) return;
    submitting = true;
    renderControls();
    try {
      await post("/api/jobs/cancel", {});
      await refresh(true);
    } catch (error) { showError(error.message); }
    finally { submitting = false; renderControls(); }
  });
  window.addEventListener("online", () => refresh());
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  window.addEventListener("beforeunload", () => { if (dirty) clearTimeout(saveTimer); });
  refresh();
})();
