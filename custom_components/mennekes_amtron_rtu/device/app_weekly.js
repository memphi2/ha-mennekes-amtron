/* Local weekly plans: preview/confirm, no automatic POST retry or credentials. */
(() => {
  "use strict";
  const languages = ["en", "de", "fr", "nl", "it"];
  const found = languages.indexOf((document.documentElement.lang || "").slice(0, 2).toLowerCase());
  const column = found < 0 ? 0 : found, locale = languages[column];
  const words = {
    reserve:["Immediate reserve per departure (kWh)", "Sofortreserve je Abfahrt (kWh)", "Réserve immédiate par départ (kWh)", "Directe reserve per vertrek (kWh)", "Riserva immediata per partenza (kWh)"],
    reserve_hint:["Charge this portion immediately when each order is accepted, then return to its time window. Included in the total ceiling. Zero disables; AC energy, not a battery percentage.", "Diese Teilmenge bei Annahme jedes Auftrags sofort laden, danach zum Zeitfenster zurückkehren. In der Gesamtobergrenze enthalten. Null deaktiviert; AC-Energie, kein Batterie-Prozentwert.", "Recharger cette part dès l’acceptation de chaque ordre, puis revenir à sa plage horaire. Comprise dans le plafond total. Zéro désactive ; énergie AC, pas un pourcentage de batterie.", "Laad dit deel direct zodra een opdracht is aanvaard en keer daarna terug naar het tijdvenster. Inbegrepen in de totale bovengrens. Nul schakelt uit; AC-energie, geen accupercentage.", "Ricaricare subito questa parte all’accettazione di ogni ordine, poi tornare alla sua finestra oraria. Inclusa nel tetto totale. Zero la disattiva; energia AC, non percentuale della batteria."],
    title:["Weekly plans", "Wochenpläne", "Plans hebdomadaires", "Weekplannen", "Piani settimanali"],
    intro:["Save a recipe, inspect its dates, then confirm the vehicle for this connection.", "Plan speichern, Termine prüfen und das Fahrzeug für diesen Steckvorgang bestätigen.", "Enregistrez un modèle, vérifiez ses dates, puis confirmez le véhicule pour cette connexion.", "Sla een sjabloon op, controleer de data en bevestig daarna het voertuig voor deze verbinding.", "Salvare un modello, controllarne le date, poi confermare il veicolo per questo collegamento."],
    loading:["Loading…", "Wird geladen…", "Chargement…", "Laden…", "Caricamento…"],
    activation:["Current activation", "Aktuelle Aktivierung", "Activation actuelle", "Huidige activering", "Attivazione attuale"],
    stop:["Stop weekly automation", "Wochenautomatik stoppen", "Arrêter l’automatisme hebdomadaire", "Weekautomatisering stoppen", "Interrompere l’automazione settimanale"],
    stop_hint:["Stops its own job and recurrence. An independent guest or manual job is not changed.", "Stoppt den zugehörigen Auftrag und weitere Termine. Ein unabhängiger Gast- oder manueller Auftrag bleibt unverändert.", "Arrête son propre ordre et la récurrence. Un ordre invité ou manuel indépendant n’est pas modifié.", "Stopt de eigen opdracht en de herhaling. Een onafhankelijke gast- of handmatige opdracht blijft ongewijzigd.", "Ferma il proprio ordine e la ricorrenza. Un ordine ospite o manuale indipendente non viene modificato."],
    saved_plans:["Saved vehicle plans", "Gespeicherte Fahrzeugpläne", "Plans de véhicule enregistrés", "Opgeslagen voertuigplannen", "Piani del veicolo salvati"],
    new_plan:["New plan", "Neuer Plan", "Nouveau plan", "Nieuw plan", "Nuovo piano"],
    recipe:["Recipe — not charging permission", "Planvorlage — noch keine Ladefreigabe", "Modèle — pas une autorisation de recharge", "Sjabloon — geen laadtoestemming", "Modello — non un consenso alla ricarica"],
    profile:["Vehicle profile reference", "Fahrzeugreferenz", "Référence du profil de véhicule", "Referentie voertuigprofiel", "Riferimento del profilo del veicolo"],
    profile_hint:["Use a stable reference such as bmw-i4. This label does not identify a vehicle automatically.", "Eine feste Referenz wie bmw-i4 verwenden. Die Bezeichnung erkennt das Fahrzeug nicht automatisch.", "Utilisez une référence stable comme bmw-i4. Ce libellé n’identifie pas automatiquement un véhicule.", "Gebruik een vaste referentie zoals bmw-i4. Dit label herkent een voertuig niet automatisch.", "Usare un riferimento stabile come bmw-i4. Questa etichetta non identifica automaticamente un veicolo."],
    timezone:["Timezone", "Zeitzone", "Fuseau horaire", "Tijdzone", "Fuso orario"],
    weekdays:["Weekdays", "Wochentage", "Jours de la semaine", "Weekdagen", "Giorni della settimana"],
    departure:["Departure", "Abfahrt", "Départ", "Vertrek", "Partenza"],
    window:["Charging window (minutes)", "Ladefenster (Minuten)", "Plage de recharge (minutes)", "Laadvenster (minuten)", "Finestra di ricarica (minuti)"],
    current:["Maximum current (A)", "Maximaler Strom (A)", "Courant maximal (A)", "Maximale stroom (A)", "Corrente massima (A)"],
    energy:["Energy ceiling per departure (kWh)", "Energieobergrenze je Abfahrt (kWh)", "Plafond d’énergie par départ (kWh)", "Energiegrens per vertrek (kWh)", "Tetto di energia per partenza (kWh)"],
    fold:["Repeated clock time", "Doppelt vorkommende Uhrzeit", "Heure répétée", "Herhaald kloktijdstip", "Orario ripetuto"],
    earlier:["Earlier occurrence", "Früherer Zeitpunkt", "Première occurrence", "Eerste keer", "Prima occorrenza"],
    later:["Later occurrence", "Späterer Zeitpunkt", "Seconde occurrence", "Tweede keer", "Seconda occorrenza"],
    exceptions:["Date exceptions", "Einmalige Ausnahmen", "Exceptions de date", "Datumuitzonderingen", "Eccezioni di data"],
    exception_hint:["One line per date: YYYY-MM-DD = skip or HH:MM. Up to 32 dates. Overrides can also add a departure.", "Je Zeile: JJJJ-MM-TT = skip oder HH:MM. Maximal 32 Tage. Eine Uhrzeit kann auch eine zusätzliche Abfahrt festlegen.", "Une ligne par date : AAAA-MM-JJ = skip ou HH:MM. Jusqu’à 32 dates. Une heure peut aussi ajouter un départ.", "Eén regel per datum: JJJJ-MM-DD = skip of UU:MM. Maximaal 32 data. Een tijd kan ook een vertrek toevoegen.", "Una riga per data: AAAA-MM-GG = skip o HH:MM. Fino a 32 date. Un orario può anche aggiungere una partenza."],
    enabled:["Enable recipe", "Planvorlage aktivieren", "Activer le modèle", "Sjabloon inschakelen", "Attivare il modello"],
    preview:["Preview changes", "Änderungen prüfen", "Aperçu des modifications", "Voorbeeld van wijzigingen", "Anteprima delle modifiche"],
    remove:["Remove saved plan", "Gespeicherten Plan entfernen", "Supprimer le plan enregistré", "Opgeslagen plan verwijderen", "Rimuovere il piano salvato"],
    proposal:["Confirm recipe", "Plan bestätigen", "Confirmer le modèle", "Sjabloon bevestigen", "Confermare il modello"],
    save:["Save this recipe", "Diesen Plan speichern", "Enregistrer ce modèle", "Dit sjabloon opslaan", "Salvare questo modello"],
    activate_title:["Activate saved plan for this vehicle", "Gespeicherten Plan für dieses Fahrzeug aktivieren", "Activer le plan enregistré pour ce véhicule", "Opgeslagen plan voor dit voertuig activeren", "Attivare il piano salvato per questo veicolo"],
    consent_hint:["Recurrence applies only to this connection, for at most seven days. Each departure has its own energy ceiling. Restart, unplugging, a manual stop or editing the active recipe ends it.", "Die Wiederholung gilt nur für diesen Steckvorgang, höchstens sieben Tage. Jede Abfahrt hat eine eigene Energieobergrenze. Neustart, Abstecken, manueller Stopp oder Änderung des aktiven Plans beenden sie.", "La récurrence ne vaut que pour cette connexion, sept jours au plus. Chaque départ a son propre plafond d’énergie. Un redémarrage, un débranchement, un arrêt manuel ou une modification du modèle actif y mettent fin.", "De herhaling geldt alleen voor deze verbinding, hoogstens zeven dagen. Elk vertrek heeft een eigen energiegrens. Een herstart, loskoppelen, een handmatige stop of het wijzigen van het actieve sjabloon beëindigt haar.", "La ricorrenza vale solo per questo collegamento, al massimo sette giorni. Ogni partenza ha il proprio tetto di energia. Riavvio, scollegamento, stop manuale o modifica del modello attivo la terminano."],
    confirm_vehicle:["I have checked that the selected vehicle is connected.", "Ich habe geprüft, dass das ausgewählte Fahrzeug angeschlossen ist.", "J’ai vérifié que le véhicule choisi est connecté.", "Ik heb gecontroleerd dat het gekozen voertuig is aangesloten.", "Ho verificato che il veicolo selezionato è collegato."],
    activate_preview:["Preview activation", "Aktivierung prüfen", "Aperçu de l’activation", "Voorbeeld van de activering", "Anteprima dell’attivazione"],
    activate:["Confirm activation", "Aktivierung bestätigen", "Confirmer l’activation", "Activering bevestigen", "Confermare l’attivazione"],
    open_job:["View or stop the accepted charging job", "Angenommenen Ladeauftrag ansehen oder stoppen", "Voir ou arrêter l’ordre de recharge accepté", "Aanvaarde laadopdracht bekijken of stoppen", "Vedere o fermare l’ordine di ricarica accettato"],
    footer:["No automatic authorization or unlocking. Missing local clock times are not silently moved. Actual starts and stops remain subject to wallbox timing.", "Keine automatische Autorisierung oder Entriegelung. Nicht existierende Uhrzeiten werden nicht verschoben. Tatsächliche Starts und Stopps unterliegen den Schaltzeiten der Wallbox.", "Aucune autorisation ni déverrouillage automatique. Les heures locales inexistantes ne sont pas déplacées en silence. Les démarrages et arrêts réels dépendent des délais de la borne.", "Geen automatische autorisatie of ontgrendeling. Niet-bestaande lokale tijden worden niet stilzwijgend verschoven. Werkelijke starts en stops hangen af van de schakeltijden van de wallbox.", "Nessuna autorizzazione o sblocco automatico. Gli orari locali inesistenti non vengono spostati in silenzio. Avvii e stop effettivi dipendono dai tempi della wallbox."],
    offline:["No current service response. Reload before changing anything.", "Keine aktuelle Service-Antwort. Vor Änderungen erneut laden.", "Aucune réponse actuelle du service. Rechargez avant toute modification.", "Geen actueel antwoord van de service. Herlaad voordat u iets wijzigt.", "Nessuna risposta attuale del servizio. Ricaricare prima di modificare qualcosa."],
    unconfirmed:["Result unconfirmed. Reload to check the actual state; the command is not retried automatically.", "Ergebnis unbestätigt. Den tatsächlichen Zustand neu laden; der Befehl wird nicht automatisch wiederholt.", "Résultat non confirmé. Rechargez pour vérifier l’état réel ; la commande n’est pas répétée automatiquement.", "Resultaat niet bevestigd. Herlaad om de werkelijke toestand te controleren; de opdracht wordt niet automatisch herhaald.", "Risultato non confermato. Ricaricare per verificare lo stato reale; il comando non viene ripetuto automaticamente."],
    live:["Current service state loaded.", "Aktueller Service-Zustand geladen.", "État actuel du service chargé.", "Actuele servicestatus geladen.", "Stato attuale del servizio caricato."],
    invalid:["Check the fields, weekdays and date exceptions.", "Bitte Eingaben, Wochentage und Datums-Ausnahmen prüfen.", "Vérifiez les champs, les jours et les exceptions de date.", "Controleer de velden, weekdagen en datumuitzonderingen.", "Verificare i campi, i giorni e le eccezioni di data."],
    edit_stop:["Saving this edit stops the active weekly job and its recurrence.", "Diese Änderung stoppt den aktiven Wochenplan-Auftrag und seine Wiederholung.", "Enregistrer cette modification arrête l’ordre hebdomadaire actif et sa récurrence.", "Deze wijziging opslaan stopt de actieve weekopdracht en de herhaling.", "Salvare questa modifica ferma l’ordine settimanale attivo e la sua ricorrenza."],
    remove_confirm:["Remove this recipe? Its active weekly job will also be stopped. Consumed dates are retained.", "Plan entfernen? Sein aktiver Wochenplan-Auftrag wird ebenfalls gestoppt. Verbrauchte Termine bleiben gespeichert.", "Supprimer ce modèle ? Son ordre hebdomadaire actif sera aussi arrêté. Les dates utilisées sont conservées.", "Dit sjabloon verwijderen? De actieve weekopdracht wordt ook gestopt. Gebruikte data blijven bewaard.", "Rimuovere questo modello? Anche il suo ordine settimanale attivo verrà fermato. Le date già usate restano salvate."],
    saved:["Recipe saved. Activation is a separate confirmation.", "Plan gespeichert. Die Aktivierung muss separat bestätigt werden.", "Modèle enregistré. L’activation est une confirmation distincte.", "Sjabloon opgeslagen. Activeren is een aparte bevestiging.", "Modello salvato. L’attivazione è una conferma separata."],
    active:["Active for this connection", "Für diesen Steckvorgang aktiv", "Actif pour cette connexion", "Actief voor deze verbinding", "Attivo per questo collegamento"],
    inactive:["Not activated", "Nicht aktiviert", "Non activé", "Niet geactiveerd", "Non attivato"],
    consumed:["Already consumed", "Bereits verbraucht", "Déjà utilisé", "Al gebruikt", "Già usato"],
    skipped:["Skipped", "Ausgesetzt", "Ignoré", "Overgeslagen", "Saltato"],
    ready:["Scheduled window", "Geplantes Ladefenster", "Plage prévue", "Gepland venster", "Finestra pianificata"],
    invalid_time:["Local time does not exist", "Ortszeit existiert nicht", "L’heure locale n’existe pas", "Lokale tijd bestaat niet", "L’ora locale non esiste"],
    weekly_changed:["State changed. Reload and preview again.", "Zustand geändert. Neu laden und erneut prüfen.", "L’état a changé. Rechargez et refaites l’aperçu.", "Toestand gewijzigd. Herlaad en bekijk het voorbeeld opnieuw.", "Lo stato è cambiato. Ricaricare e ripetere l’anteprima."],
    job_changed:["Charging state changed. Preview again.", "Ladezustand geändert. Erneut prüfen.", "L’état de recharge a changé. Refaites l’aperçu.", "Laadtoestand gewijzigd. Bekijk het voorbeeld opnieuw.", "Lo stato di ricarica è cambiato. Ripetere l’anteprima."],
    weekly_storage_unavailable:["Weekly-plan storage is unavailable or unconfirmed. Restart and inspect storage before activating.", "Wochenplan-Speicher nicht verfügbar oder unbestätigt. Vor Aktivierung neu starten und Speicher prüfen.", "Le stockage des plans hebdomadaires est indisponible ou non confirmé. Redémarrez et vérifiez le stockage avant d’activer.", "De opslag van weekplannen is niet beschikbaar of onbevestigd. Herstart en controleer de opslag voordat u activeert.", "La memoria dei piani settimanali non è disponibile o non è confermata. Riavviare e verificare la memoria prima di attivare."],
    weekly_confirmation_expired:["Confirmation expired. Preview again.", "Bestätigung abgelaufen. Erneut prüfen.", "Confirmation expirée. Refaites l’aperçu.", "Bevestiging verlopen. Bekijk het voorbeeld opnieuw.", "Conferma scaduta. Ripetere l’anteprima."],
    weekly_no_occurrence:["No unconsumed valid departure within seven days.", "Keine unverbrauchte gültige Abfahrt innerhalb von sieben Tagen.", "Aucun départ valide non utilisé dans les sept jours.", "Geen ongebruikt geldig vertrek binnen zeven dagen.", "Nessuna partenza valida non usata entro sette giorni."],
    weekly_vehicle_not_confirmed:["Vehicle or connection changed. Confirm the current vehicle again.", "Fahrzeug oder Verbindung geändert. Aktuelles Fahrzeug erneut bestätigen.", "Véhicule ou connexion modifié. Confirmez de nouveau le véhicule actuel.", "Voertuig of verbinding gewijzigd. Bevestig het huidige voertuig opnieuw.", "Veicolo o collegamento modificati. Confermare di nuovo il veicolo attuale."],
    weekly_already_active:["A weekly plan is already active for this connection. Stop it first.", "Für diesen Steckvorgang ist bereits ein Wochenplan aktiv. Zuerst diesen stoppen.", "Un plan hebdomadaire est déjà actif pour cette connexion. Arrêtez-le d’abord.", "Er is al een weekplan actief voor deze verbinding. Stop dit eerst.", "Un piano settimanale è già attivo per questo collegamento. Fermarlo prima."],
    weekly_invalid_confirmation:["This confirmation is not valid. Preview again.", "Diese Bestätigung ist ungültig. Erneut prüfen.", "Cette confirmation n’est pas valide. Refaites l’aperçu.", "Deze bevestiging is ongeldig. Bekijk het voorbeeld opnieuw.", "Questa conferma non è valida. Ripetere l’anteprima."],
    weekly_stop_unconfirmed:["A stop is not confirmed yet. Wait for the pause before starting again.", "Ein Stopp ist noch nicht bestätigt. Vor einem neuen Start die Pause abwarten.", "Un arrêt n’est pas encore confirmé. Attendez la pause avant de redémarrer.", "Een stop is nog niet bevestigd. Wacht op de pauze voordat u opnieuw start.", "Uno stop non è ancora confermato. Attendere la pausa prima di riavviare."],
    invalid_weekly_request:["Invalid request. Reload the page.", "Ungültige Anfrage. Seite neu laden.", "Requête non valide. Rechargez la page.", "Ongeldig verzoek. Herlaad de pagina.", "Richiesta non valida. Ricaricare la pagina."],
    job_exists:["Another order is still running. Stop it first.", "Ein anderer Auftrag läuft noch. Zuerst diesen stoppen.", "Un autre ordre est encore en cours. Arrêtez-le d’abord.", "Er loopt nog een andere opdracht. Stop deze eerst.", "Un altro ordine è ancora in corso. Fermarlo prima."],
    job_not_permitted:["Check connection, meter, authorization and independent stops.", "Verbindung, Zähler, Autorisierung und unabhängige Sperren prüfen.", "Vérifiez la connexion, le compteur, l’autorisation et les arrêts indépendants.", "Controleer verbinding, meter, autorisatie en onafhankelijke stops.", "Verificare collegamento, contatore, autorizzazione e stop indipendenti."],
    job_current_exceeds_cap:["Requested current exceeds the service limit.", "Gewünschter Strom überschreitet die Service-Grenze.", "Le courant demandé dépasse la limite du service.", "Gevraagde stroom overschrijdt de servicegrens.", "La corrente richiesta supera il limite del servizio."],
    job_preview_expired:["The start lies more than a minute in the past. Preview again.", "Der Start liegt mehr als eine Minute zurück. Erneut prüfen.", "Le début remonte à plus d’une minute. Refaites l’aperçu.", "De start ligt meer dan een minuut in het verleden. Bekijk het voorbeeld opnieuw.", "L’inizio risale a più di un minuto fa. Ripetere l’anteprima."],
    job_storage_unavailable:["Storage unavailable; no automatic start.", "Speicher nicht verfügbar; kein automatischer Start.", "Stockage indisponible ; aucun démarrage automatique.", "Opslag niet beschikbaar; geen automatische start.", "Memoria non disponibile; nessun avvio automatico."],
    until:["Valid until", "Gültig bis", "Valable jusqu’au", "Geldig tot", "Valido fino al"],
    first:["First departure", "Erste Abfahrt", "Premier départ", "Eerste vertrek", "Prima partenza"],
    changed_preview:["The state changed; the previous preview was discarded.", "Der Zustand wurde geändert; die vorherige Vorschau wurde verworfen.", "L’état a changé ; l’aperçu précédent a été abandonné.", "De toestand is gewijzigd; het vorige voorbeeld is vervallen.", "Lo stato è cambiato; l’anteprima precedente è stata scartata."]
  };
  const t = key => words[key]?.[column] || key;
  const el = id => document.getElementById(id);
  const days = [["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],["Mo","Di","Mi","Do","Fr","Sa","So"],["lun.","mar.","mer.","jeu.","ven.","sam.","dim."],["ma","di","wo","do","vr","za","zo"],["lun","mar","mer","gio","ven","sab","dom"]][column];
  document.querySelectorAll("[data-text]").forEach(node => { node.textContent = t(node.dataset.text); });
  document.querySelectorAll("[data-day]").forEach(node => { node.textContent = days[Number(node.dataset.day)]; });
  const dayInputs = () => [...document.querySelectorAll('input[name="weekday"]')];
  let state = null, csrf = "", selected = "", dirty = false, planProposal = null, activation = null, loadedRecipe;
  let busy = false, timer = null, controller = null, generation = 0, stopped = false;
  const visible = () => !document.hidden && !stopped;
  const plan = () => state?.plans.find(item => item.plan.id === selected)?.plan || null;
  const stamp = value => { const date = new Date(value); return Number.isFinite(date.getTime()) ? date.toLocaleString(locale, {timeZoneName:"short"}) : "—"; };
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
    for (const id of ["vehicle_profile","timezone","departure_time","charge_minutes","current_a","energy_kwh","reserve_kwh","ambiguous_time"]) {
      el(id).value = recipe ? (recipe[id] ?? (id === "reserve_kwh" ? 0 : "")) : ({vehicle_profile:"",timezone:"Europe/Berlin",departure_time:"07:40",charge_minutes:240,current_a:6,energy_kwh:10,reserve_kwh:0,ambiguous_time:"earlier"})[id];
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
  const reserveText = value => value.reserve_kwh ? ` · ${t("reserve")}: ${value.reserve_kwh}` : "";
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
    const reserve=Number(el("reserve_kwh").value);
    if(!Number.isFinite(reserve)||reserve<0||reserve>Number(el("energy_kwh").value)||Math.abs(reserve*10-Math.round(reserve*10))>1e-8)throw new Error("invalid");
    const id=selected || crypto.randomUUID().replaceAll("-","");
    return {id,...(reserve>0 ? {reserve_kwh:reserve} : {}),vehicle_profile:el("vehicle_profile").value,timezone:el("timezone").value,weekdays,
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
      el("plan_summary").textContent=`${data.plan.vehicle_profile} · ${data.plan.current_a} A · ${data.plan.energy_kwh} kWh${reserveText(data.plan)}\n${data.plan.timezone} · ${data.plan.charge_minutes} min`;
      el("plan_warning").textContent=data.will_stop_active ? t("edit_stop") : ""; rows(data.upcoming);
    });
  });
  el("save_plan").addEventListener("click",()=>{if(!planProposal)return; const p=planProposal;void post({action:"save_plan",revision:p.revision,plan:p.plan,confirm:true},data=>{selected=p.plan.id;dirty=false;discard();receive(data);notice(t("saved"));});});
  el("remove_plan").addEventListener("click",()=>{if(!state || !plan() || !confirm(t("remove_confirm")))return;void post({action:"remove_plan",revision:state.revision,plan_id:selected,confirm:true},data=>{dirty=false;selected="";discard();receive(data);notice(t("live"));});});
  el("preview_activation").addEventListener("click",()=>{
    const saved=plan(); if(!state || !saved || dirty || !el("confirm_vehicle").checked)return;
    void post({action:"preview_activation",revision:state.revision,scope:state.scope,plan_id:saved.id,vehicle_profile:saved.vehicle_profile},data=>{
      activation=data;el("activation_preview").hidden=false;
      el("activation_summary").textContent=`${data.vehicle_profile}\n${t("first")}: ${stamp(data.request.finish_at)}\n${data.request.current_a} A · ${data.request.energy_kwh} kWh${reserveText(data.request)}\n${t("until")}: ${stamp(data.active_until)}`;
    });
  });
  el("activate").addEventListener("click",()=>{if(!activation)return;const proposal=activation;void post({action:"activate",proposal,confirm:true},data=>{discard();receive(data);notice(t("active"));});});
  el("deactivate").addEventListener("click",()=>{if(!state)return;void post({action:"deactivate",revision:state.revision,confirm:true},data=>{discard();receive(data);notice(t("inactive"));});});
  document.addEventListener("visibilitychange",()=>{suspend();discard();state=null;csrf="";controls();if(visible())void refresh();});
  addEventListener("pagehide",()=>{stopped=true;suspend();discard();state=null;controls();});
  addEventListener("pageshow",()=>{stopped=false;suspend();discard();void refresh();});
  controls(); void refresh();
})();
