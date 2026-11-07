"use strict";

const messages = {
  en: {
    title: "Wallbox connection", intro: "Choose the adapter used by this app. Charging permissions and other settings remain in the app Configuration tab.",
    loading: "Loading settings…", transport: "Connection", usb: "USB / RS-485", rtu: "LAN: Modbus RTU over TCP", tcp: "LAN: Modbus TCP",
    usb_settings: "USB adapter", adapter: "Serial adapter", choose: "Select an adapter", missing: "Previously selected adapter is not connected",
    adapter_hint: "Choose your adapter from the list. Stable device names are preferred automatically; an adapter is never selected for you.",
    baudrate: "Baud rate", frame: "Serial format", lan_settings: "LAN adapter", host: "Adapter IP address or hostname", port: "Adapter TCP port",
    lan_hint: "Set the serial baud rate and format on the LAN adapter. Saving LAN settings removes any old USB adapter selection.", address: "Wallbox Modbus address",
    restart_hint: "Saving keeps the current connection running. Restart the app afterwards to apply the change; this briefly interrupts its heartbeat.",
    reload: "Reload settings and adapters", save: "Save connection", saving: "Saving…", saved: "Saved. Restart this app from its Info tab to apply the new connection.",
    security: "For Home Assistant administrators with Supervisor 2026.11.0 or newer. No service token is needed here.",
    supervisor_update_required: "Update Home Assistant Supervisor to 2026.11.0 or newer to use this configuration page. The native app Configuration tab remains available.",
    admin_required: "Sign in to Home Assistant as an administrator to change this connection.", reload_required: "Reload this page before saving again.",
    settings_changed: "Settings changed elsewhere. Reload them before saving again; your changes have not been saved.", adapter_missing: "The selected USB adapter is missing. Reconnect it or reload the adapter list.",
    connection_invalid: "Check the connection settings, address and port. The existing configuration has not been changed.",
    invalid_request: "Check the connection settings and try again.", busy: "Another request is running. Try again shortly.", supervisor_unavailable: "The settings could not be loaded or saved. Check the app and Supervisor, then reload this page.",
    detect_adapter: "Set up LAN adapter",
    detecting: "Reading adapter settings…",
    adapter_detected: "Recognized adapter",
    adapter_target: "Adapter serial settings",
    adapter_current: "Current settings",
    apply_adapter: "Apply adapter settings when saving",
    adapter_apply_hint: "Saving applies the selected TCP port, connection mode, login and serial settings to the adapter immediately. Its existing web password is not changed. This also selects passive TCP-server operation and disables the adapter’s own Modbus polling/cache, flow control, connection preambles, replay of buffered serial data, automatic parameter uploads and data-triggered restarts.",
    auth_hint: "Use the existing web password of a compatible adapter. If the adapter is already configured manually, leave “Apply adapter settings” off.",
    auth_security: "Login does not encrypt the traffic. Firmware 1.523 allows bypassing it; use a protected local network.",
    password_required: "Use the current web password: 1–9 printable ASCII characters without spaces. This does not change the password.",
    password_saved: "A password is already saved for this host and port. Leave this field empty to keep it.",
    adapter_restart_hint: "Saving applies adapter settings immediately and stops the old app connection. Restart the app afterwards to resume the heartbeat with the new settings.",
    saved_adapter: "Adapter settings applied and connection saved. The old connection is stopped. Restart this app from its Info tab to resume the heartbeat.",
    adapter_detection_required: "Read the adapter settings again before applying changes.",
    adapter_save_uncertain: "The adapter and saved app settings could not be confirmed together. The connection is stopped. Reload the settings and identify the adapter again before deciding how to reconnect; do not restart blindly.",
    options_save_uncertain: "A previous save may still complete, even after an app restart. Further saves are blocked during this app session. Check the saved connection and adapter before reconnecting.",
    gateway_authentication_failed: "The adapter rejected its existing web password. Check the password used for its web interface.",
    tcp_authentication: "TCP login",
    tcp_auth_password: "Existing adapter web password",
    gateway_unsupported: "This adapter model, firmware or operating mode cannot be configured automatically. Configure the adapter manually, then save the connection.",
    gateway_unreachable: "The adapter settings could not be read. Check the address and local network access, or use manual configuration.",
    gateway_changed: "The adapter settings changed since this preview. Start the connection setup again to read the current settings.",
    gateway_apply_failed: "The adapter settings could not be applied and verified. Check the existing web password and adapter connection.",
    gateway_rollback_failed: "The previous adapter settings could not be confirmed after a failed change. The service connection remains stopped. Check the adapter settings manually before reconnecting."
  },
  de: {
    title: "Verbindung zur Wallbox", intro: "Wähle den Adapter für diese App. Schreibfreigabe und weitere Einstellungen bleiben im Reiter Konfiguration der App.",
    loading: "Einstellungen werden geladen…", transport: "Verbindung", usb: "USB / RS-485", rtu: "LAN: Modbus RTU über TCP", tcp: "LAN: Modbus TCP",
    usb_settings: "USB-Adapter", adapter: "Serieller Adapter", choose: "Adapter auswählen", missing: "Bisheriger Adapter ist nicht angeschlossen",
    adapter_hint: "Wähle deinen Adapter aus der Liste. Stabile Gerätenamen werden bevorzugt. Es wird kein Adapter automatisch ausgewählt.",
    baudrate: "Baudrate", frame: "Serielles Format", lan_settings: "LAN-Adapter", host: "IP-Adresse oder Hostname des Adapters", port: "TCP-Port des Adapters",
    lan_hint: "Baudrate und serielles Format am LAN-Adapter einstellen. Beim Speichern einer LAN-Verbindung wird die alte USB-Auswahl entfernt.", address: "Modbus-Adresse der Wallbox",
    restart_hint: "Beim Speichern läuft die bisherige Verbindung weiter. Starte danach die App neu, damit die Änderung wirksam wird. Der Neustart unterbricht kurz den Heartbeat.",
    reload: "Einstellungen und Adapter neu laden", save: "Verbindung speichern", saving: "Wird gespeichert…", saved: "Gespeichert. Starte diese App im Reiter Info neu, um die neue Verbindung zu übernehmen.",
    security: "Für Home-Assistant-Administratoren mit Supervisor ab 2026.11.0. Ein Dienst-Token ist hier nicht erforderlich.",
    supervisor_update_required: "Aktualisiere Home Assistant Supervisor auf mindestens 2026.11.0, um diese Seite zu nutzen. Der normale Reiter Konfiguration der App bleibt verfügbar.",
    admin_required: "Melde dich bei Home Assistant als Administrator an, um die Verbindung zu ändern.", reload_required: "Lade diese Seite vor dem erneuten Speichern neu.",
    settings_changed: "Die Einstellungen wurden anderswo geändert. Lade sie erneut, bevor du speicherst. Deine Änderungen wurden nicht gespeichert.", adapter_missing: "Der ausgewählte USB-Adapter fehlt. Schließe ihn wieder an oder lade die Adapterliste neu.",
    connection_invalid: "Prüfe Verbindungseinstellungen, Adresse und Port. Die bisherige Konfiguration wurde nicht geändert.",
    invalid_request: "Prüfe die Verbindungseinstellungen und versuche es erneut.", busy: "Eine andere Anfrage läuft noch. Versuche es gleich noch einmal.", supervisor_unavailable: "Die Einstellungen konnten nicht geladen oder gespeichert werden. Prüfe App und Supervisor und lade diese Seite erneut.",
    detect_adapter: "LAN-Adapter einrichten",
    detecting: "Adaptereinstellungen werden gelesen…",
    adapter_detected: "Erkannter Adapter",
    adapter_target: "Serielle Einstellungen des Adapters",
    adapter_current: "Aktuelle Einstellungen",
    apply_adapter: "Adaptereinstellungen beim Speichern anwenden",
    adapter_apply_hint: "Beim Speichern werden der gewählte TCP-Port, Verbindungsmodus, die Anmeldung und die seriellen Einstellungen sofort am Adapter angewendet. Das vorhandene Webpasswort wird nicht geändert. Außerdem wird der passive TCP-Server-Betrieb eingerichtet: Eigene Modbus-Abfragen und Antwortspeicher, Flusssteuerung, zusätzliche Anmeldedaten, Wiedergabe gepufferter serieller Daten, automatische Parameterübertragung und datenabhängige Neustarts werden deaktiviert.",
    auth_hint: "Nutze das vorhandene Webpasswort eines kompatiblen Adapters. Ist der Adapter bereits manuell eingerichtet, lasse „Adaptereinstellungen anwenden“ ausgeschaltet.",
    auth_security: "Die Anmeldung verschlüsselt den Datenverkehr nicht. Bei Firmware 1.523 ist sie umgehbar; verwende ein geschütztes lokales Netz.",
    password_required: "Aktuelles Webpasswort: 1–9 druckbare ASCII-Zeichen ohne Leerzeichen. Das Passwort wird nicht geändert.",
    password_saved: "Für diesen Host und Port ist bereits ein Passwort gespeichert. Leer lassen, um es beizubehalten.",
    adapter_restart_hint: "Beim Speichern werden die Adaptereinstellungen sofort geändert und die alte App-Verbindung angehalten. Starte die App danach neu, damit der Heartbeat mit den neuen Einstellungen weiterläuft.",
    saved_adapter: "Adaptereinstellungen angewendet und Verbindung gespeichert. Die alte Verbindung ist angehalten. Starte diese App im Reiter Info neu, damit der Heartbeat weiterläuft.",
    adapter_detection_required: "Lies die Adaptereinstellungen vor dem Anwenden erneut ein.",
    adapter_save_uncertain: "Adapter und gespeicherte App-Einstellungen konnten nicht gemeinsam bestätigt werden. Die Verbindung ist angehalten. Lade die Einstellungen neu und erkenne den Adapter erneut, bevor du die Verbindung wiederherstellst. Nicht ungeprüft neu starten.",
    options_save_uncertain: "Ein vorheriger Speichervorgang kann auch nach einem App-Neustart noch abgeschlossen werden. Weitere Änderungen sind während dieser App-Laufzeit gesperrt. Prüfe die gespeicherte Verbindung und den Adapter vor dem Wiederverbinden.",
    gateway_authentication_failed: "Der Adapter hat sein vorhandenes Webpasswort abgelehnt. Prüfe das Passwort für seine Weboberfläche.",
    tcp_authentication: "TCP-Anmeldung",
    tcp_auth_password: "Vorhandenes Webpasswort des Adapters",
    gateway_unsupported: "Dieses Adaptermodell, diese Firmware oder Betriebsart lässt sich nicht automatisch einrichten. Richte den Adapter manuell ein und speichere anschließend die Verbindung.",
    gateway_unreachable: "Die Adaptereinstellungen konnten nicht gelesen werden. Prüfe Adresse und lokalen Netzwerkzugriff oder nutze die manuelle Einrichtung.",
    gateway_changed: "Die Adaptereinstellungen haben sich seit dieser Vorschau geändert. Starte die Verbindungseinrichtung erneut, um die aktuellen Werte einzulesen.",
    gateway_apply_failed: "Die Adaptereinstellungen konnten nicht angewendet und bestätigt werden. Prüfe das vorhandene Webpasswort und die Verbindung zum Adapter.",
    gateway_rollback_failed: "Nach der fehlgeschlagenen Änderung konnten die bisherigen Adaptereinstellungen nicht bestätigt werden. Die Dienstverbindung bleibt angehalten. Prüfe die Adaptereinstellungen manuell, bevor du die Verbindung wiederherstellst."
  },
  fr: {
    title: "Connexion à la borne", intro: "Choisissez l’adaptateur de cette application. Les autorisations d’écriture et les autres réglages restent dans l’onglet Configuration.",
    loading: "Chargement des réglages…", transport: "Connexion", usb: "USB / RS-485", rtu: "LAN : Modbus RTU sur TCP", tcp: "LAN : Modbus TCP",
    usb_settings: "Adaptateur USB", adapter: "Adaptateur série", choose: "Sélectionner un adaptateur", missing: "L’adaptateur précédent est débranché",
    adapter_hint: "Choisissez votre adaptateur dans la liste. Les noms stables sont privilégiés ; aucun adaptateur n’est sélectionné automatiquement.",
    baudrate: "Vitesse de transmission", frame: "Format série", lan_settings: "Adaptateur LAN", host: "Adresse IP ou nom d’hôte de l’adaptateur", port: "Port TCP de l’adaptateur",
    lan_hint: "Réglez la vitesse et le format série sur l’adaptateur LAN. L’enregistrement supprime l’ancienne sélection USB.", address: "Adresse Modbus de la borne",
    restart_hint: "L’enregistrement laisse la connexion actuelle active. Redémarrez ensuite l’application pour appliquer le changement ; le heartbeat sera brièvement interrompu.",
    reload: "Recharger les réglages et adaptateurs", save: "Enregistrer la connexion", saving: "Enregistrement…", saved: "Enregistré. Redémarrez cette application depuis l’onglet Infos pour appliquer la connexion.",
    security: "Pour les administrateurs Home Assistant avec Supervisor 2026.11.0 ou ultérieur. Aucun jeton de service n’est nécessaire ici.",
    supervisor_update_required: "Mettez Supervisor à jour vers 2026.11.0 ou ultérieur. L’onglet Configuration habituel reste disponible.", admin_required: "Connectez-vous à Home Assistant en tant qu’administrateur.",
    reload_required: "Rechargez cette page avant d’enregistrer.", settings_changed: "Les réglages ont changé ailleurs. Rechargez-les avant d’enregistrer ; vos changements n’ont pas été enregistrés.",
    adapter_missing: "L’adaptateur USB sélectionné est absent. Rebranchez-le ou rechargez la liste.", connection_invalid: "Vérifiez les réglages, l’adresse et le port. La configuration existante est inchangée.",
    invalid_request: "Vérifiez les réglages de connexion.", busy: "Une autre requête est en cours. Réessayez dans un instant.", supervisor_unavailable: "Impossible de charger ou d’enregistrer. Vérifiez l’application et Supervisor, puis rechargez la page.",
    detect_adapter: "Configurer l’adaptateur LAN",
    detecting: "Lecture des réglages de l’adaptateur…",
    adapter_detected: "Adaptateur reconnu",
    adapter_target: "Réglages série de l’adaptateur",
    adapter_current: "Réglages actuels",
    apply_adapter: "Appliquer les réglages de l’adaptateur lors de l’enregistrement",
    adapter_apply_hint: "L’enregistrement applique immédiatement le port TCP, le mode, l’authentification et les réglages série sélectionnés. Le mot de passe web actuel n’est pas modifié. Le mode serveur TCP passif est également activé : interrogation/cache Modbus interne, contrôle de flux, préambules de connexion, répétition des données série mémorisées, envoi automatique des paramètres et redémarrages déclenchés par les données sont désactivés.",
    auth_hint: "Utilisez le mot de passe web actuel d’un adaptateur compatible. Si l’adaptateur est déjà configuré manuellement, laissez l’application des réglages désactivée.",
    auth_security: "L’authentification ne chiffre pas les données. Elle peut être contournée avec le firmware 1.523 ; utilisez un réseau local protégé.",
    password_required: "Mot de passe web actuel : 1 à 9 caractères ASCII imprimables, sans espaces. Le mot de passe n’est pas modifié.",
    password_saved: "Un mot de passe est enregistré pour cet hôte et ce port. Laissez ce champ vide pour le conserver.",
    adapter_restart_hint: "L’enregistrement modifie immédiatement l’adaptateur et arrête l’ancienne connexion. Redémarrez ensuite l’application pour reprendre le heartbeat avec les nouveaux réglages.",
    saved_adapter: "Réglages appliqués et connexion enregistrée. L’ancienne connexion est arrêtée. Redémarrez cette application depuis Infos pour reprendre le heartbeat.",
    adapter_detection_required: "Relisez les réglages de l’adaptateur avant d’appliquer les modifications.",
    adapter_save_uncertain: "Impossible de confirmer la cohérence entre l’adaptateur et les réglages enregistrés. La connexion est arrêtée. Rechargez les réglages et identifiez à nouveau l’adaptateur avant de vous reconnecter ; ne redémarrez pas sans vérification.",
    options_save_uncertain: "Un enregistrement précédent peut encore aboutir, même après un redémarrage de l’application. Les autres enregistrements sont bloqués pendant cette session. Vérifiez la connexion enregistrée et l’adaptateur avant de vous reconnecter.",
    gateway_authentication_failed: "L’adaptateur a refusé son mot de passe web actuel. Vérifiez le mot de passe de son interface web.",
    tcp_authentication: "Authentification TCP",
    tcp_auth_password: "Mot de passe web actuel de l’adaptateur",
    gateway_unsupported: "Ce modèle, firmware ou mode ne peut pas être configuré automatiquement. Configurez l’adaptateur manuellement, puis enregistrez la connexion.",
    gateway_unreachable: "Impossible de lire les réglages. Vérifiez l’adresse et l’accès au réseau local ou utilisez la configuration manuelle.",
    gateway_changed: "Les réglages ont changé depuis cet aperçu. Recommencez la configuration de la connexion pour lire les valeurs actuelles.",
    gateway_apply_failed: "Les réglages n’ont pas pu être appliqués et vérifiés. Vérifiez le mot de passe web actuel et la connexion à l’adaptateur.",
    gateway_rollback_failed: "Impossible de confirmer les anciens réglages après l’échec. La connexion du service reste arrêtée. Vérifiez manuellement l’adaptateur avant de vous reconnecter."
  },
  nl: {
    title: "Verbinding met de laadpaal", intro: "Kies de adapter voor deze app. Schrijfrechten en overige instellingen blijven op het tabblad Configuratie.",
    loading: "Instellingen laden…", transport: "Verbinding", usb: "USB / RS-485", rtu: "LAN: Modbus RTU via TCP", tcp: "LAN: Modbus TCP",
    usb_settings: "USB-adapter", adapter: "Seriële adapter", choose: "Kies een adapter", missing: "De eerder gekozen adapter is niet aangesloten",
    adapter_hint: "Kies je adapter uit de lijst. Stabiele apparaatnamen hebben de voorkeur; er wordt nooit automatisch een adapter gekozen.",
    baudrate: "Baudsnelheid", frame: "Serieel formaat", lan_settings: "LAN-adapter", host: "IP-adres of hostnaam van de adapter", port: "TCP-poort van de adapter",
    lan_hint: "Stel baudsnelheid en serieel formaat in op de LAN-adapter. Opslaan verwijdert de oude USB-selectie.", address: "Modbus-adres van de laadpaal",
    restart_hint: "Opslaan laat de huidige verbinding actief. Herstart daarna de app om de wijziging toe te passen; dit onderbreekt de heartbeat kort.",
    reload: "Instellingen en adapters herladen", save: "Verbinding opslaan", saving: "Opslaan…", saved: "Opgeslagen. Herstart deze app op het tabblad Info om de verbinding toe te passen.",
    security: "Voor Home Assistant-beheerders met Supervisor 2026.11.0 of nieuwer. Hier is geen servicetoken nodig.",
    supervisor_update_required: "Werk Supervisor bij naar 2026.11.0 of nieuwer. Het gewone tabblad Configuratie blijft beschikbaar.", admin_required: "Meld je aan bij Home Assistant als beheerder.",
    reload_required: "Herlaad deze pagina voordat je opnieuw opslaat.", settings_changed: "De instellingen zijn elders gewijzigd. Herlaad ze; je wijzigingen zijn niet opgeslagen.",
    adapter_missing: "De gekozen USB-adapter ontbreekt. Sluit hem aan of herlaad de lijst.", connection_invalid: "Controleer de verbinding, het adres en de poort. De bestaande configuratie is niet gewijzigd.",
    invalid_request: "Controleer de verbindingsinstellingen.", busy: "Een ander verzoek loopt nog. Probeer het zo opnieuw.", supervisor_unavailable: "Laden of opslaan is mislukt. Controleer de app en Supervisor en herlaad deze pagina.",
    detect_adapter: "LAN-adapter instellen",
    detecting: "Adapterinstellingen lezen…",
    adapter_detected: "Herkende adapter",
    adapter_target: "Seriële adapterinstellingen",
    adapter_current: "Huidige instellingen",
    apply_adapter: "Adapterinstellingen toepassen bij opslaan",
    adapter_apply_hint: "Opslaan past de gekozen TCP-poort, verbindingsmodus, aanmelding en seriële instellingen direct toe. Het huidige webwachtwoord wordt niet gewijzigd. Ook wordt de passieve TCP-servermodus ingesteld. Eigen Modbus-uitlezing/cache, flowcontrole, verbindingsvoorvoegsels, herhalen van gebufferde seriële gegevens, automatische parameteruploads en door gegevens geactiveerde herstarts worden uitgeschakeld.",
    auth_hint: "Gebruik het huidige webwachtwoord van een compatibele adapter. Laat toepassen uitgeschakeld als de adapter al handmatig is ingesteld.",
    auth_security: "Aanmelding versleutelt het verkeer niet. Bij firmware 1.523 is deze te omzeilen; gebruik een beschermd lokaal netwerk.",
    password_required: "Huidig webwachtwoord: 1–9 afdrukbare ASCII-tekens zonder spaties. Het wachtwoord wordt niet gewijzigd.",
    password_saved: "Voor deze host en poort is al een wachtwoord opgeslagen. Laat dit veld leeg om het te behouden.",
    adapter_restart_hint: "Opslaan wijzigt de adapter direct en stopt de oude appverbinding. Herstart daarna de app om de heartbeat met de nieuwe instellingen te hervatten.",
    saved_adapter: "Adapterinstellingen toegepast en verbinding opgeslagen. De oude verbinding is gestopt. Herstart deze app op het tabblad Info om de heartbeat te hervatten.",
    adapter_detection_required: "Lees de adapterinstellingen opnieuw voordat je wijzigingen toepast.",
    adapter_save_uncertain: "De adapter en opgeslagen appinstellingen konden niet samen worden bevestigd. De verbinding is gestopt. Herlaad de instellingen en herken de adapter opnieuw voordat je opnieuw verbinding maakt; herstart niet zonder controle.",
    options_save_uncertain: "Een eerdere opslag kan nog worden voltooid, ook na een herstart van de app. Verder opslaan is tijdens deze appsessie geblokkeerd. Controleer de opgeslagen verbinding en adapter voordat je opnieuw verbinding maakt.",
    gateway_authentication_failed: "De adapter heeft zijn huidige webwachtwoord geweigerd. Controleer het wachtwoord voor de webinterface.",
    tcp_authentication: "TCP-aanmelding",
    tcp_auth_password: "Huidig webwachtwoord van de adapter",
    gateway_unsupported: "Dit model, deze firmware of bedrijfsmodus kan niet automatisch worden ingesteld. Stel de adapter handmatig in en sla daarna de verbinding op.",
    gateway_unreachable: "De adapterinstellingen konden niet worden gelezen. Controleer het adres en de lokale netwerktoegang of stel de adapter handmatig in.",
    gateway_changed: "De instellingen zijn gewijzigd sinds dit voorbeeld. Start de verbindingsinstellingen opnieuw om de huidige waarden te lezen.",
    gateway_apply_failed: "De instellingen konden niet worden toegepast en gecontroleerd. Controleer het huidige webwachtwoord en de adapterverbinding.",
    gateway_rollback_failed: "De vorige instellingen konden na de mislukte wijziging niet worden bevestigd. De verbinding van de dienst blijft gestopt. Controleer de adapter handmatig voordat je opnieuw verbinding maakt."
  },
  it: {
    title: "Collegamento alla wallbox", intro: "Scegli l’adattatore per questa app. I permessi di scrittura e le altre impostazioni rimangono nella scheda Configurazione.",
    loading: "Caricamento delle impostazioni…", transport: "Collegamento", usb: "USB / RS-485", rtu: "LAN: Modbus RTU su TCP", tcp: "LAN: Modbus TCP",
    usb_settings: "Adattatore USB", adapter: "Adattatore seriale", choose: "Seleziona un adattatore", missing: "L’adattatore precedente è scollegato",
    adapter_hint: "Scegli l’adattatore dall’elenco. Sono preferiti i nomi stabili; nessun adattatore viene selezionato automaticamente.",
    baudrate: "Velocità di trasmissione", frame: "Formato seriale", lan_settings: "Adattatore LAN", host: "Indirizzo IP o nome host dell’adattatore", port: "Porta TCP dell’adattatore",
    lan_hint: "Imposta velocità e formato seriale sull’adattatore LAN. Il salvataggio rimuove la precedente selezione USB.", address: "Indirizzo Modbus della wallbox",
    restart_hint: "Il salvataggio mantiene attivo il collegamento attuale. Riavvia poi l’app per applicare la modifica; l’heartbeat verrà interrotto brevemente.",
    reload: "Ricarica impostazioni e adattatori", save: "Salva collegamento", saving: "Salvataggio…", saved: "Salvato. Riavvia questa app dalla scheda Informazioni per applicare il collegamento.",
    security: "Per amministratori Home Assistant con Supervisor 2026.11.0 o successivo. Qui non serve un token del servizio.",
    supervisor_update_required: "Aggiorna Supervisor alla versione 2026.11.0 o successiva. La normale scheda Configurazione rimane disponibile.", admin_required: "Accedi a Home Assistant come amministratore.",
    reload_required: "Ricarica questa pagina prima di salvare nuovamente.", settings_changed: "Le impostazioni sono cambiate altrove. Ricaricale; le modifiche non sono state salvate.",
    adapter_missing: "L’adattatore USB selezionato non è presente. Ricollegalo o ricarica l’elenco.", connection_invalid: "Controlla impostazioni, indirizzo e porta. La configurazione esistente non è stata modificata.",
    invalid_request: "Controlla le impostazioni di collegamento.", busy: "Un’altra richiesta è in corso. Riprova tra poco.", supervisor_unavailable: "Impossibile caricare o salvare. Controlla l’app e Supervisor, poi ricarica la pagina.",
    detect_adapter: "Configura adattatore LAN",
    detecting: "Lettura delle impostazioni dell’adattatore…",
    adapter_detected: "Adattatore riconosciuto",
    adapter_target: "Impostazioni seriali dell’adattatore",
    adapter_current: "Impostazioni attuali",
    apply_adapter: "Applica le impostazioni dell’adattatore al salvataggio",
    adapter_apply_hint: "Il salvataggio applica subito porta TCP, modalità, accesso e impostazioni seriali selezionate. La password web attuale non viene modificata. Viene inoltre impostato il server TCP passivo, disattivando interrogazioni/cache Modbus interne, controllo di flusso, preamboli di connessione, ripetizione dei dati seriali memorizzati, invio automatico dei parametri e riavvii attivati dai dati.",
    auth_hint: "Usa la password web attuale di un adattatore compatibile. Se è già configurato manualmente, lascia disattivata l’applicazione delle impostazioni.",
    auth_security: "L’accesso non cifra il traffico. Con il firmware 1.523 può essere aggirato; usa una rete locale protetta.",
    password_required: "Password web attuale: da 1 a 9 caratteri ASCII stampabili, senza spazi. La password non viene modificata.",
    password_saved: "Una password è già salvata per questo host e questa porta. Lascia vuoto il campo per conservarla.",
    adapter_restart_hint: "Il salvataggio modifica subito l’adattatore e arresta il vecchio collegamento. Riavvia poi l’app per riprendere l’heartbeat con le nuove impostazioni.",
    saved_adapter: "Impostazioni applicate e collegamento salvato. Il vecchio collegamento è arrestato. Riavvia questa app dalla scheda Informazioni per riprendere l’heartbeat.",
    adapter_detection_required: "Rileggi le impostazioni dell’adattatore prima di applicare modifiche.",
    adapter_save_uncertain: "Impossibile confermare insieme adattatore e impostazioni salvate. Il collegamento è arrestato. Ricarica le impostazioni e identifica di nuovo l’adattatore prima di riconnetterti; non riavviare senza verifica.",
    options_save_uncertain: "Un salvataggio precedente potrebbe completarsi anche dopo un riavvio dell’app. Altri salvataggi sono bloccati durante questa sessione. Verifica la connessione salvata e l’adattatore prima di riconnetterti.",
    gateway_authentication_failed: "L’adattatore ha rifiutato la password web attuale. Controlla la password della sua interfaccia web.",
    tcp_authentication: "Accesso TCP",
    tcp_auth_password: "Password web attuale dell’adattatore",
    gateway_unsupported: "Questo modello, firmware o modalità non può essere configurato automaticamente. Configura manualmente l’adattatore, poi salva la connessione.",
    gateway_unreachable: "Impossibile leggere le impostazioni. Verifica indirizzo e accesso alla rete locale oppure usa la configurazione manuale.",
    gateway_changed: "Le impostazioni sono cambiate dopo questa anteprima. Riavvia la configurazione della connessione per leggere i valori attuali.",
    gateway_apply_failed: "Impossibile applicare e verificare le impostazioni. Verifica la password web attuale e la connessione all’adattatore.",
    gateway_rollback_failed: "Impossibile confermare le impostazioni precedenti dopo l’errore. La connessione del servizio resta arrestata. Controlla manualmente l’adattatore prima di riconnetterti."
  }
};

