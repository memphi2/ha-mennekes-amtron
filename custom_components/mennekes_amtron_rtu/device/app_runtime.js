/* Explicit preview/confirm flow. Never retry a configuration POST automatically. */
(() => {
  "use strict";
  const languages = ["en", "de", "fr", "nl", "it"];
  const found = languages.indexOf((document.documentElement.lang || "").slice(0, 2).toLowerCase());
  const column = found < 0 ? 0 : found;
  const names = ["scan_interval", "measurement_interval", "current_cap", "state_change_interval_seconds", "phase_switch_grace_seconds"];
  const labels = {
    title:["Runtime settings","Betriebseinstellungen","Réglages de fonctionnement","Bedrijfsinstellingen","Impostazioni di funzionamento"],
    intro:["Preview every change before saving. Polling intervals apply at the next ordinary cycle; the current poll or wait is not interrupted. Control limits require a service restart.","Vor dem Speichern jede Änderung prüfen. Pollingintervalle gelten ab dem nächsten regulären Zyklus; laufende Abfragen und Wartezeiten werden nicht unterbrochen. Steuerungsgrenzen benötigen einen Service-Neustart.","Prévisualisez chaque modification avant de l’enregistrer. Les intervalles d’interrogation s’appliquent au prochain cycle normal ; l’interrogation ou l’attente en cours n’est pas interrompue. Les limites de commande exigent un redémarrage du service.","Bekijk elke wijziging vóór het opslaan. Uitleesintervallen gelden vanaf de volgende gewone cyclus; de lopende uitlezing of wachttijd wordt niet onderbroken. Regelgrenzen vereisen een herstart van de service.","Visualizzare ogni modifica prima di salvarla. Gli intervalli di lettura valgono dal prossimo ciclo ordinario; la lettura o l’attesa in corso non viene interrotta. I limiti di controllo richiedono un riavvio del servizio."],
    setting:["Setting","Einstellung","Réglage","Instelling","Impostazione"],
    effective:["Effective","Wirksam","En vigueur","Actief","In vigore"],
    saved:["Saved","Gespeichert","Enregistré","Opgeslagen","Salvato"],
    scan_interval:["Status interval (s)","Statusintervall (s)","Intervalle d’état (s)","Statusinterval (s)","Intervallo di stato (s)"],
    measurement_interval:["Measurement interval (s)","Messintervall (s)","Intervalle de mesure (s)","Meetinterval (s)","Intervallo di misura (s)"],
    current_cap:["Service current cap (A)","Service-Stromobergrenze (A)","Limite de courant du service (A)","Stroomgrens van de service (A)","Limite di corrente del servizio (A)"],
    state_change_interval_seconds:["State-change interval (s)","Mindestabstand Zustandswechsel (s)","Intervalle entre changements d’état (s)","Interval tussen toestandswijzigingen (s)","Intervallo tra cambi di stato (s)"],
    phase_switch_grace_seconds:["Phase-switch grace (s)","Wartezeit Phasenwechsel (s)","Délai de commutation de phase (s)","Wachttijd faseomschakeling (s)","Attesa commutazione di fase (s)"],
    restart:["Requires restart","Neustart erforderlich","Redémarrage requis","Herstart vereist","Riavvio necessario"],
    live:["Next polling cycle","Nächster Pollzyklus","Prochain cycle d’interrogation","Volgende uitleescyclus","Prossimo ciclo di lettura"],
    preview:["Change preview","Änderungen prüfen","Aperçu des modifications","Voorbeeld van wijzigingen","Anteprima delle modifiche"],
    save:["Confirm and save","Bestätigen und speichern","Confirmer et enregistrer","Bevestigen en opslaan","Confermare e salvare"],
    reload:["Reload","Neu laden","Recharger","Herladen","Ricaricare"],
    limits:["Saving a lower limit does not immediately reduce current. Until restart the effective limit above remains in force. No automatic restart is performed.","Eine gespeicherte niedrigere Grenze reduziert den Ladestrom nicht sofort. Bis zum Neustart gilt die oben angezeigte wirksame Grenze weiter. Es erfolgt kein automatischer Neustart.","Enregistrer une limite plus basse ne réduit pas immédiatement le courant. Jusqu’au redémarrage, la limite en vigueur ci-dessus reste appliquée. Aucun redémarrage automatique n’a lieu.","Een lagere opgeslagen grens verlaagt de stroom niet meteen. Tot de herstart blijft de actieve grens hierboven gelden. Er volgt geen automatische herstart.","Salvare un limite più basso non riduce subito la corrente. Fino al riavvio resta valido il limite in vigore indicato sopra. Non avviene alcun riavvio automatico."],
    loading:["Loading settings…","Einstellungen werden geladen…","Chargement des réglages…","Instellingen laden…","Caricamento delle impostazioni…"],
    ready:["Settings loaded. Changes need confirmation.","Einstellungen geladen. Änderungen müssen bestätigt werden.","Réglages chargés. Les modifications doivent être confirmées.","Instellingen geladen. Wijzigingen moeten worden bevestigd.","Impostazioni caricate. Le modifiche devono essere confermate."],
    no_changes:["No changes.","Keine Änderungen.","Aucune modification.","Geen wijzigingen.","Nessuna modifica."],
    no_restart:["No restart needed for this change.","Für diese Änderung ist kein Neustart nötig.","Aucun redémarrage nécessaire pour cette modification.","Voor deze wijziging is geen herstart nodig.","Per questa modifica non serve un riavvio."],
    done:["Saved. Effective and saved values are shown separately below.","Gespeichert. Wirksame und gespeicherte Werte stehen getrennt in der Tabelle.","Enregistré. Les valeurs en vigueur et enregistrées sont affichées séparément ci-dessous.","Opgeslagen. Actieve en opgeslagen waarden staan hieronder apart.","Salvato. I valori in vigore e salvati sono mostrati separatamente qui sotto."],
    error:["Request failed. No automatic retry. Reload to check the actual saved and effective values.","Anfrage fehlgeschlagen. Kein automatischer Wiederholungsversuch. Neu laden, um gespeicherte und wirksame Werte zu prüfen.","La requête a échoué. Aucune nouvelle tentative automatique. Rechargez pour vérifier les valeurs enregistrées et en vigueur.","Verzoek mislukt. Geen automatische herhaling. Herlaad om de opgeslagen en actieve waarden te controleren.","Richiesta non riuscita. Nessun nuovo tentativo automatico. Ricaricare per verificare i valori salvati e in vigore."],
    blocked:["A previous save has an uncertain outcome. Saving is disabled until the service is restarted.","Ein früherer Speichervorgang hat einen ungewissen Ausgang. Weitere Änderungen sind bis zum Service-Neustart gesperrt.","Un enregistrement précédent a un résultat incertain. L’enregistrement est désactivé jusqu’au redémarrage du service.","Een eerdere opslag heeft een onzekere uitkomst. Opslaan is uitgeschakeld tot de service herstart.","Un salvataggio precedente ha un esito incerto. Il salvataggio è disattivato fino al riavvio del servizio."],
    connection:["Connection","Verbindung","Connexion","Verbinding","Connessione"],
    read_only:["Read-only mode","Nur-Lesen-Modus","Mode lecture seule","Alleen-lezenmodus","Modalità sola lettura"],
    listen:["Listen address","Listen-Adresse","Adresse d’écoute","Luisteradres","Indirizzo di ascolto"],
    port:["API port","API-Port","Port de l’API","API-poort","Porta API"]
  };
  const text = key => labels[key]?.[column] || key;
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
      el("runtime_changes").replaceChildren();el("runtime_preview").hidden=!data.restart_required;
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
