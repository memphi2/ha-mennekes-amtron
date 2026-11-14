/* Passive register explorer. No credentials, writes or external dependencies. */
(() => {
  "use strict";
  const languages = ["en", "de", "fr", "nl", "it"];
  const found = languages.indexOf((document.documentElement.lang || "").slice(0, 2).toLowerCase());
  const column = found < 0 ? 0 : found, locale = languages[column];
  const labels = {
    registers:["Registers","Register","Registres","Registers","Registri"],
    search:["Filter by address, name or value","Nach Adresse, Name oder Wert filtern","Filtrer par adresse, nom ou valeur","Filteren op adres, naam of waarde","Filtrare per indirizzo, nome o valore"],
    explanation:["Passive observations only. No extra Modbus transactions. Last valid values keep their original timestamps after failures.","Passive Beobachtung ohne zusätzliche Modbus-Zugriffe. Letzte gültige Werte behalten bei Fehlern ihren ursprünglichen Zeitstempel.","Observations passives uniquement, sans transaction Modbus supplémentaire. Après une erreur, les dernières valeurs valides gardent leur horodatage d’origine.","Alleen passieve waarnemingen, zonder extra Modbus-transacties. Na een fout behouden de laatste geldige waarden hun oorspronkelijke tijdstempel.","Solo osservazioni passive, senza transazioni Modbus aggiuntive. Dopo un errore gli ultimi valori validi mantengono l’orario originale."],
    quality:["OK describes a successful read, not a fault-free wallbox. Writes are acknowledgements, not readbacks. Serial number redacted.","OK bedeutet erfolgreich gelesen, nicht fehlerfreie Wallbox. Schreibbestätigungen sind keine Rücklesewerte. Die Seriennummer ist ausgeblendet.","OK signifie une lecture réussie, pas une borne sans défaut. Les écritures sont des accusés de réception, pas des relectures. Numéro de série masqué.","OK betekent succesvol gelezen, niet een storingsvrije wallbox. Schrijfbevestigingen zijn geen teruggelezen waarden. Serienummer verborgen.","OK indica una lettura riuscita, non una wallbox senza guasti. Le scritture sono conferme, non riletture. Numero di serie nascosto."],
    all:["All","Alle","Tous","Alle","Tutti"],
    problems:["Problems","Auffälligkeiten","Problèmes","Problemen","Problemi"],
    status:["Quality","Datenqualität","Qualité","Kwaliteit","Qualità"],
    sort:["Sort","Sortierung","Tri","Sorteren","Ordinamento"],
    address:["Address","Adresse","Adresse","Adres","Indirizzo"],
    name:["Register / type","Register / Datentyp","Registre / type","Register / type","Registro / tipo"],
    raw:["Raw words (hex / decimal)","Rohwörter (Hex / Dezimal)","Mots bruts (hex / décimal)","Ruwe woorden (hex / decimaal)","Parole grezze (esadecimale / decimale)"],
    value:["Decoded / translated","Dekodiert / übersetzt","Décodé / traduit","Gedecodeerd / vertaald","Decodificato / tradotto"],
    updated:["Last successful read","Letzte erfolgreiche Abfrage","Dernière lecture réussie","Laatste geslaagde uitlezing","Ultima lettura riuscita"],
    attempt:["Last attempt","Letzter Versuch","Dernière tentative","Laatste poging","Ultimo tentativo"],
    freeze:["Freeze display (bus polling continues)","Anzeige anhalten (Bus-Abfragen laufen weiter)","Figer l’affichage (l’interrogation du bus continue)","Weergave bevriezen (busuitlezing gaat door)","Bloccare la visualizzazione (la lettura del bus continua)"],
    offline:["No current service response. Table hidden until recovery.","Keine aktuelle Service-Antwort. Tabelle bis zur Wiederherstellung ausgeblendet.","Aucune réponse actuelle du service. Tableau masqué jusqu’au rétablissement.","Geen actueel antwoord van de service. Tabel verborgen tot herstel.","Nessuna risposta attuale del servizio. Tabella nascosta fino al ripristino."],
    live:["Live · updates every five seconds","Live · Aktualisierung alle fünf Sekunden","En direct · actualisation toutes les cinq secondes","Live · elke vijf seconden bijgewerkt","In tempo reale · aggiornamento ogni cinque secondi"],
    frozen:["Display frozen — values and quality are not current","Anzeige angehalten — Werte und Qualität sind nicht aktuell","Affichage figé — valeurs et qualité ne sont pas actuelles","Weergave bevroren — waarden en kwaliteit zijn niet actueel","Visualizzazione bloccata — valori e qualità non sono attuali"],
    ok:["OK","OK","OK","OK","OK"],
    degraded:["Degraded","Eingeschränkt","Dégradé","Verminderd","Degradato"],
    error:["Error","Fehler","Erreur","Fout","Errore"],
    unknown:["Unknown","Unbekannt","Inconnu","Onbekend","Sconosciuto"],
    unsupported:["Unsupported","Nicht unterstützt","Non pris en charge","Niet ondersteund","Non supportato"],
    write_only:["Write only","Nur schreibbar","Écriture seule","Alleen schrijven","Solo scrittura"],
    redacted:["Redacted","Ausgeblendet","Masqué","Verborgen","Nascosto"],
    overdue:["Refresh overdue","Aktualisierung überfällig","Actualisation en retard","Vernieuwing achterstallig","Aggiornamento in ritardo"],
    wallbox_unavailable:["Wallbox unavailable","Wallbox nicht erreichbar","Borne indisponible","Wallbox niet bereikbaar","Wallbox non raggiungibile"],
    not_read:["Not read yet","Noch nicht gelesen","Pas encore lu","Nog niet gelezen","Non ancora letto"],
    layout_unknown:["Layout unknown","Registerlayout unbekannt","Disposition inconnue","Indeling onbekend","Layout sconosciuto"],
    layout_unsupported:["Not supported by layout","Im Registerlayout nicht unterstützt","Non pris en charge par la disposition","Niet ondersteund door de indeling","Non supportato dal layout"],
    no_readback:["No readback","Kein Rücklesewert","Pas de relecture","Geen teruglezing","Nessuna rilettura"],
    unknown_enum:["Unknown enumeration value","Unbekannter Zustandswert","Valeur d’énumération inconnue","Onbekende opsommingswaarde","Valore di enumerazione sconosciuto"],
    invalid_value:["Invalid decoded value","Ungültiger dekodierter Wert","Valeur décodée non valide","Ongeldige gedecodeerde waarde","Valore decodificato non valido"],
    invalid_response:["Invalid response","Ungültige Antwort","Réponse non valide","Ongeldig antwoord","Risposta non valida"],
    timeout:["Timeout","Zeitüberschreitung","Délai dépassé","Time-out","Timeout"],
    protocol:["Protocol error","Protokollfehler","Erreur de protocole","Protocolfout","Errore di protocollo"],
    device_exception:["Modbus exception","Modbus-Ausnahme","Exception Modbus","Modbus-uitzondering","Eccezione Modbus"],
    cancelled:["Cancelled","Abgebrochen","Annulé","Geannuleerd","Annullato"],
    unexpected:["Unexpected error","Unerwarteter Fehler","Erreur inattendue","Onverwachte fout","Errore imprevisto"]
  };
  const enumWords = {
    not_initialized:["Not initialized","Nicht initialisiert","Non initialisé","Niet geïnitialiseerd","Non inizializzato"],
    idle:["Ready / no vehicle","Bereit / kein Fahrzeug","Prêt / aucun véhicule","Gereed / geen voertuig","Pronto / nessun veicolo"],
    ev_connected:["Vehicle connected","Fahrzeug verbunden","Véhicule connecté","Voertuig aangesloten","Veicolo collegato"],
    preconditions_valid:["Preconditions met","Voraussetzungen erfüllt","Conditions préalables remplies","Voorwaarden vervuld","Condizioni soddisfatte"],
    ready_to_charge:["Ready to charge","Ladebereit","Prêt à charger","Klaar om te laden","Pronto per la ricarica"],
    charging:["Charging","Lädt","Recharge en cours","Aan het laden","In ricarica"],
    error:["Error","Fehler","Erreur","Fout","Errore"],
    service_mode:["Service mode","Servicemodus","Mode maintenance","Servicemodus","Modalità di manutenzione"],
    not_used:["Not used","Nicht verwendet","Non utilisé","Niet gebruikt","Non utilizzato"],
    authorized:["Authorized","Autorisiert","Autorisé","Geautoriseerd","Autorizzato"],
    not_authorized:["Not authorized","Nicht autorisiert","Non autorisé","Niet geautoriseerd","Non autorizzato"],
    not_relevant:["Not relevant","Nicht relevant","Non pertinent","Niet relevant","Non rilevante"],
    not_downgraded:["Not reduced","Nicht reduziert","Non réduit","Niet verlaagd","Non ridotto"],
    downgraded:["Reduced","Reduziert","Réduit","Verlaagd","Ridotto"],
    init:["Initializing","Initialisierung","Initialisation","Initialiseren","Inizializzazione"],
    solar_one_phase:["Solar: one phase","Solar: eine Phase","Solaire : une phase","Zon: één fase","Solare: una fase"],
    solar_three_phases:["Solar: three phases","Solar: drei Phasen","Solaire : trois phases","Zon: drie fasen","Solare: tre fasi"],
    solar_dynamic:["Solar: dynamic","Solar: dynamisch","Solaire : dynamique","Zon: dynamisch","Solare: dinamico"],
    one_phase_only:["One phase only","Nur eine Phase","Une seule phase","Alleen één fase","Solo una fase"],
    three_phases_only:["Three phases only","Nur drei Phasen","Trois phases uniquement","Alleen drie fasen","Solo tre fasi"],
    one_or_three_phases:["One or three phases","Eine oder drei Phasen","Une ou trois phases","Eén of drie fasen","Una o tre fasi"],
    one_phase:["One phase","Eine Phase","Une phase","Eén fase","Una fase"],
    two_phases:["Two phases","Zwei Phasen","Deux phases","Twee fasen","Due fasi"],
    three_phases:["Three phases","Drei Phasen","Trois phases","Drie fasen","Tre fasi"],
    undetermined:["Undetermined","Unbestimmt","Indéterminé","Onbepaald","Indeterminato"],
    unlocked:["Unlocked","Entriegelt","Déverrouillé","Ontgrendeld","Sbloccato"],
    locked:["Locked","Verriegelt","Verrouillé","Vergrendeld","Bloccato"],
    fixed_cable:["Fixed cable","Fest angeschlagenes Kabel","Câble fixe","Vaste kabel","Cavo fisso"],
    not_active:["Not active","Nicht aktiv","Inactif","Niet actief","Non attivo"],
    fast:["Fast charging","Schnellladen","Recharge rapide","Snelladen","Ricarica rapida"],
    sunshine:["Sunshine","Sunshine","Sunshine","Sunshine","Sunshine"],
    sunshine_plus:["Sunshine+","Sunshine+","Sunshine+","Sunshine+","Sunshine+"],
    all_available:["All available phases","Alle verfügbaren Phasen","Toutes les phases disponibles","Alle beschikbare fasen","Tutte le fasi disponibili"],
    single_phase:["Single phase only","Nur eine Phase","Une seule phase","Alleen één fase","Solo monofase"]
  };
  const enums = Object.fromEntries(Object.entries(enumWords).map(([key, names]) => [key, names[column]]));
  const text = key => labels[key]?.[column] || key || "—";
  document.querySelectorAll("[data-text]").forEach(node => { node.textContent = text(node.dataset.text); });
  // The page shows once its texts are in the page language.
  document.documentElement.classList?.add("ready");
  const el = id => document.getElementById(id);
  const fmtTime = stamp => { const d = new Date(stamp); return stamp && Number.isFinite(d.getTime()) ? d.toLocaleString(locale, {timeZoneName:"short"}) : "—"; };
  let rows = [], timer = null, controller = null, generation = 0, stopped = false;
  const visible = () => !document.hidden && !stopped && !el("register_pause").checked;
  function render() {
    const query = el("register_search").value.trim().toLowerCase(), quality = el("register_status").value;
    const selected = rows.filter(row => {
      const q = quality === "all" || (quality === "problems" ? ["error","degraded","unknown"].includes(row.status) : row.status === quality);
      return q && `${row.key} ${row.address} 0x${row.address.toString(16).padStart(4,"0")} ${row.value ?? ""} ${row.enum ?? ""} ${enums[row.enum] ?? ""}`.toLowerCase().includes(query);
    });
    const order = el("register_order").value;
    selected.sort((a,b) => order === "address" ? a.address - b.address : String(a[order] ?? "").localeCompare(String(b[order] ?? "")) || a.address - b.address);
    const body = selected.map(row => {
      const tr = document.createElement("tr");
      const raw = row.redacted ? text("redacted") : Array.isArray(row.raw) ? `${row.raw.map(n=>`0x${n.toString(16).padStart(4,"0").toUpperCase()}`).join(" ")}\n${row.raw.join(" ")}` : "—";
      const translated = row.enum ? enums[row.enum] || row.enum.replaceAll("_"," ") : "";
      const value = row.redacted ? text("redacted") : `${row.value ?? "—"}${row.unit ? ` ${row.unit === "degC" ? "°C" : row.unit}` : ""}${translated ? `\n${translated}` : ""}`;
      const cells = [`0x${row.address.toString(16).padStart(4,"0").toUpperCase()} (${row.address})`, `${row.key}\n${row.datatype} · ${row.access}`, raw, value, fmtTime(row.updated_at), fmtTime(row.attempted_at), `${text(row.status)}${row.reason ? `\n${text(row.reason)}` : ""}`];
      cells.forEach(value => { const td = document.createElement("td"); td.textContent = value; tr.appendChild(td); });
      return tr;
    });
    el("register_rows").replaceChildren(...body);
    el("register_summary").textContent = `${selected.length} / ${rows.length} · OK ${rows.filter(r=>r.status === "ok").length} · ${text("degraded")} ${rows.filter(r=>r.status === "degraded").length} · ${text("error")} ${rows.filter(r=>r.status === "error").length}`;
  }
  function suspend() { generation++; clearTimeout(timer); timer = null; controller?.abort(); controller = null; }
  async function refresh() {
    if (!visible() || controller) return;
    const own = ++generation, abort = new AbortController(); controller = abort;
    const timeout = setTimeout(()=>abort.abort(),8000);
    try {
      const response = await fetch("./api/registers", {cache:"no-store", signal:abort.signal});
      const data = await response.json();
      if (!response.ok || data.schema !== 1 || !Array.isArray(data.registers) || data.registers.length > 256 || data.registers.some(r=>!r || !Number.isInteger(r.address) || !Array.isArray(r.raw ?? []) || (r.raw ?? []).some(n=>!Number.isInteger(n)||n<0||n>65535))) throw new Error("invalid response");
      if (visible() && own === generation) { rows=data.registers; render(); el("register_notice").textContent=text("live"); }
    } catch (_) {
      if (visible() && own === generation) { rows=[]; render(); el("register_notice").textContent=text("offline"); }
    } finally { clearTimeout(timeout); if (own===generation) { controller=null; if (visible()) timer=setTimeout(refresh,5000); } }
  }
  function resume() { suspend(); if (visible()) void refresh(); }
  ["register_search","register_status","register_order"].forEach(id=>el(id).addEventListener("input",render));
  el("register_pause").addEventListener("change",()=>{ suspend(); el("register_notice").textContent=text(el("register_pause").checked ? "frozen" : "live"); if (visible()) void refresh(); });
  document.addEventListener("visibilitychange",()=>{ rows=[]; render(); resume(); });
  addEventListener("pagehide",()=>{ stopped=true; suspend(); });
  addEventListener("pageshow",()=>{ stopped=false; rows=[]; render(); resume(); });
  void refresh();
})();