const language = messages[document.documentElement.lang] ? document.documentElement.lang : "en";
const text = (key) => messages[language][key] || messages.en[key] || messages.en.supervisor_unavailable;
document.querySelectorAll("[data-i18n]").forEach((element) => { element.textContent = text(element.dataset.i18n); });
const el = (id) => document.getElementById(id);
let revision = null;
let csrf = null;
let busy = false;
let savedSettings = null;
let adapter = null;
let adapterEpoch = 0;
let restartPending = false;
let uncertain = false;
let writeBlocked = false;

const sameEndpoint = () => savedSettings && el("host").value.trim().toLowerCase() === (savedSettings.host || "").toLowerCase()
  && Number(el("tcp_port").value) === Number(savedSettings.tcp_port);
const storedPasswordAvailable = () => sameEndpoint() && savedSettings.tcp_auth_password_set === true;
const applyingAdapter = () => adapter && adapter.can_configure === true && el("apply_adapter").checked;
const supportedOption = (id, value, fallback) => Array.from(el(id).options).some(option => option.value === String(value)) ? value : fallback;

function clearAdapter() {
  adapter = null;
  adapterEpoch += 1;
  el("apply_adapter").checked = false;
  el("adapter_preview").hidden = true;
}

function setBusy(value) {
  busy = value;
  el("save").disabled = value || restartPending || uncertain || writeBlocked || !revision || !csrf;
  el("reload").disabled = value;
  el("connection_type").disabled = value || restartPending;
  el("device_id").disabled = value || restartPending;
  showTransport();
}

