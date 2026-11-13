/* Guest presets reuse the job API. No credentials in storage and no POST retry. */
(() => {
  "use strict";
  const languages=["en","de","fr","nl","it"];
  const found=languages.indexOf((document.documentElement.lang||"").slice(0,2).toLowerCase());
  const column=found<0?0:found, locale=languages[column];
  const words={
    title:["Guest charging","Gastladen","Recharge invité","Gastladen","Ricarica ospite"],
    intro:["One immediate energy/time order for the connected guest vehicle. No battery data or saved vehicle target is used.","Ein sofortiger Energie-/Zeitauftrag für das angeschlossene Gastfahrzeug. Keine Batteriedaten oder gespeicherten Fahrzeugziele werden übernommen.","Un ordre immédiat d’énergie et de durée pour le véhicule invité connecté. Aucune donnée de batterie ni objectif de véhicule enregistré n’est utilisé.","Eén directe energie-/tijdopdracht voor het aangesloten gastvoertuig. Er worden geen accugegevens of opgeslagen voertuigdoelen gebruikt.","Un ordine immediato di energia e durata per il veicolo ospite collegato. Non vengono usati dati della batteria né obiettivi salvati del veicolo."],
    status:["Current order","Aktueller Auftrag","Ordre actuel","Huidige opdracht","Ordine attuale"],
    new:["New guest order","Neuer Gastauftrag","Nouvel ordre invité","Nieuwe gastopdracht","Nuovo ordine ospite"],
    energy:["Additional energy ceiling, kWh","Zusätzliche Energieobergrenze in kWh","Plafond d’énergie supplémentaire, kWh","Bovengrens extra energie, kWh","Tetto di energia aggiuntiva, kWh"],
    duration:["Maximum duration, minutes","Maximale Dauer in Minuten","Durée maximale, minutes","Maximale duur, minuten","Durata massima, minuti"],
    current:["Current per phase, A","Strom je Phase in A","Courant par phase, A","Stroom per fase, A","Corrente per fase, A"],
    permission:["Authorize locally first, for example by RFID. This order cannot unlock the wallbox, grant release or override a battery target.","Zuerst lokal autorisieren, beispielsweise per RFID. Dieser Auftrag kann die Wallbox nicht entsperren, keine Ladefreigabe erteilen und kein Batterieziel übergehen.","Autorisez d’abord localement, par exemple par RFID. Cet ordre ne peut ni déverrouiller la borne, ni accorder l’autorisation, ni passer outre un objectif de batterie.","Autoriseer eerst lokaal, bijvoorbeeld met RFID. Deze opdracht kan de wallbox niet ontgrendelen, geen vrijgave verlenen en geen accudoel negeren.","Autorizzare prima localmente, ad esempio con RFID. Questo ordine non può sbloccare la wallbox, concedere il consenso o ignorare un obiettivo della batteria."],
    preview:["Preview","Vorschau","Aperçu","Voorbeeld","Anteprima"],
    confirm_title:["Confirm guest order","Gastauftrag bestätigen","Confirmer l’ordre invité","Gastopdracht bevestigen","Confermare l’ordine ospite"],
    confirm:["Confirm and start when permitted","Bestätigen und bei gültiger Freigabe starten","Confirmer et démarrer dès que c’est autorisé","Bevestigen en starten zodra toegestaan","Confermare e avviare quando consentito"],
    stop:["Stop guest order","Gastauftrag stoppen","Arrêter l’ordre invité","Gastopdracht stoppen","Fermare l’ordine ospite"],
    clear:["Clear stopped order without resuming","Gestoppten Auftrag löschen – ohne Ladestart","Effacer l’ordre arrêté sans reprendre","Gestopte opdracht wissen zonder te hervatten","Cancellare l’ordine fermato senza riprendere"],
    scope:["Only this physical connection. Stops at the energy ceiling or deadline, subject to polling and hardware timing. Reconnection requires a new order.","Nur für diesen Anschluss. Stopp bei Energieobergrenze oder Zeitende unter Beachtung der Abfrage- und Hardwarezeiten. Nach erneutem Anstecken ist ein neuer Auftrag erforderlich.","Uniquement cette connexion physique. Arrêt au plafond d’énergie ou à l’échéance, selon les délais d’interrogation et du matériel. Une reconnexion exige un nouvel ordre.","Alleen deze fysieke verbinding. Stopt bij de energiegrens of eindtijd, afhankelijk van de uitlees- en hardwaretijden. Opnieuw aansluiten vereist een nieuwe opdracht.","Solo questo collegamento fisico. Si ferma al tetto di energia o alla scadenza, secondo i tempi di lettura e dell’hardware. Un nuovo collegamento richiede un nuovo ordine."],
    footer:["No guaranteed delivered amount. A requested stop is not a confirmed hardware stop. Check the overview and command results.","Keine garantierte Energiemenge. Ein angeforderter Stopp ist kein bestätigter Hardware-Stopp. Übersicht und Befehlsergebnisse beachten.","Aucune quantité livrée garantie. Un arrêt demandé n’est pas un arrêt matériel confirmé. Consultez la vue d’ensemble et les résultats des commandes.","Geen gegarandeerde geleverde hoeveelheid. Een gevraagde stop is geen bevestigde hardwarestop. Controleer het overzicht en de opdrachtresultaten.","Nessuna quantità erogata garantita. Uno stop richiesto non è uno stop hardware confermato. Controllare la panoramica e i risultati dei comandi."],
    ready:["Ready for a guest order.","Bereit für einen Gastauftrag.","Prêt pour un ordre invité.","Gereed voor een gastopdracht.","Pronto per un ordine ospite."],
    loading:["Reading service state…","Servicestatus wird gelesen …","Lecture de l’état du service…","Servicestatus wordt gelezen…","Lettura dello stato del servizio…"],
    offline:["No current response. Controls disabled until status is read again.","Keine aktuelle Antwort. Bedienung bis zur erneuten Statusabfrage gesperrt.","Aucune réponse actuelle. Commandes désactivées jusqu’à la prochaine lecture de l’état.","Geen actueel antwoord. Bediening uitgeschakeld tot de status opnieuw is gelezen.","Nessuna risposta attuale. Comandi disattivati fino alla prossima lettura dello stato."],
    no_job:["No order","Kein Auftrag","Aucun ordre","Geen opdracht","Nessun ordine"],
    other_job:["Another charging order exists. Manage it on the Charging jobs page.","Ein anderer Ladeauftrag besteht. Diesen auf der Seite Ladeaufträge verwalten.","Un autre ordre de recharge existe. Gérez-le sur la page Ordres de recharge.","Er bestaat een andere laadopdracht. Beheer deze op de pagina Laadopdrachten.","Esiste un altro ordine di ricarica. Gestirlo nella pagina Ordini di ricarica."],
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
    job_service_starting:["The service is still starting. Try again shortly.", "Der Service startet noch. Gleich noch einmal versuchen.", "Le service démarre encore. Réessayez dans un instant.", "De service start nog. Probeer het zo opnieuw.", "Il servizio si sta ancora avviando. Riprova tra poco."],
    weekly_service_starting:["The service is still starting. Try again shortly.", "Der Service startet noch. Gleich noch einmal versuchen.", "Le service démarre encore. Réessayez dans un instant.", "De service start nog. Probeer het zo opnieuw.", "Il servizio si sta ancora avviando. Riprova tra poco."],
    weekly_stop_unconfirmed:["The weekly plan's stop is not confirmed yet. Try again shortly.", "Der Stopp des Wochenplans ist noch nicht bestätigt. Gleich noch einmal versuchen.", "L’arrêt du plan hebdomadaire n’est pas encore confirmé. Réessayez dans un instant.", "De stop van het weekplan is nog niet bevestigd. Probeer het zo opnieuw.", "Lo stop del piano settimanale non è ancora confermato. Riprova tra poco."]
  };
  const t=k=>words[k]?.[column]||words.uncertain[column];
  const el=id=>document.getElementById(id), date=v=>new Date(v).toLocaleString(locale,{timeZoneName:"short"});
  document.querySelectorAll("[data-text]").forEach(n=>{n.textContent=t(n.dataset.text);});
  let state=null, proposal=null, busy=false, timer=null, readAbort=null, generation=0, hidden=false, lastError="";
  const live=()=>!hidden&&!document.hidden;
  function invalidate(){proposal=null;el("guest_confirmation").hidden=true;}
  function render(){
    const job=state?.job, own=job?.kind==="guest", ended=Boolean(job)&&["reached","finished","stopped","interrupted"].includes(job.state);
    const good=state?.ready&&!state.storage_error&&state.available&&state.present===true&&state.permitted&&state.meter_fresh&&!state.vehicle_target_active&&!job?.holding;
    el("guest_fields").disabled=busy||!good;
    el("guest_current").max=String(state?.max_current_a||6);
    el("guest_confirm").disabled=busy||!proposal;
    el("guest_stop").disabled=busy||!own||!job.holding||ended;
    el("guest_clear").disabled=busy||!own||!ended;
    el("guest_status").hidden=!state;
    el("guest_state").textContent=!job||(!own&&!job.holding)?t("no_job"):own?t(job.state):t("other_job");
    el("guest_detail").textContent=own?`${date(job.start_at)} → ${date(job.finish_at)} · ${job.current_a} A · ${job.delivered_kwh==null?"—":Number(job.delivered_kwh).toLocaleString(locale,{maximumFractionDigits:2})} / ${job.energy_kwh} kWh${job.scope_ended?` · ${t("scope_ended")}`:!job.holding?` · ${t("released")}`:""}${state.action_error?` · ${t("unconfirmed")}`:""}`:"";
    el("guest_notice").textContent=lastError||(!state?t("loading"):state.storage_error?t("storage_error"):state.vehicle_target_active?t("vehicle_target"):good?t("ready"):job?t(own?job.state:"other_job"):state.read_only?t("read_only"):t("unavailable"));
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
