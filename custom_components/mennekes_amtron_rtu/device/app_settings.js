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
    security: "Available to Home Assistant administrators with Supervisor 2026.11.0 or newer. No device or service credentials are needed here.",
    supervisor_update_required: "Update Home Assistant Supervisor to 2026.11.0 or newer to use this configuration page. The native app Configuration tab remains available.",
    admin_required: "Sign in to Home Assistant as an administrator to change this connection.", reload_required: "Reload this page before saving again.",
    settings_changed: "Settings changed elsewhere. Reload them before saving again; your changes have not been saved.", adapter_missing: "The selected USB adapter is missing. Reconnect it or reload the adapter list.",
    connection_invalid: "Check the connection settings, address and port. The existing configuration has not been changed.",
    invalid_request: "Check the connection settings and try again.", busy: "Another request is running. Try again shortly.", supervisor_unavailable: "The settings could not be loaded or saved. Check the app and Supervisor, then reload this page."
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
    security: "Für Home-Assistant-Administratoren mit Supervisor ab 2026.11.0. Geräte- oder Dienst-Zugangsdaten sind hier nicht erforderlich.",
    supervisor_update_required: "Aktualisiere Home Assistant Supervisor auf mindestens 2026.11.0, um diese Seite zu nutzen. Der normale Reiter Konfiguration der App bleibt verfügbar.",
    admin_required: "Melde dich bei Home Assistant als Administrator an, um die Verbindung zu ändern.", reload_required: "Lade diese Seite vor dem erneuten Speichern neu.",
    settings_changed: "Die Einstellungen wurden anderswo geändert. Lade sie erneut, bevor du speicherst. Deine Änderungen wurden nicht gespeichert.", adapter_missing: "Der ausgewählte USB-Adapter fehlt. Schließe ihn wieder an oder lade die Adapterliste neu.",
    connection_invalid: "Prüfe Verbindungseinstellungen, Adresse und Port. Die bisherige Konfiguration wurde nicht geändert.",
    invalid_request: "Prüfe die Verbindungseinstellungen und versuche es erneut.", busy: "Eine andere Anfrage läuft noch. Versuche es gleich noch einmal.", supervisor_unavailable: "Die Einstellungen konnten nicht geladen oder gespeichert werden. Prüfe App und Supervisor und lade diese Seite erneut."
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
    security: "Pour les administrateurs Home Assistant avec Supervisor 2026.11.0 ou ultérieur. Aucun identifiant de borne ou de service n’est nécessaire ici.",
    supervisor_update_required: "Mettez Supervisor à jour vers 2026.11.0 ou ultérieur. L’onglet Configuration habituel reste disponible.", admin_required: "Connectez-vous à Home Assistant en tant qu’administrateur.",
    reload_required: "Rechargez cette page avant d’enregistrer.", settings_changed: "Les réglages ont changé ailleurs. Rechargez-les avant d’enregistrer ; vos changements n’ont pas été enregistrés.",
    adapter_missing: "L’adaptateur USB sélectionné est absent. Rebranchez-le ou rechargez la liste.", connection_invalid: "Vérifiez les réglages, l’adresse et le port. La configuration existante est inchangée.",
    invalid_request: "Vérifiez les réglages de connexion.", busy: "Une autre requête est en cours. Réessayez dans un instant.", supervisor_unavailable: "Impossible de charger ou d’enregistrer. Vérifiez l’application et Supervisor, puis rechargez la page."
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
    security: "Voor Home Assistant-beheerders met Supervisor 2026.11.0 of nieuwer. Er zijn hier geen apparaat- of servicereferenties nodig.",
    supervisor_update_required: "Werk Supervisor bij naar 2026.11.0 of nieuwer. Het gewone tabblad Configuratie blijft beschikbaar.", admin_required: "Meld je aan bij Home Assistant als beheerder.",
    reload_required: "Herlaad deze pagina voordat je opnieuw opslaat.", settings_changed: "De instellingen zijn elders gewijzigd. Herlaad ze; je wijzigingen zijn niet opgeslagen.",
    adapter_missing: "De gekozen USB-adapter ontbreekt. Sluit hem aan of herlaad de lijst.", connection_invalid: "Controleer de verbinding, het adres en de poort. De bestaande configuratie is niet gewijzigd.",
    invalid_request: "Controleer de verbindingsinstellingen.", busy: "Een ander verzoek loopt nog. Probeer het zo opnieuw.", supervisor_unavailable: "Laden of opslaan is mislukt. Controleer de app en Supervisor en herlaad deze pagina."
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
    security: "Per amministratori Home Assistant con Supervisor 2026.11.0 o successivo. Qui non servono credenziali del dispositivo o del servizio.",
    supervisor_update_required: "Aggiorna Supervisor alla versione 2026.11.0 o successiva. La normale scheda Configurazione rimane disponibile.", admin_required: "Accedi a Home Assistant come amministratore.",
    reload_required: "Ricarica questa pagina prima di salvare nuovamente.", settings_changed: "Le impostazioni sono cambiate altrove. Ricaricale; le modifiche non sono state salvate.",
    adapter_missing: "L’adattatore USB selezionato non è presente. Ricollegalo o ricarica l’elenco.", connection_invalid: "Controlla impostazioni, indirizzo e porta. La configurazione esistente non è stata modificata.",
    invalid_request: "Controlla le impostazioni di collegamento.", busy: "Un’altra richiesta è in corso. Riprova tra poco.", supervisor_unavailable: "Impossibile caricare o salvare. Controlla l’app e Supervisor, poi ricarica la pagina."
  }
};

