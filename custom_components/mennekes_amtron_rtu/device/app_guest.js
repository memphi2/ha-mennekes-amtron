/* Guest presets reuse the job API. No credentials in storage and no POST retry. */
(() => {
  "use strict";
  const de=(navigator.language||"").toLowerCase().startsWith("de");
  document.documentElement.lang=de?"de":"en";
  const words={
    title:["Guest charging","Gastladen"], overview:["Overview","Übersicht"], jobs:["Charging jobs","Ladeaufträge"], registers:["Registers","Register"],
    intro:["One immediate energy/time order for the connected guest vehicle. No battery data or saved vehicle target is used.","Ein sofortiger Energie-/Zeitauftrag für das angeschlossene Gastfahrzeug. Keine Batteriedaten oder gespeicherten Fahrzeugziele werden übernommen."],
    status:["Current order","Aktueller Auftrag"], new:["New guest order","Neuer Gastauftrag"], energy:["Additional energy ceiling, kWh","Zusätzliche Energieobergrenze in kWh"], duration:["Maximum duration, minutes","Maximale Dauer in Minuten"], current:["Current per phase, A","Strom je Phase in A"],
    permission:["Authorize locally first, for example by RFID. This order cannot unlock the wallbox, grant release or override a battery target.","Zuerst lokal autorisieren, beispielsweise per RFID. Dieser Auftrag kann die Wallbox nicht entsperren, keine Ladefreigabe erteilen und kein Batterieziel übergehen."],
    preview:["Preview","Vorschau"], confirm_title:["Confirm guest order","Gastauftrag bestätigen"], confirm:["Confirm and start when permitted","Bestätigen und bei gültiger Freigabe starten"], stop:["Stop guest order","Gastauftrag stoppen"], clear:["Clear stopped order without resuming","Gestoppten Auftrag löschen – ohne Ladestart"],
    scope:["Only this physical connection. Stops at the energy ceiling or deadline, subject to polling and hardware timing. Reconnection requires a new order.","Nur für diesen Anschluss. Stopp bei Energieobergrenze oder Zeitende unter Beachtung der Abfrage- und Hardwarezeiten. Nach erneutem Anstecken ist ein neuer Auftrag erforderlich."],
    footer:["No guaranteed delivered amount. A requested stop is not a confirmed hardware stop. Check the overview and command results.","Keine garantierte Energiemenge. Ein angeforderter Stopp ist kein bestätigter Hardware-Stopp. Übersicht und Befehlsergebnisse beachten."],
    ready:["Ready for a guest order.","Bereit für einen Gastauftrag."], loading:["Reading service state…","Servicestatus wird gelesen …"], offline:["No current response. Controls disabled until status is read again.","Keine aktuelle Antwort. Bedienung bis zur erneuten Statusabfrage gesperrt."],
    no_job:["No order","Kein Auftrag"], other_job:["Another charging order exists. Manage it on the Charging jobs page.","Ein anderer Ladeauftrag besteht. Diesen auf der Seite Ladeaufträge verwalten."],
    unavailable:["Fresh meter, connected vehicle and existing release are required.","Aktueller Zählerstand, angeschlossenes Fahrzeug und bestehende Ladefreigabe erforderlich."], vehicle_target:["A vehicle battery target is active. Explicitly disable it in Home Assistant before using guest charging; it will not be cleared automatically.","Ein Fahrzeug-Batterieziel ist aktiv. Vor Gastladen ausdrücklich in Home Assistant deaktivieren; es wird nicht automatisch gelöscht."],
    storage_error:["Storage unavailable; starting is blocked.","Speicher nicht verfügbar; Ladestarts gesperrt."], uncertain:["Action not confirmed. Current state is read again; no automatic retry.","Aktion nicht bestätigt. Der aktuelle Zustand wird neu gelesen; keine automatische Wiederholung."],
    invalid:["Check energy, duration and current.","Energie, Dauer und Strom prüfen."], job_changed:["State changed. Preview again.","Zustand geändert. Vorschau wiederholen."], job_exists:["Another order already exists.","Es besteht bereits ein Auftrag."], guest_vehicle_target_active:["Vehicle battery target active; no guest order created.","Fahrzeug-Batterieziel aktiv; kein Gastauftrag angelegt."], job_current_exceeds_cap:["Current exceeds the service limit.","Strom überschreitet die Servicegrenze."], job_not_permitted:["Connection or release changed.","Anschluss oder Freigabe geändert."],
    scheduled:["Scheduled","Geplant"], charging:["Charging requested","Laden angefordert"], waiting_meter:["Waiting for valid meter","Wartet auf gültigen Zähler"], reached:["Energy ceiling reached","Energieobergrenze erreicht"], finished:["Deadline reached","Zeitende erreicht"], stopped:["Stopped","Gestoppt"], interrupted:["Interrupted; no automatic restart","Unterbrochen; kein automatischer Neustart"],
    unconfirmed:["Command not yet confirmed","Befehl noch nicht bestätigt"], scope_ended:["Connection ended","Anschluss beendet"]
  };
  const t=k=>words[k]?.[de?1:0]||words.uncertain[de?1:0];
  const el=id=>document.getElementById(id), date=v=>new Date(v).toLocaleString(de?"de-DE":"en",{timeZoneName:"short"});
  document.querySelectorAll("[data-text]").forEach(n=>{n.textContent=t(n.dataset.text);});
  let state=null, proposal=null, busy=false, timer=null, readAbort=null, generation=0, hidden=false, lastError="";
  const live=()=>!hidden&&!document.hidden;
  function invalidate(){proposal=null;el("guest_confirmation").hidden=true;}
  function render(){
    const job=state?.job, own=job?.kind==="guest";
    const good=state?.ready&&!state.storage_error&&state.available&&state.present===true&&state.permitted&&state.meter_fresh&&!state.vehicle_target_active&&(!job||job.scope_ended);
    el("guest_fields").disabled=busy||!good;
    el("guest_current").max=String(state?.max_current_a||6);
    el("guest_confirm").disabled=busy||!proposal;
    el("guest_stop").disabled=busy||!own;
    el("guest_clear").disabled=busy||!own||!["reached","finished","stopped","interrupted"].includes(job.state);
    el("guest_status").hidden=!state;
    el("guest_state").textContent=!job?t("no_job"):own?t(job.state):t("other_job");
    el("guest_detail").textContent=own?`${date(job.start_at)} → ${date(job.finish_at)} · ${job.current_a} A · ${job.delivered_kwh==null?"—":Number(job.delivered_kwh).toLocaleString(undefined,{maximumFractionDigits:2})} / ${job.energy_kwh} kWh${job.scope_ended?` · ${t("scope_ended")}`:""}${state.action_error?` · ${t("unconfirmed")}`:""}`:"";
    el("guest_notice").textContent=lastError||(!state?t("loading"):state.storage_error?t("storage_error"):state.vehicle_target_active?t("vehicle_target"):good?t("ready"):job?t(own?job.state:"other_job"):t("unavailable"));
  }
  function suspend(){generation++;clearTimeout(timer);timer=null;readAbort?.abort();readAbort=null;}
  async function read(){
    if(busy||!live()||readAbort)return;
    const own=++generation, abort=new AbortController();readAbort=abort;
    const timeout=setTimeout(()=>abort.abort(),8000);
    try{
      const response=await fetch("./api/jobs",{cache:"no-store",signal:abort.signal});
      const data=await response.json();
      if(!response.ok||data.schema!==1||typeof data.revision!=="string"||typeof data.csrf!=="string")throw new Error("invalid");
      if(own!==generation||!live())return;
      if(proposal&&proposal.revision!==data.revision)invalidate();
      state=data;render();
    }catch(_){if(own===generation&&live()){state=null;invalidate();lastError=t("offline");render();}}
    finally{clearTimeout(timeout);if(own===generation){readAbort=null;if(live())timer=setTimeout(read,5000);}}
  }
  async function post(payload){
    if(!state||busy)return null;
    busy=true;suspend();render();
    const own=generation, abort=new AbortController(), timeout=setTimeout(()=>abort.abort(),15000);
    try{
      const response=await fetch("./api/jobs",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json","X-Amtron-CSRF":state.csrf},body:JSON.stringify(payload),signal:abort.signal});
      const data=await response.json();
      if(own!==generation||!live())return null;
      if(!response.ok){lastError=t(data.error);return null;}
      return data;
    }catch(_){if(own===generation&&live())lastError=t("uncertain");return null;}
    finally{clearTimeout(timeout);busy=false;render();}
  }
  el("guest_form").addEventListener("input",()=>{invalidate();lastError="";render();});
  el("guest_form").addEventListener("submit",async event=>{
    event.preventDefault();if(!state||busy||!el("guest_form").reportValidity())return;
    invalidate();lastError="";
    const minutes=Number(el("guest_duration").value);
    if(!Number.isInteger(minutes)||minutes<1||minutes>1440){lastError=t("invalid");render();return;}
    const request={id:crypto.randomUUID().replaceAll("-",""),duration_minutes:minutes,current_a:Number(el("guest_current").value),energy_kwh:Number(el("guest_energy").value)};
    const result=await post({action:"preview_guest",revision:state.revision,scope:state.scope,request});
    if(result?.schema===1&&result.request?.kind==="guest"){
      proposal=result;el("guest_summary").textContent=`${date(result.request.start_at)} → ${date(result.request.finish_at)} · ${result.request.current_a} A · ${result.request.energy_kwh} kWh`;
      el("guest_confirmation").hidden=false;render();
    }else{state=null;render();}
    if(live())timer=setTimeout(read,5000);
  });
  el("guest_confirm").addEventListener("click",async()=>{
    if(!proposal||busy)return;const p=proposal;invalidate();lastError="";
    await post({action:"create",revision:p.revision,scope:p.scope,request:p.request});
    state=null;render();void read();
  });
  for(const action of ["stop","clear"])el(`guest_${action}`).addEventListener("click",async()=>{
    if(!state||busy||state.job?.kind!=="guest")return;const revision=state.revision;invalidate();lastError="";
    await post({action,revision,confirm:true});state=null;render();void read();
  });
  document.addEventListener("visibilitychange",()=>{suspend();invalidate();state=null;render();if(live())void read();});
  addEventListener("pagehide",()=>{hidden=true;suspend();invalidate();state=null;});
  addEventListener("pageshow",()=>{hidden=false;void read();});
  render();void read();
})();
