/* Local weekly plans: preview/confirm, no automatic POST retry or credentials. */
(() => {
  "use strict";
  const de = (navigator.language || "").toLowerCase().startsWith("de");
  const words = {
    overview:["Overview","Übersicht"], jobs:["Charging jobs","Ladeaufträge"], guest:["Guest charging","Gastladen"], title:["Weekly plans","Wochenpläne"],
    intro:["Save a recipe, inspect its dates, then confirm the vehicle for this connection.","Plan speichern, Termine prüfen und das Fahrzeug für diesen Steckvorgang bestätigen."],
    loading:["Loading…","Wird geladen…"], activation:["Current activation","Aktuelle Aktivierung"], stop:["Stop weekly automation","Wochenautomatik stoppen"],
    stop_hint:["Stops its own job and recurrence. An independent guest or manual job is not changed.","Stoppt den zugehörigen Auftrag und weitere Termine. Ein unabhängiger Gast- oder manueller Auftrag bleibt unverändert."],
    saved_plans:["Saved vehicle plans","Gespeicherte Fahrzeugpläne"], new_plan:["New plan","Neuer Plan"], recipe:["Recipe — not charging permission","Planvorlage — noch keine Ladefreigabe"],
    profile:["Vehicle profile reference","Fahrzeugreferenz"], profile_hint:["Use a stable reference such as bmw-i4. This label does not identify a vehicle automatically.","Eine feste Referenz wie bmw-i4 verwenden. Die Bezeichnung erkennt das Fahrzeug nicht automatisch."],
    timezone:["Timezone","Zeitzone"], weekdays:["Weekdays","Wochentage"], departure:["Departure","Abfahrt"], window:["Charging window (minutes)","Ladefenster (Minuten)"],
    current:["Maximum current (A)","Maximaler Strom (A)"], energy:["Energy ceiling per departure (kWh)","Energieobergrenze je Abfahrt (kWh)"], fold:["Repeated clock time","Doppelt vorkommende Uhrzeit"],
    earlier:["Earlier occurrence","Früherer Zeitpunkt"], later:["Later occurrence","Späterer Zeitpunkt"], exceptions:["Date exceptions","Einmalige Ausnahmen"],
    exception_hint:["One line per date: YYYY-MM-DD = skip or HH:MM. Up to 32 dates. Overrides can also add a departure.","Je Zeile: JJJJ-MM-TT = skip oder HH:MM. Maximal 32 Tage. Eine Uhrzeit kann auch eine zusätzliche Abfahrt festlegen."],
    enabled:["Enable recipe","Planvorlage aktivieren"], preview:["Preview changes","Änderungen prüfen"], remove:["Remove saved plan","Gespeicherten Plan entfernen"],
    proposal:["Confirm recipe","Plan bestätigen"], save:["Save this recipe","Diesen Plan speichern"], activate_title:["Activate saved plan for this vehicle","Gespeicherten Plan für dieses Fahrzeug aktivieren"],
    consent_hint:["Recurrence applies only to this connection, for at most seven days. Each departure has its own energy ceiling. Restart, unplugging, a manual stop or editing the active recipe ends it.","Die Wiederholung gilt nur für diesen Steckvorgang, höchstens sieben Tage. Jede Abfahrt hat eine eigene Energieobergrenze. Neustart, Abstecken, manueller Stopp oder Änderung des aktiven Plans beenden sie."],
    confirm_vehicle:["I have checked that the selected vehicle is connected.","Ich habe geprüft, dass das ausgewählte Fahrzeug angeschlossen ist."],
    activate_preview:["Preview activation","Aktivierung prüfen"], activate:["Confirm activation","Aktivierung bestätigen"], open_job:["View or stop the accepted charging job","Angenommenen Ladeauftrag ansehen oder stoppen"],
    footer:["No automatic authorization or unlocking. Missing local clock times are not silently moved. Actual starts and stops remain subject to wallbox timing.","Keine automatische Autorisierung oder Entriegelung. Nicht existierende Uhrzeiten werden nicht verschoben. Tatsächliche Starts und Stopps unterliegen den Schaltzeiten der Wallbox."],
    offline:["No current service response. Reload before changing anything.","Keine aktuelle Service-Antwort. Vor Änderungen erneut laden."],
    unconfirmed:["Result unconfirmed. Reload to check the actual state; the command is not retried automatically.","Ergebnis unbestätigt. Den tatsächlichen Zustand neu laden; der Befehl wird nicht automatisch wiederholt."],
    live:["Current service state loaded.","Aktueller Service-Zustand geladen."], invalid:["Check the fields, weekdays and date exceptions.","Bitte Eingaben, Wochentage und Datums-Ausnahmen prüfen."],
    edit_stop:["Saving this edit stops the active weekly job and its recurrence.","Diese Änderung stoppt den aktiven Wochenplan-Auftrag und seine Wiederholung."],
    remove_confirm:["Remove this recipe? Its active weekly job will also be stopped. Consumed dates are retained.","Plan entfernen? Sein aktiver Wochenplan-Auftrag wird ebenfalls gestoppt. Verbrauchte Termine bleiben gespeichert."],
    saved:["Recipe saved. Activation is a separate confirmation.","Plan gespeichert. Die Aktivierung muss separat bestätigt werden."],
    active:["Active for this connection","Für diesen Steckvorgang aktiv"], inactive:["Not activated","Nicht aktiviert"],
    consumed:["Already consumed","Bereits verbraucht"], skipped:["Skipped","Ausgesetzt"], ready:["Scheduled window","Geplantes Ladefenster"], invalid_time:["Local time does not exist","Ortszeit existiert nicht"],
    weekly_changed:["State changed. Reload and preview again.","Zustand geändert. Neu laden und erneut prüfen."],
    job_changed:["Charging state changed. Preview again.","Ladezustand geändert. Erneut prüfen."],
    weekly_storage_unavailable:["Weekly-plan storage is unavailable or unconfirmed. Restart and inspect storage before activating.","Wochenplan-Speicher nicht verfügbar oder unbestätigt. Vor Aktivierung neu starten und Speicher prüfen."],
    weekly_confirmation_expired:["Confirmation expired. Preview again.","Bestätigung abgelaufen. Erneut prüfen."],
    weekly_no_occurrence:["No unconsumed valid departure within seven days.","Keine unverbrauchte gültige Abfahrt innerhalb von sieben Tagen."],
    weekly_vehicle_not_confirmed:["Vehicle or connection changed. Confirm the current vehicle again.","Fahrzeug oder Verbindung geändert. Aktuelles Fahrzeug erneut bestätigen."],
    job_exists:["An existing job must be stopped/cleared explicitly first.","Ein vorhandener Auftrag muss zuerst ausdrücklich gestoppt bzw. entfernt werden."],
    job_not_permitted:["Check connection, meter, authorization and independent stops.","Verbindung, Zähler, Autorisierung und unabhängige Sperren prüfen."],
    job_current_exceeds_cap:["Requested current exceeds the service limit.","Gewünschter Strom überschreitet die Service-Grenze."],
    until:["Valid until","Gültig bis"], first:["First departure","Erste Abfahrt"], changed_preview:["The state changed; the previous preview was discarded.","Der Zustand wurde geändert; die vorherige Vorschau wurde verworfen."]
  };
  const t = key => words[key]?.[de ? 1 : 0] || key;
  const el = id => document.getElementById(id);
  const days = de ? ["Mo","Di","Mi","Do","Fr","Sa","So"] : ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
  document.documentElement.lang = de ? "de" : "en";
  document.querySelectorAll("[data-text]").forEach(node => { node.textContent = t(node.dataset.text); });
  document.querySelectorAll("[data-day]").forEach(node => { node.textContent = days[Number(node.dataset.day)]; });
  const dayInputs = () => [...document.querySelectorAll('input[name="weekday"]')];
  let state = null, csrf = "", selected = "", dirty = false, planProposal = null, activation = null, loadedRecipe;
  let busy = false, timer = null, controller = null, generation = 0, stopped = false;
  const visible = () => !document.hidden && !stopped;
  const plan = () => state?.plans.find(item => item.plan.id === selected)?.plan || null;
  const stamp = value => { const date = new Date(value); return Number.isFinite(date.getTime()) ? date.toLocaleString(de ? "de-DE" : "en", {timeZoneName:"short"}) : "—"; };
  function notice(text, error = false) { el("weekly_notice").textContent = text; el("weekly_notice").dataset.error = String(error); }
  function discard() { planProposal = null; activation = null; el("weekly_preview").hidden = true; el("activation_preview").hidden = true; }
  function controls() {
    const usable = Boolean(state?.ready && !state.storage_error && !busy);
    el("weekly_fields").disabled = !usable;
    el("plan_select").disabled = !usable;
    el("confirm_vehicle").disabled = !usable;
    el("preview_activation").disabled = !usable || !plan() || dirty || !el("confirm_vehicle").checked || Boolean(state?.active);
    el("activate").disabled = !usable || !activation;
    el("save_plan").disabled = !usable || !planProposal;
    el("remove_plan").disabled = !usable || !plan();
    el("deactivate").disabled = !usable || !state?.active;
  }
  function fill(recipe) {
    loadedRecipe = JSON.stringify(recipe);
    for (const id of ["vehicle_profile","timezone","departure_time","charge_minutes","current_a","energy_kwh","ambiguous_time"]) {
      el(id).value = recipe ? recipe[id] : ({vehicle_profile:"",timezone:"Europe/Berlin",departure_time:"07:40",charge_minutes:240,current_a:6,energy_kwh:10,ambiguous_time:"earlier"})[id];
    }
    dayInputs().forEach(node => { node.checked = (recipe?.weekdays || [0,3]).includes(Number(node.value)); });
    el("enabled").checked = recipe?.enabled ?? true;
    el("exceptions").value = (recipe?.exceptions || []).map(item=>`${item.date} = ${item.departure_time ?? "skip"}`).join("\n");
    el("confirm_vehicle").checked = false;
  }
  function receive(data) {
    if (data.schema !== 1 || !Array.isArray(data.plans) || data.plans.length > 16 || typeof data.revision !== "string") throw new Error("invalid");
    const revisionChanged = state && state.revision !== data.revision;
    if (revisionChanged) discard();
    state = data; if (typeof data.csrf === "string") csrf = data.csrf;
    if (selected && !data.plans.some(item=>item.plan.id === selected)) { selected=""; dirty=false; }
    const opts = [new Option(t("new_plan"),""), ...data.plans.map(item=>new Option(item.plan.vehicle_profile,item.plan.id))];
    el("plan_select").replaceChildren(...opts); el("plan_select").value=selected;
    if (!dirty && loadedRecipe !== JSON.stringify(plan())) fill(plan());
    el("weekly_active").hidden = !data.active;
    el("active_description").textContent = data.active ? `${data.active.vehicle_profile}\n${t("until")}: ${stamp(data.active.until)}` : "";
    controls();
  }
  function rows(values) {
    el("upcoming_rows").replaceChildren(...values.map(row=>{
      const li=document.createElement("li");
      li.textContent=`${row.date} · ${row.departure_time} · ${row.timezone}\n${row.consumed ? t("consumed") : t(row.status)}${row.start_at ? ` · ${stamp(row.start_at)} → ${stamp(row.finish_at)}` : ""}`;
      return li;
    }));
  }
  function suspend() { generation++; clearTimeout(timer); timer=null; controller?.abort(); controller=null; }
  function schedule() { clearTimeout(timer); if(visible() && !busy) timer=setTimeout(refresh,5000); }
  async function readJson(response) { const data=await response.json(); if(!response.ok) throw new Error(data.error || "unconfirmed"); return data; }
  async function refresh() {
    if (!visible() || busy || controller) return;
    const own=++generation, abort=new AbortController(); controller=abort;
    const timeout=setTimeout(()=>abort.abort(),8000);
    try {
      const data=await readJson(await fetch("./api/weekly",{cache:"no-store",signal:abort.signal}));
      if(own===generation && visible()) { receive(data); notice(data.storage_error ? t("weekly_storage_unavailable") : t("live"),data.storage_error); }
    } catch (_) {
      if(own===generation && visible()) { state=null; csrf=""; discard(); el("weekly_active").hidden=true; controls(); notice(t("offline"),true); }
    } finally { clearTimeout(timeout); if(own===generation) { controller=null; schedule(); } }
  }
  async function post(payload, apply) {
    if (busy || !state || !csrf || !visible()) return;
    suspend(); const own=generation, abort=new AbortController(); controller=abort;
    busy=true; controls(); const timeout=setTimeout(()=>abort.abort(),10000);
    try {
      const data=await readJson(await fetch("./api/weekly",{method:"POST",headers:{"Content-Type":"application/json","X-Amtron-CSRF":csrf},body:JSON.stringify(payload),signal:abort.signal}));
      if(own===generation && visible()) apply(data);
    } catch (error) {
      if(own===generation && visible()) { state=null; csrf=""; discard(); notice(`${t(error.message)}\n${t("unconfirmed")}`,true); }
    } finally { clearTimeout(timeout); busy=false; if(own===generation) controller=null; controls(); schedule(); }
  }
  function inputRecipe() {
    if(!el("weekly_form").reportValidity()) throw new Error("invalid");
    const weekdays=dayInputs().filter(n=>n.checked).map(n=>Number(n.value));
    if(!weekdays.length) throw new Error("invalid");
    const exceptions=el("exceptions").value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean).map(line=>{
      const match=/^(\d{4}-\d{2}-\d{2})\s*=\s*(skip|(?:[01]\d|2[0-3]):[0-5]\d)$/.exec(line);
      if(!match) throw new Error("invalid");
      return {date:match[1],departure_time:match[2]==="skip" ? null : match[2]};
    });
    if(exceptions.length>32) throw new Error("invalid");
    const id=selected || crypto.randomUUID().replaceAll("-","");
    return {id,vehicle_profile:el("vehicle_profile").value,timezone:el("timezone").value,weekdays,
            departure_time:el("departure_time").value,charge_minutes:Number(el("charge_minutes").value),
            current_a:Number(el("current_a").value),energy_kwh:Number(el("energy_kwh").value),
            ambiguous_time:el("ambiguous_time").value,enabled:el("enabled").checked,exceptions};
  }
  el("plan_select").addEventListener("change",()=>{selected=el("plan_select").value;dirty=false;discard();fill(plan());controls();});
  el("weekly_form").addEventListener("input",()=>{dirty=true;discard();controls();});
  el("confirm_vehicle").addEventListener("change",()=>{activation=null;el("activation_preview").hidden=true;controls();});
  el("weekly_form").addEventListener("submit",event=>{
    event.preventDefault(); if(!state || busy) return;
    let recipe; try {recipe=inputRecipe();} catch (_) {notice(t("invalid"),true);return;}
    void post({action:"preview_plan",revision:state.revision,plan:recipe},data=>{
      planProposal=data;el("weekly_preview").hidden=false;
      el("plan_summary").textContent=`${data.plan.vehicle_profile} · ${data.plan.current_a} A · ${data.plan.energy_kwh} kWh\n${data.plan.timezone} · ${data.plan.charge_minutes} min`;
      el("plan_warning").textContent=data.will_stop_active ? t("edit_stop") : ""; rows(data.upcoming);
    });
  });
  el("save_plan").addEventListener("click",()=>{if(!planProposal)return; const p=planProposal;void post({action:"save_plan",revision:p.revision,plan:p.plan,confirm:true},data=>{selected=p.plan.id;dirty=false;discard();receive(data);notice(t("saved"));});});
  el("remove_plan").addEventListener("click",()=>{if(!state || !plan() || !confirm(t("remove_confirm")))return;void post({action:"remove_plan",revision:state.revision,plan_id:selected,confirm:true},data=>{dirty=false;selected="";discard();receive(data);notice(t("live"));});});
  el("preview_activation").addEventListener("click",()=>{
    const saved=plan(); if(!state || !saved || dirty || !el("confirm_vehicle").checked)return;
    void post({action:"preview_activation",revision:state.revision,scope:state.scope,plan_id:saved.id,vehicle_profile:saved.vehicle_profile},data=>{
      activation=data;el("activation_preview").hidden=false;
      el("activation_summary").textContent=`${data.vehicle_profile}\n${t("first")}: ${stamp(data.request.finish_at)}\n${data.request.current_a} A · ${data.request.energy_kwh} kWh\n${t("until")}: ${stamp(data.active_until)}`;
    });
  });
  el("activate").addEventListener("click",()=>{if(!activation)return;const proposal=activation;void post({action:"activate",proposal,confirm:true},data=>{discard();receive(data);notice(t("active"));});});
  el("deactivate").addEventListener("click",()=>{if(!state)return;void post({action:"deactivate",revision:state.revision,confirm:true},data=>{discard();receive(data);notice(t("inactive"));});});
  document.addEventListener("visibilitychange",()=>{suspend();discard();state=null;csrf="";controls();if(visible())void refresh();});
  addEventListener("pagehide",()=>{stopped=true;suspend();discard();state=null;controls();});
  addEventListener("pageshow",()=>{stopped=false;suspend();discard();void refresh();});
  controls(); void refresh();
})();
