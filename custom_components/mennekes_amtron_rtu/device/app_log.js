/* Live log of the bus transactions. It starts by itself and follows the newest line. */
(() => {
  "use strict";
  const languages = ["en", "de", "fr", "nl", "it"];
  const found = languages.indexOf((document.documentElement.lang || "").slice(0, 2).toLowerCase());
  const column = found < 0 ? 0 : found;
  const words = {
    title:["Live log","Live-Protokoll","Journal en direct","Live-logboek","Registro in tempo reale"],
    intro:["Every exchange with the wallbox, as it happens. The log runs while this page is open.","Jeder Austausch mit der Wallbox, während er passiert. Das Protokoll läuft, solange diese Seite offen ist.","Chaque échange avec la borne, en direct. Le journal tourne tant que cette page est ouverte.","Elke uitwisseling met de wallbox, terwijl ze gebeurt. Het logboek loopt zolang deze pagina open is.","Ogni scambio con la wallbox, mentre avviene. Il registro scorre finché questa pagina è aperta."],
    filter:["Filter","Filter","Filtre","Filter","Filtro"],
    pause:["Pause","Anhalten","Pause","Pauzeren","Pausa"],
    resume:["Resume","Fortsetzen","Reprendre","Hervatten","Riprendi"],
    clear:["Clear","Leeren","Vider","Wissen","Svuota"],
    download:["Download","Herunterladen","Télécharger","Downloaden","Scarica"],
    starting:["Starting…","Startet …","Démarrage…","Starten…","Avvio…"],
    live:["Live","Live","En direct","Live","In diretta"],
    paused:["Paused; new lines are collected","Angehalten; neue Zeilen werden gesammelt","En pause ; les nouvelles lignes sont collectées","Gepauzeerd; nieuwe regels worden verzameld","In pausa; le nuove righe vengono raccolte"],
    lines:["lines","Zeilen","lignes","regels","righe"],
    shown:["shown","angezeigt","affichées","getoond","mostrate"],
    offline:["No answer from the app. Trying again…","Keine Antwort der App. Neuer Versuch …","Pas de réponse de l’application. Nouvel essai…","Geen antwoord van de app. Nieuwe poging…","Nessuna risposta dall’app. Nuovo tentativo…"],
    privacy:["Serial numbers and text values are hidden and the network login is never recorded. Nothing is saved; the recording ends a few minutes after this page is closed.","Seriennummern und Textwerte sind ausgeblendet, die Netzwerkanmeldung wird nie aufgezeichnet. Nichts wird gespeichert; die Aufzeichnung endet wenige Minuten nach dem Schließen dieser Seite.","Les numéros de série et les valeurs textuelles sont masqués, la connexion réseau n’est jamais enregistrée. Rien n’est sauvegardé ; l’enregistrement s’arrête quelques minutes après la fermeture de cette page.","Serienummers en tekstwaarden zijn verborgen, de netwerkaanmelding wordt nooit vastgelegd. Er wordt niets opgeslagen; de opname stopt enkele minuten nadat deze pagina is gesloten.","Numeri di serie e valori di testo sono nascosti, l’accesso di rete non viene mai registrato. Nulla viene salvato; la registrazione termina pochi minuti dopo la chiusura di questa pagina."],
    read:["Read","Lesen","Lecture","Lezen","Lettura"],
    write:["Write","Schreiben","Écriture","Schrijven","Scrittura"],
    heartbeat:["Heartbeat","Heartbeat","Signal de présence","Heartbeat","Heartbeat"],
    ok:["OK","OK","OK","OK","OK"],
    connection:["Connection failed","Verbindung fehlgeschlagen","Échec de connexion","Verbinding mislukt","Connessione non riuscita"],
    timeout:["No response","Keine Antwort","Aucune réponse","Geen antwoord","Nessuna risposta"],
    protocol:["Invalid response","Ungültige Antwort","Réponse invalide","Ongeldig antwoord","Risposta non valida"],
    device_exception:["Rejected by wallbox","Von der Wallbox abgelehnt","Refus de la borne","Geweigerd door wallbox","Rifiutato dalla wallbox"],
    cancelled:["Cancelled","Abgebrochen","Annulé","Geannuleerd","Annullato"],
    unexpected:["Transaction failed","Zugriff fehlgeschlagen","Échec de l’échange","Transactie mislukt","Scambio non riuscito"],
    redacted:["hidden","ausgeblendet","masqué","verborgen","nascosto"],
    unknown:["not decoded","nicht dekodiert","non décodé","niet gedecodeerd","non decodificato"],
    unavailable:["Configure the wallbox connection and restart the app to see the log.","Wallbox-Verbindung einrichten und die App neu starten, um das Protokoll zu sehen.","Configurez la connexion à la borne et redémarrez l’application pour voir le journal.","Stel de wallboxverbinding in en herstart de app om het logboek te zien.","Configura la connessione alla wallbox e riavvia l’app per vedere il registro."],
  };
  const t = key => words[key]?.[column] || key;
  document.querySelectorAll("[data-text]").forEach(node => { node.textContent = t(node.dataset.text); });
  // The page shows once its texts are in the page language.
  document.documentElement.classList?.add("ready");
  const el = id => document.getElementById(id);
  const KEEP = 2000, RENEW_BELOW = 240;
  let lines = [], sequence = 0, generation = null, csrf = "", paused = false, stopped = false;
  let timer = null, busy = false, problem = "", drawn = 0, evicted = 0;
  let requestGeneration = 0, controller = null;

  const clock = stamp => { const d = new Date(stamp), p = (n, w = 2) => String(n).padStart(w, "0"); return Number.isFinite(d.getTime()) ? `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}` : "--:--:--.---"; };
  function line(record) {
    const values = record.values.map(value => `${value.register.replaceAll("_", " ")}=${value.redacted ? t("redacted") : `${value.value ?? t("unknown")}${value.unit ? ` ${value.unit}` : ""}${value.enum ? ` (${value.enum})` : ""}`}`).join(", ");
    const result = record.error ? `${t(record.error)}${record.exception_code !== null && record.exception_code !== undefined ? ` (${record.exception_code})` : ""}` : t("ok");
    const head = `${t(record.operation).toUpperCase().padEnd(9)} ${record.address} ×${record.count}`;
    return {time: clock(record.time), operation: record.operation, head, failed: Boolean(record.error), result,
            tail: `${String(record.duration_ms).padStart(6)} ms${values ? `  ${values}` : ""}`};
  }
  const plain = row => `${row.time}  ${row.head}  ${row.result}  ${row.tail}`;
  const matches = row => { const query = el("log_filter").value.trim().toLowerCase(); return !query || plain(row).toLowerCase().includes(query); };
  function node(row) {
    const div = document.createElement("div");
    for (const [name, value] of [["time", `${row.time}  `], [row.operation, `${row.head}  `], [row.failed ? "failed" : "ok", `${row.result}  `], ["", row.tail]]) {
      const span = document.createElement("span");
      span.className = name; span.textContent = value; div.appendChild(span);
    }
    return div;
  }
  function status() {
    const shown = lines.filter(matches).length;
    el("log_status").textContent = problem || `${t(paused ? "paused" : "live")} · ${lines.length} ${t("lines")}${shown === lines.length ? "" : ` · ${shown} ${t("shown")}`}`;
    el("log_pause").textContent = t(paused ? "resume" : "pause");
    el("log_download").disabled = el("log_clear").disabled = !lines.length;
  }
  // The view follows the newest line unless the reader scrolled up.
  function draw(all = false) {
    const box = el("log_lines"), follow = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    if (all) { box.replaceChildren(...lines.filter(matches).map(node)); drawn = lines.length; evicted = 0; }
    else if (!paused) {
      while (evicted > 0 && box.firstElementChild) { box.firstElementChild.remove(); evicted--; }
      evicted = 0;
      for (const row of lines.slice(drawn)) if (matches(row)) box.appendChild(node(row));
      drawn = lines.length;
      while (box.childElementCount > KEEP) box.firstElementChild.remove();
    }
    if (all || (follow && !paused)) box.scrollTop = box.scrollHeight;
    status();
  }
  function take(data) {
    if (typeof data.csrf === "string") csrf = data.csrf;
    // After the capture ended or was cleared the numbering starts again.
    const reset = data.generation !== generation || data.latest_sequence < sequence;
    generation = data.generation;
    const fresh = data.records.filter(record => reset || record.sequence > sequence).map(line);
    sequence = data.latest_sequence;
    if (!fresh.length) return;
    const dropped = Math.max(0, lines.length + fresh.length - KEEP);
    evicted += lines.slice(0, Math.min(dropped, drawn)).filter(matches).length;
    lines = lines.concat(fresh).slice(-KEEP);
    drawn = Math.max(0, drawn - dropped);
  }
  const post = (payload, signal) => fetch("./api/modbus-debug", {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json", "X-Amtron-CSRF": csrf}, body: JSON.stringify(payload), signal});
  function suspend() {
    requestGeneration++;
    clearTimeout(timer); timer = null;
    controller?.abort(); controller = null;
    busy = false;
  }
  async function poll() {
    clearTimeout(timer); timer = null;
    if (busy || stopped || document.hidden) return;
    busy = true;
    const own = requestGeneration, abort = new AbortController();
    controller = abort;
    const timeout = setTimeout(() => abort.abort(), 8000);
    const current = () => own === requestGeneration && !stopped && !document.hidden;
    try {
      let response = await fetch(`./api/modbus-debug?after=${sequence}`, {cache: "no-store", signal: abort.signal}), data = await response.json();
      if (!current()) return;
      if (!response.ok) throw new Error("refused");
      if (!data.available) { problem = t("unavailable"); return; }
      take(data);
      // Start the capture, and keep it while the page is open.
      if ((!data.enabled || data.expires_in < RENEW_BELOW) && csrf) {
        response = await post({enabled: true}, abort.signal); data = await response.json();
        if (!current()) return;
        if (!response.ok) throw new Error("refused");
        take(data);
      }
      problem = "";
    } catch (_) { if (current()) problem = t("offline"); }
    finally {
      clearTimeout(timeout);
      if (own === requestGeneration) {
        busy = false; controller = null; draw();
        if (current()) timer = setTimeout(poll, problem ? 5000 : 1000);
      }
    }
  }
  el("log_filter").addEventListener("input", () => draw(true));
  el("log_pause").addEventListener("click", () => { paused = !paused; draw(!paused); });
  el("log_clear").addEventListener("click", () => { lines = []; draw(true); });
  el("log_download").addEventListener("click", () => {
    const link = document.createElement("a"), url = URL.createObjectURL(new Blob([`${lines.filter(matches).map(plain).join("\n")}\n`], {type: "text/plain"}));
    link.href = url; link.download = `amtron-log-${new Date().toISOString().slice(0, 19).replaceAll(":", "-")}.txt`;
    link.click(); URL.revokeObjectURL(url);
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) suspend(); else void poll(); });
  addEventListener("pagehide", () => { stopped = true; suspend(); });
  addEventListener("pageshow", () => { stopped = false; void poll(); });
  status(); void poll();
})();