function showError(key) {
  el("error").textContent = text(key);
  el("error").hidden = false;
  el("status").textContent = "";
}

function showTransport() {
  const serial = el("connection_type").value === "serial";
  el("serial_fields").hidden = !serial;
  el("serial_fields").disabled = busy || !serial || restartPending;
  el("network_fields").hidden = serial;
  el("network_fields").disabled = busy || serial || restartPending;
  el("detect_adapter").disabled = busy || serial || restartPending;
  el("apply_adapter").disabled = busy || !adapter || adapter.can_configure !== true || restartPending;
  const managed = !serial && applyingAdapter();
  el("adapter_fields").hidden = !managed;
  el("adapter_fields").disabled = busy || !managed || restartPending;
  const password = !serial && (el("tcp_authentication").checked || managed);
  el("password_fields").hidden = !password;
  el("tcp_auth_password").disabled = busy || !password || restartPending;
  el("tcp_auth_password").required = password && !storedPasswordAvailable()
    && (el("tcp_authentication").checked || (managed && adapter.tcp_authentication));
  el("password_hint").textContent = text(storedPasswordAvailable() ? "password_saved" : "password_required");
  el("restart_hint").textContent = text(managed || restartPending ? "adapter_restart_hint" : "restart_hint");
}

async function readSettings() {
  if (busy) return;
  clearAdapter();
  el("tcp_auth_password").value = "";
  revision = null;
  csrf = null;
  setBusy(true);
  el("error").hidden = true;
  el("status").textContent = text("loading");
  try {
    const response = await fetch("./api/config", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) { showError(data.error); return; }
    revision = data.revision;
    csrf = data.csrf;
    writeBlocked = data.write_blocked === true;
    const settings = data.settings;
    savedSettings = settings;
    el("connection_type").value = settings.connection_type;
    const port = el("serial_port");
    port.replaceChildren(new Option(text("choose"), ""));
    for (const adapter of data.adapters) port.add(new Option(adapter.label, adapter.value));
    if (settings.serial_port && !data.adapters.some((item) => item.value === settings.serial_port)) {
      const missing = new Option(`${text("missing")}: ${settings.serial_port}`, settings.serial_port);
      missing.disabled = true;
      port.add(missing);
    }
    port.value = settings.serial_port || "";
    for (const [key, fallback] of Object.entries({ host: "", tcp_port: 502, device_id: 50, baudrate: "57600", frame: "8n2" })) {
      el(key).value = settings[key] ?? fallback;
    }
    el("tcp_authentication").checked = settings.tcp_authentication === true;
    showTransport();
    el("connection_form").hidden = false;
    el("status").textContent = restartPending ? text("saved_adapter") : "";
    if (writeBlocked) showError("options_save_uncertain");
    else if (uncertain) showError("adapter_save_uncertain");
  } catch (_) { showError("supervisor_unavailable"); }
  finally { setBusy(false); }
}

