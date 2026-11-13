/* Explicit preview/confirm flow. Never retry a configuration POST automatically. */
(() => {
  "use strict";
  const languages = ["en", "de", "fr", "nl", "it"];
  const found = languages.indexOf((document.documentElement.lang || "").slice(0, 2).toLowerCase());
  const column = found < 0 ? 0 : found;
  const names = ["scan_interval", "measurement_interval", "current_cap", "state_change_interval_seconds", "phase_switch_grace_seconds"];
  const labels = {
    title:["Runtime settings","Betriebseinstellungen","Réglages de fonctionnement","Bedrijfsinstellingen","Impostazioni di funzionamento"],
    intro:["Preview every change before saving. Nothing here needs a restart: polling intervals apply at the next ordinary cycle without interrupting the current poll or wait, control limits apply at once.","Vor dem Speichern jede Änderung prüfen. Nichts davon braucht einen Neustart: Pollingintervalle gelten ab dem nächsten regulären Zyklus, ohne laufende Abfragen oder Wartezeiten zu unterbrechen; Steuerungsgrenzen gelten sofort.","Prévisualisez chaque modification avant de l’enregistrer. Aucun redémarrage n’est nécessaire : les intervalles d’interrogation s’appliquent au prochain cycle normal sans interrompre l’interrogation ou l’attente en cours, les limites de commande s’appliquent immédiatement.","Bekijk elke wijziging vóór het opslaan. Niets hiervan vereist een herstart: uitleesintervallen gelden vanaf de volgende gewone cyclus zonder de lopende uitlezing of wachttijd te onderbreken, regelgrenzen gelden meteen.","Visualizzare ogni modifica prima di salvarla. Nulla richiede un riavvio: gli intervalli di lettura valgono dal prossimo ciclo ordinario senza interrompere la lettura o l’attesa in corso, i limiti di controllo valgono subito."],
    setting:["Setting","Einstellung","Réglage","Instelling","Impostazione"],
    effective:["Effective","Wirksam","En vigueur","Actief","In vigore"],
    saved:["Saved","Gespeichert","Enregistré","Opgeslagen","Salvato"],
    scan_interval:["Status interval (s)","Statusintervall (s)","Intervalle d’état (s)","Statusinterval (s)","Intervallo di stato (s)"],
    measurement_interval:["Measurement interval (s)","Messintervall (s)","Intervalle de mesure (s)","Meetinterval (s)","Intervallo di misura (s)"],
    current_cap:["Service current cap (A)","Service-Stromobergrenze (A)","Limite de courant du service (A)","Stroomgrens van de service (A)","Limite di corrente del servizio (A)"],
    state_change_interval_seconds:["State-change interval (s)","Mindestabstand Zustandswechsel (s)","Intervalle entre changements d’état (s)","Interval tussen toestandswijzigingen (s)","Intervallo tra cambi di stato (s)"],
    phase_switch_grace_seconds:["Phase-switch grace (s)","Wartezeit Phasenwechsel (s)","Délai de commutation de phase (s)","Wachttijd faseomschakeling (s)","Attesa commutazione di fase (s)"],
    restart:["Requires restart","Neustart erforderlich","Redémarrage requis","Herstart vereist","Riavvio necessario"],
    limits_title:["Control limits","Steuerungsgrenzen","Limites de commande","Regelgrenzen","Limiti di controllo"],
    next_cycle:["Next polling cycle","Nächster Pollzyklus","Prochain cycle d’interrogation","Volgende uitleescyclus","Prossimo ciclo di lettura"],
    immediate:["Applies at once","Gilt sofort","S’applique immédiatement","Geldt meteen","Vale subito"],
    next_switch:["Next phase-switch wait","Nächste Wartezeit beim Phasenwechsel","Prochaine attente de commutation de phase","Volgende wachttijd voor faseomschakeling","Prossima attesa di commutazione di fase"],
    preview:["Change preview","Änderungen prüfen","Aperçu des modifications","Voorbeeld van wijzigingen","Anteprima delle modifiche"],
    save:["Confirm and save","Bestätigen und speichern","Confirmer et enregistrer","Bevestigen en opslaan","Confermare e salvare"],
    reload:["Reload","Neu laden","Recharger","Herladen","Ricaricare"],
    limits:["New commands use a lower current cap at once; a charge running above it is lowered after the next poll. A start or stop waiting for the state-change interval is timed again. A phase-switch wait that already runs keeps its length.","Neue Befehle nutzen eine niedrigere Stromobergrenze sofort; ein Ladevorgang darüber wird nach dem nächsten Poll abgesenkt. Ein Start oder Stopp, der auf den Mindestabstand wartet, wird neu berechnet. Eine bereits laufende Wartezeit beim Phasenwechsel behält ihre Länge.","Les nouvelles commandes appliquent immédiatement une limite de courant plus basse ; une charge au-dessus est réduite après la prochaine interrogation. Un démarrage ou un arrêt qui attend l’intervalle entre changements d’état est recalculé. Une attente de commutation de phase déjà en cours garde sa durée.","Nieuwe opdrachten gebruiken een lagere stroomgrens meteen; een lading daarboven wordt na de volgende uitlezing verlaagd. Een start of stop die op het interval tussen toestandswijzigingen wacht, wordt opnieuw berekend. Een lopende wachttijd voor faseomschakeling behoudt zijn duur.","I nuovi comandi usano subito un limite di corrente più basso; una ricarica al di sopra viene ridotta dopo la prossima lettura. Un avvio o un arresto in attesa dell’intervallo tra cambi di stato viene ricalcolato. Un’attesa di commutazione di fase già in corso mantiene la sua durata."],
    loading:["Loading settings…","Einstellungen werden geladen…","Chargement des réglages…","Instellingen laden…","Caricamento delle impostazioni…"],
    ready:["Settings loaded. Changes need confirmation.","Einstellungen geladen. Änderungen müssen bestätigt werden.","Réglages chargés. Les modifications doivent être confirmées.","Instellingen geladen. Wijzigingen moeten worden bevestigd.","Impostazioni caricate. Le modifiche devono essere confermate."],
    unapplied:["Some saved values are not in use yet. Select Change preview, then Confirm and save, to apply them without a restart.","Einige gespeicherte Werte sind noch nicht wirksam. Mit „Änderungen prüfen“ und „Bestätigen und speichern“ gelten sie ohne Neustart.","Certaines valeurs enregistrées ne sont pas encore en vigueur. Avec « Aperçu des modifications » puis « Confirmer et enregistrer », elles s’appliquent sans redémarrage.","Sommige opgeslagen waarden zijn nog niet actief. Met ‘Voorbeeld van wijzigingen’ en ‘Bevestigen en opslaan’ gelden ze zonder herstart.","Alcuni valori salvati non sono ancora in vigore. Con «Anteprima delle modifiche» e «Confermare e salvare» valgono senza riavvio."],
    no_changes:["No changes.","Keine Änderungen.","Aucune modification.","Geen wijzigingen.","Nessuna modifica."],
    no_restart:["No restart needed for this change.","Für diese Änderung ist kein Neustart nötig.","Aucun redémarrage nécessaire pour cette modification.","Voor deze wijziging is geen herstart nodig.","Per questa modifica non serve un riavvio."],
    done:["Saved. Effective and saved values are shown separately below.","Gespeichert. Wirksame und gespeicherte Werte stehen getrennt in der Tabelle.","Enregistré. Les valeurs en vigueur et enregistrées sont affichées séparément ci-dessous.","Opgeslagen. Actieve en opgeslagen waarden staan hieronder apart.","Salvato. I valori in vigore e salvati sono mostrati separatamente qui sotto."],
    error:["Request failed. No automatic retry. Reload to check the actual saved and effective values.","Anfrage fehlgeschlagen. Kein automatischer Wiederholungsversuch. Neu laden, um gespeicherte und wirksame Werte zu prüfen.","La requête a échoué. Aucune nouvelle tentative automatique. Rechargez pour vérifier les valeurs enregistrées et en vigueur.","Verzoek mislukt. Geen automatische herhaling. Herlaad om de opgeslagen en actieve waarden te controleren.","Richiesta non riuscita. Nessun nuovo tentativo automatico. Ricaricare per verificare i valori salvati e in vigore."],
    blocked:["A previous save has an uncertain outcome. Saving is disabled until the service is restarted.","Ein früherer Speichervorgang hat einen ungewissen Ausgang. Weitere Änderungen sind bis zum Service-Neustart gesperrt.","Un enregistrement précédent a un résultat incertain. L’enregistrement est désactivé jusqu’au redémarrage du service.","Een eerdere opslag heeft een onzekere uitkomst. Opslaan is uitgeschakeld tot de service herstart.","Un salvataggio precedente ha un esito incerto. Il salvataggio è disattivato fino al riavvio del servizio."],
    connection:["Connection","Verbindung","Connexion","Verbinding","Connessione"],
    read_only:["Read-only mode","Nur-Lesen-Modus","Mode lecture seule","Alleen-lezenmodus","Modalità sola lettura"],
    listen:["Listen address","Listen-Adresse","Adresse d’écoute","Luisteradres","Indirizzo di ascolto"],
    port:["API port","API-Port","Port de l’API","API-poort","Porta API"],
    busy:["Another request is running. Try again shortly.", "Eine andere Anfrage läuft noch. Gleich noch einmal versuchen.", "Une autre requête est en cours. Réessayez dans un instant.", "Een ander verzoek loopt nog. Probeer het zo opnieuw.", "Un’altra richiesta è in corso. Riprova tra poco."],
    reload_required:["Reload this page and try again.", "Seite neu laden und erneut versuchen.", "Rechargez cette page et réessayez.", "Herlaad deze pagina en probeer het opnieuw.", "Ricaricare la pagina e riprovare."],
    admin_required:["Sign in to Home Assistant as an administrator.", "Bei Home Assistant als Administrator anmelden.", "Connectez-vous à Home Assistant en tant qu’administrateur.", "Meld je aan bij Home Assistant als beheerder.", "Accedi a Home Assistant come amministratore."],
    supervisor_unavailable:["Home Assistant Supervisor did not answer. Reload this page.", "Home Assistant Supervisor hat nicht geantwortet. Seite neu laden.", "Supervisor n’a pas répondu. Rechargez la page.", "Supervisor heeft niet geantwoord. Herlaad deze pagina.", "Supervisor non ha risposto. Ricaricare la pagina."],
    supervisor_update_required:["Update Home Assistant Supervisor to 2026.10.4 or newer.", "Home Assistant Supervisor auf 2026.10.4 oder neuer aktualisieren.", "Mettez Supervisor à jour vers 2026.10.4 ou ultérieur.", "Werk Supervisor bij naar 2026.10.4 of nieuwer.", "Aggiorna Supervisor alla versione 2026.10.4 o successiva."],
    invalid_request:["The request was rejected. Reload this page and try again.", "Anfrage abgelehnt. Seite neu laden und erneut versuchen.", "Requête refusée. Rechargez la page et réessayez.", "Verzoek geweigerd. Herlaad de pagina en probeer het opnieuw.", "Richiesta rifiutata. Ricaricare la pagina e riprovare."],
    runtime_apply_pending:["Saved, but not applied yet. Reload to check the effective values.", "Gespeichert, aber noch nicht übernommen. Neu laden, um die wirksamen Werte zu prüfen.", "Enregistré, mais pas encore appliqué. Rechargez pour vérifier les valeurs en vigueur.", "Opgeslagen, maar nog niet toegepast. Herlaad om de actieve waarden te controleren.", "Salvato, ma non ancora applicato. Ricaricare per verificare i valori in vigore."],
    settings_changed:["Settings changed elsewhere. Reload before saving again; nothing was saved.", "Einstellungen wurden anderswo geändert. Vor dem Speichern neu laden; nichts wurde gespeichert.", "Les réglages ont changé ailleurs. Rechargez avant d’enregistrer ; rien n’a été enregistré.", "De instellingen zijn elders gewijzigd. Herlaad voor het opslaan; er is niets opgeslagen.", "Le impostazioni sono cambiate altrove. Ricaricare prima di salvare; non è stato salvato nulla."],
    runtime_settings_invalid:["Values outside the allowed range.", "Werte außerhalb des erlaubten Bereichs.", "Valeurs hors de la plage autorisée.", "Waarden buiten het toegestane bereik.", "Valori fuori dall’intervallo consentito."]
  };
  const text = key => labels[key]?.[column] || key;
  // A known server key names the cause; anything else stays the generic text.
  const failure = err => { const key = err?.message === "options_save_uncertain" ? "blocked" : err?.message; return labels[key] ? text(key) : text("error"); };
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
      if (!response.ok) throw new Error(typeof data?.error === "string" ? data.error : "error");
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
      // A change from the Configuration tab waits; saving it here applies it.
      const unapplied=names.some(key=>data.active[key]!==data.saved[key]);
      el("runtime_form").hidden=false; el("runtime_notice").textContent=text(blocked ? "blocked" : unapplied ? "unapplied" : "ready");
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
    } catch (err) { el("runtime_error").textContent=failure(err);el("runtime_error").hidden=false; }
    finally {busy=false;buttons();}
  });
  el("runtime_save").addEventListener("click",async()=>{
    if (busy||blocked||!pending) return;
    const payload={...pending,confirm:true};busy=true;buttons();el("runtime_error").hidden=true;
    try {
      const data=await request("./api/runtime",payload);values(data);state={...state,...data};
      pending=null;el("runtime_preview").hidden=false;el("runtime_changes").replaceChildren();el("runtime_notice").textContent=text("done");
    } catch (err) {pending=null;blocked=true;el("runtime_error").textContent=failure(err);el("runtime_error").hidden=false;}
    finally {busy=false;buttons();}
  });
  el("runtime_reload").addEventListener("click",()=>void load());
  void load();
})();
