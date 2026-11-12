/* Passive register explorer. No credentials, writes or external dependencies. */
(() => {
  "use strict";
  const de = (navigator.language || "").toLowerCase().startsWith("de");
  document.documentElement.lang = de ? "de" : "en";
  const labels = {
    overview:["Overview","Übersicht"], connection:["Connection","Verbindung"], registers:["Registers","Register"],
    search:["Filter by address, name or value","Nach Adresse, Name oder Wert filtern"],
    explanation:["Passive observations only. No extra Modbus transactions. Last valid values keep their original timestamps after failures.","Passive Beobachtung ohne zusätzliche Modbus-Zugriffe. Letzte gültige Werte behalten bei Fehlern ihren ursprünglichen Zeitstempel."],
    quality:["OK describes a successful read, not a fault-free wallbox. Writes are acknowledgements, not readbacks. Serial number redacted.","OK bedeutet erfolgreich gelesen, nicht fehlerfreie Wallbox. Schreibbestätigungen sind keine Rücklesewerte. Die Seriennummer ist ausgeblendet."],
    all:["All","Alle"], problems:["Problems","Auffälligkeiten"], status:["Quality","Datenqualität"], sort:["Sort","Sortierung"],
    address:["Address","Adresse"], name:["Register / type","Register / Datentyp"], raw:["Raw words (hex / decimal)","Rohwörter (Hex / Dezimal)"],
    value:["Decoded / translated","Dekodiert / übersetzt"], updated:["Last successful read","Letzte erfolgreiche Abfrage"], attempt:["Last attempt","Letzter Versuch"],
    freeze:["Freeze display (bus polling continues)","Anzeige anhalten (Bus-Abfragen laufen weiter)"],
    offline:["No current service response. Table hidden until recovery.","Keine aktuelle Service-Antwort. Tabelle bis zur Wiederherstellung ausgeblendet."],
    live:["Live · updates every five seconds","Live · Aktualisierung alle fünf Sekunden"],
    frozen:["Display frozen — values and quality are not current","Anzeige angehalten — Werte und Qualität sind nicht aktuell"],
    ok:["OK","OK"], degraded:["Degraded","Eingeschränkt"], error:["Error","Fehler"], unknown:["Unknown","Unbekannt"],
    unsupported:["Unsupported","Nicht unterstützt"], write_only:["Write only","Nur schreibbar"], redacted:["Redacted","Ausgeblendet"],
    overdue:["Refresh overdue","Aktualisierung überfällig"], wallbox_unavailable:["Wallbox unavailable","Wallbox nicht erreichbar"],
    not_read:["Not read yet","Noch nicht gelesen"], layout_unknown:["Layout unknown","Registerlayout unbekannt"],
    layout_unsupported:["Not supported by layout","Im Registerlayout nicht unterstützt"], no_readback:["No readback","Kein Rücklesewert"],
    unknown_enum:["Unknown enumeration value","Unbekannter Zustandswert"], invalid_value:["Invalid decoded value","Ungültiger dekodierter Wert"], invalid_response:["Invalid response","Ungültige Antwort"],
    timeout:["Timeout","Zeitüberschreitung"], protocol:["Protocol error","Protokollfehler"], device_exception:["Modbus exception","Modbus-Ausnahme"], cancelled:["Cancelled","Abgebrochen"], unexpected:["Unexpected error","Unerwarteter Fehler"]
  };
  const enums = {
    not_initialized:"Nicht initialisiert", idle:"Bereit / kein Fahrzeug", ev_connected:"Fahrzeug verbunden", preconditions_valid:"Voraussetzungen erfüllt", ready_to_charge:"Ladebereit", charging:"Lädt", error:"Fehler", service_mode:"Servicemodus",
    not_used:"Nicht verwendet", authorized:"Autorisiert", not_authorized:"Nicht autorisiert", not_relevant:"Nicht relevant", not_downgraded:"Nicht reduziert", downgraded:"Reduziert",
    init:"Initialisierung", solar_one_phase:"Solar: eine Phase", solar_three_phases:"Solar: drei Phasen", solar_dynamic:"Solar: dynamisch",
    one_phase_only:"Nur eine Phase", three_phases_only:"Nur drei Phasen", one_or_three_phases:"Eine oder drei Phasen", one_phase:"Eine Phase", two_phases:"Zwei Phasen", three_phases:"Drei Phasen",
    undetermined:"Unbestimmt", unlocked:"Entriegelt", locked:"Verriegelt", fixed_cable:"Fest angeschlagenes Kabel", not_active:"Nicht aktiv", fast:"Schnellladen", sunshine:"Sunshine", sunshine_plus:"Sunshine+", all_available:"Alle verfügbaren Phasen", single_phase:"Nur eine Phase"
  };
  const text = key => labels[key]?.[de ? 1 : 0] || key || "—";
  document.querySelectorAll("[data-text]").forEach(node => { node.textContent = text(node.dataset.text); });
  const el = id => document.getElementById(id);
  const fmtTime = stamp => { const d = new Date(stamp); return stamp && Number.isFinite(d.getTime()) ? d.toLocaleString(de ? "de-DE" : "en", {timeZoneName:"short"}) : "—"; };
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
      const translated = row.enum ? (de ? enums[row.enum] || row.enum.replaceAll("_"," ") : row.enum.replaceAll("_"," ")) : "";
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