const language = messages[document.documentElement.lang] ? document.documentElement.lang : "en";
const text = (key) => messages[language][key] || messages.en[key] || messages.en.supervisor_unavailable;
document.querySelectorAll("[data-i18n]").forEach((element) => { element.textContent = text(element.dataset.i18n); });
const el = (id) => document.getElementById(id);
let revision = null;
let csrf = null;
let busy = false;

function setBusy(value) {
  busy = value;
  el("save").disabled = value;
  el("reload").disabled = value;
  el("connection_type").disabled = value;
  el("device_id").disabled = value;
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
  el("serial_fields").disabled = busy || !serial;
  el("network_fields").hidden = serial;
  el("network_fields").disabled = busy || serial;
}

async function readSettings() {
  if (busy) return;
  setBusy(true);
  el("error").hidden = true;
  el("status").textContent = text("loading");
  try {
    const response = await fetch("./api/config", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) { showError(data.error); return; }
    revision = data.revision;
    csrf = data.csrf;
    const settings = data.settings;
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
    showTransport();
    el("connection_form").hidden = false;
    el("status").textContent = "";
  } catch (_) { showError("supervisor_unavailable"); }
  finally { setBusy(false); }
}

el("connection_type").addEventListener("change", showTransport);
el("reload").addEventListener("click", readSettings);
el("connection_form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !revision || !csrf || !event.target.reportValidity()) return;
  const serial = el("connection_type").value === "serial";
  const payload = { revision, connection_type: el("connection_type").value, device_id: Number(el("device_id").value) };
  if (serial) Object.assign(payload, { serial_port: el("serial_port").value, baudrate: el("baudrate").value, frame: el("frame").value });
  else Object.assign(payload, { host: el("host").value.trim(), tcp_port: Number(el("tcp_port").value) });
  setBusy(true);
  el("error").hidden = true;
  el("status").textContent = text("saving");
  try {
    const response = await fetch("./api/config", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Amtron-CSRF": csrf }, body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) { showError(data.error); return; }
    revision = data.revision;
    el("status").textContent = text("saved");
  } catch (_) { showError("supervisor_unavailable"); }
  finally { setBusy(false); }
});

readSettings();
