/* Explicit edits of existing persistent targets. No storage, credentials or POST retry. */
(() => {
  "use strict";
  const langs=["en","de","fr","nl","it"], index=langs.indexOf(document.documentElement.lang.slice(0,2)), lang=index<0?0:index;
  const words={
    guest_started:["Guest start requested; check the overview for confirmation.", "Gaststart angefordert; die Übersicht zeigt die Bestätigung.", "Démarrage invité demandé ; vérifiez la confirmation dans la vue d’ensemble.", "Gaststart aangevraagd; controleer de bevestiging in het overzicht.", "Avvio ospite richiesto; verifica la conferma nella panoramica."],
    guest_hint:["Charges the connected car now as a guest, also with Plug & Charge, and lifts a pause. Guest charging ends when the car is unplugged.", "Lädt das angeschlossene Fahrzeug jetzt als Gast, auch mit Plug & Charge, und hebt eine Pause auf. Das Gastladen endet mit dem Abstecken.", "Recharge maintenant le véhicule branché en tant qu’invité, même avec Plug & Charge, et lève une pause. La recharge invité se termine au débranchement.", "Laadt de aangesloten auto nu als gast, ook met Plug & Charge, en heft een pauze op. Gastladen eindigt bij het loskoppelen.", "Ricarica ora il veicolo collegato come ospite, anche con Plug & Charge, e annulla una pausa. La ricarica ospite termina allo scollegamento."],
    limits_hint:["Apply to your own car and to guests.","Gelten für das eigene Fahrzeug und für Gäste.","S’appliquent à votre véhicule et aux invités.","Gelden voor de eigen auto en voor gasten.","Valgono per il proprio veicolo e per gli ospiti."],
    guest_title:["Guest charging","Gastladen","Recharge invité","Gastladen","Ricarica ospite"],
    guest_ends:["Ends at","Endet bei","Se termine à","Eindigt bij","Termina a"],
    guest_open:["No target set: charges until the car is full or charging is paused.","Kein Ziel gesetzt: lädt, bis das Fahrzeug voll ist oder pausiert wird.","Aucun objectif : recharge jusqu’à ce que le véhicule soit plein ou mis en pause.","Geen doel ingesteld: laadt tot de auto vol is of wordt gepauzeerd.","Nessun obiettivo: ricarica finché il veicolo è pieno o viene messa in pausa."],
    guest_running:["Guest charging is active","Gastladen läuft","Recharge invité active","Gastladen actief","Ricarica ospite attiva"],
    guest_paused:["Guest charging is paused","Gastladen ist pausiert","Recharge invité en pause","Gastladen gepauzeerd","Ricarica ospite in pausa"],
    not_for_guest:["does not apply to the guest","gilt nicht für den Gast","ne s’applique pas à l’invité","geldt niet voor de gast","non vale per l’ospite"],
    guest:["Start guest charging", "Gastladen starten", "Démarrer la recharge invité", "Gastladen starten", "Avvia ricarica ospite"],
    departure_hint:["Own car: the departure plan charges to the vehicle charge target by this time; set it up in Home Assistant. Guest: the charge ends at this time.", "Eigenes Fahrzeug: Die Abfahrtsplanung lädt bis zu diesem Zeitpunkt auf das Fahrzeug-Ladeziel; Einrichtung in Home Assistant. Gast: Die Ladung endet zu diesem Zeitpunkt.", "Votre véhicule : la planification du départ charge jusqu’à l’objectif de charge du véhicule pour cette heure ; à configurer dans Home Assistant. Invité : la recharge se termine à cette heure.", "Eigen auto: de vertrekplanning laadt tegen dit tijdstip tot het laaddoel van het voertuig; instellen in Home Assistant. Gast: het laden eindigt op dit tijdstip.", "Proprio veicolo: la pianificazione della partenza carica fino all’obiettivo di carica del veicolo entro questo orario; da configurare in Home Assistant. Ospite: la ricarica termina a questo orario."],
    departure_label:["Target time", "Zielzeitpunkt", "Heure cible", "Doeltijd", "Orario obiettivo"],
    simple_hint:["Targets are optional and shared with Home Assistant. Energy, cost and vehicle charge target never start a charge; the departure plan of your own car may.", "Ziele sind optional und gelten gemeinsam mit Home Assistant. Ladeziel, Kostenziel und Fahrzeug-Ladeziel starten keine Ladung; die Abfahrtsplanung des eigenen Fahrzeugs kann es.", "Les objectifs sont facultatifs et partagés avec Home Assistant. Les objectifs énergétique, de coût et de charge du véhicule ne démarrent aucune recharge ; la planification du départ de votre véhicule peut le faire.", "Doelen zijn optioneel en worden gedeeld met Home Assistant. Energiedoel, kostendoel en laaddoel van het voertuig starten geen laadsessie; de vertrekplanning van de eigen auto kan dat wel.", "Gli obiettivi sono facoltativi e condivisi con Home Assistant. Obiettivo energetico, di costo e di carica del veicolo non avviano alcuna ricarica; la pianificazione della partenza del proprio veicolo può farlo."],
    vehicle_title:["Own car only", "Nur eigenes Fahrzeug", "Votre véhicule uniquement", "Alleen eigen auto", "Solo il proprio veicolo"],
    limits:["For every charge", "Für jede Ladung", "Pour chaque recharge", "Voor elke laadsessie", "Per ogni ricarica"],
    charging:["Charging", "Laden", "Recharge", "Laden", "Ricarica"],
    "plan_disabled":["Disabled", "Deaktiviert", "Désactivé", "Uitgeschakeld", "Disattivato"],
    plan_waiting_for_target:["Waiting for vehicle charge target", "Wartet auf Fahrzeug-Ladeziel", "En attente de l’objectif de charge du véhicule", "Wacht op laaddoel van voertuig", "In attesa dell’obiettivo di carica del veicolo"],
    "plan_waiting_for_capacity":["Waiting for battery capacity", "Wartet auf Akkukapazität", "En attente de la capacité", "Wacht op accucapaciteit", "In attesa capacità"],
    "plan_waiting_for_soc":["Waiting for vehicle charge level", "Wartet auf Fahrzeugladestand", "En attente du niveau de charge", "Wacht op laadniveau", "In attesa livello di carica"],
    "plan_waiting_for_device":["Waiting for wallbox", "Wartet auf Wallbox", "En attente de la borne", "Wacht op laadpaal", "In attesa wallbox"],
    "plan_vehicle_unconfirmed":["Car not confirmed", "Fahrzeug nicht bestätigt", "Véhicule non confirmé", "Voertuig niet bevestigd", "Veicolo non confermato"],
    "plan_read_only":["Read only", "Nur lesen", "Lecture seule", "Alleen lezen", "Sola lettura"],
    plan_guest_mode:["Ends the guest charge", "Beendet das Gastladen", "Met fin à la recharge invité", "Beëindigt het gastladen", "Termina la ricarica ospite"],
    "plan_queued":["Change queued", "Änderung vorgemerkt", "Modification en attente", "Wijziging in wachtrij", "Modifica in coda"],
    "plan_paused_until_start":["Paused until start", "Pausiert bis zum Start", "En pause jusqu’au démarrage", "Gepauzeerd tot start", "In pausa fino all’avvio"],
    "plan_minimum_level":["Charging to the minimum level", "Lädt bis zum Mindestladestand", "Recharge jusqu’au niveau minimal", "Laadt tot het minimumniveau", "Ricarica fino al livello minimo"],
    "plan_charging":["Charging at full current", "Lädt mit vollem Strom", "Charge au courant maximal", "Laadt met volle stroom", "Ricarica alla corrente massima"],
    "plan_finishing":["Finishing at minimum current", "Lädt mit Mindeststrom bis zur Abfahrt", "Termine au courant minimal", "Rondt af met minimale stroom", "Completa alla corrente minima"],
    "plan_unreachable":["Target may be unreachable", "Ziel eventuell nicht erreichbar", "Cible peut-être inaccessible", "Doel mogelijk onhaalbaar", "Obiettivo forse irraggiungibile"],
    "plan_late":["Deadline passed", "Zielzeit überschritten", "Heure cible dépassée", "Streeftijd verstreken", "Scadenza superata"],
    "plan_estimated_reached":["Charged to the estimated target", "Bis zum errechneten Ziel geladen", "Chargé jusqu’à l’objectif estimé", "Geladen tot het berekende doel", "Caricato fino all’obiettivo stimato"],
    "plan_reached":["Target reached", "Ziel erreicht", "Cible atteinte", "Doel bereikt", "Obiettivo raggiunto"],
    "plan_write_pending":["Write pending", "Schreibbefehl ausstehend", "Écriture en attente", "Schrijfopdracht wacht", "Scrittura in attesa"],
    "plan_error":["Error", "Fehler", "Erreur", "Fout", "Errore"],
    energy_label:["Energy target", "Ladeziel", "Objectif énergétique", "Energiedoel", "Obiettivo energetico"],
    energy_hint:["Stops the charge after this much energy from the wallbox, counted from saving. Saving it ends a cost target.", "Beendet die Ladung nach dieser Energiemenge ab Wallbox, gezählt ab dem Speichern. Speichern beendet ein Kostenziel.", "Arrête la recharge après cette quantité d’énergie à la borne, comptée dès l’enregistrement. L’enregistrer met fin à un objectif de coût.", "Stopt het laden na deze hoeveelheid energie vanaf de wallbox, geteld vanaf het opslaan. Opslaan beëindigt een kostendoel.", "Ferma la ricarica dopo questa energia erogata dalla wallbox, contata dal salvataggio. Salvarlo termina un obiettivo di costo."],
    cost_label:["Cost target", "Kostenziel", "Objectif de coût", "Kostendoel", "Obiettivo di costo"],
    cost_hint:["Stops the charge after this amount, counted from saving, at the price Home Assistant reports. It sets the energy target; an estimate, not for billing.", "Beendet die Ladung nach diesem Betrag, gezählt ab dem Speichern, zum Preis, den Home Assistant meldet. Es setzt das Ladeziel; eine Schätzung, nicht zur Abrechnung.", "Arrête la recharge après ce montant, compté dès l’enregistrement, au prix fourni par Home Assistant. Il fixe l’objectif énergétique ; une estimation, pas pour facturer.", "Stopt het laden na dit bedrag, geteld vanaf het opslaan, tegen de prijs die Home Assistant meldt. Het stelt het energiedoel in; een schatting, niet voor facturatie.", "Ferma la ricarica dopo questo importo, contato dal salvataggio, al prezzo comunicato da Home Assistant. Imposta l’obiettivo energetico; una stima, non per la fatturazione."],
    cost_used:["used","verbraucht","utilisé","verbruikt","usati"],
    from_cost:["from the cost target", "aus dem Kostenziel", "issu de l’objectif de coût", "uit het kostendoel", "dall’obiettivo di costo"],
    price_unknown:["No price yet: configure charging costs in Home Assistant.","Noch kein Preis: Ladekosten in Home Assistant einrichten.","Pas encore de prix : configurez les coûts de recharge dans Home Assistant.","Nog geen prijs: stel laadkosten in Home Assistant in.","Nessun prezzo: configurare i costi di ricarica in Home Assistant."],
    invalid_cost:["Enter 0 or 0.10–1000.","0 oder 0,10–1000 eingeben.","Saisissez 0 ou 0,10–1000.","Voer 0 of 0,10–1000 in.","Inserire 0 o 0,10–1000."],
    must_wait:["Not accepted now: a waiting stop or the wallbox write interval comes first. Try again later.","Gerade nicht angenommen: Ein wartender Stopp oder der Schreibabstand der Wallbox geht vor. Später erneut versuchen.","Non accepté pour l’instant : un arrêt en attente ou l’intervalle d’écriture passe d’abord. Réessayez plus tard.","Nu niet geaccepteerd: een wachtende stop of het schrijfinterval gaat voor. Probeer het later opnieuw.","Non accettato ora: prima un arresto in attesa o l’intervallo di scrittura. Riprovare più tardi."],
    planned_start:["Planned start","Geplanter Start","Démarrage prévu","Geplande start","Avvio pianificato"],
    plan_unsaved:["The departure plan could not be saved. Check the app log.","Der Abfahrtsplan konnte nicht gespeichert werden. App-Protokoll prüfen.","Le plan de départ n’a pas pu être enregistré. Vérifiez le journal.","Het vertrekplan kon niet worden opgeslagen. Controleer het logboek.","Impossibile salvare il piano di partenza. Controllare il registro."],
    price_since:["reported by Home Assistant","von Home Assistant gemeldet","signalé par Home Assistant","gemeld door Home Assistant","comunicato da Home Assistant"],
    unknown:["Unknown state","Unbekannter Zustand","État inconnu","Onbekende status","Stato sconosciuto"],
    soc_label:["Vehicle charge target", "Fahrzeug-Ladeziel", "Objectif de charge du véhicule", "Laaddoel van voertuig", "Obiettivo di carica del veicolo"],
    soc_hint:["Stops the charge at this battery level. Needs the vehicle profile in Home Assistant; at most the car's own charge limit. Never applies to a guest.", "Beendet die Ladung bei diesem Akkustand. Braucht das Fahrzeugprofil in Home Assistant; höchstens das Ladelimit des Fahrzeugs. Gilt nie für Gäste.", "Arrête la recharge à ce niveau de batterie. Nécessite le profil du véhicule dans Home Assistant ; au plus la limite de charge du véhicule. Ne s’applique jamais à un invité.", "Stopt het laden bij dit accuniveau. Vereist het voertuigprofiel in Home Assistant; hoogstens de laadlimiet van de auto. Geldt nooit voor gasten.", "Ferma la ricarica a questo livello di batteria. Richiede il profilo veicolo in Home Assistant; al massimo il limite di carica del veicolo. Non vale mai per gli ospiti."],
    shared:["Save each change; unsaved input is not used.", "Jede Änderung einzeln speichern; ungespeicherte Eingaben gelten nicht.", "Enregistrez chaque modification ; une saisie non enregistrée ne s’applique pas.", "Sla elke wijziging op; niet-opgeslagen invoer geldt niet.", "Salvare ogni modifica; i valori non salvati non valgono."],
    guest_stop:["Pause guest charging","Gastladen pausieren","Mettre la recharge invité en pause","Gastladen pauzeren","Metti in pausa la ricarica ospite"],
    save:["Save","Speichern","Enregistrer","Opslaan","Salva"],clear:["Clear", "Löschen", "Effacer", "Wissen", "Cancella"],reload:["Reload saved values","Gespeicherte Werte neu laden","Recharger les valeurs","Opgeslagen waarden laden","Ricarica valori salvati"],
    ready:["Ready","Bereit","Prêt","Gereed","Pronto"],saved:["Saved","Gespeichert","Enregistré","Opgeslagen","Salvato"],
    offline:["No current response. Reload before editing.","Keine aktuelle Antwort. Vor dem Ändern neu laden.","Aucune réponse actuelle. Rechargez avant de modifier.","Geen actueel antwoord. Herlaad vóór wijzigen.","Nessuna risposta attuale. Ricaricare prima di modificare."],
    changed:["Values changed elsewhere. Reload and check before saving.","Werte wurden andernorts geändert. Vor dem Speichern neu laden und prüfen.","Valeurs modifiées ailleurs. Rechargez et vérifiez avant d’enregistrer.","Waarden elders gewijzigd. Herlaad en controleer vóór opslaan.","Valori modificati altrove. Ricaricare e verificare prima di salvare."],
    uncertain:["Save not confirmed. Reload to check; do not assume it failed.","Speichern nicht bestätigt. Neu laden und prüfen; die Änderung kann bereits übernommen sein.","Enregistrement non confirmé. Rechargez pour vérifier ; la modification peut avoir été appliquée.","Opslaan niet bevestigd. Herlaad en controleer; de wijziging kan al zijn toegepast.","Salvataggio non confermato. Ricaricare e verificare; la modifica potrebbe essere stata applicata."],
    configure:["Configure the vehicle source/planning in Home Assistant first.","Zuerst Fahrzeugquelle bzw. Abfahrtsplanung in Home Assistant einrichten.","Configurez d’abord la source/planification du véhicule dans Home Assistant.","Configureer eerst voertuigbron/planning in Home Assistant.","Configurare prima fonte/pianificazione del veicolo in Home Assistant."],
    read_only:["Read-only mode: enable writes in the app configuration.","Nur lesen: Schreiben in der App-Konfiguration einschalten.","Lecture seule : activez les écritures dans la configuration.","Alleen lezen: schakel schrijven in bij de appconfiguratie.","Sola lettura: abilitare le scritture nella configurazione."],
    limit_missing:["The car's charge limit is missing. Wait for the vehicle integration.", "Ladelimit des Fahrzeugs fehlt. Auf die Fahrzeug-Integration warten.", "Limite de charge du véhicule manquante. Attendez l’intégration du véhicule.", "Laadlimiet van de auto ontbreekt. Wacht op de voertuigintegratie.", "Limite di carica del veicolo mancante. Attendere l’integrazione del veicolo."],
    not_permitted:["Guest charging cannot start: plug in the car, unlock the wallbox, clear any wallbox error and wait for a current wallbox status.","Gastladen startet nicht: Fahrzeug anstecken, Wallbox entsperren, Wallbox-Fehler beheben und auf einen aktuellen Wallbox-Status warten.","La recharge invité ne démarre pas : branchez le véhicule, déverrouillez la borne, corrigez toute erreur et attendez un état actuel de la borne.","Gastladen start niet: sluit het voertuig aan, ontgrendel de laadpaal, verhelp fouten en wacht op een actuele laadpaalstatus.","La ricarica ospite non parte: collegare il veicolo, sbloccare la wallbox, eliminare gli errori e attendere uno stato attuale della wallbox."],
    confirm_guest:["Guest charging was interrupted. Start it again; until then charging stays paused.","Gastladen wurde unterbrochen. Erneut starten; bis dahin bleibt das Laden pausiert.","La recharge invité a été interrompue. Redémarrez-la ; d’ici là, la recharge reste en pause.","Gastladen is onderbroken. Start opnieuw; tot dan blijft het laden gepauzeerd.","La ricarica ospite è stata interrotta. Riavviarla; fino ad allora la ricarica resta in pausa."],
    time_passed:["The saved target time has passed. Change or clear it first.","Der gespeicherte Zielzeitpunkt ist vorbei. Zuerst ändern oder löschen.","L’heure cible enregistrée est passée. Modifiez-la ou effacez-la d’abord.","De opgeslagen doeltijd is verstreken. Wijzig of wis deze eerst.","L’orario obiettivo salvato è passato. Modificarlo o cancellarlo prima."],
    limit_reached:["A target is already reached. Raise or clear it first.", "Ein Ziel ist bereits erreicht. Zuerst erhöhen oder löschen.", "Un objectif est déjà atteint. Augmentez-le ou effacez-le d’abord.", "Een doel is al bereikt. Verhoog of wis het eerst.", "Un obiettivo è già raggiunto. Aumentarlo o cancellarlo prima."],
    invalid:["Enter 0 or 0.1–1000 kWh.","0 oder 0,1–1000 kWh eingeben.","Saisissez 0 ou 0,1–1000 kWh.","Voer 0 of 0,1–1000 kWh in.","Inserire 0 o 0,1–1000 kWh."],
    invalid_soc:["Enter 0–100 %, at most the vehicle's charge limit.","0–100 % eingeben, höchstens das Ladelimit des Fahrzeugs.","Saisissez 0–100 %, au plus la limite de charge du véhicule.","Voer 0–100 % in, hoogstens de laadlimiet van het voertuig.","Inserire 0–100 %, al massimo il limite di carica del veicolo."],
    time_future:["Choose a time in the future.","Einen Zeitpunkt in der Zukunft wählen.","Choisissez une heure future.","Kies een tijdstip in de toekomst.","Scegliere un orario futuro."],
    invalid_time:["Enter a complete date and time.","Vollständiges Datum mit Uhrzeit eingeben.","Saisissez une date et une heure complètes.","Voer een volledige datum en tijd in.","Inserire data e ora complete."],
    meter_missing:["The energy target cannot be changed right now. Wait for a current wallbox reading and try again.", "Das Ladeziel kann gerade nicht geändert werden. Auf einen aktuellen Wallbox-Wert warten und erneut versuchen.", "L’objectif énergétique ne peut pas être modifié pour l’instant. Attendez une mesure actuelle de la borne et réessayez.", "Het energiedoel kan nu niet worden gewijzigd. Wacht op een actuele wallboxwaarde en probeer opnieuw.", "L’obiettivo energetico non può essere modificato ora. Attendere un valore attuale della wallbox e riprovare."],
    active:["Active","Aktiv","Actif","Actief","Attivo"],disabled:["Disabled","Deaktiviert","Désactivé","Uitgeschakeld","Disattivato"],reached:["Target reached","Ziel erreicht","Objectif atteint","Doel bereikt","Obiettivo raggiunto"],
    meter_unavailable:["Energy reading missing","Energiemessung fehlt","Mesure d’énergie absente","Energiemeting ontbreekt","Misura energia mancante"],meter_reset:["Energy meter reset","Energiezähler zurückgesetzt","Compteur réinitialisé","Energiemeter gereset","Contatore azzerato"],
  };
  const t=k=>words[k]?.[lang]||words.unknown[lang], el=id=>document.getElementById(id);
  const refusals={soc_goal_source_changed:"changed",soc_goal_limit_unavailable:"limit_missing",read_only_mode:"read_only",
    guest_start_not_permitted:"not_permitted",guest_deadline_expired:"time_passed",time_limit_reached:"time_passed",
    energy_goal_stop_active:"limit_reached",guest_confirmation_required:"confirm_guest",
    energy_goal_invalid:"invalid",soc_goal_invalid:"invalid_soc",energy_goal_unavailable:"meter_missing",
    cost_goal_invalid:"invalid_cost",cost_goal_price_unknown:"price_unknown",energy_goal_not_supported:"meter_missing",
    target_time_not_future:"time_future",write_rate_limited:"must_wait"};
  document.querySelectorAll("[data-text]").forEach(n=>{n.textContent=t(n.dataset.text);});
  document.documentElement.classList?.add("ready");
  const keys=["energy","cost","departure","soc"];
  const moment=v=>new Date(v).toLocaleString(langs[lang],{dateStyle:"short",timeStyle:"short"});
  let state=null,busy=false,timer=null,abort=null,generation=0,hidden=false,error="";
  const dirty=new Set(),revisions={},originalDates={};
  const live=()=>!hidden&&!document.hidden;
  function localDate(v){if(!v)return "";const d=new Date(v);if(!Number.isFinite(d.getTime()))return "";const z=n=>String(n).padStart(2,"0");return `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;}
  function revision(key){return key==="departure"?state?.departure?.revision:state?.revision;}
  function editable(key){return state&&!state.read_only&&(key==="energy"||(key==="cost"?state.cost_price!==null||state.cost!==null:key==="soc"?state.soc?.configured&&state.soc.maximum_required!==null:state.departure?.configured));}
  function chosen(key){return key==="cost"?state?.cost!==null:key==="departure"?!!state?.departure?.deadline:state?.[key]?.target>0;}
  // Home Assistant's currency comes with its price; without it, the bare number.
  const euro=(v,digits=2)=>v.toLocaleString(langs[lang],state?.cost_currency?{style:"currency",currency:state.cost_currency,maximumFractionDigits:digits}:{minimumFractionDigits:2,maximumFractionDigits:digits});
  function costText(){
    const c=state?.cost;if(c)return `${euro(c.amount)} · ${euro(c.spent)} ${t("cost_used")} · ${euro(c.price,4)}/kWh`;
    if(state?.cost_price===null)return t("price_unknown");
    const at=state?.cost_price_at?new Date(state.cost_price_at):null;
    return `${t("disabled")} · ${euro(state.cost_price,4)}/kWh`+(at&&Number.isFinite(at.getTime())?` (${t("price_since")} ${moment(at)})`:"");
  }
  function render(){
    el("notice").textContent=error||(!state?t("offline"):state.read_only?t("read_only"):state.guest_confirmation_required?t("confirm_guest"):t("ready"));
    for(const key of keys){
      const info=state?.[key], input=el(key+"_value");
      input.disabled=busy||!editable(key);el(key+"_save").disabled=input.disabled;
      el(key+"_clear").disabled=input.disabled||!chosen(key);
      el(key+"_status").textContent=key==="cost"?(state?costText():"—"):!info?"—":key==="departure"?(info.deadline?`${moment(info.deadline)} · ${t("plan_"+info.status)}`:t("disabled")):!editable(key)?t(state?.read_only?"plan_read_only":"configure"):`${info.target} ${key==="energy"?"kWh":"%"} · ${key==="soc"&&state.guest_mode&&info.target>0?t("not_for_guest"):t(info.status)}${key==="energy"&&state.cost?` · ${t("from_cost")}`:""}`;
      el(key+"_status").className="charging-state "+(key==="cost"?(state?.cost?"state-active":"state-muted"):!editable(key)||(key==="soc"&&state.guest_mode)?"state-muted":["reached","estimated_reached"].includes(info?.status)?"state-good":["active","charging","minimum_level","finishing"].includes(info?.status)?"state-active":["error","meter_reset"].includes(info?.status)?"state-error":info?.status==="disabled"?"state-muted":"state-wait");
      if(key==="soc"&&info){input.max=String(info.maximum??100);if(info.maximum_required&&info.maximum===null)el(key+"_status").textContent=t("limit_missing");}
      if(key==="departure"){
        // Only a plan that waits for its start has one; a guest or an error holds it.
        const start=info?.status==="paused_until_start"&&info.planned_start?new Date(info.planned_start):null;
        el("departure_summary").textContent=info?.storage_error?t("plan_unsaved"):start&&Number.isFinite(start.getTime())?`${t("planned_start")}: ${moment(start)}`:"";
      }
    }
    // What ends a guest charge: the saved targets of every charge, by name.
    const ends=!state?[]:[state.cost?`${euro(state.cost.amount)} (${state.energy.target} kWh)`:state.energy.target>0?`${state.energy.target} kWh`:"",
      state.departure?.deadline?moment(state.departure.deadline):""].filter(Boolean);
    el("cost_unit").textContent=state?.cost_currency||"";
    el("guest_status").textContent=!state?.guest_mode?"":t(state.guest_active?"guest_running":"guest_paused");
    el("guest_status").className=!state?.guest_mode?"":"charging-state "+(state.guest_active?"state-active":"state-wait");
    el("guest_summary").textContent=!state?"":ends.length?`${t("guest_ends")}: ${ends.join(" · ")}`:t("guest_open");
    el("guest_start").textContent=t(state?.guest_active?"guest_stop":"guest");
    el("guest_start").disabled=busy||!state||state.read_only||!state.guest_revision||(!state.guest_active&&dirty.size>0);
    el("reload").disabled=busy;
  }
  function validate(data){
    const num=(v,max)=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=max;
    if(!data||data.schema!==1||typeof data.csrf!=="string"||typeof data.revision!=="string"||typeof data.read_only!=="boolean"
        ||typeof data.guest_active!=="boolean"||typeof data.guest_mode!=="boolean"
        ||!(data.guest_revision===null||typeof data.guest_revision==="string")
        ||!data.energy||!num(data.energy.target,1000)||typeof data.energy.status!=="string"
        ||typeof data.guest_confirmation_required!=="boolean"
        ||!(data.cost_price===null||typeof data.cost_price==="number"&&Number.isFinite(data.cost_price))
        ||!(data.cost_price_at===null||typeof data.cost_price_at==="string")
        ||!(data.cost_currency===null||typeof data.cost_currency==="string"&&/^[A-Z]{3}$/.test(data.cost_currency))
        ||(data.cost!==null&&(!data.cost||!num(data.cost.amount,1000)||![data.cost.spent,data.cost.price].every(Number.isFinite)))
        ||!data.soc||typeof data.soc.configured!=="boolean"||!num(data.soc.target,100)
        ||!(data.soc.maximum===null||num(data.soc.maximum,100))
        ||!(data.soc.maximum_required===null||typeof data.soc.maximum_required==="boolean")
        ||typeof data.soc.status!=="string"
        ||(data.departure!==null&&(!data.departure||typeof data.departure.configured!=="boolean"
          ||typeof data.departure.revision!=="string"||typeof data.departure.status!=="string"
          ||!(data.departure.planned_start===null||typeof data.departure.planned_start==="string")
          ||typeof data.departure.storage_error!=="boolean"
          ||!(data.departure.deadline===null||typeof data.departure.deadline==="string"&&localDate(data.departure.deadline)))))throw new Error("invalid charging state");
  }
  function apply(data){
    validate(data);
    state=data;
    for(const key of keys){
      if(dirty.has(key)){if(revisions[key]!==revision(key))error=t("changed");continue;}
      revisions[key]=revision(key);
      if(key==="departure"){originalDates[key]=state.departure?.deadline??null;el(key+"_value").value=localDate(originalDates[key]);}
      else el(key+"_value").value=String(key==="cost"?state.cost?.amount??0:state[key]?.target??0);
    }
    render();
  }
  function suspend(){generation++;clearTimeout(timer);timer=null;abort?.abort();abort=null;}
  function schedule(){clearTimeout(timer);timer=null;if(live()&&!busy&&!abort)timer=setTimeout(read,5000);}
  async function read(){
    if(!live()||busy||abort)return;
    const own=++generation, controller=new AbortController();abort=controller;const timeout=setTimeout(()=>controller.abort(),8000);
    try{const r=await fetch("./api/charging",{cache:"no-store",signal:controller.signal});const data=await r.json();if(!r.ok||data.schema!==1||typeof data.csrf!=="string"||typeof data.revision!=="string")throw new Error();if(own!==generation||!live())return;if(error===t("offline"))error="";apply(data);}
    catch(_){if(own===generation&&live()){state=null;error=t("offline");render();}}
    finally{clearTimeout(timeout);if(own===generation){abort=null;schedule();}}
  }
  async function save(key,clear=false){
    if(busy||(key==="guest"?(!state||state.read_only||(!state.guest_active&&dirty.size>0)||!state.guest_revision):(!editable(key)||(!clear&&!el(key+"_form").reportValidity()))))return;
    let value=key==="guest"?true:clear?(key==="departure"?null:0):el(key+"_value").value;
    if(key==="departure"&&!clear){const original=originalDates[key];if(original&&localDate(original)===value)value=original;
      else {const date=new Date(value);if(!value||!Number.isFinite(date.getTime())||localDate(date.toISOString())!==value){error=t("invalid_time");render();return;}value=date.toISOString();}}
    else if(key!=="departure"&&key!=="guest"&&!clear)value=Number(value);
    const action=key==="guest"&&state.guest_active?"pause":key;
    const csrf=state.csrf,rev=action==="pause"?state.revision:key==="guest"?state.guest_revision:revisions[key];busy=true;suspend();error="";render();
    const own=generation,controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
    try{const r=await fetch("./api/charging",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json","X-Amtron-CSRF":csrf},body:JSON.stringify({action,value,revision:rev,confirm:true}),signal:controller.signal});const data=await r.json();if(own!==generation||!live())return;if(!r.ok){error=t(Object.prototype.hasOwnProperty.call(refusals,data?.error)?refusals[data.error]:"uncertain");return;}validate(data);dirty.delete(key);if(key!=="departure"&&key!=="guest")for(const other of keys)if(other!=="departure"&&revisions[other]===rev)revisions[other]=data.revision;error=t(action==="guest"?"guest_started":"saved");apply(data);}
    catch(_){if(own===generation&&live())error=t("uncertain");}
    finally{clearTimeout(timeout);busy=false;render();schedule();}
  }
  for(const key of keys){el(key+"_form").addEventListener("input",()=>{dirty.add(key);error="";render();});el(key+"_form").addEventListener("submit",e=>{e.preventDefault();void save(key);});}
  for(const key of keys)el(key+"_clear").addEventListener("click",()=>{void save(key,true);});
  el("guest_start").addEventListener("click",()=>{void save("guest");});
  el("reload").addEventListener("click",()=>{if(busy)return;dirty.clear();error="";suspend();void read();});
  document.addEventListener("visibilitychange",()=>{suspend();state=null;render();if(live())void read();});
  addEventListener("pagehide",()=>{hidden=true;suspend();state=null;});addEventListener("pageshow",()=>{hidden=false;void read();});
  render();void read();
})();
