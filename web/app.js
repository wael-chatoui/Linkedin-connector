"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("search-form");
  const numberFormat = new Intl.NumberFormat("fr-FR");
  const dateFormat = new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "short" });
  const defaults = { keywords: "", pages: 2, network: "S", geography: "france", min_score: 40, limit: 3, active_only: false, note: false, schedule_time: "10:30", schedule_days: "everyday" };
  let state = null;
  let initialized = false;
  let online = false;
  let submitting = false;
  let dirty = false;
  let saveTimer;
  let pollTimer;
  let saveRequest = null;
  let refreshRequest = null;
  let requestVersion = 0;
  let tableSignature = "";
  let jobSignature = "";
  let searchSignature = "";

  const settings = () => ({
    keywords: $("keywords").value.trim(), pages: Number($("pages").value),
    network: $("network").value, geography: $("geography").value,
    min_score: Number($("min-score").value), limit: Number($("limit").value),
    active_only: $("active-only").checked, note: $("note").checked,
    schedule_time: $("schedule-time").value, schedule_days: $("schedule-days").value,
  });
  const isEnabled = () => Boolean(state?.automation?.enabled ?? state?.settings?.automation_enabled);
  const isJobRunning = () => state?.job?.status === "running";
  const isAutoRunning = () => Boolean(state?.automation?.running);
  const needsConnection = () => !state?.session?.profile_exists || state?.session?.status === "disconnected" || ["login", "linkedin_check"].includes(state?.automation?.attention?.kind);
  const isBusy = () => submitting || isJobRunning() || isAutoRunning();
  const rows = () => Array.isArray(state?.prospects) ? state.prospects : [];
  const appliedSettings = () => isEnabled() ? { ...defaults, ...state.settings } : settings();
  const num = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const format = (value) => numberFormat.format(num(value));
  const setText = (id, text) => { if ($(id).textContent !== text) $(id).textContent = text; };
  const formatDate = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Date indisponible" : dateFormat.format(date);
  };
  const formatNextRun = (value, timezone) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    try {
      return new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", ...(timezone ? { timeZone: timezone } : {}) }).format(date);
    } catch (_) { return formatDate(value); }
  };

  function showError(message, focus = true) {
    setText("action-error", message);
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
    const current = appliedSettings();
    const age = Number(row.last_post_days);
    return !row.status && num(row.score) >= current.min_score && (!current.active_only || (row.last_post_days !== "" && row.last_post_days != null && Number.isFinite(age) && age >= 0 && age <= 30));
  }

  function fillSettings(values) {
    const current = { ...defaults, ...values };
    for (const [id, key] of [["keywords", "keywords"], ["pages", "pages"], ["network", "network"], ["geography", "geography"], ["min-score", "min_score"], ["limit", "limit"], ["schedule-time", "schedule_time"], ["schedule-days", "schedule_days"]]) $(id).value = current[key];
    $("active-only").checked = current.active_only;
    $("note").checked = current.note;
  }

  async function post(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(path, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": state?.csrf_token || "" },
        body: JSON.stringify(body), credentials: "same-origin", signal: controller.signal,
      });
      let payload;
      try { payload = await response.json(); } catch (error) {
        if (error.name === "AbortError") throw error;
        throw new Error("L’application n’a pas répondu correctement. Rechargez la page puis réessayez.");
      }
      if (!response.ok || payload.error) throw new Error(payload.error || "L’action n’a pas pu démarrer. Réessayez.");
      return payload;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("La réponse prend trop de temps. Vérifiez l’état affiché avant de réessayer.");
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function fieldError(id, message) {
    const field = $(id);
    field.setAttribute("aria-invalid", message ? "true" : "false");
    setText(`${id}-error`, message);
    $(`${id}-error`).hidden = !message;
  }

  function validateSettings(requireKeywords = false, report = true) {
    const searches = settings().keywords.split(/\r?\n/).filter((line) => line.trim());
    $("keywords").setCustomValidity(searches.length > 10 ? "Utilisez au maximum 10 recherches, une par ligne." : "");
    const messages = { keywords: searches.length > 10 ? "Gardez au maximum 10 recherches, une par ligne." : "Écrivez au moins un métier ou un domaine à rechercher.", pages: "Choisissez un nombre entier de pages entre 1 et 10.", "min-score": "Choisissez un score entier entre 0 et 100. Le score 0 désactive ce filtre.", limit: "Choisissez un nombre entier d’invitations entre 1 et 20.", "schedule-time": "Choisissez une heure, par exemple 10:30." };
    let firstInvalid = null;
    for (const id of ["keywords", "pages", "min-score", "limit", "schedule-time"]) {
      if (id === "keywords" && !requireKeywords && !searches.length) continue;
      const valid = $(id).checkValidity();
      if (report) fieldError(id, valid ? "" : messages[id]);
      if (!valid && !firstInvalid) firstInvalid = $(id);
    }
    if (firstInvalid && report) {
      const details = firstInvalid.closest("details");
      if (details) details.open = true;
      firstInvalid.focus();
      setText("settings-status", "Vérifiez les champs indiqués avant de continuer.");
    }
    return !firstInvalid;
  }

  async function saveSettings() {
    if (!initialized || !online || isBusy() || isEnabled()) return;
    if (saveRequest) { await saveRequest; if (dirty) return saveSettings(); return; }
    if (!validateSettings(false, false)) {
      setText("settings-status", "Vérifiez les valeurs du formulaire.");
      return;
    }
    const version = requestVersion;
    saveRequest = (async () => {
      try {
        const payload = await post("/api/settings", settings());
        if (version === requestVersion) {
          state.settings = payload.settings;
          dirty = false;
          setText("settings-status", "Brouillon enregistré. Activez l’automatisation pour le programmer.");
          renderAutomation();
        }
      } catch (error) {
        setText("settings-status", "Brouillon non enregistré. Réessayez en activant l’automatisation.");
        showError(error.message, false);
      }
    })();
    await saveRequest;
    saveRequest = null;
  }

  function renderSession() {
    const session = state?.session || {};
    const connected = session.status === "connected" && !needsConnection();
    const label = connected ? "Connexion LinkedIn vérifiée" : session.profile_exists && needsConnection() ? "Reconnectez votre compte LinkedIn" : session.profile_exists ? "Connexion enregistrée, à vérifier au prochain passage" : "Votre compte n’est pas encore connecté";
    setText("session-status", online ? label : "Application arrêtée ou inaccessible");
    $("session-status").classList.toggle("connected", connected && online);
    $("login-button").firstChild.textContent = isJobRunning() && state.job.action === "login" ? "Connexion en cours… " : connected ? "Vérifier ma connexion " : "Se connecter à LinkedIn ";
    setText("session-help", isJobRunning() && state.job.action === "login" ? "Terminez votre connexion dans la fenêtre LinkedIn qui vient de s’ouvrir. Revenez ensuite ici : le résultat s’affichera automatiquement." : needsConnection() ? "Une fenêtre LinkedIn s’ouvrira. Entrez vos identifiants sur LinkedIn, puis revenez ici." : "Votre connexion est conservée sur ce Mac. Si LinkedIn la redemande, utilisez le bouton ci-dessous.");
  }

  function renderAutomation() {
    const automation = state?.automation || {};
    const scheduler = automation.scheduler || {};
    const saved = { ...defaults, ...state?.settings };
    const enabled = isEnabled();
    const attention = automation.attention;
    const failedStart = enabled && !isAutoRunning() && scheduler.last_exit_code != null && scheduler.last_exit_code !== 0;
    const mismatch = scheduler.loaded && (scheduler.schedule_time !== saved.schedule_time || scheduler.schedule_days !== saved.schedule_days);
    const scheduled = online && enabled && scheduler.installed && scheduler.loaded && !scheduler.foreign && !scheduler.error && !scheduler.legacy && !automation.error && !mismatch;
    const status = !online ? "État indisponible" : scheduler.foreign ? "Autre copie du projet" : automation.error ? "Configuration à vérifier" : scheduler.error ? "Planification à vérifier" : isAutoRunning() ? (enabled ? "Passage en cours" : "Pause demandée") : enabled && attention ? attention.title || "Une action est nécessaire" : failedStart ? "Dernier passage en échec" : enabled ? (scheduled ? "Active" : "Planification à vérifier") : automation.configured ? "En pause" : "À configurer";
    setText("automation-status", status);
    $("automation-status").classList.toggle("active", Boolean(scheduled && !attention && !failedStart));
    $("automation-status").classList.toggle("error", Boolean(online && (scheduler.foreign || scheduler.error || automation.error || failedStart || (enabled && (!scheduled || attention)))));
    const cadence = saved.schedule_days === "weekdays" ? "Du lundi au vendredi" : "Tous les jours";
    setText("automation-schedule", scheduler.foreign ? "La programmation appartient à une autre copie du projet. Ses envois sont indépendants de ces réglages." : enabled ? `${cadence} à ${saved.schedule_time}, heure locale du Mac.` : "Vos réglages sont conservés sur cet ordinateur.");
    const nextDate = scheduled && automation.next_run_at ? formatNextRun(automation.next_run_at, automation.timezone) : null;
    setText("automation-next-run", !online ? "Rouvrez l’application pour vérifier la programmation." : isAutoRunning() ? enabled ? "Recherche et invitations en cours." : "Arrêt des prochains envois demandé." : !enabled ? automation.configured ? "Aucune invitation automatique pendant la pause." : "Aucune invitation automatique pour le moment." : !scheduled ? "La programmation doit être vérifiée avant le prochain passage." : nextDate ? `Prochain passage prévu : ${nextDate}.` : `Prochain passage à l’horaire programmé : ${saved.schedule_time}.`);
    setText("automation-attention", enabled && attention ? attention.message || "Vérifiez le dernier passage avant de poursuivre." : "");
    $("automation-attention").hidden = !enabled || !attention;
    $("connection-help-link").hidden = !enabled || !["login", "linkedin_check"].includes(attention?.kind);
    const searches = String(saved.keywords).split(/\r?\n/).filter((line) => line.trim());
    setText("automation-criteria", searches.length ? `${enabled ? "Réglages appliqués" : "Réglages enregistrés"} : ${saved.geography === "international" ? "International" : "France"} · ${format(searches.length)} recherche${searches.length > 1 ? "s" : ""} · Jusqu’à ${format(saved.limit)} invitations par jour. Mots-clés : ${searches.join(" ; ")}.` : "Commencez par connecter votre compte, puis choisissez vos mots-clés.");
    const run = automation.last_run;
    const statuses = { running: "En cours", success: "Terminée", error: "Échec", skipped: "Non exécutée", cancelled: "Arrêtée", paused: "En pause" };
    setText("automation-last-run", run ? `${run.started_at ? formatDate(run.started_at) + " · " : ""}${statuses[run.status] || run.status || "Passage"}${run.sent != null ? ` · ${format(run.sent)} invitation${num(run.sent) > 1 ? "s" : ""} envoyée${num(run.sent) > 1 ? "s" : ""}` : ""}${run.summary ? `. ${run.summary}` : "."}` : enabled ? "Aucun passage pour le moment. Il démarrera à l’heure prévue." : "Aucun passage automatique enregistré.");
    let warning = "";
    if (online && scheduler.error) warning = scheduler.error;
    else if (online && scheduler.foreign) warning = "Une autre copie du projet possède cette planification. Gérez l’automatisation depuis cette copie.";
    else if (online && automation.error) warning = automation.error;
    else if (online && scheduler.supported === false) warning = "La planification automatique est disponible sur macOS. Vous pouvez conserver vos réglages ici.";
    else if (online && enabled && !scheduled) warning = scheduler.error || (scheduler.legacy ? "La planification doit être mise à jour. Enregistrez les modifications pour appliquer vos réglages à l’automatisation." : mismatch ? "L’horaire installé ne correspond pas aux réglages. Enregistrez les modifications pour le synchroniser." : "L’automatisation est configurée mais la planification n’est pas chargée. Enregistrez les modifications pour la réinstaller.");
    else if (online && failedStart && !attention) warning = `Le dernier passage n’a pas pu se terminer (code ${scheduler.last_exit_code}). Consultez les détails techniques plus bas. Si aucun détail n’apparaît, enregistrez les modifications pour réinstaller la programmation.`;
    else if (online && isAutoRunning() && !enabled) warning = "Pause demandée : l’invitation déjà en cours peut se terminer. Aucun nouvel envoi ne commencera ensuite.";
    else if (online && !enabled && scheduler.loaded) warning = "La planification est encore présente, mais les invitations sont désactivées. Aucune nouvelle invitation ne sera envoyée.";
    setText("schedule-warning", warning);
    $("schedule-warning").hidden = !warning;
    const draft = settings();
    const draftSearches = draft.keywords.split(/\r?\n/).filter((line) => line.trim());
    const draftCadence = draft.schedule_days === "weekdays" ? "du lundi au vendredi" : "tous les jours, week-end compris";
    setText("activation-preview", draftSearches.length && Number.isInteger(draft.limit) && draft.limit >= 1 && draft.limit <= 20 && draft.schedule_time ? `Jusqu’à ${format(draft.limit)} invitations ${draftCadence} à ${draft.schedule_time}, ${draft.geography === "international" ? "dans tous les pays" : "en France"}, pour ${format(draftSearches.length)} recherche${draftSearches.length > 1 ? "s" : ""}. Le total reste limité à 80 invitations sur 7 jours.` : "Choisissez vos mots-clés, un nombre d’invitations de 1 à 20 et une heure pour préparer les envois.");
  }

  function renderControls() {
    const unavailable = !online || isBusy();
    for (const item of form.elements) item.disabled = unavailable;
    $("login-button").disabled = unavailable;
    $("find-button").disabled = unavailable || needsConnection();
    setText("search-test-help", needsConnection() ? "Connectez d’abord votre compte à l’étape 1 pour tester la recherche." : "Facultatif : les résultats de ce test apparaîtront plus bas, séparés de vos anciennes recherches.");
    $("automation-button").disabled = unavailable || needsConnection() || state?.automation?.scheduler?.foreign || state?.automation?.scheduler?.supported === false || (settings().note && !state?.note_template);
    setText("activation-help", !online ? "Rouvrez Démarrer.command pour retrouver les commandes de cette page." : state?.automation?.scheduler?.foreign ? "La programmation appartient à une autre copie de l’application. Ouvrez cette copie pour la modifier." : state?.automation?.scheduler?.supported === false ? "Les invitations programmées sont disponibles sur Mac uniquement." : needsConnection() ? "Connectez d’abord votre compte LinkedIn à l’étape 1 pour activer les invitations." : settings().note && !state?.note_template ? "Le message à joindre est indisponible. Décochez cette option ou rechargez la page avant d’activer." : isAutoRunning() ? "Un passage est en cours. Vous pouvez le mettre en pause en haut de cette page." : isJobRunning() ? "Attendez la fin de l’action en cours avant de modifier la programmation." : isEnabled() ? "Les modifications seront utilisées au prochain passage après leur enregistrement." : "En activant, vous autorisez les invitations automatiques avec les réglages ci-dessus.");
    setText("automation-button", submitting ? "Enregistrement…" : isEnabled() ? "Enregistrer les modifications" : "Activer l’automatisation");
    $("reset-settings-button").hidden = !dirty || !isEnabled();
    $("pause-button").hidden = !isEnabled();
    // Pausing must remain available during a scheduled run.
    $("pause-button").disabled = !online || submitting;
    $("enrich-button").disabled = unavailable || !rows().some((row) => !row.status && num(row.score) >= appliedSettings().min_score && (row.last_post_days === "" || row.last_post_days == null));
    $("rescore-button").disabled = unavailable || !rows().length;
    $("cancel-button").hidden = !isJobRunning();
    $("cancel-button").disabled = submitting;
    $("profile-filter").disabled = !initialized;
    $("note-preview").hidden = !settings().note;
    $("score-filter-hint").hidden = settings().min_score === 0;
    setText("note-template", state?.note_template || "Le modèle de note n’est pas disponible. Rechargez la page avant d’activer les invitations avec une note.");
    setText("find-button", isJobRunning() && state.job.action === "find" ? "Recherche en cours…" : "Tester la recherche sans envoyer");
  }

  function statusLabel(row) {
    const labels = { sent: "Invitation envoyée", pending: "Déjà en attente", no_connect_button: "Connexion indisponible", email_required: "E-mail requis", not_found: "Profil introuvable" };
    if (row.status) return labels[row.status] || (String(row.status).startsWith("error") ? "Erreur d’envoi" : String(row.status));
    if (num(row.score) < appliedSettings().min_score) return "Non envoyée : filtre de score";
    if (!isEligible(row)) return "Non envoyée : activité à vérifier";
    return "Pas encore envoyée";
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderRows(force = false) {
    const latest = state?.last_search;
    $("latest-search-option").hidden = !latest;
    const nextSearchSignature = latest ? JSON.stringify(latest) : "";
    if (nextSearchSignature && nextSearchSignature !== searchSignature) $("profile-filter").value = "latest";
    searchSignature = nextSearchSignature;
    if (!latest && $("profile-filter").value === "latest") $("profile-filter").value = "all";
    const filter = $("profile-filter").value;
    const matches = new Set(latest?.urls || []);
    const visible = rows().filter((row) => filter === "latest" ? matches.has(row.url) : filter === "eligible" ? isEligible(row) : filter === "sent" ? row.status === "sent" : true);
    setText("results-description", filter === "latest" ? `Test du ${formatDate(latest.finished_at)} : ${latest.keywords.join(" ; ")} · ${latest.geography === "international" ? "International" : latest.geography === "paris" ? "Région parisienne" : "France"}. Ces profils ont été trouvés pendant ce test, y compris ceux déjà enregistrés. Aucune invitation n’est envoyée par le test.` : "L’historique inclut vos anciennes recherches. Il ne prédit pas qui sera invité au prochain passage.");
    const signature = JSON.stringify([visible, appliedSettings().min_score, appliedSettings().active_only, online, filter]);
    if (!force && signature === tableSignature) return;
    tableSignature = signature;
    $("profiles-body").replaceChildren();
    setText("result-count", `${format(visible.length)} profil${visible.length > 1 ? "s" : ""}`);
    $("empty-state").hidden = visible.length > 0;
    $("table-wrapper").hidden = !visible.length;
    if (!visible.length) {
      setText("empty-heading", !online ? "L’application est inaccessible" : filter === "latest" ? "Aucun profil trouvé pour ce test" : rows().length ? "Aucun profil dans cette vue" : "Vos futurs profils apparaîtront ici");
      setText("empty-copy", !online ? "Ouvrez Démarrer.command dans le dossier de l’application pour retrouver cette page." : filter === "latest" ? "Essayez d’autres mots-clés ou une autre localisation, puis testez à nouveau. Vos anciens profils restent dans « Tous les profils enregistrés »." : rows().length ? "Changez le filtre pour retrouver les autres profils enregistrés." : "Activez l’automatisation pour démarrer à l’heure prévue, ou testez une recherche sans envoyer d’invitation.");
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const row of visible) {
      const tr = element("tr");
      const profileCell = element("td");
      const url = safeProfileUrl(row.url);
      const name = element(url ? "a" : "span", "profile-name", row.name || "Profil LinkedIn");
      if (url) {
        name.href = url; name.target = "_blank"; name.rel = "noopener noreferrer";
        name.setAttribute("aria-label", `${row.name || "Profil LinkedIn"}, ouvrir dans un nouvel onglet`);
        const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        icon.setAttribute("class", "icon"); icon.setAttribute("aria-hidden", "true");
        const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        use.setAttribute("href", "#icon-external"); icon.append(use); name.append(icon);
      }
      profileCell.append(name, element("p", "profile-headline", row.headline || "Titre non renseigné"), element("p", "profile-location", row.location || "Localisation non renseignée"));
      const details = element("details", "score-reasons");
      details.append(element("summary", "", `Détail du score : ${format(row.score)} / 100`), element("p", "", Array.isArray(row.reasons) ? row.reasons.join(", ") || "Aucun détail enregistré." : row.reasons || "Aucun détail enregistré."));
      profileCell.append(details);
      const statusCell = element("td");
      const status = element("span", `profile-status${row.status === "sent" ? " sent" : String(row.status || "").startsWith("error") ? " error" : ""}`);
      const dot = element("span", "status-dot"); dot.setAttribute("aria-hidden", "true");
      status.append(dot, document.createTextNode(statusLabel(row)));
      const age = row.last_post_days === "" || row.last_post_days == null ? "Activité non vérifiée" : `Publication : ${format(row.last_post_days)} j`;
      statusCell.append(status, element("p", "activity-age", age));
      tr.append(profileCell, statusCell); fragment.append(tr);
    }
    $("profiles-body").append(fragment);
  }

  function renderJob() {
    const job = state?.job;
    const auto = state?.automation;
    const run = auto?.last_run;
    const jobTime = job?.started_at || "";
    const autoTime = run?.started_at || "";
    const showAuto = isAutoRunning() || (!isJobRunning() && run && autoTime >= jobTime);
    const current = showAuto ? run : job;
    if (!current) return;
    const titles = { login: "Connexion LinkedIn", find: "Test de recherche", enrich: "Vérification de l’activité", rescore: "Recalcul des scores" };
    const statuses = { running: "en cours", success: "terminé", error: "échec", cancelled: "arrêté", skipped: "non exécuté", paused: "en pause" };
    const publicMessage = showAuto ? current.summary : current.message;
    setText("job-status", `${showAuto ? "Invitations automatiques" : titles[job.action] || "Action"} : ${statuses[current.status] || current.status}${publicMessage ? `. ${publicMessage}` : "."}`);
    $("job-status").classList.toggle("running", current.status === "running");
    const logs = showAuto ? auto.logs : job.logs;
    const signature = JSON.stringify([showAuto, current.status, logs]);
    if (signature === jobSignature) return;
    jobSignature = signature;
    const log = $("activity-log");
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    const lines = Array.isArray(logs) ? logs.map((line) => typeof line === "string" ? line : line.message || JSON.stringify(line)) : [String(logs || "")];
    log.textContent = lines.join("\n") || current.message || "La tâche démarre…";
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  function render() {
    const stats = state?.stats || {};
    $("demo-notice").hidden = !state?.demo;
    setText("stat-total", format(stats.total ?? rows().length));
    setText("stat-eligible", format(rows().filter(isEligible).length));
    setText("stat-today", format(stats.sent_today)); setText("stat-week", format(stats.sent_week));
    setText("daily-cap", format(stats.daily_cap ?? 20)); setText("weekly-cap", format(stats.weekly_cap ?? 80));
    setText("app-version", state?.version ? `Version ${state.version}` : "");
    renderSession(); renderAutomation(); renderRows(); renderControls(); renderJob();
  }

  async function refresh(force = false) {
    clearTimeout(pollTimer);
    if (refreshRequest) { await refreshRequest; if (force) return refresh(true); return; }
    refreshRequest = loadState();
    await refreshRequest;
    refreshRequest = null;
    clearTimeout(pollTimer);
    pollTimer = setTimeout(() => refresh(), isJobRunning() || isAutoRunning() ? 1000 : 4000);
  }

  async function loadState() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/api/state", { cache: "no-store", credentials: "same-origin", signal: controller.signal });
      if (!response.ok) throw new Error(`Serveur local : erreur ${response.status}.`);
      const next = await response.json();
      if (!next.csrf_token || !next.settings) throw new Error("Réponse du serveur local incomplète.");
      const jobWasRunning = isJobRunning();
      state = next; online = true; $("connection-error").hidden = true;
      if (!initialized || (!dirty && !saveRequest && !submitting)) fillSettings(state.settings);
      initialized = true; render();
      if (jobWasRunning && !isJobRunning() && dirty && !isEnabled()) {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(saveSettings, 0);
      }
    } catch (error) {
      online = false;
      setText("connection-error", "Cette page ne peut plus joindre l’application. Ouvrez Démarrer.command dans le dossier de l’application. Cette page se reconnectera automatiquement. Cela ne met pas vos invitations programmées en pause.");
      $("connection-error").hidden = false;
      renderSession(); renderAutomation(); renderRows(true); renderControls();
    } finally { clearTimeout(timeout); }
  }

  async function configureAutomation(enabled) {
    if (!online || submitting || (enabled && isBusy())) return;
    if (enabled && state?.automation?.scheduler?.foreign) {
      showError(state.automation.scheduler.error || "Gérez cette planification depuis l’autre copie du projet.");
      return;
    }
    if (enabled && needsConnection()) {
      $("login-button").focus();
      showError("Connectez votre compte LinkedIn à l’étape 1, puis activez les invitations.", false);
      return;
    }
    if (enabled && !validateSettings(true)) return;
    clearTimeout(saveTimer);
    submitting = true; $("action-error").hidden = true; renderControls();
    try {
      if (saveRequest) await saveRequest;
      const payload = await post("/api/automation", enabled ? { enabled: true, settings: settings() } : { enabled: false });
      if (payload.settings) state.settings = payload.settings;
      // Pausing must not silently save unsubmitted changes to the active criteria.
      if (enabled) dirty = false;
      await refresh(true);
      setText("settings-status", enabled ? "Automatisation enregistrée. Les invitations partiront à l’heure prévue." : state?.automation?.scheduler?.foreign ? "Réglage local en pause. La planification de l’autre copie reste indépendante." : isAutoRunning() ? "Pause demandée. Une invitation déjà en cours peut se terminer." : "Automatisation en pause. Les invitations automatiques sont désactivées.");
    } catch (error) { showError(error.message); await refresh(true); }
    finally { submitting = false; render(); }
  }

  async function startJob(action) {
    if (isBusy() || !online || (action !== "login" && !validateSettings(action === "find"))) return;
    clearTimeout(saveTimer);
    submitting = true; $("action-error").hidden = true; renderControls();
    try {
      if (saveRequest) await saveRequest;
      await post("/api/jobs", action === "login" ? { action } : { action, settings: settings() });
      if (!isEnabled() && action !== "login") dirty = false;
      if (action !== "login") $("activity-panel").scrollIntoView({ block: "nearest" });
      await refresh(true);
    } catch (error) { showError(error.message); }
    finally {
      submitting = false; renderControls();
      if (dirty && !isEnabled()) saveTimer = setTimeout(saveSettings, 1000);
    }
  }

  form.addEventListener("submit", (event) => { event.preventDefault(); configureAutomation(true); });
  form.addEventListener("input", (event) => {
    $("keywords").setCustomValidity(""); dirty = true; requestVersion += 1; clearTimeout(saveTimer);
    if ($(`${event.target.id}-error`)) fieldError(event.target.id, "");
    setText("settings-status", isEnabled() ? "Modifications non appliquées. Enregistrez pour mettre à jour l’automatisation." : "Enregistrement du brouillon…");
    if (!isEnabled()) saveTimer = setTimeout(saveSettings, 1000);
    render();
  });
  $("keywords").addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); configureAutomation(true); } });
  $("pause-button").addEventListener("click", () => configureAutomation(false));
  $("connection-help-link").addEventListener("click", (event) => { event.preventDefault(); $("login-button").focus(); });
  $("score-filter-link").addEventListener("click", (event) => { event.preventDefault(); $("min-score").closest("details").open = true; $("min-score").focus(); });
  $("reset-settings-button").addEventListener("click", () => {
    clearTimeout(saveTimer); requestVersion += 1; dirty = false;
    fillSettings(state.settings);
    for (const id of ["keywords", "pages", "min-score", "limit", "schedule-time"]) fieldError(id, "");
    $("keywords").setCustomValidity("");
    setText("settings-status", "Modifications annulées. Les réglages appliqués sont affichés.");
    render(); $("keywords").focus();
  });
  $("profile-filter").addEventListener("change", () => renderRows());
  for (const [id, action] of [["login-button", "login"], ["find-button", "find"], ["enrich-button", "enrich"], ["rescore-button", "rescore"]]) $(id).addEventListener("click", () => startJob(action));
  $("cancel-button").addEventListener("click", async () => {
    if (submitting || !isJobRunning()) return;
    submitting = true; renderControls();
    try { await post("/api/jobs/cancel", {}); await refresh(true); }
    catch (error) { showError(error.message); }
    finally { submitting = false; renderControls(); }
  });
  window.addEventListener("online", () => refresh());
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  refresh();
})();
