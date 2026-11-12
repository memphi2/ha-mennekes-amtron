/* Passive Ingress overview. No credentials, writes or external dependencies. */
(() => {
  "use strict";
  const languages = ["en", "de", "fr", "nl", "it"];
  const language = languages.indexOf(document.documentElement.lang.split("-")[0]);
  const column = language < 0 ? 0 : language;
  const words = {
    overview: ["Overview", "Übersicht", "Vue d’ensemble", "Overzicht", "Panoramica"],
    connection: ["Connection", "Verbindung", "Connexion", "Verbinding", "Connessione"],
    debug: ["Debug log", "Debug-Protokoll", "Journal de débogage", "Debuglog", "Registro di debug"],
    title: ["Charging overview", "Ladeübersicht", "Vue de la recharge", "Laadoverzicht", "Panoramica ricarica"],
    intro: ["Observed state and service decisions. This page does not change charging.", "Messzustand und Entscheidungen des Services. Diese Seite verändert die Ladung nicht.", "État observé et décisions du service. Cette page ne modifie pas la recharge.", "Gemeten toestand en beslissingen van de service. Deze pagina wijzigt het laden niet.", "Stato osservato e decisioni del servizio. Questa pagina non modifica la ricarica."],
    loading: ["Loading service state…", "Service-Zustand wird geladen…", "Chargement de l’état…", "Servicestatus laden…", "Caricamento dello stato…"],
    live: ["Service snapshot · refreshes every 5 seconds", "Service-Zustand · Aktualisierung alle 5 Sekunden", "État du service · actualisation toutes les 5 secondes", "Servicestatus · elke 5 seconden vernieuwd", "Stato del servizio · aggiornamento ogni 5 secondi"],
    offline: ["Service state unavailable. Old readings are hidden; retrying automatically.", "Service-Zustand nicht erreichbar. Alte Werte sind ausgeblendet; erneuter Abruf erfolgt automatisch.", "État indisponible. Les anciennes valeurs sont masquées ; nouvelle tentative automatique.", "Servicestatus niet beschikbaar. Oude waarden zijn verborgen; automatische nieuwe poging.", "Stato non disponibile. I vecchi valori sono nascosti; nuovo tentativo automatico."],
    admin: ["Administrator access is required.", "Administrator-Zugriff erforderlich.", "Accès administrateur requis.", "Beheerderstoegang vereist.", "Accesso amministratore richiesto."],
    supervisor: ["Update Supervisor to a supported version to open this page.", "Für diese Seite Supervisor auf eine unterstützte Version aktualisieren.", "Mettez Supervisor à jour vers une version compatible.", "Werk Supervisor bij naar een ondersteunde versie.", "Aggiornare Supervisor a una versione supportata."],
    observed: ["Observed charging state", "Beobachteter Ladezustand", "État de recharge observé", "Gemeten laadstatus", "Stato di ricarica osservato"],
    power: ["Power", "Leistung", "Puissance", "Vermogen", "Potenza"],
    energy: ["Session energy", "Energie dieses Ladevorgangs", "Énergie de la session", "Energie van deze sessie", "Energia della sessione"],
    limit: ["Observed current limit", "Gelesene Stromvorgabe", "Limite de courant lue", "Uitgelezen stroomlimiet", "Limite di corrente letto"],
    unlimited: ["Unlimited (0 A)", "Unbegrenzt (0 A)", "Sans limite (0 A)", "Onbegrensd (0 A)", "Illimitato (0 A)"],
    pause_limit: ["Pause setpoint", "Pausenvorgabe", "Consigne de pause", "Pauze-instelling", "Impostazione di pausa"],
    next: ["Next queued action", "Nächste vorgemerkte Aktion", "Prochaine action en attente", "Volgende geplande actie", "Prossima azione in coda"],
    no_pending: ["No action is queued in the service.", "Im Service ist keine weitere Aktion vorgemerkt.", "Aucune action en attente dans le service.", "Er staat geen actie in de servicewachtrij.", "Nessuna azione in coda nel servizio."],
    not_before: ["Earliest execution", "Frühestmögliche Ausführung", "Exécution au plus tôt", "Vroegste uitvoering", "Prima esecuzione possibile"],
    no_due: ["No execution time confirmed.", "Kein Ausführungszeitpunkt bestätigt.", "Aucune heure d’exécution confirmée.", "Geen uitvoeringstijd bevestigd.", "Nessun orario di esecuzione confermato."],
    guard_hint: ["Queued does not mean executed. The earliest time can change; all control guards still apply.", "Vorgemerkt bedeutet nicht ausgeführt. Der früheste Zeitpunkt kann sich ändern; alle Steuerungsbedingungen bleiben wirksam.", "En attente ne signifie pas exécuté. L’heure peut changer ; toutes les protections restent actives.", "In de wachtrij is niet uitgevoerd. Het tijdstip kan veranderen; alle voorwaarden blijven gelden.", "In coda non significa eseguito. L’orario può cambiare; tutte le condizioni restano attive."],
    energy_target: ["Energy target", "Energieziel", "Objectif énergétique", "Energiedoel", "Obiettivo energetico"],
    soc_target: ["Battery target", "Batterieziel", "Objectif batterie", "Accudoel", "Obiettivo batteria"],
    disabled: ["Off", "Aus", "Désactivé", "Uit", "Disattivato"],
    plan_hint: ["Targets are saved limits, not progress. Vehicle identity, current battery level and the full departure plan belong to Home Assistant and are not available here.", "Ziele sind gespeicherte Grenzen, kein Ladefortschritt. Fahrzeugzuordnung, aktueller Akkustand und vollständiger Abfahrtsplan liegen in Home Assistant und sind hier nicht verfügbar.", "Les objectifs sont des limites enregistrées, pas une progression. Le véhicule, son niveau actuel et le plan de départ complet restent dans Home Assistant.", "Doelen zijn opgeslagen grenzen, geen voortgang. Voertuigkoppeling, actuele accustand en het volledige vertrekplan staan in Home Assistant.", "Gli obiettivi sono limiti salvati, non progressi. Veicolo, livello attuale e piano completo di partenza restano in Home Assistant."],
    commands: ["Recent command results", "Letzte Befehlsergebnisse", "Derniers résultats de commandes", "Recente opdrachtresultaten", "Risultati recenti dei comandi"],
    history_hint: ["Up to 32 results since service start, newest first. Sequence numbers are not timestamps. This is not a session journal.", "Bis zu 32 Ergebnisse seit Service-Start, neueste zuerst. Laufende Nummern sind keine Zeitangaben. Dies ist kein Ladejournal.", "Jusqu’à 32 résultats depuis le démarrage, les plus récents d’abord. Les numéros ne sont pas des horaires. Ce n’est pas un journal de sessions.", "Maximaal 32 resultaten sinds servicestart, nieuwste eerst. Volgnummers zijn geen tijdstippen. Dit is geen sessielogboek.", "Fino a 32 risultati dall’avvio, dal più recente. I numeri non sono orari. Non è un registro delle sessioni."],
    no_commands: ["No command results yet.", "Noch keine Befehlsergebnisse.", "Aucun résultat pour le moment.", "Nog geen opdrachtresultaten.", "Nessun risultato disponibile."],
    footer: ["Local, read-only service overview. Open Home Assistant to change charging.", "Lokale, nur lesende Service-Übersicht. Ladung weiterhin in Home Assistant bedienen.", "Vue locale en lecture seule. Modifiez la recharge dans Home Assistant.", "Lokaal, alleen-lezen overzicht. Bedien het laden in Home Assistant.", "Panoramica locale di sola lettura. Modificare la ricarica in Home Assistant."],
    readonly: ["Controls: read-only", "Steuerung: nur lesend", "Commandes : lecture seule", "Bediening: alleen lezen", "Comandi: sola lettura"],
    writable: ["Service writes enabled", "Service-Schreibzugriff aktiviert", "Écriture du service activée", "Schrijftoegang ingeschakeld", "Scrittura del servizio abilitata"],
    heartbeat: ["Heartbeat", "Heartbeat", "Heartbeat", "Heartbeat", "Heartbeat"],
    ok: ["confirmed", "bestätigt", "confirmé", "bevestigd", "confermato"],
    problem: ["not confirmed in time", "nicht rechtzeitig bestätigt", "non confirmé à temps", "niet op tijd bevestigd", "non confermato in tempo"],
    unknown: ["Unknown", "Unbekannt", "Inconnu", "Onbekend", "Sconosciuto"],
    initializing: ["Initializing", "Initialisierung", "Initialisation", "Initialiseren", "Inizializzazione"],
    wallbox_error: ["Wallbox reports a fault", "Wallbox meldet einen Fehler", "La borne signale un défaut", "Wallbox meldt een storing", "La wallbox segnala un guasto"],
    service_mode: ["Wallbox in service mode", "Wallbox im Servicemodus", "Borne en mode maintenance", "Wallbox in servicemodus", "Wallbox in manutenzione"],
    no_vehicle: ["No vehicle connected", "Kein Fahrzeug angeschlossen", "Aucun véhicule connecté", "Geen voertuig aangesloten", "Nessun veicolo collegato"],
    charging: ["Charging", "Lädt", "Recharge en cours", "Aan het laden", "In ricarica"],
    locked: ["Wallbox locked", "Wallbox gesperrt", "Borne verrouillée", "Wallbox vergrendeld", "Wallbox bloccata"],
    release_disabled: ["Charging release off", "Ladefreigabe aus", "Autorisation de recharge désactivée", "Laadvrijgave uit", "Consenso alla ricarica disattivato"],
    paused: ["Charging paused", "Laden pausiert", "Recharge en pause", "Laden gepauzeerd", "Ricarica in pausa"],
    authorization_required: ["Local authorization required", "Lokale Autorisierung erforderlich", "Autorisation locale requise", "Lokale autorisatie vereist", "Autorizzazione locale richiesta"],
    preparing: ["Preparing to charge", "Ladung wird vorbereitet", "Préparation de la recharge", "Laden voorbereiden", "Preparazione della ricarica"],
    waiting_for_vehicle: ["Wallbox ready; waiting for vehicle", "Wallbox bereit; wartet auf Fahrzeug", "Borne prête ; attente du véhicule", "Wallbox gereed; wacht op voertuig", "Wallbox pronta; attesa del veicolo"],
    setup_required: ["Configure the connection first.", "Zuerst die Verbindung einrichten.", "Configurez d’abord la connexion.", "Stel eerst de verbinding in.", "Configurare prima la connessione."],
    service_starting: ["Service is initializing; no current wallbox state yet.", "Service initialisiert; noch kein aktueller Wallbox-Zustand.", "Service en initialisation ; état encore indisponible.", "Service initialiseert; nog geen actuele wallboxstatus.", "Servizio in inizializzazione; stato non ancora disponibile."],
    wallbox_unavailable: ["No current wallbox reading. Saved targets and queued intent are not proof of execution.", "Keine aktuelle Wallbox-Meldung. Gespeicherte Ziele und vorgemerkte Befehle belegen keine Ausführung.", "Aucune mesure actuelle. Les objectifs et commandes en attente ne prouvent pas leur exécution.", "Geen actuele wallboxmeting. Opgeslagen doelen en opdrachten bewijzen geen uitvoering.", "Nessuna lettura attuale. Obiettivi e comandi salvati non provano l’esecuzione."],
    state_unknown: ["Readings are missing or contradictory; charging state cannot be confirmed.", "Messwerte fehlen oder widersprechen sich; Ladezustand nicht bestätigt.", "Mesures absentes ou contradictoires ; état non confirmé.", "Metingen ontbreken of spreken elkaar tegen; laadstatus niet bevestigd.", "Letture mancanti o contraddittorie; stato non confermato."],
    goal_stop_requested: ["A charge target requires a stop; this is intent, not proof that charging has stopped.", "Ein Ladeziel fordert einen Stopp; das ist eine Anforderung, keine bestätigte Ladeunterbrechung.", "Un objectif demande l’arrêt ; cela ne prouve pas que la recharge est arrêtée.", "Een laaddoel vraagt een stop; dit bewijst niet dat het laden is gestopt.", "Un obiettivo richiede uno stop; non prova che la ricarica sia interrotta."],
    release_stop_requested: ["The service retains a request to disable charging release.", "Im Service ist das Abschalten der Ladefreigabe angefordert.", "Le service conserve une demande de désactivation de la recharge.", "De service bewaart een verzoek om laadvrijgave uit te zetten.", "Il servizio conserva una richiesta di disattivazione del consenso."],
    pause_requested: ["The service retains a pause request. Its source is not supplied by this snapshot.", "Im Service ist eine Pause angefordert. Ihre Herkunft ist in diesem Zustand nicht enthalten.", "Le service conserve une demande de pause. Sa source n’est pas fournie.", "De service bewaart een pauzeverzoek. De bron staat niet in deze status.", "Il servizio conserva una richiesta di pausa. La fonte non è disponibile."],
    plug_charge_waiting: ["Plug & Charge has no grant for this connection. Check vehicle recognition in Home Assistant.", "Plug & Charge hat für diese Verbindung keine Freigabe. Fahrzeugerkennung in Home Assistant prüfen.", "Plug & Charge n’a pas autorisé cette connexion. Vérifiez le véhicule dans Home Assistant.", "Plug & Charge heeft geen vrijgave voor deze verbinding. Controleer herkenning in Home Assistant.", "Plug & Charge non ha autorizzato questa connessione. Verificare il veicolo in Home Assistant."],
    pause: ["Pause", "Pausieren", "Pause", "Pauzeren", "Pausa"],
    resume: ["Resume", "Fortsetzen", "Reprendre", "Hervatten", "Riprendere"],
    set_current: ["Set charging current", "Ladestrom setzen", "Régler le courant", "Laadstroom instellen", "Impostare corrente"],
    single_phase: ["Select one phase", "Einphasig umschalten", "Passer en monophasé", "Naar één fase", "Passare a una fase"],
    all_phases: ["Select all phases", "Alle Phasen auswählen", "Activer toutes les phases", "Alle fasen kiezen", "Selezionare tutte le fasi"],
    disable_charging: ["Disable charging release", "Ladefreigabe ausschalten", "Désactiver la recharge", "Laadvrijgave uitzetten", "Disattivare consenso"],
    enable_charging: ["Enable charging release", "Ladefreigabe einschalten", "Autoriser la recharge", "Laadvrijgave aanzetten", "Abilitare consenso"],
    lock: ["Lock wallbox", "Wallbox sperren", "Verrouiller la borne", "Wallbox vergrendelen", "Bloccare wallbox"],
    unlock: ["Unlock wallbox", "Wallbox entsperren", "Déverrouiller la borne", "Wallbox ontgrendelen", "Sbloccare wallbox"],
    recover: ["Recover connection state", "Zustand wiederherstellen", "Restaurer l’état", "Toestand herstellen", "Ripristinare stato"],
    executed: ["Executed", "Ausgeführt", "Exécuté", "Uitgevoerd", "Eseguito"],
    replaced: ["Replaced", "Ersetzt", "Remplacé", "Vervangen", "Sostituito"],
    cancelled: ["Cancelled", "Verworfen", "Annulé", "Geannuleerd", "Annullato"],
    failed: ["Failed", "Fehlgeschlagen", "Échec", "Mislukt", "Fallito"],
    uncertain: ["Outcome uncertain", "Ergebnis ungewiss", "Résultat incertain", "Uitkomst onzeker", "Esito incerto"],
    recovery_succeeded: ["Recovery confirmed", "Wiederherstellung bestätigt", "Restauration confirmée", "Herstel bevestigd", "Ripristino confermato"],
    recovery_failed: ["Recovery failed", "Wiederherstellung fehlgeschlagen", "Échec de restauration", "Herstel mislukt", "Ripristino fallito"],
    completed: ["Acknowledged", "Bestätigt", "Confirmé", "Bevestigd", "Confermato"],
    shutdown: ["Service stopped", "Service beendet", "Service arrêté", "Service gestopt", "Servizio arrestato"],
    phase_timeout: ["Phase-switch timeout", "Zeitüberschreitung beim Phasenwechsel", "Délai de changement de phase dépassé", "Time-out fasewisseling", "Timeout cambio fase"],
    phase_unconfirmed: ["Phase switch unconfirmed", "Phasenwechsel nicht bestätigt", "Changement de phase non confirmé", "Fasewisseling niet bevestigd", "Cambio fase non confermato"],
    connection_error: ["Communication error", "Kommunikationsfehler", "Erreur de communication", "Communicatiefout", "Errore di comunicazione"],
    write_rejected: ["Write rejected", "Schreiben abgelehnt", "Écriture refusée", "Schrijven geweigerd", "Scrittura rifiutata"],
    permission_denied: ["Permission denied", "Berechtigung fehlt", "Permission refusée", "Toestemming geweigerd", "Permesso negato"],
    status_refresh_failed: ["Status refresh failed", "Statusabgleich fehlgeschlagen", "Actualisation échouée", "Statusvernieuwing mislukt", "Aggiornamento stato fallito"],
    error_still_active: ["Fault still active", "Fehler weiterhin aktiv", "Défaut toujours actif", "Storing nog actief", "Guasto ancora attivo"],
    user_cancelled: ["Cancelled by user", "Vom Benutzer verworfen", "Annulé par l’utilisateur", "Door gebruiker geannuleerd", "Annullato dall’utente"],
    energy_goal: ["Charge target prevents command", "Ladeziel verhindert Befehl", "Objectif empêchant la commande", "Laaddoel voorkomt opdracht", "Obiettivo impedisce comando"],
    deadline_expired: ["Deadline expired", "Gültigkeit abgelaufen", "Échéance dépassée", "Deadline verstreken", "Scadenza superata"]
  };
  const text = key => words[key]?.[column] ?? words.unknown[column];
  document.querySelectorAll("[data-overview]").forEach(node => { node.textContent = text(node.dataset.overview); });
  const el = id => document.getElementById(id);
  if (!el("overview")) {
    if (location.hash === "#debug") el("debug_tab")?.click();
    return;
  }
  const number = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const format = (value, unit, digits = 1) => number(value)
    ? `${value.toLocaleString(languages[column], { maximumFractionDigits: digits })} ${unit}` : "—";
  const put = (id, value) => { el(id).textContent = value; };
  function render(data) {
    if (!data || data.schema !== 1 || typeof data.available !== "boolean" || !data.measurements || !data.targets
        || !Array.isArray(data.commands) || data.commands.length > 32) throw new Error("invalid overview");
    put("charge_state", text(data.state));
    put("charge_reason", text(data.reason));
    put("service_mode", text(data.read_only === true ? "readonly" : data.read_only === false ? "writable" : "unknown"));
    put("heartbeat_state", `${text("heartbeat")}: ${text(data.heartbeat)}`);
    const measurement = data.available ? data.measurements : {};
    put("power", format(number(measurement.power_w) ? measurement.power_w / 1000 : null, "kW"));
    put("energy", format(measurement.session_kwh, "kWh", 2));
    const current = measurement.current_limit_a;
    put("current_limit", current === 0 ? text("unlimited")
      : number(current) && current < 6 ? `${text("pause_limit")} (${format(current, "A", 2)})` : format(current, "A", 2));
    put("next_action", data.pending ? text(data.pending.action) : text("no_pending"));
    const stamp = data.pending?.not_before;
    const due = stamp ? new Date(stamp) : null;
    put("next_due", due && Number.isFinite(due.getTime())
      ? `${text("not_before")}: ${due.toLocaleString(languages[column], { timeZoneName: "short" })}` : data.pending ? text("no_due") : "");
    put("energy_target", data.targets.energy_kwh === 0 ? text("disabled") : format(data.targets.energy_kwh, "kWh", 2));
    put("soc_target", data.targets.soc_percent === 0 ? text("disabled") : format(data.targets.soc_percent, "%"));
    const rows = data.commands.map(command => {
      if (!Number.isSafeInteger(command.sequence) || command.sequence <= 0) throw new Error("invalid sequence");
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = `#${command.sequence} · ${text(command.action)} — ${text(command.outcome)}`;
      const reason = document.createElement("small");
      reason.textContent = text(command.reason);
      item.appendChild(title); item.appendChild(reason);
      return item;
    });
    el("command_results").replaceChildren(...rows);
    el("commands_empty").hidden = rows.length > 0;
    el("overview_data").hidden = false;
    put("overview_notice", text("live"));
  }
  let timer = null, controller = null, generation = 0, stopped = false;
  const visible = () => !document.hidden && !stopped;
  function suspend() {
    generation += 1;
    clearTimeout(timer); timer = null;
    if (controller) controller.abort();
    controller = null;
  }
  async function refresh() {
    if (!visible() || controller) return;
    const own = ++generation;
    const abort = new AbortController();
    controller = abort;
    const timeout = setTimeout(() => abort.abort(), 8000);
    let error = "offline";
    try {
      const response = await fetch("./api/overview", { cache: "no-store", signal: abort.signal });
      const data = await response.json();
      if (!response.ok) {
        error = response.status === 403 ? "admin" : data.error === "supervisor_update_required" ? "supervisor" : "offline";
        throw new Error("overview unavailable");
      }
      if (visible() && own === generation) render(data);
    } catch (_) {
      if (visible() && own === generation) {
        el("overview_data").hidden = true;
        put("overview_notice", text(error));
      }
    } finally {
      clearTimeout(timeout);
      if (own === generation) {
        controller = null;
        if (visible()) timer = setTimeout(refresh, 5000);
      }
    }
  }
  function resume() {
    suspend();
    el("overview_data").hidden = true;
    put("overview_notice", text("loading"));
    if (visible()) void refresh();
  }
  document.addEventListener("visibilitychange", resume);
  addEventListener("pagehide", () => { stopped = true; suspend(); });
  addEventListener("pageshow", () => { stopped = false; resume(); });
  void refresh();
})();