el("connection_type").addEventListener("change", () => {
  if (el("connection_type").value === "serial") {
    clearAdapter();
    el("tcp_auth_password").value = "";
  }
  showTransport();
});
el("host").addEventListener("input", () => {
  clearAdapter();
  el("tcp_auth_password").value = "";
  el("status").textContent = "";
  showTransport();
});
el("tcp_port").addEventListener("input", () => {
  el("tcp_auth_password").value = "";
  showTransport();
});
el("tcp_authentication").addEventListener("change", showTransport);
el("apply_adapter").addEventListener("change", showTransport);
el("detect_adapter").addEventListener("click", async () => {
  if (busy || restartPending || !revision || !csrf || !el("host").reportValidity()) return;
  clearAdapter();
  const epoch = adapterEpoch;
  const host = el("host").value.trim();
  setBusy(true);
  el("error").hidden = true;
  el("status").textContent = text("detecting");
  try {
    const response = await fetch("./api/adapter", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Amtron-CSRF": csrf },
      body: JSON.stringify({ host, revision })
    });
    const data = await response.json();
    if (epoch !== adapterEpoch || host !== el("host").value.trim()) return;
    if (!response.ok) { showError(data.error); return; }
    adapter = data;
    el("adapter_identity").textContent = `${data.model} · ${data.firmware} · ${data.identifier}`;
    el("adapter_current").textContent = `${text("adapter_current")}: ${data.baudrate} baud · ${data.frame.toUpperCase()} · TCP ${data.tcp_port}`;
    el("adapter_preview").hidden = false;
    el("adapter_unsupported").hidden = data.can_configure === true;
    // Recognition selects the existing adapter mode, never applies anything.
    el("tcp_port").value = data.tcp_port;
    el("connection_type").value = data.connection_type;
    el("tcp_authentication").checked = data.tcp_authentication;
    el("tcp_auth_password").value = "";
    const fromSerial = savedSettings.connection_type === "serial";
    el("adapter_baudrate").value = supportedOption("adapter_baudrate", fromSerial ? savedSettings.baudrate : data.baudrate, 57600);
    el("adapter_frame").value = supportedOption("adapter_frame", fromSerial ? savedSettings.frame : data.frame, "8n2");
    uncertain = false;
    el("status").textContent = "";
    if (writeBlocked) showError("options_save_uncertain");
  } catch (_) { clearAdapter(); showError("gateway_unreachable"); }
  finally { setBusy(false); }
});
el("reload").addEventListener("click", readSettings);
el("connection_form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || restartPending || uncertain || writeBlocked || !revision || !csrf || !event.target.reportValidity()) return;
  const serial = el("connection_type").value === "serial";
  const managed = !serial && applyingAdapter();
  const payload = { revision, connection_type: el("connection_type").value, device_id: Number(el("device_id").value) };
  if (serial) Object.assign(payload, { serial_port: el("serial_port").value, baudrate: el("baudrate").value, frame: el("frame").value });
  else {
    Object.assign(payload, {
      host: el("host").value.trim(), tcp_port: Number(el("tcp_port").value),
      tcp_authentication: el("tcp_authentication").checked, tcp_auth_password: el("tcp_auth_password").value
    });
    if (managed) Object.assign(payload, {
      adapter_token: adapter.adapter_token, adapter_baudrate: Number(el("adapter_baudrate").value), adapter_frame: el("adapter_frame").value
    });
  }
  const keepPassword = storedPasswordAvailable();
  setBusy(true);
  el("error").hidden = true;
  el("status").textContent = text("saving");
  try {
    const response = await fetch("./api/config", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Amtron-CSRF": csrf }, body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) {
      uncertain = data.error === "adapter_save_uncertain" || data.error === "gateway_rollback_failed";
      if (data.error === "adapter_save_uncertain" || data.error === "options_save_uncertain") writeBlocked = true;
      showError(data.error);
      return;
    }
    revision = data.revision;
    savedSettings = { ...payload, tcp_auth_password_set: !serial && payload.tcp_authentication && Boolean(payload.tcp_auth_password || keepPassword) };
    delete savedSettings.tcp_auth_password;
    delete savedSettings.adapter_token;
    restartPending = Boolean(managed);
    el("status").textContent = text(managed ? "saved_adapter" : "saved");
  } catch (_) {
    // A lost response cannot tell us whether a physical change was applied.
    uncertain = Boolean(managed);
    showError(managed ? "adapter_save_uncertain" : "supervisor_unavailable");
  } finally {
    clearAdapter();
    el("tcp_auth_password").value = "";
    setBusy(false);
  }
});

readSettings();
