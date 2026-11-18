/* Passive Ingress overview. No credentials, writes or external dependencies. */
(() => {
  "use strict";
  const languages = ["en", "de", "fr", "nl", "it"];
  const language = languages.indexOf(document.documentElement.lang.split("-")[0]);
  const column = language < 0 ? 0 : language;
  const words = {
    requested_current: ["Requested", "Angefordert", "Demandé", "Gevraagd", "Richiesto"],
    vehicle_authorized: ["Recognized vehicle released for this connection", "Erkanntes Fahrzeug für diesen Steckvorgang freigegeben", "Véhicule reconnu autorisé pour ce branchement", "Herkend voertuig vrijgegeven voor deze aansluiting", "Veicolo riconosciuto autorizzato per questo collegamento"],
    vehicle_waiting: ["Waiting for an authorized vehicle", "Warten auf ein freigegebenes Fahrzeug", "En attente d’un véhicule autorisé", "Wachten op een toegestaan voertuig", "In attesa di un veicolo autorizzato"],

    source_unknown: ["Source not reported", "Quelle nicht übermittelt", "Source non transmise", "Bron niet gemeld", "Origine non comunicata"],
    source_home_assistant: ["Home Assistant (control or automation)", "Home Assistant (Bedienung oder Automation)", "Home Assistant (commande ou automatisation)", "Home Assistant (bediening of automatisering)", "Home Assistant (comando o automazione)"],
    source_app: ["App interface", "App-Oberfläche", "Interface de l’application", "Appinterface", "Interfaccia app"],
    source_plug_charge: ["Virtual Plug & Charge", "Virtuelles Plug & Charge", "Plug & Charge virtuel", "Virtuele Plug & Charge", "Plug & Charge virtuale"],
    source_departure_plan: ["Departure plan", "Abfahrtsplanung", "Plan de départ", "Vertrekplanning", "Pianificazione partenza"],
    source_energy_goal: ["Energy target protection", "Schutz des Energieziels", "Protection de l’objectif énergétique", "Bescherming energiedoel", "Protezione obiettivo energetico"],
    source_soc_goal: ["Vehicle percentage target", "Fahrzeug-Prozentziel", "Objectif de charge du véhicule", "Laadniveau voertuig", "Obiettivo percentuale veicolo"],
    source_recovery: ["Error recovery", "Fehlerbehebung", "Récupération automatique", "Automatisch foutherstel", "Ripristino automatico"],
    source_unplug: ["Unplugged", "Ausgesteckt", "Débranché", "Uitgetrokken", "Scollegato"],
    source_guest: ["Guest charging", "Gastladen", "Recharge invité", "Gastladen", "Ricarica ospite"],

    limits_details: ["Saved limits and queued actions", "Gespeicherte Grenzen und vorgemerkte Aktionen", "Limites enregistrées et actions en attente", "Opgeslagen grenzen en geplande acties", "Limiti salvati e azioni in attesa"],
    guest_help: ["Start now; optional energy and target-time limits apply.", "Jetzt starten; optionale Energie- und Zeitpunktgrenzen gelten.", "Démarrer maintenant ; limites d’énergie et heure cible facultatives.", "Start nu; optionele energiegrens en eindtijd gelden.", "Avvia ora; valgono i limiti facoltativi di energia e orario."],
    departure_help: ["Vehicle departure or guest stop time.", "Fahrzeugabfahrt oder Stoppzeit für Gäste.", "Départ du véhicule ou heure d’arrêt pour l’invité.", "Vertrektijd van het voertuig of stoptijd voor gasten.", "Partenza del veicolo o orario di arresto ospite."],
    target_help: ["Limit energy or battery level without starting.", "Energie oder Akkustand begrenzen, ohne die Ladung zu starten.", "Limiter l’énergie ou le niveau de batterie sans démarrer.", "Beperk energie of accuniveau zonder te starten.", "Limita energia o livello batteria senza avviare."],
    choose: ["Plan your charging", "Laden vorbereiten", "Préparer la recharge", "Laden voorbereiden", "Prepara la ricarica"],
    departure_link: ["Target time", "Zielzeitpunkt", "Heure cible", "Doeltijd", "Orario obiettivo"],
    advice_connect: ["Connect the vehicle to the wallbox and wait for a fresh reading.", "Fahrzeug an die Wallbox anschließen und auf eine aktuelle Meldung warten.", "Branchez le véhicule et attendez une mesure récente.", "Sluit de auto aan en wacht op een actuele meting.", "Collegare il veicolo e attendere una lettura aggiornata."],
    intent_rejected: ["Saved settings rejected. Open Home Assistant Settings → System → Repairs. Confirmation keeps charging paused and release off.", "Gespeicherte Einstellungen abgelehnt. Öffne in Home Assistant Einstellungen → System → Reparaturen. Nach Bestätigung bleiben Pause aktiv und Ladefreigabe aus.", "Réglages enregistrés rejetés. Ouvrez Paramètres → Système → Réparations dans Home Assistant. La confirmation conserve la pause et coupe l’autorisation.", "Opgeslagen instellingen geweigerd. Open Instellingen → Systeem → Reparaties in Home Assistant. Bevestigen houdt pauze aan en vrijgave uit.", "Impostazioni salvate rifiutate. Apri Impostazioni → Sistema → Riparazioni in Home Assistant. Dopo la conferma pausa attiva e consenso disattivato."],
    advice_target: ["The target stops charging. Open Charge target to change the limit.", "Das Ziel stoppt die Ladung. Zum Ändern der Grenze Ladeziel öffnen.", "L’objectif arrête la recharge. Ouvrez Objectif de charge pour modifier la limite.", "Het doel stopt het laden. Open Laaddoel om de grens te wijzigen.", "L’obiettivo ferma la ricarica. Aprire Obiettivo di ricarica per modificare il limite."],
    advice_readonly: ["Enable writing in the app configuration if you want the app to control charging.", "Schreibzugriff unter App-Konfiguration aktivieren, wenn die App das Laden steuern soll.", "Activez l’écriture dans la configuration de l’application pour piloter la recharge.", "Schakel schrijven in via de appconfiguratie om laden te bedienen.", "Abilitare la scrittura in configurazione dell’app per controllare la ricarica."],
    advice_connection: ["Check Connection and the diagnostic log. Saved settings are not a live wallbox reading.", "Verbindung und Diagnoseprotokoll prüfen. Gespeicherte Einstellungen sind keine aktuelle Wallbox-Meldung.", "Vérifiez Connexion et le journal de diagnostic. Les réglages enregistrés ne sont pas des mesures actuelles.", "Controleer Verbinding en het diagnoselog. Opgeslagen instellingen zijn geen actuele meting.", "Verificare Connessione e registro diagnostico. Le impostazioni salvate non sono letture attuali."],
    advice_pause: ["Check the pause and charging-release switches in Home Assistant before resuming.", "Vor dem Fortsetzen in Home Assistant die Schalter Ladepause und Ladefreigabe prüfen.", "Vérifiez pause et autorisation de recharge dans Home Assistant avant de reprendre.", "Controleer pauze en laadvrijgave in Home Assistant voordat je hervat.", "Verificare pausa e consenso alla ricarica in Home Assistant prima di riprendere."],
    advice_wait: ["The next step is scheduled. No action is needed unless you want to change the plan.", "Der nächste Schritt ist geplant. Nur eingreifen, wenn du den Plan ändern möchtest.", "La prochaine étape est prévue. Intervenez seulement pour changer le plan.", "De volgende stap is gepland. Alleen ingrijpen als je het plan wilt wijzigen.", "Il prossimo passo è pianificato. Intervenire solo per cambiare il piano."],
    advice_plan: ["Check the target, charge level and battery capacity in Home Assistant’s vehicle settings.", "Ziel, Ladestand und Akkukapazität in den Fahrzeugeinstellungen von Home Assistant prüfen.", "Vérifiez objectif, niveau et capacité dans les réglages du véhicule de Home Assistant.", "Controleer doel, laadniveau en accucapaciteit in de voertuiginstellingen van Home Assistant.", "Verifica obiettivo, livello e capacità nelle impostazioni veicolo di Home Assistant."],
    advice_vehicle: ["Check the selected vehicle and its cable/home signals in Home Assistant.", "In Home Assistant das ausgewählte Fahrzeug und dessen Kabel-/Zuhause-Signale prüfen.", "Vérifiez le véhicule sélectionné et ses signaux câble/domicile dans Home Assistant.", "Controleer het geselecteerde voertuig en de kabel-/thuissignalen in Home Assistant.", "Controllare veicolo selezionato e segnali cavo/casa in Home Assistant."],
    guest_link: ["Guest charging", "Gastladen", "Recharge invité", "Gastladen", "Ricarica ospite"],
    targets_link: ["Charge target", "Ladeziel", "Objectif de charge", "Laaddoel", "Obiettivo di ricarica"],
    title: ["Charging overview", "Ladeübersicht", "Vue de la recharge", "Laadoverzicht", "Panoramica ricarica"],
    intro: ["Your charging at a glance. Choose a target, plan your next departure or start guest charging.", "Deine Ladung auf einen Blick. Ladeziel setzen, die nächste Abfahrt planen oder Gastladen starten.", "Votre recharge en un coup d’œil. Choisissez un objectif, planifiez le prochain départ ou lancez une recharge invité.", "Je laadsessie in één oogopslag. Kies een doel, plan het volgende vertrek of start gastladen.", "La ricarica a colpo d’occhio. Scegli un obiettivo, pianifica la prossima partenza o avvia la ricarica ospite."],
    loading: ["Loading service state…", "Service-Zustand wird geladen…", "Chargement de l’état…", "Servicestatus laden…", "Caricamento dello stato…"],
    live: ["Service snapshot · refreshes every 5 seconds", "Service-Zustand · Aktualisierung alle 5 Sekunden", "État du service · actualisation toutes les 5 secondes", "Servicestatus · elke 5 seconden vernieuwd", "Stato del servizio · aggiornamento ogni 5 secondi"],
    offline: ["Service state unavailable. Old readings are hidden; retrying automatically.", "Service-Zustand nicht erreichbar. Alte Werte sind ausgeblendet; erneuter Abruf erfolgt automatisch.", "État indisponible. Les anciennes valeurs sont masquées ; nouvelle tentative automatique.", "Servicestatus niet beschikbaar. Oude waarden zijn verborgen; automatische nieuwe poging.", "Stato non disponibile. I vecchi valori sono nascosti; nuovo tentativo automatico."],
    admin: ["Administrator access is required.", "Administrator-Zugriff erforderlich.", "Accès administrateur requis.", "Beheerderstoegang vereist.", "Accesso amministratore richiesto."],
    supervisor: ["Update Supervisor to a supported version to open this page.", "Für diese Seite Supervisor auf eine unterstützte Version aktualisieren.", "Mettez Supervisor à jour vers une version compatible.", "Werk Supervisor bij naar een ondersteunde versie.", "Aggiornare Supervisor a una versione supportata."],
    observed: ["Charging now", "Laden im Moment", "Recharge en cours", "Laden op dit moment", "Ricarica attuale"],
    power: ["Power", "Leistung", "Puissance", "Vermogen", "Potenza"],
    energy: ["Session energy", "Energie dieses Ladevorgangs", "Énergie de la session", "Energie van deze sessie", "Energia della sessione"],
    limit: ["Observed current limit", "Gelesene Stromvorgabe", "Limite de courant lue", "Uitgelezen stroomlimiet", "Limite di corrente letto"],
    unlimited: ["Unlimited (0 A)", "Unbegrenzt (0 A)", "Sans limite (0 A)", "Onbegrensd (0 A)", "Illimitato (0 A)"],
    pause_limit: ["Pause setpoint", "Pausenvorgabe", "Consigne de pause", "Pauze-instelling", "Impostazione di pausa"],
    next: ["Next queued action", "Nächste vorgemerkte Aktion", "Prochaine action en attente", "Volgende geplande actie", "Prossima azione in coda"],
    no_pending: ["No action is queued in the service.", "Im Service ist keine weitere Aktion vorgemerkt.", "Aucune action en attente dans le service.", "Er staat geen actie in de servicewachtrij.", "Nessuna azione in coda nel servizio."],
    not_before: ["Earliest execution", "Frühestmögliche Ausführung", "Exécution au plus tôt", "Vroegste uitvoering", "Prima esecuzione possibile"],
    no_due: ["No execution time confirmed.", "Kein Ausführungszeitpunkt bestätigt.", "Aucune heure d’exécution confirmée.", "Geen uitvoeringstijd bevestigd.", "Nessun orario di esecuzione confermato."],
    guard_hint: ["Queued does not mean executed. The earliest time can change; all control guards still apply.", "Vorgemerkt bedeutet nicht ausgeführt. Der früheste Zeitpunkt kann sich ändern; alle Steuerungsbedingungen bleiben wirksam.", "En attente ne signifie pas exécuté. L’heure peut changer ; toutes les protections restent actives.", "In de wachtrij is niet uitgevoerd. Het tijdstip kan veranderen; alle voorwaarden blijven gelden.", "In coda non significa eseguito. L’orario può cambiare; tutte le condizioni restano attive."],
    energy_target: ["Energy target", "Energieziel", "Objectif énergétique", "Energiedoel", "Obiettivo energetico"],
    soc_target: ["Battery target", "Batterieziel", "Objectif batterie", "Accudoel", "Obiettivo batteria"],
    disabled: ["Off", "Aus", "Désactivé", "Uit", "Disattivato"],
    plan_hint: ["Targets are saved limits, not progress. The car's current battery level stays in Home Assistant.", "Ziele sind gespeicherte Grenzen, kein Ladefortschritt. Der aktuelle Akkustand des Fahrzeugs bleibt in Home Assistant.", "Les objectifs sont des limites enregistrées, pas une progression. Le niveau actuel du véhicule reste dans Home Assistant.", "Doelen zijn opgeslagen grenzen, geen voortgang. De actuele accustand van het voertuig blijft in Home Assistant.", "Gli obiettivi sono limiti salvati, non progressi. Il livello attuale del veicolo resta in Home Assistant."],
    commands: ["Recent command results", "Letzte Befehlsergebnisse", "Derniers résultats de commandes", "Recente opdrachtresultaten", "Risultati recenti dei comandi"],
    history_hint: ["Up to 32 results since service start, newest first. Sequence numbers are not timestamps. This is not a session journal.", "Bis zu 32 Ergebnisse seit Service-Start, neueste zuerst. Laufende Nummern sind keine Zeitangaben. Dies ist kein Ladejournal.", "Jusqu’à 32 résultats depuis le démarrage, les plus récents d’abord. Les numéros ne sont pas des horaires. Ce n’est pas un journal de sessions.", "Maximaal 32 resultaten sinds servicestart, nieuwste eerst. Volgnummers zijn geen tijdstippen. Dit is geen sessielogboek.", "Fino a 32 risultati dall’avvio, dal più recente. I numeri non sono orari. Non è un registro delle sessioni."],
    no_commands: ["No command results yet.", "Noch keine Befehlsergebnisse.", "Aucun résultat pour le moment.", "Nog geen opdrachtresultaten.", "Nessun risultato disponibile."],
    footer: ["Charging: targets, next departure and guest charging. Vehicle setup and manual switches: Home Assistant.", "Laden: Ziele, nächste Abfahrt und Gastladen. Fahrzeugeinrichtung und manuelle Schalter: Home Assistant.", "Recharge : objectifs, prochain départ et invités. Profils et interrupteurs manuels : Home Assistant.", "Laden: doelen, volgend vertrek en gasten. Voertuigconfiguratie en handmatige schakelaars: Home Assistant.", "Ricarica: obiettivi, prossima partenza e ospiti. Profili e interruttori manuali: Home Assistant."],
    op_title: ["Current charging plan", "Aktueller Ladeablauf", "Plan de recharge actuel", "Huidig laadplan", "Piano di ricarica attuale"],
    op_wait: ["Why it waits", "Warum es wartet", "Pourquoi elle attend", "Waarom het wacht", "Perché attende"],
    op_next: ["Next step", "Nächster Schritt", "Prochaine étape", "Volgende stap", "Prossimo passo"],
    op_target: ["Target", "Ziel", "Cible", "Doel", "Obiettivo"],
    op_vehicle: ["Vehicle", "Fahrzeug", "Véhicule", "Voertuig", "Veicolo"],
    op_by: ["by", "bis", "d’ici", "tegen", "entro"],
    op_hint: ["Limits in % describe the battery; kWh describes additional energy at the wallbox.", "Ziele in % betreffen den Akku; kWh bedeutet zusätzliche Energie an der Wallbox.", "Les objectifs en % concernent la batterie ; les kWh sont l’énergie supplémentaire à la borne.", "Doelen in % gelden voor de accu; kWh is extra energie aan de laadpaal.", "Gli obiettivi in % riguardano la batteria; i kWh indicano energia aggiuntiva alla wallbox."],
    op_unavailable: ["Wallbox unavailable", "Wallbox nicht erreichbar", "Borne injoignable", "Wallbox niet bereikbaar", "Wallbox non raggiungibile"],
    op_guest: ["Guest charging", "Gastladen", "Recharge invité", "Gastladen", "Ricarica ospite"],
    op_departure_plan: ["Departure planning", "Abfahrtsplanung", "Planification du départ", "Vertrekplanning", "Pianificazione della partenza"],
    op_direct: ["Direct control", "Direkte Steuerung", "Commande directe", "Directe bediening", "Controllo diretto"],
    wait_none: ["Nothing is holding the charge", "Nichts hält das Laden auf", "Rien ne retient la recharge", "Niets houdt het laden tegen", "Nulla trattiene la ricarica"],
    wait_wallbox_unavailable: ["Wallbox unavailable", "Wallbox nicht erreichbar", "Borne injoignable", "Wallbox niet bereikbaar", "Wallbox non raggiungibile"],
    wait_no_vehicle: ["No vehicle connected", "Kein Fahrzeug angeschlossen", "Aucun véhicule connecté", "Geen voertuig aangesloten", "Nessun veicolo collegato"],
    wait_release_off: ["Charging release switched off", "Ladefreigabe ausgeschaltet", "Autorisation de recharge désactivée", "Laadvrijgave uitgeschakeld", "Abilitazione alla ricarica disattivata"],
    wait_plug_charge_waiting: ["Virtual Plug & Charge has not released the car", "Virtuelles Plug & Charge hat das Fahrzeug nicht freigegeben", "Le Plug & Charge virtuel n’a pas autorisé le véhicule", "Virtueel Plug & Charge heeft het voertuig niet vrijgegeven", "Il Plug & Charge virtuale non ha abilitato il veicolo"],
    wait_meter_unavailable: ["Waiting for the energy meter", "Wartet auf den Energiezähler", "En attente du compteur d’énergie", "Wacht op de energiemeter", "In attesa del contatore di energia"],
    wait_vehicle_unconfirmed: ["Car not confirmed", "Fahrzeug nicht bestätigt", "Véhicule non confirmé", "Voertuig niet bevestigd", "Veicolo non confermato"],
    wait_until_planned_start: ["Paused until the planned start", "Pausiert bis zum geplanten Start", "En pause jusqu’au démarrage prévu", "Gepauzeerd tot de geplande start", "In pausa fino all’avvio pianificato"],
    wait_estimated_target_reached: ["Charged to the estimated target", "Bis zum errechneten Ziel geladen", "Chargé jusqu’à l’objectif estimé", "Geladen tot het berekende doel", "Caricato fino all’obiettivo stimato"],
    wait_waiting_for_device: ["Waiting for wallbox", "Wartet auf Wallbox", "En attente de la borne", "Wacht op laadpaal", "In attesa wallbox"],
    wait_waiting_for_soc: ["Waiting for vehicle charge level", "Wartet auf Fahrzeugladestand", "En attente du niveau de charge", "Wacht op laadniveau", "In attesa livello di carica"],
    wait_waiting_for_capacity: ["Waiting for battery capacity", "Wartet auf Akkukapazität", "En attente de la capacité", "Wacht op accucapaciteit", "In attesa capacità"],
    wait_waiting_for_target: ["Waiting for percentage target", "Wartet auf Prozentziel", "En attente de la cible en %", "Wacht op procentdoel", "In attesa obiettivo %"],
    wait_read_only: ["Read only", "Nur lesen", "Lecture seule", "Alleen lezen", "Sola lettura"],
    wait_command_pending: ["Change queued", "Änderung vorgemerkt", "Modification en attente", "Wijziging in wachtrij", "Modifica in coda"],
    wait_target_reached: ["Target reached", "Ziel erreicht", "Cible atteinte", "Doel bereikt", "Obiettivo raggiunto"],
    wait_paused: ["Charging paused", "Laden pausiert", "Recharge en pause", "Laden gepauzeerd", "Ricarica in pausa"],
    step_none: ["Nothing scheduled", "Nichts geplant", "Rien de prévu", "Niets gepland", "Nulla in programma"],
    step_queued_command: ["Queued change runs", "Vorgemerkte Änderung wird ausgeführt", "La modification en attente s’exécute", "Wijziging in wachtrij wordt uitgevoerd", "La modifica in coda viene eseguita"],
    step_plan_start: ["Planned charge starts", "Geplantes Laden startet", "La recharge planifiée démarre", "Gepland laden start", "Inizia la ricarica pianificata"],
    step_target_reached: ["Target expected", "Ziel voraussichtlich erreicht", "Cible attendue", "Doel verwacht", "Obiettivo previsto"],
    step_departure: ["Target time", "Zielzeitpunkt", "Heure cible", "Doeltijd", "Orario obiettivo"],
    car_unknown: ["Vehicle status not confirmed", "Fahrzeugstatus nicht bestätigt", "État du véhicule non confirmé", "Voertuigstatus niet bevestigd", "Stato del veicolo non confermato"],
    car_none: ["Not connected", "Nicht angeschlossen", "Non connecté", "Niet aangesloten", "Non collegato"],
    car_connected: ["Connected", "Angeschlossen", "Connecté", "Aangesloten", "Collegato"],
    car_confirmed: ["Confirmed", "Bestätigt", "Confirmé", "Bevestigd", "Confermato"],
    readonly: ["Controls: read-only", "Steuerung: nur lesend", "Commandes : lecture seule", "Bediening: alleen lezen", "Comandi: sola lettura"],
    writable: ["Service writes enabled", "Service-Schreibzugriff aktiviert", "Écriture du service activée", "Schrijftoegang ingeschakeld", "Scrittura del servizio abilitata"],
    heartbeat: ["Heartbeat", "Heartbeat", "Heartbeat", "Heartbeat", "Heartbeat"],
    ok: ["confirmed", "bestätigt", "confirmé", "bevestigd", "confermato"],
    problem: ["not confirmed in time", "nicht rechtzeitig bestätigt", "non confirmé à temps", "niet op tijd bevestigd", "non confermato in tempo"],
    unknown: ["Not yet confirmed", "Noch nicht bestätigt", "Pas encore confirmé", "Nog niet bevestigd", "Non ancora confermato"],
    checking_status: ["Checking status", "Status wird abgeglichen", "Vérification de l’état", "Status wordt gecontroleerd", "Verifica dello stato"],
    state_uncertain: ["State is ambiguous", "Zustand nicht eindeutig", "État ambigu", "Status niet eenduidig", "Stato non univoco"],
    data_missing: ["Waiting for readings", "Warte auf Messwerte", "En attente de mesures", "Wachten op metingen", "In attesa di letture"],
    connection_lost: ["Connection interrupted", "Verbindung unterbrochen", "Connexion interrompue", "Verbinding verbroken", "Connessione interrotta"],
    inconsistent_readings: ["Required wallbox readings still disagree. Check the connection and diagnostics if this persists.", "Die benötigten Wallbox-Meldungen passen noch nicht zusammen. Bei anhaltender Anzeige Verbindung und Diagnose prüfen.", "Les mesures nécessaires de la borne divergent encore. Si cela persiste, vérifiez la connexion et le diagnostic.", "De benodigde wallboxmetingen komen nog niet overeen. Controleer bij aanhouden de verbinding en diagnose.", "Le letture necessarie della wallbox non concordano ancora. Se persiste, verificare connessione e diagnostica."],
    missing_readings: ["Required readings are missing; no previous state is shown as current.", "Benötigte Messwerte fehlen; ein früherer Zustand wird nicht als aktuell angezeigt.", "Des mesures nécessaires manquent ; aucun état précédent n’est affiché comme actuel.", "Benodigde metingen ontbreken; een eerdere status wordt niet als actueel getoond.", "Mancano letture necessarie; uno stato precedente non viene mostrato come attuale."],
    initializing: ["Initializing", "Initialisierung", "Initialisation", "Initialiseren", "Inizializzazione"],
    wallbox_error: ["Wallbox reports a fault", "Wallbox meldet einen Fehler", "La borne signale un défaut", "Wallbox meldt een storing", "La wallbox segnala un guasto"],
    service_mode: ["Wallbox in service mode", "Wallbox im Servicemodus", "Borne en mode maintenance", "Wallbox in servicemodus", "Wallbox in manutenzione"],
    no_vehicle: ["No vehicle connected", "Kein Fahrzeug angeschlossen", "Aucun véhicule connecté", "Geen voertuig aangesloten", "Nessun veicolo collegato"],
    charging: ["Charging", "Lädt", "Recharge en cours", "Aan het laden", "In ricarica"],
    locked: ["Wallbox locked", "Wallbox gesperrt", "Borne verrouillée", "Wallbox vergrendeld", "Wallbox bloccata"],
    release_disabled: ["Charging release off", "Ladefreigabe aus", "Autorisation de recharge désactivée", "Laadvrijgave uit", "Consenso alla ricarica disattivato"],
    paused: ["Charging paused", "Laden pausiert", "Recharge en pause", "Laden gepauzeerd", "Ricarica in pausa"],
    authorization_required: ["Local authorization required", "Lokale Autorisierung erforderlich", "Autorisation locale requise", "Lokale autorisatie vereist", "Autorizzazione locale richiesta"],
    preparing: ["Preparing to charge", "Ladung wird vorbereitet", "Préparation de la recharge", "Laden voorbereiden", "Preparazione della ricarica"],
    waiting_for_vehicle: ["Wallbox ready; waiting for vehicle", "Wallbox bereit; wartet auf Fahrzeug", "Borne prête ; attente du véhicule", "Wallbox gereed; wacht op voertuig", "Wallbox pronta; attesa del veicolo"],
    setup_required: ["Configure the connection first.", "Zuerst die Verbindung einrichten.", "Configurez d’abord la connexion.", "Stel eerst de verbinding in.", "Configurare prima la connessione."],
    service_starting: ["Service is initializing; no current wallbox state yet.", "Service initialisiert; noch kein aktueller Wallbox-Zustand.", "Service en initialisation ; état encore indisponible.", "Service initialiseert; nog geen actuele wallboxstatus.", "Servizio in inizializzazione; stato non ancora disponibile."],
    wallbox_unavailable: ["No current wallbox reading. Saved targets and queued intent are not proof of execution.", "Keine aktuelle Wallbox-Meldung. Gespeicherte Ziele und vorgemerkte Befehle belegen keine Ausführung.", "Aucune mesure actuelle. Les objectifs et commandes en attente ne prouvent pas leur exécution.", "Geen actuele wallboxmeting. Opgeslagen doelen en opdrachten bewijzen geen uitvoering.", "Nessuna lettura attuale. Obiettivi e comandi salvati non provano l’esecuzione."],
    goal_stop_requested: ["A charge target requires a stop; this is intent, not proof that charging has stopped.", "Ein Ladeziel fordert einen Stopp; das ist eine Anforderung, keine bestätigte Ladeunterbrechung.", "Un objectif demande l’arrêt ; cela ne prouve pas que la recharge est arrêtée.", "Een laaddoel vraagt een stop; dit bewijst niet dat het laden is gestopt.", "Un obiettivo richiede uno stop; non prova che la ricarica sia interrotta."],
    release_stop_requested: ["The service retains a request to disable charging release.", "Im Service ist das Abschalten der Ladefreigabe angefordert.", "Le service conserve une demande de désactivation de la recharge.", "De service bewaart een verzoek om laadvrijgave uit te zetten.", "Il servizio conserva una richiesta di disattivazione del consenso."],
    pause_requested: ["The service retains a pause request. Its source is not supplied by this snapshot.", "Im Service ist eine Pause angefordert. Ihre Herkunft ist in diesem Zustand nicht enthalten.", "Le service conserve une demande de pause. Sa source n’est pas fournie.", "De service bewaart een pauzeverzoek. De bron staat niet in deze status.", "Il servizio conserva una richiesta di pausa. La fonte non è disponibile."],
    plug_charge_waiting: ["Plug & Charge has no grant for this connection. Check vehicle recognition in Home Assistant.", "Plug & Charge hat für diese Verbindung keine Freigabe. Fahrzeugerkennung in Home Assistant prüfen.", "Plug & Charge n’a pas autorisé cette connexion. Vérifiez le véhicule dans Home Assistant.", "Plug & Charge heeft geen vrijgave voor deze verbinding. Controleer herkenning in Home Assistant.", "Plug & Charge non ha autorizzato questa connessione. Verificare il veicolo in Home Assistant."],
    pause: ["Pause", "Pausieren", "Pause", "Pauzeren", "Pausa"],
    resume: ["Resume", "Fortsetzen", "Reprendre", "Hervatten", "Riprendere"],
    set_current: ["Set charging current", "Ladestrom setzen", "Régler le courant", "Laadstroom instellen", "Impostare corrente"],
    single_phase: ["Select one phase", "Einphasig umschalten", "Passer en monophasé", "Naar één fase", "Passare a una fase"],
    all_phases: ["Select all phases", "Alle Phasen auswählen", "Activer toutes les phases", "Alle fasen kiezen", "Selezionare tutte le fasi"],
    disable_charging: ["Disable charging release", "Ladefreigabe ausschalten", "Désactiver la recharge", "Laadvrijgave uitzetten", "Disattivare consenso"],
    enable_charging: ["Enable charging release", "Ladefreigabe einschalten", "Autoriser la recharge", "Laadvrijgave aanzetten", "Abilitare consenso"],
    lock: ["Lock wallbox", "Wallbox sperren", "Verrouiller la borne", "Wallbox vergrendelen", "Bloccare wallbox"],
    unlock: ["Unlock wallbox", "Wallbox entsperren", "Déverrouiller la borne", "Wallbox ontgrendelen", "Sbloccare wallbox"],
    recover: ["Recover connection state", "Zustand wiederherstellen", "Restaurer l’état", "Toestand herstellen", "Ripristinare stato"],
    executed: ["Command completed", "Befehl abgeschlossen", "Commande terminée", "Opdracht voltooid", "Comando completato"],
    replaced: ["Replaced", "Ersetzt", "Remplacé", "Vervangen", "Sostituito"],
    cancelled: ["Cancelled", "Verworfen", "Annulé", "Geannuleerd", "Annullato"],
    failed: ["Failed", "Fehlgeschlagen", "Échec", "Mislukt", "Fallito"],
    uncertain: ["Outcome uncertain", "Ergebnis ungewiss", "Résultat incertain", "Uitkomst onzeker", "Esito incerto"],
    recovery_succeeded: ["Recovery confirmed", "Wiederherstellung bestätigt", "Restauration confirmée", "Herstel bevestigd", "Ripristino confermato"],
    recovery_failed: ["Recovery failed", "Wiederherstellung fehlgeschlagen", "Échec de restauration", "Herstel mislukt", "Ripristino fallito"],
    completed: ["Acknowledged", "Bestätigt", "Confirmé", "Bevestigd", "Confermato"],
    shutdown: ["Service stopped", "Service beendet", "Service arrêté", "Service gestopt", "Servizio arrestato"],
    phase_timeout: ["Phase-switch timeout", "Zeitüberschreitung beim Phasenwechsel", "Délai de changement de phase dépassé", "Time-out fasewisseling", "Timeout cambio fase"],
    phase_unconfirmed: ["Phase switch unconfirmed", "Phasenwechsel nicht bestätigt", "Changement de phase non confirmé", "Fasewisseling niet bevestigd", "Cambio fase non confermato"],
    connection_error: ["Communication error", "Kommunikationsfehler", "Erreur de communication", "Communicatiefout", "Errore di comunicazione"],
    write_rejected: ["Write rejected", "Schreiben abgelehnt", "Écriture refusée", "Schrijven geweigerd", "Scrittura rifiutata"],
    permission_denied: ["Permission denied", "Berechtigung fehlt", "Permission refusée", "Toestemming geweigerd", "Permesso negato"],
    status_refresh_failed: ["Status refresh failed", "Statusabgleich fehlgeschlagen", "Actualisation échouée", "Statusvernieuwing mislukt", "Aggiornamento stato fallito"],
    error_still_active: ["Fault still active", "Fehler weiterhin aktiv", "Défaut toujours actif", "Storing nog actief", "Guasto ancora attivo"],
    user_cancelled: ["Cancelled by user", "Vom Benutzer verworfen", "Annulé par l’utilisateur", "Door gebruiker geannuleerd", "Annullato dall’utente"],
    energy_goal: ["Stop requested by a charge target", "Ladestopp durch ein Ladeziel", "Arrêt demandé par un objectif de charge", "Laadstop door een laaddoel", "Arresto richiesto da un obiettivo di ricarica"],
    guest_confirmation: ["Guest start requires confirmation", "Gaststart muss bestätigt werden", "Confirmez le démarrage invité", "Bevestig het starten voor de gast", "Conferma avvio ospite"],
    time_limit: ["Time limit reached", "Zeitgrenze erreicht", "Limite horaire atteinte", "Tijdslimiet bereikt", "Limite orario raggiunto"],
    deadline_expired: ["Deadline expired", "Gültigkeit abgelaufen", "Échéance dépassée", "Deadline verstreken", "Scadenza superata"]
  };
  const text = key => words[key]?.[column] ?? words.unknown[column];
  document.querySelectorAll("[data-overview]").forEach(node => { node.textContent = text(node.dataset.overview); });
  // The page shows once its texts are in the page language.
  document.documentElement.classList?.add("ready");
  const el = id => document.getElementById(id);
  if (!el("overview")) return;
  const number = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const format = (value, unit, digits = 1) => number(value)
    ? `${value.toLocaleString(languages[column], { maximumFractionDigits: digits })} ${unit}` : "—";
  const put = (id, value) => { el(id).textContent = value; };
  const moment = stamp => {
    const time = typeof stamp === "string" ? new Date(stamp) : null;
    return time && Number.isFinite(time.getTime())
      ? time.toLocaleString(languages[column], { timeZoneName: "short" }) : null;
  };
  function render(data) {
    if (!data || data.schema !== 1 || typeof data.available !== "boolean" || !data.measurements || !data.targets
        || !Array.isArray(data.commands) || data.commands.length > 32
        || (data.operation && !(data.operation.next && data.operation.target))) throw new Error("invalid overview");
    put("charge_state", text(data.state));
    el("charge_state").className="state charging-state "+(["setup_required","service_starting"].includes(data.state)?"state-muted":!data.available?"state-error":data.state==="charging"?"state-active":data.state==="wallbox_error"?"state-error":["paused","locked","release_disabled","authorization_required","preparing","waiting_for_vehicle","state_uncertain","data_missing"].includes(data.state)?"state-wait":"state-muted");
    put("charge_reason", text(data.reason));
    el("charge_reason").hidden=text(data.reason)===text(data.state);
    put("service_mode", text(data.read_only === true ? "readonly" : data.read_only === false ? "writable" : "unknown"));
    put("heartbeat_state", `${text("heartbeat")}: ${text(data.heartbeat)}`);
    const measurement = data.available ? data.measurements : {};
    put("power", format(number(measurement.power_w) ? measurement.power_w / 1000 : null, "kW"));
    put("energy", format(measurement.session_kwh, "kWh", 2));
    const current = measurement.current_limit_a;
    put("current_limit", current === 0 ? text("unlimited")
      : number(current) && current < 6 ? `${text("pause_limit")} (${format(current, "A", 2)})` : format(current, "A", 2));
    put("next_action", data.pending ? text(data.pending.action) : text("no_pending"));
    const due = moment(data.pending?.not_before);
    put("next_due", due ? `${text("not_before")}: ${due}` : data.pending ? text("no_due") : "");
    const summary = data.operation;
    el("operation").hidden = !summary;
    if (summary) {
      put("op_controller", text(`op_${summary.controller}`));
      put("op_waiting", text(`wait_${summary.waiting}`));
      const advice = {
        plug_charge_waiting: "vehicle", vehicle_unconfirmed: "vehicle", no_vehicle: "connect",
        waiting_for_soc: "plan", waiting_for_capacity: "plan", waiting_for_target: "plan",
        until_planned_start: "wait", command_pending: "wait",
        paused: "pause", release_off: "pause", read_only: "readonly",
        wallbox_unavailable: "connection", waiting_for_device: "connection", meter_unavailable: "connection",
        target_reached: "target", estimated_target_reached: "target",
      }[summary.waiting];
      put("op_advice", data.reason === "intent_rejected" ? text("intent_rejected") : advice ? text(`advice_${advice}`) : "");
      const at = moment(summary.next.at);
      put("op_step", at ? `${text(`step_${summary.next.event}`)} · ${at}` : text("step_none"));
      const target = summary.target, by = moment(target.deadline);
      const amount = target.kind === "level" ? format(target.value, "%") : format(target.value, "kWh", 2);
      put("op_goal", target.kind === "none" ? text("disabled") : by ? `${amount} ${text("op_by")} ${by}` : amount);
      put("op_car", text(`car_${summary.vehicle}`));
    }
    put("energy_target", data.targets.energy_kwh === 0 ? text("disabled") : format(data.targets.energy_kwh, "kWh", 2));
    put("soc_target", data.targets.soc_percent === 0 ? text("disabled") : format(data.targets.soc_percent, "%"));
    const rows = data.commands.filter(command => !(command.action === "set_current" && command.outcome === "replaced")).map(command => {
      if (!Number.isSafeInteger(command.sequence) || command.sequence <= 0) throw new Error("invalid sequence");
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = `${moment(command.occurred_at) || "—"} · ${text(command.action)}${["resume", "set_current"].includes(command.action) && command.requested_value != null ? ` (${text("requested_current")}: ${command.requested_value === 0 ? text("unlimited") : format(command.requested_value, "A")})` : ""} — ${text(command.outcome)}`;
      const reason = document.createElement("small");
      reason.textContent = `${text(`source_${command.source || "unknown"}`)}${command.reason === "completed" ? "" : ` · ${text(command.reason)}`}`;
      item.appendChild(title); item.appendChild(reason);
      return item;
    });
    el("command_results").replaceChildren(...rows);
    el("commands_empty").hidden = rows.length > 0;
    el("overview_data").hidden = false;
    put("overview_notice", text("live"));
  }
  let timer = null, controller = null, generation = 0, stopped = false;
  const visible = () => !document.hidden && !stopped;
  function suspend() {
    generation += 1;
    clearTimeout(timer); timer = null;
    if (controller) controller.abort();
    controller = null;
  }
  async function refresh() {
    if (!visible() || controller) return;
    const own = ++generation;
    const abort = new AbortController();
    controller = abort;
    const timeout = setTimeout(() => abort.abort(), 8000);
    let error = "offline";
    try {
      const response = await fetch("./api/overview", { cache: "no-store", signal: abort.signal });
      const data = await response.json();
      if (!response.ok) {
        error = response.status === 403 ? "admin" : data.error === "supervisor_update_required" ? "supervisor" : "offline";
        throw new Error("overview unavailable");
      }
      if (visible() && own === generation) render(data);
    } catch (_) {
      if (visible() && own === generation) {
        el("overview_data").hidden = true;
        put("overview_notice", text(error));
      }
    } finally {
      clearTimeout(timeout);
      if (own === generation) {
        controller = null;
        if (visible()) timer = setTimeout(refresh, 5000);
      }
    }
  }
  function resume() {
    suspend();
    el("overview_data").hidden = true;
    put("overview_notice", text("loading"));
    if (visible()) void refresh();
  }
  document.addEventListener("visibilitychange", resume);
  addEventListener("pagehide", () => { stopped = true; suspend(); });
  addEventListener("pageshow", () => { stopped = false; resume(); });
  void refresh();
})();
