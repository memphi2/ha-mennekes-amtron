"""The navigation every app page shares, rendered by the server.

The pages arrive complete and in the browser's language, so a change of page
shows no empty bar and no English labels that are replaced a moment later.
"""

from __future__ import annotations

# ruff: noqa: RUF001 - typographic apostrophes are part of the translations
from collections.abc import Collection

LANGUAGES = ("en", "de", "fr", "nl", "it")
PLACEHOLDER = b"<!-- app-nav -->"

# Group, then its pages as (path, labels in LANGUAGES order). A group with one
# page links to it directly; the others show their pages in a second row.
_NAV: tuple[tuple[tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (("Overview", "Übersicht", "Vue d’ensemble", "Overzicht", "Panoramica"), (
        ("/", ("Overview", "Übersicht", "Vue d’ensemble", "Overzicht", "Panoramica")),
        ("/registers", ("Registers", "Register", "Registres", "Registers", "Registri")),
    )),
    (("Charging", "Laden", "Recharge", "Laden", "Ricarica"), (
        ("/jobs", ("Charging jobs", "Ladeaufträge", "Ordres de recharge", "Laadopdrachten", "Ordini di ricarica")),
        ("/guest", ("Guest charging", "Gastladen", "Recharge invité", "Gastladen", "Ricarica ospite")),
    )),
    (("Settings", "Einstellungen", "Réglages", "Instellingen", "Impostazioni"), (
        ("/connection", ("Connection", "Verbindung", "Connexion", "Verbinding", "Connessione")),
        ("/runtime", ("Runtime", "Betrieb", "Fonctionnement", "Werking", "Funzionamento")),
    )),
    (("Log", "Protokoll", "Journal", "Logboek", "Registro"), (
        ("/log", ("Log", "Protokoll", "Journal", "Logboek", "Registro")),
    )),
)
_LABEL = ("App pages", "App-Seiten", "Pages de l’application", "App-pagina’s", "Pagine dell’app")


def _link(path: str, text: str, current: bool) -> str:
    # Relative links keep working behind the Ingress path prefix.
    marker = ' aria-current="page"' if current else ""
    return f'<a href=".{path}"{marker}>{text}</a>'


def render_nav(path: str, language: str, available: Collection[str]) -> bytes:
    """Return the app bar for ``path``; pages this app does not serve are left out."""
    column = LANGUAGES.index(language) if language in LANGUAGES else 0
    tabs, pages = [], ""
    for labels, group in _NAV:
        served = [(target, names) for target, names in group if target in available]
        if not served:
            continue
        active = any(target == path for target, _ in served)
        tabs.append(_link(served[0][0], labels[column], active).replace(
            ' aria-current="page"', ' aria-current="true"' if len(served) > 1 else ' aria-current="page"',
        ))
        if active and len(served) > 1:
            pages = '<nav class="app-pages" aria-label="{}">{}</nav>'.format(
                labels[column], "".join(_link(target, names[column], target == path) for target, names in served),
            )
    return (
        '<header class="app-bar"><span class="app-name">Mennekes Amtron</span>'
        f'<nav class="app-tabs" aria-label="{_LABEL[column]}">{"".join(tabs)}</nav></header>{pages}'
    ).encode()
