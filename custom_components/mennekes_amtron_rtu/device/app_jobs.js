/* Local job UI. The server remains the sole authority; never retry POSTs. */
(() => {
  "use strict";
  const languages = ["en", "de", "fr", "nl", "it"];
  const found = languages.indexOf((document.documentElement.lang || "").slice(0, 2).toLowerCase());
  const column = found < 0 ? 0 : found, locale = languages[column];
  const words = {
    forecast_title:["Estimated time to the AC-energy ceiling","Schätzung bis zur AC-Energieobergrenze","Estimation jusqu’au plafond d’énergie AC","Schatting tot de AC-energiegrens","Stima fino al tetto di energia AC"],
    forecast_hint:["Conditional on similar charging conditions. Not a battery percentage or a guaranteed finish; other limits may stop earlier.","Ähnliche Ladebedingungen vorausgesetzt. Kein Batterie-Prozentwert und keine garantierte Endzeit; andere Grenzen können früher stoppen.","Sous réserve de conditions de recharge similaires. Ni un pourcentage de batterie ni une fin garantie ; d’autres limites peuvent arrêter plus tôt.","Onder vergelijkbare laadomstandigheden. Geen accupercentage en geen gegarandeerd einde; andere grenzen kunnen eerder stoppen.","A condizioni di ricarica simili. Non è una percentuale della batteria né una fine garantita; altri limiti possono fermare prima."],
    reserve:["Immediate reserve, kWh","Sofortreserve in kWh","Réserve immédiate, kWh","Directe reserve, kWh","Riserva immediata, kWh"],
    reserve_hint:["Charge this portion now, then wait for the planned start. Included in the total energy ceiling, not extra. Zero disables; AC meter energy, not battery percentage.","Diese Teilmenge jetzt laden, danach auf den geplanten Start warten. In der Gesamtobergrenze enthalten, nicht zusätzlich. Null deaktiviert; AC-Zählerenergie, kein Batterie-Prozentwert.","Recharger cette part maintenant, puis attendre le début prévu. Comprise dans le plafond total, pas en plus. Zéro désactive ; énergie du compteur AC, pas un pourcentage de batterie.","Laad dit deel nu en wacht daarna op de geplande start. Inbegrepen in de totale bovengrens, niet extra. Nul schakelt uit; AC-meterenergie, geen accupercentage.","Ricaricare subito questa parte, poi attendere l’inizio previsto. Inclusa nel tetto totale, non in aggiunta. Zero la disattiva; energia del contatore AC, non percentuale della batteria."],
    reserve_done:["reserve reached","Reserve erreicht","réserve atteinte","reserve bereikt","riserva raggiunta"],
    reserve_pending:["reserve not reached yet","Reserve noch nicht erreicht","réserve pas encore atteinte","reserve nog niet bereikt","riserva non ancora raggiunta"],
    jobs:["Charging jobs","Ladeaufträge","Ordres de recharge","Laadopdrachten","Ordini di ricarica"],
    intro:["A local time window with an additional-energy ceiling. Existing release and battery limits remain in force.","Ein lokales Zeitfenster mit zusätzlicher Energieobergrenze. Bestehende Freigabe- und Batteriegrenzen bleiben wirksam.","Une plage horaire locale avec un plafond d’énergie supplémentaire. L’autorisation et les limites de batterie existantes restent en vigueur.","Een lokaal tijdvenster met een bovengrens voor extra energie. Bestaande vrijgave- en accugrenzen blijven gelden.","Una finestra oraria locale con un tetto di energia aggiuntiva. Consenso e limiti della batteria esistenti restano validi."],
    active:["Current order","Aktueller Auftrag","Ordre actuel","Huidige opdracht","Ordine attuale"],
    stop:["Stop automation for this connection","Automatik für diesen Anschluss stoppen","Arrêter l’automatisme pour cette connexion","Automatisering voor deze verbinding stoppen","Interrompere l’automazione per questo collegamento"],
    clear:["Clear stopped order without resuming","Gestoppten Auftrag löschen – ohne Ladestart","Effacer l’ordre arrêté sans reprendre","Gestopte opdracht wissen zonder te hervatten","Cancellare l’ordine fermato senza riprendere"],
    new:["New order","Neuer Auftrag","Nouvel ordre","Nieuwe opdracht","Nuovo ordine"],
    window:["Local charging window","Lokales Ladezeitfenster","Plage de recharge locale","Lokaal laadvenster","Finestra di ricarica locale"],
    start:["Start","Beginn","Début","Begin","Inizio"],
    finish:["Finish no later than","Spätestes Ende","Fin au plus tard","Uiterlijk klaar om","Fine al più tardi"],
    energy:["Additional energy, kWh","Zusätzliche Energie in kWh","Énergie supplémentaire, kWh","Extra energie, kWh","Energia aggiuntiva, kWh"],
    current:["Current per phase, A","Strom je Phase in A","Courant par phase, A","Stroom per fase, A","Corrente per fase, A"],
    scope:["Applies only to this confirmed physical connection. The order does not identify or authorize the vehicle. After a service restart or a lasting connection loss it continues only if the wallbox proves the same plug-in; otherwise a new order is needed.","Gilt nur für diesen bestätigten Anschluss. Der Auftrag identifiziert oder autorisiert kein Fahrzeug. Nach einem Service-Neustart oder längerem Verbindungsverlust läuft er nur weiter, wenn die Wallbox denselben Steckvorgang belegt; sonst ist ein neuer Auftrag nötig.","S’applique uniquement à cette connexion physique confirmée. L’ordre n’identifie ni n’autorise le véhicule. Après un redémarrage du service ou une perte de connexion durable, il ne continue que si la borne prouve le même branchement ; sinon, un nouvel ordre est nécessaire.","Geldt alleen voor deze bevestigde fysieke verbinding. De opdracht identificeert of autoriseert het voertuig niet. Na een herstart van de service of een langdurig verbindingsverlies loopt ze alleen door als de laadpaal dezelfde aansluiting aantoont; anders is een nieuwe opdracht nodig.","Vale solo per questo collegamento fisico confermato. L’ordine non identifica né autorizza il veicolo. Dopo un riavvio del servizio o una perdita di collegamento prolungata continua solo se la wallbox dimostra lo stesso collegamento; altrimenti serve un nuovo ordine."],
    preview:["Preview","Vorschau","Aperçu","Voorbeeld","Anteprima"],
    confirm_title:["Confirm this action","Aktion bestätigen","Confirmer cette action","Deze actie bevestigen","Confermare questa azione"],
    confirm:["Confirm this action","Diese Aktion bestätigen","Confirmer cette action","Deze actie bevestigen","Confermare questa azione"],
    limits:["This is not a guaranteed energy delivery or finish-time forecast. The vehicle and all existing safety limits remain authoritative.","Keine garantierte Energiemenge oder Fertigstellungsprognose. Das Fahrzeug und alle bestehenden Schutzgrenzen bleiben maßgeblich.","Ni la quantité d’énergie ni l’heure de fin ne sont garanties. Le véhicule et toutes les limites de sécurité existantes restent déterminants.","Geen gegarandeerde energiehoeveelheid of eindtijd. Het voertuig en alle bestaande veiligheidsgrenzen blijven bepalend.","Né la quantità di energia né l’orario di fine sono garantiti. Il veicolo e tutti i limiti di sicurezza esistenti restano determinanti."],
    footer:["A requested stop is not a confirmed physical stop. Check the overview and command results. No automatic retry after an uncertain browser response.","Ein angeforderter Stopp ist kein bestätigter physischer Stopp. Übersicht und Befehlsergebnisse beachten. Keine automatische Wiederholung bei ungewisser Browser-Antwort.","Un arrêt demandé n’est pas un arrêt physique confirmé. Consultez la vue d’ensemble et les résultats des commandes. Aucune nouvelle tentative automatique après une réponse incertaine.","Een gevraagde stop is geen bevestigde fysieke stop. Controleer het overzicht en de opdrachtresultaten. Geen automatische herhaling na een onzeker antwoord.","Uno stop richiesto non è uno stop fisico confermato. Controllare la panoramica e i risultati dei comandi. Nessun nuovo tentativo automatico dopo una risposta incerta."],
    loading:["Reading service state…","Servicestatus wird gelesen …","Lecture de l’état du service…","Servicestatus wordt gelezen…","Lettura dello stato del servizio…"],
    offline:["No current service response. Reload before changing an order.","Keine aktuelle Service-Antwort. Vor Änderungen neu laden.","Aucune réponse actuelle du service. Rechargez avant de modifier un ordre.","Geen actueel antwoord van de service. Herlaad voordat u een opdracht wijzigt.","Nessuna risposta attuale del servizio. Ricaricare prima di modificare un ordine."],
    ready:["Ready. Preview an order for the connected vehicle.","Bereit. Auftrag für das angeschlossene Fahrzeug in der Vorschau prüfen.","Prêt. Prévisualisez un ordre pour le véhicule connecté.","Gereed. Bekijk een opdracht voor het aangesloten voertuig.","Pronto. Visualizzare l’anteprima di un ordine per il veicolo collegato."],
    unavailable:["A fresh meter, connected vehicle and existing release are required.","Aktueller Zählerstand, angeschlossenes Fahrzeug und bestehende Ladefreigabe erforderlich.","Un compteur à jour, un véhicule connecté et une autorisation existante sont requis.","Een actuele meterstand, een aangesloten voertuig en een bestaande vrijgave zijn vereist.","Servono un contatore aggiornato, un veicolo collegato e un consenso esistente."],
    saved:["Order accepted; actual charging remains subject to device conditions.","Auftrag angenommen; die tatsächliche Ladung bleibt von den Gerätebedingungen abhängig.","Ordre accepté ; la recharge réelle dépend des conditions de l’appareil.","Opdracht aanvaard; het werkelijke laden blijft afhankelijk van de apparaatvoorwaarden.","Ordine accettato; la ricarica effettiva dipende dalle condizioni del dispositivo."],
    uncertain:["Operation could not be confirmed. Read the current state before retrying.","Vorgang nicht bestätigt. Vor einem erneuten Versuch den aktuellen Zustand prüfen.","Opération non confirmée. Vérifiez l’état actuel avant de réessayer.","Handeling niet bevestigd. Controleer de huidige toestand voordat u het opnieuw probeert.","Operazione non confermata. Verificare lo stato attuale prima di riprovare."],
    scheduled:["Waiting for the window","Wartet auf das Zeitfenster","En attente de la plage","Wacht op het tijdvenster","In attesa della finestra"],
    charging:["Charging requested","Laden angefordert","Recharge demandée","Laden gevraagd","Ricarica richiesta"],
    waiting_meter:["Waiting for a valid meter reading","Wartet auf gültigen Zählerstand","En attente d’un relevé de compteur valide","Wacht op een geldige meterstand","In attesa di una lettura valida del contatore"],
    reached:["Energy ceiling reached","Energieobergrenze erreicht","Plafond d’énergie atteint","Energiegrens bereikt","Tetto di energia raggiunto"],
    finished:["Time window ended","Zeitfenster beendet","Plage horaire terminée","Tijdvenster afgelopen","Finestra oraria terminata"],
    stopped:["Stopped by user","Vom Benutzer gestoppt","Arrêté par l’utilisateur","Gestopt door gebruiker","Fermato dall’utente"],
    suspended:["Interrupted; checking whether the same plug-in continues","Unterbrochen; prüft, ob derselbe Steckvorgang weiterläuft","Interrompu ; vérifie si le même branchement continue","Onderbroken; controleert of dezelfde aansluiting doorloopt","Interrotto; verifica se lo stesso collegamento prosegue"],
    interrupted:["Interrupted; no automatic restart","Unterbrochen; kein automatischer Neustart","Interrompu ; pas de redémarrage automatique","Onderbroken; geen automatische herstart","Interrotto; nessun riavvio automatico"],
    user_stop:["User stop","Benutzerstopp","Arrêt utilisateur","Stop door gebruiker","Stop dell’utente"],
    service_restarted:["Service restarted","Service neu gestartet","Service redémarré","Service herstart","Servizio riavviato"],
    connection_lost:["Connection lost or ambiguous","Verbindung verloren oder unklar","Connexion perdue ou incertaine","Verbinding verloren of onduidelijk","Collegamento perso o incerto"],
    vehicle_changed:["Connection or vehicle binding changed","Anschluss oder Fahrzeugzuordnung geändert","Connexion ou association du véhicule modifiée","Verbinding of voertuigkoppeling gewijzigd","Collegamento o associazione del veicolo modificati"],
    independent_stop:["Another charging limit requires a stop","Eine andere Ladegrenze verlangt den Stopp","Une autre limite de recharge exige l’arrêt","Een andere laadgrens vereist een stop","Un altro limite di ricarica richiede lo stop"],
    meter_reset:["Meter reset","Zählerrücksetzung","Compteur réinitialisé","Meter teruggezet","Contatore azzerato"],
    clock_changed:["Clock moved backwards","Uhrzeit zurückgesprungen","L’horloge a reculé","Klok is teruggesprongen","L’orologio è tornato indietro"],
    storage_error:["Storage could not be confirmed; starts blocked","Speicherung nicht bestätigt; Ladestarts gesperrt","Enregistrement non confirmé ; démarrages bloqués","Opslag niet bevestigd; starten geblokkeerd","Salvataggio non confermato; avvii bloccati"],
    meter_unavailable:["Meter unavailable","Zähler nicht verfügbar","Compteur indisponible","Meter niet beschikbaar","Contatore non disponibile"],
    energy_reached:["Energy ceiling reached","Energieobergrenze erreicht","Plafond d’énergie atteint","Energiegrens bereikt","Tetto di energia raggiunto"],
    window_ended:["Window ended","Zeitfenster beendet","Plage terminée","Tijdvenster afgelopen","Finestra terminata"],
    goal_changed:["Charging target changed","Ladeziel geändert","Objectif de recharge modifié","Laaddoel gewijzigd","Obiettivo di ricarica modificato"],
    no_job:["No order","Kein Auftrag","Aucun ordre","Geen opdracht","Nessun ordine"],
    job_changed:["State changed. Reload and preview again.","Zustand geändert. Neu laden und Vorschau wiederholen.","L’état a changé. Rechargez et refaites l’aperçu.","Toestand gewijzigd. Herlaad en bekijk het voorbeeld opnieuw.","Lo stato è cambiato. Ricaricare e ripetere l’anteprima."],
    job_exists:["Another order is still running. Stop it first.","Ein anderer Auftrag läuft noch. Zuerst diesen stoppen.","Un autre ordre est encore en cours. Arrêtez-le d’abord.","Er loopt nog een andere opdracht. Stop deze eerst.","Un altro ordine è ancora in corso. Fermarlo prima."],
    job_not_permitted:["Connection or release changed; no order was created.","Anschluss oder Freigabe geändert; kein Auftrag angelegt.","Connexion ou autorisation modifiée ; aucun ordre créé.","Verbinding of vrijgave gewijzigd; geen opdracht aangemaakt.","Collegamento o consenso modificati; nessun ordine creato."],
    job_current_exceeds_cap:["Current exceeds the service limit.","Strom überschreitet die Servicegrenze.","Le courant dépasse la limite du service.","Stroom overschrijdt de servicegrens.","La corrente supera il limite del servizio."],
    job_clear_requires_pause:["Clearing requires a confirmed pause or unplug.","Löschen erfordert eine bestätigte Pause oder abgezogenes Fahrzeug.","L’effacement exige une pause confirmée ou un véhicule débranché.","Wissen vereist een bevestigde pauze of een losgekoppeld voertuig.","La cancellazione richiede una pausa confermata o il veicolo scollegato."],
    job_stop_first:["Stop the active order first.","Zuerst den laufenden Auftrag stoppen.","Arrêtez d’abord l’ordre en cours.","Stop eerst de lopende opdracht.","Fermare prima l’ordine in corso."],
    job_storage_unavailable:["Storage unavailable; no automatic start.","Speicher nicht verfügbar; kein automatischer Start.","Stockage indisponible ; aucun démarrage automatique.","Opslag niet beschikbaar; geen automatische start.","Memoria non disponibile; nessun avvio automatico."],
    invalid_job_request:["Invalid order. Check time, current and energy.","Ungültiger Auftrag. Zeit, Strom und Energie prüfen.","Ordre non valide. Vérifiez l’heure, le courant et l’énergie.","Ongeldige opdracht. Controleer tijd, stroom en energie.","Ordine non valido. Verificare orario, corrente ed energia."],
    job_preview_expired:["The start lies more than a minute in the past. Preview again.","Der Start liegt mehr als eine Minute zurück. Vorschau wiederholen.","Le début remonte à plus d’une minute. Refaites l’aperçu.","De start ligt meer dan een minuut in het verleden. Bekijk het voorbeeld opnieuw.","L’inizio risale a più di un minuto fa. Ripetere l’anteprima."],
    command_unconfirmed:["Command not confirmed yet","Befehl noch nicht bestätigt","Commande pas encore confirmée","Opdracht nog niet bevestigd","Comando non ancora confermato"],
    end_scope:["Connection ended","Anschluss beendet","Connexion terminée","Verbinding beëindigd","Collegamento terminato"],
    released:["Pause confirmed; Home Assistant controls charging again","Pause bestätigt; Home Assistant steuert die Ladung wieder","Pause confirmée ; Home Assistant pilote de nouveau la recharge","Pauze bevestigd; Home Assistant regelt het laden weer","Pausa confermata; Home Assistant controlla di nuovo la ricarica"],
    boost_title:["Temporary early charging","Vorübergehend früher laden","Recharge anticipée temporaire","Tijdelijk eerder laden","Ricarica anticipata temporanea"],
    boost_hint:["Charge before the scheduled start, then return to the original plan. Current and energy limits stay unchanged.","Vor dem geplanten Start laden, danach zum ursprünglichen Plan zurückkehren. Strom- und Energiegrenzen bleiben unverändert.","Recharger avant le début prévu, puis revenir au plan initial. Les limites de courant et d’énergie restent inchangées.","Laden vóór de geplande start en daarna terug naar het oorspronkelijke plan. Stroom- en energiegrenzen blijven ongewijzigd.","Ricaricare prima dell’inizio previsto, poi tornare al piano originale. I limiti di corrente ed energia restano invariati."],
    boost_minutes:["Duration in minutes (1–120)","Dauer in Minuten (1–120)","Durée en minutes (1–120)","Duur in minuten (1–120)","Durata in minuti (1–120)"],
    boost_preview:["Preview boost","Boost-Vorschau","Aperçu du boost","Voorbeeld boost","Anteprima boost"],
    boost_cancel:["Return to plan now","Jetzt zum Plan zurückkehren","Revenir au plan maintenant","Nu terug naar het plan","Tornare ora al piano"],
    boost_until:["Boost until","Boost bis","Boost jusqu’à","Boost tot","Boost fino a"],
    job_boost_unavailable:["Boost requires an active order before its planned start and current permission.","Boost erfordert einen aktiven Auftrag vor dessen geplantem Start und eine aktuelle Freigabe.","Le boost exige un ordre actif avant son début prévu et une autorisation actuelle.","Boost vereist een actieve opdracht vóór de geplande start en een actuele vrijgave.","Il boost richiede un ordine attivo prima del suo inizio previsto e un consenso attuale."],
    invalid_boost_duration:["Enter a whole number from 1 to 120 minutes.","Eine ganze Zahl von 1 bis 120 Minuten eingeben.","Saisissez un nombre entier de 1 à 120 minutes.","Voer een geheel getal van 1 tot 120 minuten in.","Inserire un numero intero da 1 a 120 minuti."],
    read_only:["Read-only mode: writes are disabled in the app configuration.", "Nur-Lesen-Modus: Schreiben ist in der App-Konfiguration abgeschaltet.", "Mode lecture seule : les écritures sont désactivées dans la configuration de l’application.", "Alleen-lezenmodus: schrijven is uitgeschakeld in de app-configuratie.", "Modalità sola lettura: le scritture sono disattivate nella configurazione dell’app."],
    busy:["Another request is running. Try again shortly.", "Eine andere Anfrage läuft noch. Gleich noch einmal versuchen.", "Une autre requête est en cours. Réessayez dans un instant.", "Een ander verzoek loopt nog. Probeer het zo opnieuw.", "Un’altra richiesta è in corso. Riprova tra poco."],
    reload_required:["Reload this page and try again.", "Seite neu laden und erneut versuchen.", "Rechargez cette page et réessayez.", "Herlaad deze pagina en probeer het opnieuw.", "Ricaricare la pagina e riprovare."],
    admin_required:["Sign in to Home Assistant as an administrator.", "Bei Home Assistant als Administrator anmelden.", "Connectez-vous à Home Assistant en tant qu’administrateur.", "Meld je aan bij Home Assistant als beheerder.", "Accedi a Home Assistant come amministratore."],
    supervisor_unavailable:["Home Assistant Supervisor did not answer. Reload this page.", "Home Assistant Supervisor hat nicht geantwortet. Seite neu laden.", "Supervisor n’a pas répondu. Rechargez la page.", "Supervisor heeft niet geantwoord. Herlaad deze pagina.", "Supervisor non ha risposto. Ricaricare la pagina."],
    supervisor_update_required:["Update Home Assistant Supervisor to 2026.10.4 or newer.", "Home Assistant Supervisor auf 2026.10.4 oder neuer aktualisieren.", "Mettez Supervisor à jour vers 2026.10.4 ou ultérieur.", "Werk Supervisor bij naar 2026.10.4 of nieuwer.", "Aggiorna Supervisor alla versione 2026.10.4 o successiva."],
    invalid_request:["The request was rejected. Reload this page and try again.", "Anfrage abgelehnt. Seite neu laden und erneut versuchen.", "Requête refusée. Rechargez la page et réessayez.", "Verzoek geweigerd. Herlaad de pagina en probeer het opnieuw.", "Richiesta rifiutata. Ricaricare la pagina e riprovare."],
    job_stop_unconfirmed:["The stop is not confirmed yet. Try again shortly.", "Der Stopp ist noch nicht bestätigt. Gleich noch einmal versuchen.", "L’arrêt n’est pas encore confirmé. Réessayez dans un instant.", "De stop is nog niet bevestigd. Probeer het zo opnieuw.", "L’arresto non è ancora confermato. Riprova tra poco."],
    job_service_starting:["The service is still starting. Try again shortly.", "Der Service startet noch. Gleich noch einmal versuchen.", "Le service démarre encore. Réessayez dans un instant.", "De service start nog. Probeer het zo opnieuw.", "Il servizio si sta ancora avviando. Riprova tra poco."],
    guest_vehicle_target_active:["Vehicle battery target active; no guest order created.", "Fahrzeug-Batterieziel aktiv; kein Gastauftrag angelegt.", "Objectif de batterie du véhicule actif ; aucun ordre invité créé.", "Batterijdoel van het voertuig actief; geen gastopdracht aangemaakt.", "Obiettivo batteria del veicolo attivo; nessun ordine ospite creato."]
  };
  const t = k => words[k]?.[column] || k;
  document.querySelectorAll("[data-text]").forEach(n => { n.textContent = t(n.dataset.text); });
  // The page shows once its texts are in the page language.
  document.documentElement.classList?.add("ready");
  const el = id => document.getElementById(id), notice = message => { el("job_notice").textContent = message; };
  const date = value => new Date(value).toLocaleString(locale, {timeZoneName:"short"});
  const local = d => { const p = n=>String(n).padStart(2,"0"); return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
  let state = null, proposal = null, busy = false, timer = null, readAbort = null, generation = 0, hidden = false, lastError = "";
  el("job_start").value = local(new Date(Date.now()+60000));
  el("job_finish").value = local(new Date(Date.now()+8*3600000));
  const ended = job => ["reached","finished","stopped","interrupted"].includes(job.state);
  const reserveText = job => job?.reserve_kwh ? ` · ${t("reserve")}: ${job.reserve_kwh}${typeof job.reserve_reached === "boolean" ? ` (${t(job.reserve_reached ? "reserve_done" : "reserve_pending")})` : ""}` : "";
  const forecastWords = {
    awaiting_meter:["Waiting for a new meter reading to confirm progress","Wartet auf einen neuen Zählerstand zur Fortschrittsbestätigung","En attente d’un nouveau relevé du compteur pour confirmer la progression","Wacht op een nieuwe meterstand om de voortgang te bevestigen","In attesa di una nuova lettura del contatore per confermare l’avanzamento"],
    learning:["Collecting valid measurement intervals","Sammelt gültige Messintervalle","Collecte d’intervalles de mesure valides","Verzamelt geldige meetintervallen","Raccolta di intervalli di misura validi"],
    scheduled:["Waiting for the window; no matching learned rate yet","Wartet auf das Zeitfenster; noch keine passende Messbasis","En attente de la plage ; pas encore de puissance apprise correspondante","Wacht op het tijdvenster; nog geen passend geleerd vermogen","In attesa della finestra; nessuna potenza appresa corrispondente"],
    unavailable:["No reliable current forecast","Keine verlässliche aktuelle Prognose","Aucune prévision actuelle fiable","Geen betrouwbare actuele prognose","Nessuna previsione attuale affidabile"],
    stale:["Measurement basis is too old","Messbasis ist veraltet","La base de mesure est trop ancienne","Meetbasis is te oud","La base di misura è troppo vecchia"],
    unstable:["Power varies too much for a useful finish range","Leistung schwankt zu stark für einen sinnvollen Zeitbereich","La puissance varie trop pour une plage de fin utile","Vermogen schommelt te sterk voor een bruikbaar eindbereik","La potenza varia troppo per un intervallo di fine utile"],
    meter_unavailable:["No current meter reading","Kein aktueller Zählerstand","Aucun relevé de compteur actuel","Geen actuele meterstand","Nessuna lettura attuale del contatore"],
    measurement_gap:["Measurement gap; learning again","Messlücke; Messbasis wird neu aufgebaut","Interruption de mesure ; nouvel apprentissage","Meetonderbreking; opnieuw leren","Interruzione della misura; nuovo apprendimento"],
    meter_reset:["Counter or clock changed; learning again","Zähler oder Uhrzeit verändert; Messbasis wird neu aufgebaut","Compteur ou horloge modifié ; nouvel apprentissage","Meter of klok gewijzigd; opnieuw leren","Contatore o orologio modificato; nuovo apprendimento"],
    meter_jump:["Implausible energy interval","Unplausibles Energieintervall","Intervalle d’énergie invraisemblable","Onwaarschijnlijk energie-interval","Intervallo di energia non plausibile"],
    not_charging:["No confirmed energy intake","Keine bestätigte Energieaufnahme","Aucune absorption d’énergie confirmée","Geen bevestigde energieopname","Nessun assorbimento di energia confermato"],
    waiting_vehicle:["Waiting for confirmed energy intake","Wartet auf bestätigte Energieaufnahme","En attente d’une absorption d’énergie confirmée","Wacht op bevestigde energieopname","In attesa di un assorbimento di energia confermato"],
    no_energy_progress:["No measured energy increase","Kein gemessener Energiezuwachs","Aucune hausse d’énergie mesurée","Geen gemeten energietoename","Nessun aumento di energia misurato"],
    complete:["AC-energy ceiling reached","AC-Energieobergrenze erreicht","Plafond d’énergie AC atteint","AC-energiegrens bereikt","Tetto di energia AC raggiunto"],
    stopped:["Order ended; no active forecast","Auftrag beendet; keine aktive Prognose","Ordre terminé ; aucune prévision active","Opdracht beëindigd; geen actieve prognose","Ordine terminato; nessuna previsione attiva"],
    beyond_horizon:["Target lies beyond the forecast horizon at this rate","Ziel liegt bei dieser Leistung außerhalb des Prognosehorizonts","À cette puissance, l’objectif dépasse l’horizon de prévision","Bij dit vermogen ligt het doel buiten de prognosehorizon","A questa potenza l’obiettivo è oltre l’orizzonte di previsione"],
    live:["Current measured intervals","Aktuelle Messintervalle","Intervalles mesurés actuels","Actuele meetintervallen","Intervalli misurati attuali"],
    history:["Previous measured intervals","Frühere Messintervalle","Intervalles mesurés précédents","Eerdere meetintervallen","Intervalli misurati precedenti"],
    likely:["Within the accepted time window","Innerhalb des angenommenen Zeitfensters","Dans la plage horaire acceptée","Binnen het aanvaarde tijdvenster","Entro la finestra oraria accettata"],
    at_risk:["Time window may be too short","Zeitfenster könnte zu kurz sein","La plage horaire pourrait être trop courte","Tijdvenster is mogelijk te kort","La finestra oraria potrebbe essere troppo breve"],
    unlikely:["Energy target unlikely within the time window","Energieziel voraussichtlich nicht im Zeitfenster erreichbar","Objectif d’énergie peu probable dans la plage horaire","Energiedoel waarschijnlijk niet binnen het tijdvenster","Obiettivo di energia improbabile entro la finestra oraria"],
    unknown:["Deadline feasibility unknown","Erreichbarkeit im Zeitfenster unbekannt","Faisabilité dans la plage inconnue","Haalbaarheid binnen het tijdvenster onbekend","Fattibilità entro la finestra sconosciuta"],
    storage:["Learned rates are not currently being saved","Lernwerte werden derzeit nicht dauerhaft gespeichert","Les puissances apprises ne sont pas enregistrées actuellement","Geleerde waarden worden momenteel niet opgeslagen","I valori appresi non vengono salvati al momento"]
  };
  const ft = key => (Object.hasOwn(forecastWords, key) ? forecastWords[key] : forecastWords.unavailable)[column];
  const validDate = value => typeof value === "string" && value.length <= 64 && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
  function renderForecast(value, hasJob) {
    el("job_forecast").hidden = !value || !hasJob;
    el("job_forecast_summary").textContent = "";
    el("job_forecast_detail").textContent = "";
    el("job_forecast_storage").textContent = "";
    if (!value || !hasJob) return;
    const valid = value.basis === "ac_meter" && value.state === "estimate" && ["live","history"].includes(value.source)
      && typeof value.power_kw === "number" && Number.isFinite(value.power_kw) && value.power_kw > 0 && value.power_kw <= 30
      && Number.isInteger(value.samples) && value.samples >= 3 && value.samples <= 16
      && validDate(value.earliest_finish) && validDate(value.latest_finish) && validDate(value.observed_at)
      && Date.parse(value.earliest_finish) <= Date.parse(value.latest_finish);
    el("job_forecast_summary").textContent = valid ? `${date(value.earliest_finish)} → ${date(value.latest_finish)} · ${ft(value.deadline_status)}` : ft(value.basis === "ac_meter" ? value.state : "unavailable");
    if (valid) el("job_forecast_detail").textContent = `${value.power_kw.toLocaleString(locale, {maximumFractionDigits:1})} kW · ${ft(value.source)}: ${value.samples} · ${date(value.observed_at)}`;
    if (value.history_storage === "unavailable") el("job_forecast_storage").textContent = ft("storage");
  }
  function invalidate() { proposal=null; el("job_confirmation").hidden=true; }
  function render() {
    const good = state?.schema === 1 && state.ready && !state.storage_error;
    el("job_status").hidden = !state;
    el("job_fields").disabled = busy || !good || !state.available || state.present !== true || !state.permitted || !state.meter_fresh || Boolean(state.job?.holding);
    el("job_current").max=String(state?.max_current_a || 6);
    el("job_stop").disabled=busy || !state?.job?.holding || ended(state.job);
    el("job_clear").disabled=busy || !state || (!state.job && !state.storage_error && !state.manual_stop_pending);
    el("job_confirm").disabled=busy || !proposal;
    el("job_boost_preview").disabled=busy || state?.boost_available !== true;
    el("job_boost_minutes").disabled=busy || state?.boost_available !== true;
    el("job_cancel_boost").disabled=busy || !state?.job?.boost_until;
    const job=state?.job;
    renderForecast(state?.forecast, Boolean(job));
    el("job_state").textContent=job ? t(job.state) : t("no_job");
    el("job_detail").textContent=job ? `${t(job.reason)} · ${date(job.start_at)} → ${date(job.finish_at)} · ${job.current_a} A${job.scope_ended ? ` · ${t("end_scope")}` : !job.holding ? ` · ${t("released")}` : ""}` : "";
    el("job_progress").textContent=job ? `${job.delivered_kwh == null ? "—" : Number(job.delivered_kwh).toLocaleString(locale,{maximumFractionDigits:2})} / ${job.energy_kwh} kWh${reserveText(job)}${state.action_error ? ` · ${t(state.action_error)}` : ""}` : "";
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
      // A refusal stays visible until the next action.
      state=data;render();notice(lastError || (data.storage_error ? t("storage_error") : data.job ? t(data.job.state) : data.read_only ? t("read_only") : data.available && data.permitted && data.meter_fresh ? t("ready") : t("unavailable")));
    } catch (_) { if(own===generation){ state=null;invalidate();render();notice(t("offline")); } }
    finally {clearTimeout(timeout);if(own===generation){readAbort=null;if(!hidden&&!document.hidden)timer=setTimeout(read,5000);}}
  }
  async function post(payload) {
    if (busy || !state) return null;
    busy=true;lastError="";suspend();render();
    const own=generation, abort=new AbortController(), timeout=setTimeout(()=>abort.abort(),15000);
    try {
      const response=await fetch("./api/jobs",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json","X-Amtron-CSRF":state.csrf},body:JSON.stringify(payload),signal:abort.signal});
      const data=await response.json();
      if(own!==generation || hidden || document.hidden)return null;
      if(!response.ok){lastError=t(data.error || "uncertain");notice(lastError);return null;}
      return data;
    } catch (_) { lastError=t("uncertain");notice(lastError);return null; }
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
    const reserve=Number(el("job_reserve")?.value || 0);
    if(!Number.isFinite(reserve)||reserve<0||reserve>request.energy_kwh||Math.abs(reserve*10-Math.round(reserve*10))>1e-8){notice(t("invalid_job_request"));return;}
    if(reserve>0)request.reserve_kwh=reserve;
    const result=await post({action:"preview",revision:state.revision,scope:state.scope,request});
    if(result?.schema===1&&result.request){proposal={...result,kind:"create"};el("job_summary").textContent=`${date(result.request.start_at)} → ${date(result.request.finish_at)} · ${result.request.current_a} A · ${result.request.energy_kwh} kWh${reserveText(result.request)}`;el("job_confirmation").hidden=false;render();}else{state=null;render();}
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
  addEventListener("pagehide",()=>{hidden=true;suspend();invalidate();state=null;render();});
  addEventListener("pageshow",()=>{hidden=false;void read();});
  notice(t("loading"));render();void read();
})();
