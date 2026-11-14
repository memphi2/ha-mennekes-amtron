/* Guest presets reuse the job API. No credentials in storage and no POST retry. */
(() => {
  "use strict";
  const languages=["en","de","fr","nl","it"];
  const found=languages.indexOf((document.documentElement.lang||"").slice(0,2).toLowerCase());
  const column=found<0?0:found, locale=languages[column];
  const words={
    draft_tools:["Saved settings", "Gespeicherte Einstellungen", "Réglages enregistrés", "Opgeslagen instellingen", "Impostazioni salvate"],
    stop_help: ["Stopping pauses charging. Clearing an ended charge does not restart it.", "Stoppen pausiert die Ladung. Das Entfernen einer beendeten Ladung startet sie nicht erneut.", "Arrêter met en pause. Retirer une recharge terminée ne la redémarre pas.", "Stoppen pauzeert het laden. Verwijderen van een beëindigde laadsessie start deze niet opnieuw.", "Fermare mette in pausa. Rimuovere una ricarica terminata non la riavvia."],
    energy_progress: ["Energy progress", "Energie-Fortschritt", "Progression de l’énergie", "Energievoortgang", "Avanzamento energia"],
    ends: ["Ends by", "Spätestes Ende", "Fin au plus tard", "Uiterlijk einde", "Fine entro"],
    started: ["Started", "Beginn", "Début", "Begin", "Inizio"],
    delivered: ["Energy delivered / limit", "Geladene Energie / Grenze", "Énergie fournie / limite", "Geladen energie / grens", "Energia erogata / limite"],
    empty_help: ["No guest charge yet. Enter energy and duration, then review and confirm.", "Noch keine Gastladung. Energie und Dauer eingeben, anschließend prüfen und bestätigen.", "Pas encore de recharge invité. Saisissez l’énergie et la durée, puis vérifiez et confirmez.", "Nog geen gastlaadsessie. Vul energie en duur in, controleer en bevestig.", "Nessuna ricarica ospite. Inserisci energia e durata, poi verifica e conferma."],
    reset_draft:["Reset draft (keeps running order)","Entwurf zurücksetzen (laufender Auftrag bleibt)","Réinitialiser le brouillon (ordre conservé)","Concept resetten (opdracht blijft)","Reimposta bozza (ordine invariato)"],
    draft_storage_error:["Draft could not be read or saved. Reset the draft to recover; the current order is unchanged.","Entwurf konnte nicht gelesen oder gespeichert werden. Entwurf zum Wiederherstellen zurücksetzen; der laufende Auftrag bleibt unverändert.","Lecture ou enregistrement du brouillon impossible. Réinitialisez-le ; l’ordre actuel reste inchangé.","Concept kon niet worden gelezen of opgeslagen. Reset het concept; de huidige opdracht blijft ongewijzigd.","Impossibile leggere o salvare la bozza. Reimpostarla; l’ordine attuale resta invariato."],
    draft_saved:["Draft saved; charging unchanged.","Entwurf gespeichert; Laden unverändert.","Brouillon enregistré ; recharge inchangée.","Concept opgeslagen; laden ongewijzigd.","Bozza salvata; ricarica invariata."],
    save_draft:["Save draft","Entwurf speichern","Enregistrer le brouillon","Concept opslaan","Salva bozza"],
    reload_draft:["Reload saved draft","Gespeicherten Entwurf laden","Recharger le brouillon","Opgeslagen concept laden","Ricarica bozza salvata"],
    title:["Guest charging","Gastladen","Recharge invité","Gastladen","Ricarica ospite"],
    intro:["One immediate energy/time order for the connected guest vehicle. No battery data or saved vehicle target is used.","Ein sofortiger Energie-/Zeitauftrag für das angeschlossene Gastfahrzeug. Keine Batteriedaten oder gespeicherten Fahrzeugziele werden übernommen.","Un ordre immédiat d’énergie et de durée pour le véhicule invité connecté. Aucune donnée de batterie ni objectif de véhicule enregistré n’est utilisé.","Eén directe energie-/tijdopdracht voor het aangesloten gastvoertuig. Er worden geen accugegevens of opgeslagen voertuigdoelen gebruikt.","Un ordine immediato di energia e durata per il veicolo ospite collegato. Non vengono usati dati della batteria né obiettivi salvati del veicolo."],
    status: ["Current charge", "Aktuelle Ladung", "Recharge actuelle", "Huidige laadsessie", "Ricarica attuale"],
    new: ["New charge", "Neue Ladung", "Nouvelle recharge", "Nieuwe laadsessie", "Nuova ricarica"],
    energy:["Additional energy ceiling, kWh","Zusätzliche Energieobergrenze in kWh","Plafond d’énergie supplémentaire, kWh","Bovengrens extra energie, kWh","Tetto di energia aggiuntiva, kWh"],
    duration:["Maximum duration, minutes","Maximale Dauer in Minuten","Durée maximale, minutes","Maximale duur, minuten","Durata massima, minuti"],
    current:["Current per phase, A","Strom je Phase in A","Courant par phase, A","Stroom per fase, A","Corrente per fase, A"],
    permission:["Authorize locally first, for example by RFID. This order cannot unlock the wallbox, grant release or override a battery target.","Zuerst lokal autorisieren, beispielsweise per RFID. Dieser Auftrag kann die Wallbox nicht entsperren, keine Ladefreigabe erteilen und kein Batterieziel übergehen.","Autorisez d’abord localement, par exemple par RFID. Cet ordre ne peut ni déverrouiller la borne, ni accorder l’autorisation, ni passer outre un objectif de batterie.","Autoriseer eerst lokaal, bijvoorbeeld met RFID. Deze opdracht kan de wallbox niet ontgrendelen, geen vrijgave verlenen en geen accudoel negeren.","Autorizzare prima localmente, ad esempio con RFID. Questo ordine non può sbloccare la wallbox, concedere il consenso o ignorare un obiettivo della batteria."],
    preview: ["Review and continue", "Prüfen und weiter", "Vérifier et continuer", "Controleren en doorgaan", "Verifica e continua"],
    confirm_title:["Confirm guest order","Gastauftrag bestätigen","Confirmer l’ordre invité","Gastopdracht bevestigen","Confermare l’ordine ospite"],
    confirm: ["Start guest charging", "Gastladen starten", "Démarrer la recharge invité", "Gastladen starten", "Avvia ricarica ospite"],
    stop: ["Stop charging", "Ladung stoppen", "Arrêter la recharge", "Laden stoppen", "Ferma ricarica"],
    clear: ["Clear ended charge", "Beendete Ladung entfernen", "Retirer la recharge terminée", "Beëindigde laadsessie verwijderen", "Rimuovi ricarica terminata"],
    scope:["Only this physical connection. Stops at the energy ceiling or deadline, subject to polling and hardware timing. Reconnection requires a new order.","Nur für diesen Anschluss. Stopp bei Energieobergrenze oder Zeitende unter Beachtung der Abfrage- und Hardwarezeiten. Nach erneutem Anstecken ist ein neuer Auftrag erforderlich.","Uniquement cette connexion physique. Arrêt au plafond d’énergie ou à l’échéance, selon les délais d’interrogation et du matériel. Une reconnexion exige un nouvel ordre.","Alleen deze fysieke verbinding. Stopt bij de energiegrens of eindtijd, afhankelijk van de uitlees- en hardwaretijden. Opnieuw aansluiten vereist een nieuwe opdracht.","Solo questo collegamento fisico. Si ferma al tetto di energia o alla scadenza, secondo i tempi di lettura e dell’hardware. Un nuovo collegamento richiede un nuovo ordine."],
    footer:["No guaranteed delivered amount. A requested stop is not a confirmed hardware stop. Check the overview and command results.","Keine garantierte Energiemenge. Ein angeforderter Stopp ist kein bestätigter Hardware-Stopp. Übersicht und Befehlsergebnisse beachten.","Aucune quantité livrée garantie. Un arrêt demandé n’est pas un arrêt matériel confirmé. Consultez la vue d’ensemble et les résultats des commandes.","Geen gegarandeerde geleverde hoeveelheid. Een gevraagde stop is geen bevestigde hardwarestop. Controleer het overzicht en de opdrachtresultaten.","Nessuna quantità erogata garantita. Uno stop richiesto non è uno stop hardware confermato. Controllare la panoramica e i risultati dei comandi."],
    ready:["Ready for a guest order.","Bereit für einen Gastauftrag.","Prêt pour un ordre invité.","Gereed voor een gastopdracht.","Pronto per un ordine ospite."],
    loading:["Reading service state…","Servicestatus wird gelesen …","Lecture de l’état du service…","Servicestatus wordt gelezen…","Lettura dello stato del servizio…"],
    offline:["No current response. Controls disabled until status is read again.","Keine aktuelle Antwort. Bedienung bis zur erneuten Statusabfrage gesperrt.","Aucune réponse actuelle. Commandes désactivées jusqu’à la prochaine lecture de l’état.","Geen actueel antwoord. Bediening uitgeschakeld tot de status opnieuw is gelezen.","Nessuna risposta attuale. Comandi disattivati fino alla prossima lettura dello stato."],
    no_job:["No order","Kein Auftrag","Aucun ordre","Geen opdracht","Nessun ordine"],
    other_job:["Unsupported saved session. Stop and clear it before starting guest charging.", "Nicht unterstützter gespeicherter Ladevorgang. Vor Gastladen stoppen und entfernen.", "Session enregistrée non prise en charge. Arrêtez-la et retirez-la avant une recharge invité.", "Niet-ondersteunde opgeslagen sessie. Stop en verwijder vóór gastladen.", "Sessione salvata non supportata. Fermare e rimuovere prima della ricarica ospite."],
    unavailable:["Fresh meter, connected vehicle and existing release are required.","Aktueller Zählerstand, angeschlossenes Fahrzeug und bestehende Ladefreigabe erforderlich.","Un compteur à jour, un véhicule connecté et une autorisation existante sont requis.","Een actuele meterstand, een aangesloten voertuig en een bestaande vrijgave zijn vereist.","Servono un contatore aggiornato, un veicolo collegato e un consenso esistente."],
    vehicle_target:["A vehicle battery target is active. Explicitly disable it in Home Assistant before using guest charging; it will not be cleared automatically.","Ein Fahrzeug-Batterieziel ist aktiv. Vor Gastladen ausdrücklich in Home Assistant deaktivieren; es wird nicht automatisch gelöscht.","Un objectif de batterie du véhicule est actif. Désactivez-le explicitement dans Home Assistant avant la recharge invité ; il n’est pas effacé automatiquement.","Er is een accudoel van het voertuig actief. Schakel het in Home Assistant uitdrukkelijk uit vóór gastladen; het wordt niet automatisch gewist.","È attivo un obiettivo della batteria del veicolo. Disattivarlo esplicitamente in Home Assistant prima della ricarica ospite; non viene cancellato automaticamente."],
    storage_error:["Storage unavailable; starting is blocked.","Speicher nicht verfügbar; Ladestarts gesperrt.","Stockage indisponible ; démarrage bloqué.","Opslag niet beschikbaar; starten geblokkeerd.","Memoria non disponibile; avvio bloccato."],
    uncertain:["Action not confirmed. Current state is read again; no automatic retry.","Aktion nicht bestätigt. Der aktuelle Zustand wird neu gelesen; keine automatische Wiederholung.","Action non confirmée. L’état actuel est relu ; aucune nouvelle tentative automatique.","Actie niet bevestigd. De huidige toestand wordt opnieuw gelezen; geen automatische herhaling.","Azione non confermata. Lo stato attuale viene riletto; nessun nuovo tentativo automatico."],
    invalid:["Check energy, duration and current.","Energie, Dauer und Strom prüfen.","Vérifiez l’énergie, la durée et le courant.","Controleer energie, duur en stroom.","Verificare energia, durata e corrente."],
    job_changed:["State changed. Preview again.","Zustand geändert. Vorschau wiederholen.","L’état a changé. Refaites l’aperçu.","Toestand gewijzigd. Bekijk het voorbeeld opnieuw.","Lo stato è cambiato. Ripetere l’anteprima."],
    job_exists:["Another order already exists.","Es besteht bereits ein Auftrag.","Un ordre existe déjà.","Er bestaat al een opdracht.","Esiste già un ordine."],
    guest_vehicle_target_active:["Vehicle battery target active; no guest order created.","Fahrzeug-Batterieziel aktiv; kein Gastauftrag angelegt.","Objectif de batterie du véhicule actif ; aucun ordre invité créé.","Accudoel van het voertuig actief; geen gastopdracht aangemaakt.","Obiettivo della batteria del veicolo attivo; nessun ordine ospite creato."],
    job_current_exceeds_cap:["Current exceeds the service limit.","Strom überschreitet die Servicegrenze.","Le courant dépasse la limite du service.","Stroom overschrijdt de servicegrens.","La corrente supera il limite del servizio."],
    job_not_permitted:["Connection or release changed.","Anschluss oder Freigabe geändert.","Connexion ou autorisation modifiée.","Verbinding of vrijgave gewijzigd.","Collegamento o consenso modificati."],
    job_preview_expired:["The preview is older than a minute. Preview again.","Die Vorschau ist älter als eine Minute. Vorschau wiederholen.","L’aperçu date de plus d’une minute. Refaites l’aperçu.","Het voorbeeld is ouder dan een minuut. Bekijk het opnieuw.","L’anteprima ha più di un minuto. Ripetere l’anteprima."],
    invalid_job_request:["Invalid order. Check energy, duration and current.","Ungültiger Auftrag. Energie, Dauer und Strom prüfen.","Ordre non valide. Vérifiez l’énergie, la durée et le courant.","Ongeldige opdracht. Controleer energie, duur en stroom.","Ordine non valido. Verificare energia, durata e corrente."],
    job_clear_requires_pause:["Clearing requires a confirmed pause or unplug.","Löschen erfordert eine bestätigte Pause oder abgezogenes Fahrzeug.","L’effacement exige une pause confirmée ou un véhicule débranché.","Wissen vereist een bevestigde pauze of een losgekoppeld voertuig.","La cancellazione richiede una pausa confermata o il veicolo scollegato."],
    job_stop_first:["Stop the active order first.","Zuerst den laufenden Auftrag stoppen.","Arrêtez d’abord l’ordre en cours.","Stop eerst de lopende opdracht.","Fermare prima l’ordine in corso."],
    job_storage_unavailable:["Storage unavailable; no automatic start.","Speicher nicht verfügbar; kein automatischer Start.","Stockage indisponible ; aucun démarrage automatique.","Opslag niet beschikbaar; geen automatische start.","Memoria non disponibile; nessun avvio automatico."],
    scheduled:["Scheduled","Geplant","Planifié","Gepland","Pianificato"],
    charging:["Charging requested","Laden angefordert","Recharge demandée","Laden gevraagd","Ricarica richiesta"],
    waiting_meter:["Waiting for valid meter","Wartet auf gültigen Zähler","En attente d’un compteur valide","Wacht op geldige meter","In attesa di un contatore valido"],
    reached:["Energy ceiling reached","Energieobergrenze erreicht","Plafond d’énergie atteint","Energiegrens bereikt","Tetto di energia raggiunto"],
    finished:["Deadline reached","Zeitende erreicht","Échéance atteinte","Eindtijd bereikt","Scadenza raggiunta"],
    stopped:["Stopped","Gestoppt","Arrêté","Gestopt","Fermato"],
    suspended:["Interrupted; checking whether the same plug-in continues","Unterbrochen; prüft, ob derselbe Steckvorgang weiterläuft","Interrompu ; vérifie si le même branchement continue","Onderbroken; controleert of dezelfde aansluiting doorloopt","Interrotto; verifica se lo stesso collegamento prosegue"],
    interrupted:["Interrupted; no automatic restart","Unterbrochen; kein automatischer Neustart","Interrompu ; pas de redémarrage automatique","Onderbroken; geen automatische herstart","Interrotto; nessun riavvio automatico"],
    unconfirmed:["Command not yet confirmed","Befehl noch nicht bestätigt","Commande pas encore confirmée","Opdracht nog niet bevestigd","Comando non ancora confermato"],
    scope_ended:["Connection ended","Anschluss beendet","Connexion terminée","Verbinding beëindigd","Collegamento terminato"],
    released:["Pause confirmed; Home Assistant controls charging again","Pause bestätigt; Home Assistant steuert die Ladung wieder","Pause confirmée ; Home Assistant pilote de nouveau la recharge","Pauze bevestigd; Home Assistant regelt het laden weer","Pausa confermata; Home Assistant controlla di nuovo la ricarica"],
    read_only:["Read-only mode: writes are disabled in the app configuration.", "Nur-Lesen-Modus: Schreiben ist in der App-Konfiguration abgeschaltet.", "Mode lecture seule : les écritures sont désactivées dans la configuration de l’application.", "Alleen-lezenmodus: schrijven is uitgeschakeld in de app-configuratie.", "Modalità sola lettura: le scritture sono disattivate nella configurazione dell’app."],
    busy:["Another request is running. Try again shortly.", "Eine andere Anfrage läuft noch. Gleich noch einmal versuchen.", "Une autre requête est en cours. Réessayez dans un instant.", "Een ander verzoek loopt nog. Probeer het zo opnieuw.", "Un’altra richiesta è in corso. Riprova tra poco."],
    reload_required:["Reload this page and try again.", "Seite neu laden und erneut versuchen.", "Rechargez cette page et réessayez.", "Herlaad deze pagina en probeer het opnieuw.", "Ricaricare la pagina e riprovare."],
    admin_required:["Sign in to Home Assistant as an administrator.", "Bei Home Assistant als Administrator anmelden.", "Connectez-vous à Home Assistant en tant qu’administrateur.", "Meld je aan bij Home Assistant als beheerder.", "Accedi a Home Assistant come amministratore."],
    supervisor_unavailable:["Home Assistant Supervisor did not answer. Reload this page.", "Home Assistant Supervisor hat nicht geantwortet. Seite neu laden.", "Supervisor n’a pas répondu. Rechargez la page.", "Supervisor heeft niet geantwoord. Herlaad deze pagina.", "Supervisor non ha risposto. Ricaricare la pagina."],
    supervisor_update_required:["Update Home Assistant Supervisor to 2026.10.4 or newer.", "Home Assistant Supervisor auf 2026.10.4 oder neuer aktualisieren.", "Mettez Supervisor à jour vers 2026.10.4 ou ultérieur.", "Werk Supervisor bij naar 2026.10.4 of nieuwer.", "Aggiorna Supervisor alla versione 2026.10.4 o successiva."],
    invalid_request:["The request was rejected. Reload this page and try again.", "Anfrage abgelehnt. Seite neu laden und erneut versuchen.", "Requête refusée. Rechargez la page et réessayez.", "Verzoek geweigerd. Herlaad de pagina en probeer het opnieuw.", "Richiesta rifiutata. Ricaricare la pagina e riprovare."],
    job_stop_unconfirmed:["The stop is not confirmed yet. Try again shortly.", "Der Stopp ist noch nicht bestätigt. Gleich noch einmal versuchen.", "L’arrêt n’est pas encore confirmé. Réessayez dans un instant.", "De stop is nog niet bevestigd. Probeer het zo opnieuw.", "L’arresto non è ancora confermato. Riprova tra poco."],
    job_service_starting:["The service is still starting. Try again shortly.", "Der Service startet noch. Gleich noch einmal versuchen.", "Le service démarre encore. Réessayez dans un instant.", "De service start nog. Probeer het zo opnieuw.", "Il servizio si sta ancora avviando. Riprova tra poco."],
  };
  const t=k=>words[k]?.[column]||words.uncertain[column];
  const el=id=>document.getElementById(id), date=v=>new Date(v).toLocaleString(locale,{timeZoneName:"short"});
  document.querySelectorAll("[data-text]").forEach(n=>{n.textContent=t(n.dataset.text);});
  // The page shows once its texts are in the page language.
  document.documentElement.classList?.add("ready");
  let state=null, proposal=null, busy=false, timer=null, readAbort=null, generation=0, hidden=false, lastError="";
  let draftRevision=null, draftDirty=false;
  const fields={duration_minutes:"guest_duration",current_a:"guest_current",energy_kwh:"guest_energy"};
  const live=()=>!hidden&&!document.hidden;
  function invalidate(){proposal=null;el("guest_confirmation").hidden=true;}
  function render(){
    const job=state?.job, own=job?.kind==="guest", ended=Boolean(job)&&["reached","finished","stopped","interrupted"].includes(job.state);
    const good=state?.ready&&!state.storage_error&&state.available&&state.present===true&&state.permitted&&state.meter_fresh&&!state.vehicle_target_active&&!job?.holding;
    el("guest_fields").disabled=busy||!state?.ready||state.read_only||!state.guest_draft||state.guest_draft.storage_error;
    el("guest_preview").disabled=busy||!good;
    el("guest_reset_draft").disabled=busy||!state?.guest_draft||state.read_only;
    el("guest_reload_draft").disabled=busy||!state;
    el("guest_current").max=String(state?.max_current_a||6);
    el("guest_confirm").disabled=busy||!proposal;
    el("guest_stop").disabled=busy||state?.read_only||!own||!job.holding||ended;
    el("guest_clear").disabled=busy||!state||state.read_only||(!state.storage_error&&(!own||!ended));
    el("guest_stop").hidden=!own||ended;
    el("guest_clear").hidden=!state?.storage_error&&(!own||!ended);
    el("guest_status").hidden=!state;
    el("guest_state").textContent=!job||(!own&&!job.holding)?t("no_job"):own?t(job.state):t("other_job");
    el("guest_state").className="state charging-state "+(state?.action_error||state?.storage_error?"state-error":job?.state==="reached"?"state-good":job?.state==="charging"?"state-active":!job?"state-muted":"state-wait");
    el("guest_empty").hidden=Boolean(job);
    el("guest_metrics").hidden=!own;
    const delivered=job?.delivered_kwh, ceiling=job?.energy_kwh;
    const known=typeof delivered==="number"&&Number.isFinite(delivered)&&delivered>=0;
    const bounded=typeof ceiling==="number"&&Number.isFinite(ceiling)&&ceiling>0;
    const amount=v=>Number(v).toLocaleString(locale,{maximumFractionDigits:2});
    el("guest_delivered").textContent=own?`${known?amount(delivered):"—"} / ${bounded?amount(ceiling):"—"} kWh`:"";
    el("guest_progress").hidden=!own||!known||!bounded;
    el("guest_progress").value=known&&bounded?Math.min(100,delivered/ceiling*100):0;
    el("guest_started").textContent=own?date(job.start_at):"";
    el("guest_ends").textContent=own?date(job.finish_at):"";
    el("guest_amperes").textContent=own?`${amount(job.current_a)} A`:"";
    el("guest_detail").textContent=own?[job.scope_ended?t("scope_ended"):!job.holding?t("released"):"",state.action_error?t("unconfirmed"):""].filter(Boolean).join(" · "):"";
    el("guest_notice").textContent=lastError||(!state?t("loading"):state.guest_draft?.storage_error?t("draft_storage_error"):state.storage_error?t("storage_error"):state.vehicle_target_active?t("vehicle_target"):good?t("ready"):job?t(own?job.state:"other_job"):state.read_only?t("read_only"):t("unavailable"));
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
      if(proposal&&(proposal.revision!==data.revision||proposal.draft_revision!==data.guest_draft?.revision))invalidate();
      if(!draftDirty&&data.guest_draft?.values){
        draftRevision=data.guest_draft.revision;
        for(const [key,id] of Object.entries(fields))el(id).value=String(data.guest_draft.values[key]);
      }else if(draftDirty&&draftRevision!==data.guest_draft?.revision)lastError=t("job_changed");
      if(lastError===t("offline"))lastError="";
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
  el("guest_form").addEventListener("input",()=>{draftDirty=true;invalidate();lastError="";render();});
  function scheduleRead(){clearTimeout(timer);timer=null;if(live()&&!busy&&!readAbort)timer=setTimeout(read,5000);}
  async function saveDraft(){
    if(!state||!draftRevision||busy||!el("guest_form").reportValidity())return false;
    const changes=Object.fromEntries(Object.entries(fields).map(([key,id])=>[key,Number(el(id).value)]));
    if(!Number.isInteger(changes.duration_minutes)||changes.duration_minutes<1||changes.duration_minutes>1440){lastError=t("invalid");render();return false;}
    const csrf=state.csrf;
    const result=await post({action:"edit_guest_draft",draft_revision:draftRevision,changes});
    if(!result?.guest_draft)return false;
    state={...result,csrf};draftRevision=result.guest_draft.revision;draftDirty=false;render();return true;
  }
  el("guest_save_draft").addEventListener("click",async()=>{invalidate();if(await saveDraft())lastError=t("draft_saved");render();scheduleRead();});
  el("guest_reload_draft").addEventListener("click",()=>{if(busy)return;draftDirty=false;lastError="";invalidate();suspend();void read();});
  el("guest_reset_draft").addEventListener("click",async()=>{
    if(!state||busy)return;invalidate();lastError="";
    const result=await post({action:"reset_guest_draft",draft_revision:draftRevision,confirm:true});
    if(result)draftDirty=false;
    state=null;render();void read();
  });
  el("guest_form").addEventListener("submit",async event=>{
    event.preventDefault();if(!state||busy||!el("guest_form").reportValidity())return;
    invalidate();lastError="";
    if(!await saveDraft()){scheduleRead();return;}
    const result=await post({action:"preview_guest_draft",draft_revision:draftRevision,revision:state.revision,scope:state.scope});
    if(result?.schema===1&&result.request?.kind==="guest"){
      proposal=result;el("guest_summary").textContent=`${date(result.request.start_at)} → ${date(result.request.finish_at)} · ${result.request.current_a} A · ${result.request.energy_kwh} kWh`;
      el("guest_confirmation").hidden=false;render();
    }else{state=null;render();}
    scheduleRead();
  });
  el("guest_confirm").addEventListener("click",async()=>{
    if(!proposal||busy)return;const p=proposal;invalidate();lastError="";
    await post({action:"create_guest_draft",draft_revision:p.draft_revision,revision:p.revision,scope:p.scope,request:p.request});
    state=null;render();void read();
  });
  for(const action of ["stop","clear"])el(`guest_${action}`).addEventListener("click",async()=>{
    if(!state||busy||(state.job?.kind!=="guest"&&!(action==="clear"&&state.storage_error)))return;const revision=state.revision;invalidate();lastError="";
    await post({action,revision,confirm:true});state=null;render();void read();
  });
  document.addEventListener("visibilitychange",()=>{suspend();invalidate();state=null;render();if(live())void read();});
  addEventListener("pagehide",()=>{hidden=true;suspend();invalidate();state=null;});
  addEventListener("pageshow",()=>{hidden=false;void read();});
  render();void read();
})();
