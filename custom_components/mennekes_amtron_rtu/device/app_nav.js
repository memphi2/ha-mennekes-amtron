/* One navigation for every app page. Text only; no credentials or requests. */
(() => {
  "use strict";
  const languages = ["en", "de", "fr", "nl", "it"];
  const found = languages.indexOf((document.documentElement.lang || "").slice(0, 2).toLowerCase());
  const column = found < 0 ? 0 : found;
  const pages = [
    ["overview", "./", ["Overview", "Übersicht", "Vue d’ensemble", "Overzicht", "Panoramica"]],
    ["connection", "./connection", ["Connection", "Verbindung", "Connexion", "Verbinding", "Connessione"]],
    ["debug", "./connection#debug", ["Debug log", "Debugprotokoll", "Journal de débogage", "Debuglogboek", "Registro di debug"]],
    ["registers", "./registers", ["Registers", "Register", "Registres", "Registers", "Registri"]],
    ["runtime", "./runtime", ["Runtime", "Betrieb", "Fonctionnement", "Werking", "Funzionamento"]],
    ["jobs", "./jobs", ["Charging jobs", "Ladeaufträge", "Ordres de recharge", "Laadopdrachten", "Ordini di ricarica"]],
    ["guest", "./guest", ["Guest charging", "Gastladen", "Recharge invité", "Gastladen", "Ricarica ospite"]],
    ["weekly", "./weekly", ["Weekly plans", "Wochenpläne", "Plans hebdomadaires", "Weekplannen", "Piani settimanali"]],
  ];
  const label = ["Service pages", "Service-Seiten", "Pages du service", "Servicepagina’s", "Pagine del servizio"][column];
  document.querySelectorAll("nav[data-nav]").forEach(nav => {
    nav.setAttribute("aria-label", label);
    const links = pages.map(([key, href, names]) => {
      const link = document.createElement("a");
      link.href = href;
      link.textContent = names[column];
      if (key === nav.dataset.nav) link.setAttribute("aria-current", "page");
      return link;
    });
    nav.replaceChildren(...links);
  });
  // The connection page shows the debug log as its second tab.
  const debug = () => { if (location.hash === "#debug") document.getElementById("debug_tab")?.click(); };
  if (document.getElementById("debug_tab")) {
    debug();
    addEventListener("hashchange", debug);
  }
})();
