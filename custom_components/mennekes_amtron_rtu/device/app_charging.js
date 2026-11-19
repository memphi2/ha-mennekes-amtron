/* Explicit edits of existing persistent targets. No storage, credentials or POST retry. */
(() => {
  "use strict";
  const langs=["en","de","fr","nl","it"], index=langs.indexOf(document.documentElement.lang.slice(0,2)), lang=index<0?0:index;
  const words={
    guest_started:["Guest start requested; check the overview for confirmation.", "Gaststart angefordert; die Übersicht zeigt die Bestätigung.", "Démarrage invité demandé ; vérifiez la confirmation dans la vue d’ensemble.", "Gaststart aangevraagd; controleer de bevestiging in het overzicht.", "Avvio ospite richiesto; verifica la conferma nella panoramica."],
    overview:["View live overview", "Live-Übersicht öffnen", "Ouvrir la vue en direct", "Liveoverzicht openen", "Apri panoramica live"],
    guest_limits:["Uses the saved kWh limit and time, whichever is reached first. Unset limits do not apply. The vehicle percentage target stays saved but does not apply to the guest.", "Verwendet gespeichertes kWh-Ziel und Zeitpunkt: Was zuerst erreicht ist, stoppt die Ladung. Ungesetzte Grenzen entfallen. Das Fahrzeug-Prozentziel bleibt gespeichert, gilt aber nicht für den Gast.", "Utilise la limite kWh et l’heure enregistrées ; la première atteinte arrête la recharge. Une limite non définie ne s’applique pas. L’objectif du véhicule reste enregistré mais ne s’applique pas à l’invité.", "Gebruikt de opgeslagen kWh-grens en tijd: wat het eerst wordt bereikt stopt het laden. Niet-ingestelde grenzen gelden niet. Het voertuigdoel blijft opgeslagen maar geldt niet voor de gast.", "Usa limite kWh e orario salvati: il primo raggiunto ferma la ricarica. I limiti non impostati non si applicano. L’obiettivo del veicolo resta salvato ma non vale per l’ospite."],
    guest_hint:["Start the connected guest now, including with Plug & Charge. This lifts a deliberate pause.", "Das angeschlossene Gastfahrzeug jetzt laden, auch mit Plug & Charge. Eine bewusste Pause wird aufgehoben.", "Rechargez maintenant le véhicule invité connecté, même avec Plug & Charge. Une pause volontaire est levée.", "Laad de aangesloten gast nu, ook met Plug & Charge. Een bewuste pauze wordt opgeheven.", "Ricarica ora il veicolo ospite collegato, anche con Plug & Charge. Una pausa volontaria viene annullata."],
    guest:["Start guest charging", "Gastladen starten", "Démarrer la recharge invité", "Gastladen starten", "Avvia ricarica ospite"],
    time_hint:["Recognized vehicle: intelligent planning in HA. Guest: stop by this time.", "Erkanntes Fahrzeug: intelligente Planung in HA. Gast: spätestens zu diesem Zeitpunkt stoppen.", "Véhicule reconnu : planification intelligente dans HA. Invité : arrêt à cette heure.", "Herkend voertuig: slimme planning in HA. Gast: uiterlijk op dit tijdstip stoppen.", "Veicolo riconosciuto: pianificazione intelligente in HA. Ospite: fermarsi entro questo orario."],
    time_label:["Target time (optional)", "Zielzeitpunkt (optional)", "Heure cible (facultative)", "Doeltijd (optioneel)", "Orario obiettivo (opzionale)"],
    simple_hint:["Set only the limits you need. Values are shared with Home Assistant.", "Nur die benötigten Grenzen setzen. Die Werte gelten gemeinsam mit Home Assistant.", "Définissez uniquement les limites nécessaires. Les valeurs sont partagées avec Home Assistant.", "Stel alleen de benodigde grenzen in. Waarden worden gedeeld met Home Assistant.", "Imposta solo i limiti necessari. I valori sono condivisi con Home Assistant."],
    current_title:["Current charging", "Aktuelle Ladung", "Recharge actuelle", "Huidige laadsessie", "Ricarica attuale"],
    limits:["Charging limits", "Ladegrenzen", "Limites de recharge", "Laadgrenzen", "Limiti di ricarica"],
    charging:["Charging", "Laden", "Recharge", "Laden", "Ricarica"],
    "plan_disabled":["Disabled", "Deaktiviert", "Désactivé", "Uitgeschakeld", "Disattivato"],
    "plan_waiting_for_target":["Waiting for percentage target", "Wartet auf Prozentziel", "En attente de la cible en %", "Wacht op procentdoel", "In attesa obiettivo %"],
    "plan_waiting_for_capacity":["Waiting for battery capacity", "Wartet auf Akkukapazität", "En attente de la capacité", "Wacht op accucapaciteit", "In attesa capacità"],
    "plan_waiting_for_soc":["Waiting for vehicle charge level", "Wartet auf Fahrzeugladestand", "En attente du niveau de charge", "Wacht op laadniveau", "In attesa livello di carica"],
    "plan_waiting_for_device":["Waiting for wallbox", "Wartet auf Wallbox", "En attente de la borne", "Wacht op laadpaal", "In attesa wallbox"],
    "plan_vehicle_unconfirmed":["Car not confirmed", "Fahrzeug nicht bestätigt", "Véhicule non confirmé", "Voertuig niet bevestigd", "Veicolo non confermato"],
    "plan_read_only":["Read only", "Nur lesen", "Lecture seule", "Alleen lezen", "Sola lettura"],
    "plan_guest_mode":["Guest charging has priority", "Gastladen hat Vorrang", "La recharge invité est prioritaire", "Gastladen heeft voorrang", "La ricarica ospite ha priorità"],
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
    targets:["Charge target","Ladeziel","Objectif de charge","Laaddoel","Obiettivo di ricarica"],
    targets_hint:["Limits only: setting a target does not start or resume charging. If both limits are active, the first one reached stops charging.","Nur begrenzen: Ein Ziel startet keine Ladung und hebt keine Pause auf. Sind beide Grenzen aktiv, stoppt die zuerst erreichte Grenze.","Limites uniquement : définir un objectif ne démarre ni ne reprend la charge. Si les deux limites sont actives, la première atteinte arrête la charge.","Alleen begrenzen: een doel start of hervat het laden niet. Bij twee actieve grenzen stopt de eerst bereikte grens het laden.","Solo limiti: impostare un obiettivo non avvia né riprende la ricarica. Con entrambi attivi, il primo raggiunto ferma la ricarica."],
    energy:["Additional energy","Zusätzliche Energie","Énergie supplémentaire","Extra energie","Energia aggiuntiva"],
    energy_label:["Energy ceiling","Energiegrenze","Limite d’énergie","Energiegrens","Limite energia"],
    energy_hint:["Wallbox energy, not battery energy. Each save measures the chosen amount from now, even at the same value, and ends a cost ceiling. 0 disables the limit without resuming a pause.", "Wallboxenergie, keine Akkuenergie. Jedes Speichern zählt die gewählte Menge ab jetzt, auch beim gleichen Wert, und beendet eine Kostengrenze. 0 deaktiviert die Grenze; eine Pause bleibt bestehen.", "Énergie à la borne, pas dans la batterie. Chaque enregistrement compte la quantité dès maintenant, même avec la même valeur, et met fin à une limite de coût. 0 désactive la limite sans reprendre une pause.", "Wallboxenergie, geen accu-energie. Elke opslag telt de gekozen hoeveelheid vanaf nu, ook bij dezelfde waarde, en beëindigt een kostengrens. 0 schakelt de grens uit zonder een pauze te beëindigen.", "Energia alla wallbox, non nella batteria. Ogni salvataggio conta la quantità da ora, anche con lo stesso valore, e termina un limite di costo. 0 disattiva il limite senza riprendere una pausa."],
    cost:["Cost ceiling","Kostengrenze","Limite de coût","Kostengrens","Limite di costo"],
    cost_label:["Cost ceiling (EUR)","Kostengrenze (EUR)","Limite de coût (EUR)","Kostengrens (EUR)","Limite di costo (EUR)"],
    cost_hint:["Charges for this amount from now at the price Home Assistant reports; a new price re-aims the energy target. An estimate, not for billing. 0 ends it together with its energy target.","Lädt ab jetzt für diesen Betrag zum Preis, den Home Assistant meldet; ein neuer Preis passt das Energieziel an. Eine Schätzung, nicht zur Abrechnung. 0 beendet sie mitsamt Energieziel.","Recharge dès maintenant pour ce montant au prix fourni par Home Assistant ; un nouveau prix ajuste l’objectif énergétique. Une estimation, pas pour facturer. 0 y met fin avec son objectif énergétique.","Laadt vanaf nu voor dit bedrag tegen de prijs die Home Assistant meldt; een nieuwe prijs past het energiedoel aan. Een schatting, niet voor facturatie. 0 beëindigt het samen met het energiedoel.","Ricarica da ora per questo importo al prezzo comunicato da Home Assistant; un nuovo prezzo adegua l’obiettivo energetico. Una stima, non per la fatturazione. 0 lo termina insieme all’obiettivo energetico."],
    cost_used:["used","verbraucht","utilisé","verbruikt","usati"],
    from_cost:["from the cost ceiling","aus der Kostengrenze","issu de la limite de coût","uit de kostengrens","dal limite di costo"],
    price_unknown:["No price yet: configure charging costs in Home Assistant.","Noch kein Preis: Ladekosten in Home Assistant einrichten.","Pas encore de prix : configurez les coûts de recharge dans Home Assistant.","Nog geen prijs: stel laadkosten in Home Assistant in.","Nessun prezzo: configurare i costi di ricarica in Home Assistant."],
    invalid_cost:["Enter 0 or 0.10–1000 EUR.","0 oder 0,10–1000 EUR eingeben.","Saisissez 0 ou 0,10–1000 EUR.","Voer 0 of 0,10–1000 EUR in.","Inserire 0 o 0,10–1000 EUR."],
    must_wait:["Not accepted now: a waiting stop or the wallbox write interval comes first. Try again later.","Gerade nicht angenommen: Ein wartender Stopp oder der Schreibabstand der Wallbox geht vor. Später erneut versuchen.","Non accepté pour l’instant : un arrêt en attente ou l’intervalle d’écriture passe d’abord. Réessayez plus tard.","Nu niet geaccepteerd: een wachtende stop of het schrijfinterval gaat voor. Probeer het later opnieuw.","Non accettato ora: prima un arresto in attesa o l’intervallo di scrittura. Riprovare più tardi."],
    planned_start:["Planned start","Geplanter Start","Démarrage prévu","Geplande start","Avvio pianificato"],
    plan_unsaved:["The departure plan could not be saved. Check the app log.","Der Abfahrtsplan konnte nicht gespeichert werden. App-Protokoll prüfen.","Le plan de départ n’a pas pu être enregistré. Vérifiez le journal.","Het vertrekplan kon niet worden opgeslagen. Controleer het logboek.","Impossibile salvare il piano di partenza. Controllare il registro."],
    price_since:["reported by Home Assistant","von Home Assistant gemeldet","signalé par Home Assistant","gemeld door Home Assistant","comunicato da Home Assistant"],
    unknown:["Unknown state","Unbekannter Zustand","État inconnu","Onbekende status","Stato sconosciuto"],
    soc:["Vehicle charge level","Fahrzeug-Ladestand","Niveau de batterie","Accuniveau","Livello batteria"],
    soc_label:["Target charge level","Ziel-Ladestand","Niveau cible","Doelniveau","Livello obiettivo"],
    soc_hint:["Uses the vehicle profile configured in Home Assistant and its optional maximum. 0 disables only this percentage target.","Nutzt das in Home Assistant eingerichtete Fahrzeugprofil und dessen optionale Obergrenze. 0 deaktiviert nur das Prozentziel.","Utilise le profil du véhicule dans Home Assistant et son maximum facultatif. 0 désactive uniquement cet objectif en pourcentage.","Gebruikt het voertuigprofiel in Home Assistant en het optionele maximum. 0 schakelt alleen dit procentdoel uit.","Usa il profilo veicolo in Home Assistant e il massimo opzionale. 0 disattiva solo questo obiettivo percentuale."],
    departure:["Plan departure","Abfahrt planen","Planifier le départ","Vertrek plannen","Pianifica partenza"],
    departure_hint:["One next trip: set a percentage target first. The plan uses vehicle charge level and usable capacity to choose start and current.","Eine nächste Fahrt: zuerst ein Prozentziel setzen. Der Plan bestimmt Start und Strom aus Fahrzeug-Ladestand und nutzbarer Akkukapazität.","Un seul prochain trajet : définissez d’abord un objectif en pourcentage. Le plan utilise le niveau et la capacité utile pour choisir début et courant.","Eén volgende rit: stel eerst een procentdoel in. Het plan bepaalt start en stroom uit accuniveau en bruikbare capaciteit.","Un solo prossimo viaggio: impostare prima un obiettivo percentuale. Il piano usa livello batteria e capacità utile per scegliere inizio e corrente."],
    shared:["Saved values are shared with Home Assistant. Save changes explicitly; starting a guest charge is a separate action.","Gespeicherte Werte sind gemeinsam mit Home Assistant. Änderungen ausdrücklich speichern; Gastladen wird separat gestartet.","Valeurs enregistrées partagées avec Home Assistant. Enregistrez explicitement ; la recharge invité démarre séparément.","Opgeslagen waarden worden gedeeld met Home Assistant. Wijzigingen expliciet opslaan; gastladen start apart.","Valori salvati condivisi con Home Assistant. Salvare esplicitamente; la ricarica ospite si avvia separatamente."],
    guest_stop:["Pause guest charging","Gastladen pausieren","Mettre la recharge invité en pause","Gastladen pauzeren","Metti in pausa la ricarica ospite"],
    save:["Save","Speichern","Enregistrer","Opslaan","Salva"],clear:["Clear time","Zeitpunkt löschen","Effacer l’heure","Tijd wissen","Cancella orario"],reload:["Reload saved values","Gespeicherte Werte neu laden","Recharger les valeurs","Opgeslagen waarden laden","Ricarica valori salvati"],
    ready:["Ready","Bereit","Prêt","Gereed","Pronto"],saved:["Saved","Gespeichert","Enregistré","Opgeslagen","Salvato"],
    offline:["No current response. Reload before editing.","Keine aktuelle Antwort. Vor dem Ändern neu laden.","Aucune réponse actuelle. Rechargez avant de modifier.","Geen actueel antwoord. Herlaad vóór wijzigen.","Nessuna risposta attuale. Ricaricare prima di modificare."],
    changed:["Values changed elsewhere. Reload and check before saving.","Werte wurden andernorts geändert. Vor dem Speichern neu laden und prüfen.","Valeurs modifiées ailleurs. Rechargez et vérifiez avant d’enregistrer.","Waarden elders gewijzigd. Herlaad en controleer vóór opslaan.","Valori modificati altrove. Ricaricare e verificare prima di salvare."],
    uncertain:["Save not confirmed. Reload to check; do not assume it failed.","Speichern nicht bestätigt. Neu laden und prüfen; die Änderung kann bereits übernommen sein.","Enregistrement non confirmé. Rechargez pour vérifier ; la modification peut avoir été appliquée.","Opslaan niet bevestigd. Herlaad en controleer; de wijziging kan al zijn toegepast.","Salvataggio non confermato. Ricaricare e verificare; la modifica potrebbe essere stata applicata."],
    configure:["Configure the vehicle source/planning in Home Assistant first.","Zuerst Fahrzeugquelle bzw. Abfahrtsplanung in Home Assistant einrichten.","Configurez d’abord la source/planification du véhicule dans Home Assistant.","Configureer eerst voertuigbron/planning in Home Assistant.","Configurare prima fonte/pianificazione del veicolo in Home Assistant."],
    read_only:["Read-only mode: enable writes in the app configuration.","Nur lesen: Schreiben in der App-Konfiguration einschalten.","Lecture seule : activez les écritures dans la configuration.","Alleen lezen: schakel schrijven in bij de appconfiguratie.","Sola lettura: abilitare le scritture nella configurazione."],
    limit_missing:["Vehicle maximum unavailable. Wait for the vehicle integration.","Fahrzeug-Obergrenze fehlt. Auf die Fahrzeug-Integration warten.","Maximum du véhicule indisponible. Attendez l’intégration du véhicule.","Voertuigmaximum ontbreekt. Wacht op de voertuigintegratie.","Massimo del veicolo non disponibile. Attendere l’integrazione veicolo."],
    not_permitted:["Guest charging cannot start: plug in the car, unlock the wallbox, clear any wallbox error and wait for a current wallbox status.","Gastladen startet nicht: Fahrzeug anstecken, Wallbox entsperren, Wallbox-Fehler beheben und auf einen aktuellen Wallbox-Status warten.","La recharge invité ne démarre pas : branchez le véhicule, déverrouillez la borne, corrigez toute erreur et attendez un état actuel de la borne.","Gastladen start niet: sluit het voertuig aan, ontgrendel de laadpaal, verhelp fouten en wacht op een actuele laadpaalstatus.","La ricarica ospite non parte: collegare il veicolo, sbloccare la wallbox, eliminare gli errori e attendere uno stato attuale della wallbox."],
    confirm_guest:["Guest charging was interrupted. Start it again; until then charging stays paused.","Gastladen wurde unterbrochen. Erneut starten; bis dahin bleibt das Laden pausiert.","La recharge invité a été interrompue. Redémarrez-la ; d’ici là, la recharge reste en pause.","Gastladen is onderbroken. Start opnieuw; tot dan blijft het laden gepauzeerd.","La ricarica ospite è stata interrotta. Riavviarla; fino ad allora la ricarica resta in pausa."],
    time_passed:["The saved target time has passed. Change or clear it first.","Der gespeicherte Zielzeitpunkt ist vorbei. Zuerst ändern oder löschen.","L’heure cible enregistrée est passée. Modifiez-la ou effacez-la d’abord.","De opgeslagen doeltijd is verstreken. Wijzig of wis deze eerst.","L’orario obiettivo salvato è passato. Modificarlo o cancellarlo prima."],
    limit_reached:["A charging limit is already reached. Raise it or set it to 0 first.","Eine Ladegrenze ist bereits erreicht. Zuerst erhöhen oder mit 0 deaktivieren.","Une limite de recharge est déjà atteinte. Augmentez-la ou mettez-la à 0 d’abord.","Een laadgrens is al bereikt. Verhoog deze of zet deze eerst op 0.","Un limite di ricarica è già raggiunto. Aumentarlo o impostarlo a 0 prima."],
    invalid:["Enter 0 or 0.1–1000 kWh.","0 oder 0,1–1000 kWh eingeben.","Saisissez 0 ou 0,1–1000 kWh.","Voer 0 of 0,1–1000 kWh in.","Inserire 0 o 0,1–1000 kWh."],
    invalid_soc:["Enter 0–100 %, at most the vehicle's charge limit.","0–100 % eingeben, höchstens das Ladelimit des Fahrzeugs.","Saisissez 0–100 %, au plus la limite de charge du véhicule.","Voer 0–100 % in, hoogstens de laadlimiet van het voertuig.","Inserire 0–100 %, al massimo il limite di carica del veicolo."],
    invalid_time:["Enter a complete date and time.","Vollständiges Datum mit Uhrzeit eingeben.","Saisissez une date et une heure complètes.","Voer een volledige datum en tijd in.","Inserire data e ora complete."],
    meter_missing:["The energy limit cannot be changed right now. Wait for a current wallbox reading and try again.","Die Energiegrenze kann gerade nicht geändert werden. Auf einen aktuellen Wallbox-Wert warten und erneut versuchen.","La limite d’énergie ne peut pas être modifiée maintenant. Attendez une mesure actuelle de la borne et réessayez.","De energiegrens kan nu niet worden gewijzigd. Wacht op een actuele laadpaalwaarde en probeer opnieuw.","Il limite di energia non può essere modificato ora. Attendere un valore attuale della wallbox e riprovare."],
    active:["Active","Aktiv","Actif","Actief","Attivo"],disabled:["Disabled","Deaktiviert","Désactivé","Uitgeschakeld","Disattivato"],reached:["Target reached","Ziel erreicht","Objectif atteint","Doel bereikt","Obiettivo raggiunto"],
    meter_unavailable:["Energy reading missing","Energiemessung fehlt","Mesure d’énergie absente","Energiemeting ontbreekt","Misura energia mancante"],meter_reset:["Energy meter reset","Energiezähler zurückgesetzt","Compteur réinitialisé","Energiemeter gereset","Contatore azzerato"],
  };
  const t=k=>words[k]?.[lang]||words.unknown[lang], el=id=>document.getElementById(id);
  const refusals={soc_goal_source_changed:"changed",soc_goal_limit_unavailable:"limit_missing",read_only_mode:"read_only",
    guest_start_not_permitted:"not_permitted",guest_deadline_expired:"time_passed",time_limit_reached:"time_passed",
    energy_goal_stop_active:"limit_reached",guest_confirmation_required:"confirm_guest",
    energy_goal_invalid:"invalid",soc_goal_invalid:"invalid_soc",energy_goal_unavailable:"meter_missing",
    cost_goal_invalid:"invalid_cost",cost_goal_price_unknown:"price_unknown",energy_goal_not_supported:"meter_missing",
    target_time_not_future:"time_passed",write_rate_limited:"must_wait"};
  document.querySelectorAll("[data-text]").forEach(n=>{n.textContent=t(n.dataset.text);});
  document.documentElement.classList?.add("ready");
  const keys=["energy","cost","departure","soc"];
  let state=null,busy=false,timer=null,abort=null,generation=0,hidden=false,error="";
  const dirty=new Set(),revisions={},originalDates={};
  const live=()=>!hidden&&!document.hidden;
  function localDate(v){if(!v)return "";const d=new Date(v);if(!Number.isFinite(d.getTime()))return "";const z=n=>String(n).padStart(2,"0");return `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;}
  function revision(key){return key==="departure"?state?.departure?.revision:state?.revision;}
  function editable(key){return state&&!state.read_only&&(key==="energy"||(key==="cost"?state.cost_price!==null||state.cost!==null:key==="soc"?state.soc?.configured&&state.soc.maximum_required!==null:state.departure?.configured));}
  const euro=(v,digits=2)=>v.toLocaleString(langs[lang],{style:"currency",currency:"EUR",maximumFractionDigits:digits});
  function costText(){
    const c=state?.cost;if(c)return `${euro(c.amount)} · ${euro(c.spent)} ${t("cost_used")} · ${euro(c.price,4)}/kWh`;
    if(state?.cost_price===null)return t("price_unknown");
    const at=state?.cost_price_at?new Date(state.cost_price_at):null;
    return `${t("disabled")} · ${euro(state.cost_price,4)}/kWh`+(at&&Number.isFinite(at.getTime())?` (${t("price_since")} ${at.toLocaleString(langs[lang],{dateStyle:"short",timeStyle:"short"})})`:"");
  }
  function render(){
    el("notice").textContent=error||(!state?t("offline"):state.read_only?t("read_only"):state.guest_confirmation_required?t("confirm_guest"):t("ready"));
    for(const key of keys){
      const info=state?.[key], input=el(key+"_value");
      input.disabled=busy||!editable(key);el(key+"_save").disabled=input.disabled;
      el(key+"_status").textContent=key==="cost"?(state?costText():"—"):!info?"—":key==="departure"?(info.deadline?`${new Date(info.deadline).toLocaleString(langs[lang])} · ${t("plan_"+info.status)}`:t("disabled")):!editable(key)?t(state?.read_only?"plan_read_only":"configure"):`${info.target} ${key==="energy"?"kWh":"%"} · ${t(info.status)}${key==="energy"&&state.cost?` · ${t("from_cost")}`:""}`;
      el(key+"_status").className="charging-state "+(key==="cost"?(state?.cost?"state-active":"state-muted"):!editable(key)?"state-muted":["reached","estimated_reached"].includes(info?.status)?"state-good":["active","charging","minimum_level","finishing"].includes(info?.status)?"state-active":["error","meter_reset"].includes(info?.status)?"state-error":info?.status==="disabled"?"state-muted":"state-wait");
      if(key==="soc"&&info){input.max=String(info.maximum??100);if(info.maximum_required&&info.maximum===null)el(key+"_status").textContent=t("limit_missing");}
      if(key==="departure"){
        el("departure_clear").disabled=busy||!editable(key);
        const start=info?.planned_start?new Date(info.planned_start):null;
        el("departure_summary").textContent=info?.storage_error?t("plan_unsaved"):start&&Number.isFinite(start.getTime())?`${t("planned_start")}: ${start.toLocaleString(langs[lang])}`:"";
      }
    }
    el("guest_start").textContent=t(state?.guest_active?"guest_stop":"guest");
    el("guest_start").disabled=busy||!state||state.read_only||!state.guest_revision||(!state.guest_active&&dirty.size>0);
    el("reload").disabled=busy;
  }
  function validate(data){
    const num=(v,max)=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=max;
    if(!data||data.schema!==1||typeof data.csrf!=="string"||typeof data.revision!=="string"||typeof data.read_only!=="boolean"
        ||typeof data.guest_active!=="boolean"
        ||!(data.guest_revision===null||typeof data.guest_revision==="string")
        ||!data.energy||!num(data.energy.target,1000)||typeof data.energy.status!=="string"
        ||typeof data.guest_confirmation_required!=="boolean"
        ||!(data.cost_price===null||typeof data.cost_price==="number"&&Number.isFinite(data.cost_price))
        ||!(data.cost_price_at===null||typeof data.cost_price_at==="string")
        ||(data.cost!==null&&(!data.cost||!num(data.cost.amount,1000)||![data.cost.spent,data.cost.price].every(Number.isFinite)))
        ||!data.soc||typeof data.soc.configured!=="boolean"||!num(data.soc.target,100)
        ||!(data.soc.maximum===null||num(data.soc.maximum,100))
        ||!(data.soc.maximum_required===null||typeof data.soc.maximum_required==="boolean")
        ||typeof data.soc.status!=="string"
        ||(data.departure!==null&&(!data.departure||typeof data.departure.configured!=="boolean"
          ||typeof data.departure.revision!=="string"||typeof data.departure.status!=="string"
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
    let value=key==="guest"?true:clear?null:el(key+"_value").value;
    if(key==="departure"&&!clear){const original=originalDates[key];if(original&&localDate(original)===value)value=original;
      else {const date=new Date(value);if(!value||!Number.isFinite(date.getTime())||localDate(date.toISOString())!==value){error=t("invalid_time");render();return;}value=date.toISOString();}}
    else if(key!=="departure"&&key!=="guest")value=Number(value);
    const action=key==="guest"&&state.guest_active?"pause":key;
    const csrf=state.csrf,rev=action==="pause"?state.revision:key==="guest"?state.guest_revision:revisions[key];busy=true;suspend();error="";render();
    const own=generation,controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
    try{const r=await fetch("./api/charging",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json","X-Amtron-CSRF":csrf},body:JSON.stringify({action,value,revision:rev,confirm:true}),signal:controller.signal});const data=await r.json();if(own!==generation||!live())return;if(!r.ok){error=t(Object.prototype.hasOwnProperty.call(refusals,data?.error)?refusals[data.error]:"uncertain");return;}validate(data);dirty.delete(key);if(key!=="departure"&&key!=="guest")for(const other of keys)if(other!=="departure")revisions[other]=data.revision;error=t(action==="guest"?"guest_started":"saved");apply(data);}
    catch(_){if(own===generation&&live())error=t("uncertain");}
    finally{clearTimeout(timeout);busy=false;render();schedule();}
  }
  for(const key of keys){el(key+"_form").addEventListener("input",()=>{dirty.add(key);error="";render();});el(key+"_form").addEventListener("submit",e=>{e.preventDefault();void save(key);});}
  if(keys.includes("departure"))el("departure_clear").addEventListener("click",()=>{void save("departure",true);});
  el("guest_start").addEventListener("click",()=>{void save("guest");});
  el("reload").addEventListener("click",()=>{if(busy)return;dirty.clear();error="";suspend();void read();});
  document.addEventListener("visibilitychange",()=>{suspend();state=null;render();if(live())void read();});
  addEventListener("pagehide",()=>{hidden=true;suspend();state=null;});addEventListener("pageshow",()=>{hidden=false;void read();});
  render();void read();
})();
