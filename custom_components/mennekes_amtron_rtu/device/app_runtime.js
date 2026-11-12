/* Explicit preview/confirm flow. Never retry a configuration POST automatically. */
(() => {
  "use strict";
  const de = (navigator.language || "").toLowerCase().startsWith("de");
  document.documentElement.lang = de ? "de" : "en";
  const names = ["scan_interval", "measurement_interval", "current_cap", "state_change_interval_seconds", "phase_switch_grace_seconds"];
  const labels = {
    title:["Runtime settings","Betriebseinstellungen"], overview:["Overview","Übersicht"], registers:["Registers","Register"], connection:["Connection","Verbindung"],
    intro:["Preview every change before saving. Polling intervals apply at the next ordinary cycle; the current poll or wait is not interrupted. Control limits require a service restart.","Vor dem Speichern jede Änderung prüfen. Pollingintervalle gelten ab dem nächsten regulären Zyklus; laufende Abfragen und Wartezeiten werden nicht unterbrochen. Steuerungsgrenzen benötigen einen Service-Neustart."],
    setting:["Setting","Einstellung"], effective:["Effective","Wirksam"], saved:["Saved","Gespeichert"],
    scan_interval:["Status interval (s)","Statusintervall (s)"], measurement_interval:["Measurement interval (s)","Messintervall (s)"], current_cap:["Service current cap (A)","Service-Stromobergrenze (A)"],
    state_change_interval_seconds:["State-change interval (s)","Mindestabstand Zustandswechsel (s)"], phase_switch_grace_seconds:["Phase-switch grace (s)","Wartezeit Phasenwechsel (s)"],
    restart:["Requires restart","Neustart erforderlich"], live:["Next polling cycle","Nächster Pollzyklus"], preview:["Change preview","Änderungen prüfen"], save:["Confirm and save","Bestätigen und speichern"], reload:["Reload","Neu laden"],
    limits:["Saving a lower limit does not immediately reduce current. Until restart the effective limit above remains in force. No automatic restart is performed.","Eine gespeicherte niedrigere Grenze reduziert den Ladestrom nicht sofort. Bis zum Neustart gilt die oben angezeigte wirksame Grenze weiter. Es erfolgt kein automatischer Neustart."],
    loading:["Loading settings…","Einstellungen werden geladen…"], ready:["Settings loaded. Changes need confirmation.","Einstellungen geladen. Änderungen müssen bestätigt werden."],
    no_changes:["No changes.","Keine Änderungen."], no_restart:["No restart needed for this change.","Für diese Änderung ist kein Neustart nötig."],
    done:["Saved. Effective and saved values are shown separately below.","Gespeichert. Wirksame und gespeicherte Werte stehen getrennt in der Tabelle."],
    error:["Request failed. No automatic retry. Reload to check the actual saved and effective values.","Anfrage fehlgeschlagen. Kein automatischer Wiederholungsversuch. Neu laden, um gespeicherte und wirksame Werte zu prüfen."],
    blocked:["A previous save has an uncertain outcome. Saving is disabled until the service is restarted.","Ein früherer Speichervorgang hat einen ungewissen Ausgang. Weitere Änderungen sind bis zum Service-Neustart gesperrt."],
    read_only:["Read-only mode","Nur-Lesen-Modus"], listen:["Listen address","Listen-Adresse"], port:["API port","API-Port"]
  };
  const text = key => labels[key]?.[de ? 1 : 0] || key;
  document.querySelectorAll("[data-text]").forEach(node => { node.textContent = text(node.dataset.text); });
  const el = id => document.getElementById(id);
  let state = null, pending = null, busy = false, blocked = false;
  function buttons() { names.forEach(key=>{el(key).disabled=busy||blocked;}); el("runtime_check").disabled=busy||blocked; el("runtime_reload").disabled=busy; el("runtime_save").disabled=busy||blocked||!pending; }
  function invalidate() { pending=null; el("runtime_preview").hidden=true; buttons(); }
  function values(data) {
    if (!data || data.schema!==1 || !data.active || !data.saved || !Array.isArray(data.restart_fields)) throw new Error("invalid settings");
    const rows=names.map(key=>{ const tr=document.createElement("tr"); [text(key),data.active[key],data.saved[key]].forEach(value=>{const td=document.createElement("td");td.textContent=String(value);tr.appendChild(td);});return tr; });
    el("runtime_values").replaceChildren(...rows);
    el("runtime_restart").textContent=data.restart_required ? `${text("restart")}: ${data.restart_fields.map(text).join(", ")}` : text("no_restart");
  }
  async function request(path, payload) {
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),10000);
    try {
      const options={cache:"no-store",signal:controller.signal};
      if (payload) Object.assign(options,{method:"POST",headers:{"Content-Type":"application/json","X-Amtron-CSRF":state.csrf},body:JSON.stringify(payload)});
      const response=await fetch(path,options), data=await response.json();
      if (!response.ok) throw new Error("request failed");
      return data;
    } finally { clearTimeout(timeout); }
  }
  async function load() {
    if (busy) return;
    busy=true; invalidate(); el("runtime_error").hidden=true; el("runtime_notice").textContent=text("loading");
    try {
      const data=await request("./api/runtime"); values(data); state=data; blocked=data.write_blocked===true;
      names.forEach(key=>{el(key).value=String(data.saved[key]);});
      el("runtime_form").hidden=false; el("runtime_notice").textContent=text(blocked ? "blocked" : "ready");
    } catch (_) { state=null;blocked=true;el("runtime_form").hidden=false;el("runtime_values").replaceChildren();names.forEach(key=>{el(key).value="";});el("runtime_notice").textContent=text("error"); }
    finally { busy=false; buttons(); }
  }
  el("runtime_form").addEventListener("input",invalidate);
  el("runtime_form").addEventListener("submit",async event=>{
    event.preventDefault(); if (busy||blocked||!state||!el("runtime_form").reportValidity()) return;
    const changes=Object.fromEntries(names.map(key=>[key,Number(el(key).value)]));
    busy=true; invalidate(); el("runtime_error").hidden=true;
    try {
      const data=await request("./api/runtime/preview",{revision:state.revision,changes}); values(data);
      pending={revision:data.revision,changes};
      const rows=data.changes.map(change=>{const li=document.createElement("li");li.textContent=`${text(change.key)}: ${change.effective} → ${change.proposed} · ${text(change.effect)}`;return li;});
      if (!rows.length) { const li=document.createElement("li");li.textContent=text("no_changes");rows.push(li); }
      el("runtime_changes").replaceChildren(...rows); el("runtime_preview").hidden=false;
    } catch (_) { el("runtime_error").textContent=text("error");el("runtime_error").hidden=false; }
    finally {busy=false;buttons();}
  });
  el("runtime_save").addEventListener("click",async()=>{
    if (busy||blocked||!pending) return;
    const payload={...pending,confirm:true};busy=true;buttons();el("runtime_error").hidden=true;
    try {
      const data=await request("./api/runtime",payload);values(data);state={...state,...data};
      pending=null;el("runtime_preview").hidden=false;el("runtime_changes").replaceChildren();el("runtime_notice").textContent=text("done");
    } catch (_) {pending=null;blocked=true;el("runtime_error").textContent=text("error");el("runtime_error").hidden=false;}
    finally {busy=false;buttons();}
  });
  el("runtime_reload").addEventListener("click",()=>void load());
  void load();
})();
