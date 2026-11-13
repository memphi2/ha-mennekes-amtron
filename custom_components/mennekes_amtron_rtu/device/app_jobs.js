/* Local job UI. The server remains the sole authority; never retry POSTs. */
(() => {
  "use strict";
  const de = (navigator.language || "").toLowerCase().startsWith("de");
  document.documentElement.lang = de ? "de" : "en";
  const words = {
    overview:["Overview","Übersicht"], jobs:["Charging jobs","Ladeaufträge"], runtime:["Runtime","Betrieb"], registers:["Registers","Register"], connection:["Connection","Verbindung"],
    intro:["A local time window with an additional-energy ceiling. Existing release and battery limits remain in force.","Ein lokales Zeitfenster mit zusätzlicher Energieobergrenze. Bestehende Freigabe- und Batteriegrenzen bleiben wirksam."],
    active:["Current order","Aktueller Auftrag"], stop:["Stop automation for this connection","Automatik für diesen Anschluss stoppen"], clear:["Clear stopped order without resuming","Gestoppten Auftrag löschen – ohne Ladestart"],
    new:["New order","Neuer Auftrag"], window:["Local charging window","Lokales Ladezeitfenster"], start:["Start","Beginn"], finish:["Finish no later than","Spätestes Ende"], energy:["Additional energy, kWh","Zusätzliche Energie in kWh"], current:["Current per phase, A","Strom je Phase in A"],
    scope:["Applies only to this confirmed physical connection. The order does not identify or authorize the vehicle. Reconnection or service restart requires a new confirmation.","Gilt nur für diesen bestätigten Anschluss. Der Auftrag identifiziert oder autorisiert kein Fahrzeug. Nach Verbindungsverlust oder Service-Neustart ist eine neue Bestätigung erforderlich."],
    preview:["Preview","Vorschau"], confirm_title:["Confirm this action","Aktion bestätigen"], confirm:["Confirm this action","Diese Aktion bestätigen"],
    limits:["This is not a guaranteed energy delivery or finish-time forecast. The vehicle and all existing safety limits remain authoritative.","Keine garantierte Energiemenge oder Fertigstellungsprognose. Das Fahrzeug und alle bestehenden Schutzgrenzen bleiben maßgeblich."],
    footer:["A requested stop is not a confirmed physical stop. Check the overview and command results. No automatic retry after an uncertain browser response.","Ein angeforderter Stopp ist kein bestätigter physischer Stopp. Übersicht und Befehlsergebnisse beachten. Keine automatische Wiederholung bei ungewisser Browser-Antwort."],
    loading:["Reading service state…","Servicestatus wird gelesen …"], offline:["No current service response. Reload before changing an order.","Keine aktuelle Service-Antwort. Vor Änderungen neu laden."],
    ready:["Ready. Preview an order for the connected vehicle.","Bereit. Auftrag für das angeschlossene Fahrzeug in der Vorschau prüfen."], unavailable:["A fresh meter, connected vehicle and existing release are required.","Aktueller Zählerstand, angeschlossenes Fahrzeug und bestehende Ladefreigabe erforderlich."],
    saved:["Order accepted; actual charging remains subject to device conditions.","Auftrag angenommen; die tatsächliche Ladung bleibt von den Gerätebedingungen abhängig."], uncertain:["Operation could not be confirmed. Read the current state before retrying.","Vorgang nicht bestätigt. Vor einem erneuten Versuch den aktuellen Zustand prüfen."],
    scheduled:["Waiting for the window","Wartet auf das Zeitfenster"], charging:["Charging requested","Laden angefordert"], waiting_meter:["Waiting for a valid meter reading","Wartet auf gültigen Zählerstand"], reached:["Energy ceiling reached","Energieobergrenze erreicht"], finished:["Time window ended","Zeitfenster beendet"], stopped:["Stopped by user","Vom Benutzer gestoppt"], interrupted:["Interrupted; no automatic restart","Unterbrochen; kein automatischer Neustart"],
    user_stop:["User stop","Benutzerstopp"], service_restarted:["Service restarted","Service neu gestartet"], connection_lost:["Connection lost or ambiguous","Verbindung verloren oder unklar"], vehicle_changed:["Connection or vehicle binding changed","Anschluss oder Fahrzeugzuordnung geändert"], independent_stop:["Another charging limit requires a stop","Eine andere Ladegrenze verlangt den Stopp"], meter_reset:["Meter reset","Zählerrücksetzung"], clock_changed:["Clock moved backwards","Uhrzeit zurückgesprungen"], storage_error:["Storage could not be confirmed; starts blocked","Speicherung nicht bestätigt; Ladestarts gesperrt"], meter_unavailable:["Meter unavailable","Zähler nicht verfügbar"], energy_reached:["Energy ceiling reached","Energieobergrenze erreicht"], window_ended:["Window ended","Zeitfenster beendet"], goal_changed:["Charging target changed","Ladeziel geändert"],
    no_job:["No order","Kein Auftrag"], job_changed:["State changed. Reload and preview again.","Zustand geändert. Neu laden und Vorschau wiederholen."], job_exists:["Stop and clear the existing order first.","Zuerst den vorhandenen Auftrag stoppen und löschen."], job_not_permitted:["Connection or release changed; no order was created.","Anschluss oder Freigabe geändert; kein Auftrag angelegt."], job_current_exceeds_cap:["Current exceeds the service limit.","Strom überschreitet die Servicegrenze."], job_clear_requires_pause:["Clearing requires a confirmed pause or unplug.","Löschen erfordert eine bestätigte Pause oder abgezogenes Fahrzeug."], job_stop_first:["Stop the active order first.","Zuerst den laufenden Auftrag stoppen."], job_storage_unavailable:["Storage unavailable; no automatic start.","Speicher nicht verfügbar; kein automatischer Start."], invalid_job_request:["Invalid order. Check time, current and energy.","Ungültiger Auftrag. Zeit, Strom und Energie prüfen."], command_unconfirmed:["Command not confirmed yet","Befehl noch nicht bestätigt"], end_scope:["Connection ended","Anschluss beendet"],
    boost_title:["Temporary early charging","Vorübergehend früher laden"], boost_hint:["Charge before the scheduled start, then return to the original plan. Current and energy limits stay unchanged.","Vor dem geplanten Start laden, danach zum ursprünglichen Plan zurückkehren. Strom- und Energiegrenzen bleiben unverändert."],
    boost_minutes:["Duration in minutes (1–120)","Dauer in Minuten (1–120)"], boost_preview:["Preview boost","Boost-Vorschau"], boost_cancel:["Return to plan now","Jetzt zum Plan zurückkehren"], boost_until:["Boost until","Boost bis"],
    job_boost_unavailable:["Boost requires an active order before its planned start and current permission.","Boost erfordert einen aktiven Auftrag vor dessen geplantem Start und eine aktuelle Freigabe."], invalid_boost_duration:["Enter a whole number from 1 to 120 minutes.","Eine ganze Zahl von 1 bis 120 Minuten eingeben."]
  };
  const t = k => words[k]?.[de ? 1 : 0] || k;
  document.querySelectorAll("[data-text]").forEach(n => { n.textContent = t(n.dataset.text); });
  const el = id => document.getElementById(id), notice = message => { el("job_notice").textContent = message; };
  const date = value => new Date(value).toLocaleString(de ? "de-DE" : "en", {timeZoneName:"short"});
  const local = d => { const p = n=>String(n).padStart(2,"0"); return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
  let state = null, proposal = null, busy = false, timer = null, readAbort = null, generation = 0, hidden = false;
  el("job_start").value = local(new Date(Date.now()+60000));
  el("job_finish").value = local(new Date(Date.now()+8*3600000));
  function invalidate() { proposal=null; el("job_confirmation").hidden=true; }
  function render() {
    const good = state?.schema === 1 && state.ready && !state.storage_error;
    el("job_status").hidden = !state;
    el("job_fields").disabled = busy || !good || !state.available || state.present !== true || !state.permitted || !state.meter_fresh || Boolean(state.job && !state.job.scope_ended);
    el("job_current").max=String(state?.max_current_a || 6);
    el("job_stop").disabled=busy || !state?.job;
    el("job_clear").disabled=busy || !state || (!state.job && !state.storage_error);
    el("job_confirm").disabled=busy || !proposal;
    el("job_boost_preview").disabled=busy || state?.boost_available !== true;
    el("job_boost_minutes").disabled=busy || state?.boost_available !== true;
    el("job_cancel_boost").disabled=busy || !state?.job?.boost_until;
    const job=state?.job;
    el("job_state").textContent=job ? t(job.state) : t("no_job");
    el("job_detail").textContent=job ? `${t(job.reason)} · ${date(job.start_at)} → ${date(job.finish_at)} · ${job.current_a} A${job.scope_ended ? ` · ${t("end_scope")}` : ""}` : "";
    el("job_progress").textContent=job ? `${job.delivered_kwh == null ? "—" : Number(job.delivered_kwh).toLocaleString(undefined,{maximumFractionDigits:2})} / ${job.energy_kwh} kWh${state.action_error ? ` · ${t(state.action_error)}` : ""}` : "";
    el("job_boost_until").textContent=job?.boost_until ? `${t("boost_until")} ${date(job.boost_until)}` : "";
  }
  function suspend() { generation++; clearTimeout(timer); timer=null; readAbort?.abort(); readAbort=null; }
  async function read() {
    if (busy || hidden || document.hidden || readAbort) return;
    const own=++generation, abort=new AbortController();readAbort=abort;
    const timeout=setTimeout(()=>abort.abort(),8000);
    try {
      const response=await fetch("./api/jobs",{cache:"no-store",signal:abort.signal});
      const data=await response.json();
      if (!response.ok || data.schema!==1 || typeof data.revision!=="string" || typeof data.csrf!=="string") throw new Error("invalid");
      if (own!==generation || busy) return;
      if (proposal && proposal.revision!==data.revision) invalidate();
      state=data;render();notice(data.storage_error ? t("storage_error") : data.job ? t(data.job.state) : data.available && data.permitted && data.meter_fresh ? t("ready") : t("unavailable"));
    } catch (_) { if(own===generation){ state=null;invalidate();render();notice(t("offline")); } }
    finally {clearTimeout(timeout);if(own===generation){readAbort=null;if(!hidden&&!document.hidden)timer=setTimeout(read,5000);}}
  }
  async function post(payload) {
    if (busy || !state) return null;
    busy=true;suspend();render();
    const own=generation, abort=new AbortController(), timeout=setTimeout(()=>abort.abort(),15000);
    try {
      const response=await fetch("./api/jobs",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json","X-Amtron-CSRF":state.csrf},body:JSON.stringify(payload),signal:abort.signal});
      const data=await response.json();
      if(own!==generation || hidden || document.hidden)return null;
      if(!response.ok){notice(t(data.error || "uncertain"));return null;}
      return data;
    } catch (_) { notice(t("uncertain"));return null; }
    finally {clearTimeout(timeout);busy=false;render();}
  }
  el("job_form").addEventListener("input",invalidate);
  el("job_boost_minutes").addEventListener("input",invalidate);
  el("job_form").addEventListener("submit",async event=>{
    event.preventDefault();if(!state||busy||!el("job_form").reportValidity())return;
    invalidate();
    const start=new Date(el("job_start").value), finish=new Date(el("job_finish").value);
    if(!Number.isFinite(start.getTime())||!Number.isFinite(finish.getTime())||finish<=start){notice(t("invalid_job_request"));return;}
    const request={id:crypto.randomUUID().replaceAll("-",""),start_at:start.toISOString(),finish_at:finish.toISOString(),current_a:Number(el("job_current").value),energy_kwh:Number(el("job_energy").value)};
    const result=await post({action:"preview",revision:state.revision,scope:state.scope,request});
    if(result?.schema===1&&result.request){proposal={...result,kind:"create"};el("job_summary").textContent=`${date(result.request.start_at)} → ${date(result.request.finish_at)} · ${result.request.current_a} A · ${result.request.energy_kwh} kWh`;el("job_confirmation").hidden=false;render();}else{state=null;render();}
    timer=setTimeout(read,5000);
  });
  el("job_boost_preview").addEventListener("click",async()=>{
    if(!state || busy || state.boost_available!==true)return;
    invalidate();
    const minutes=Number(el("job_boost_minutes").value);
    if(!Number.isInteger(minutes)||minutes<1||minutes>120){notice(t("invalid_boost_duration"));return;}
    const result=await post({action:"preview_boost",revision:state.revision,scope:state.scope,minutes});
    if(result?.schema===1&&typeof result.until==="string"){
      proposal={...result,kind:"boost"};
      el("job_summary").textContent=`${t("boost_until")} ${date(result.until)} · ${result.current_a} A · ${result.energy_kwh} kWh`;
      el("job_confirmation").hidden=false;render();
    }else{state=null;render();}
    timer=setTimeout(read,5000);
  });
  el("job_confirm").addEventListener("click",async()=>{
    if(!proposal||busy)return;const p=proposal;invalidate();
    const payload=p.kind==="boost" ? {action:"boost",revision:p.revision,scope:p.scope,until:p.until} : {action:"create",revision:p.revision,scope:p.scope,request:p.request};
    const result=await post(payload);
    if(result)notice(t("saved"));state=null;render();void read();
  });
  for(const action of ["stop","clear","cancel_boost"])el(`job_${action}`).addEventListener("click",async()=>{
    if(!state||busy)return;const revision=state.revision;invalidate();
    await post({action,revision,confirm:true});state=null;render();void read();
  });
  document.addEventListener("visibilitychange",()=>{suspend();invalidate();state=null;render();if(!document.hidden)void read();});
  addEventListener("pagehide",()=>{hidden=true;suspend();});
  addEventListener("pageshow",()=>{hidden=false;void read();});
  notice(t("loading"));render();void read();
})();
