"""Gemeinsame Begriffe und Status-Plaketten des Dashboards (Issue #595).

Farben und Wörter bedeuteten je nach Seite etwas anderes (UX-Review, Befund 4). Die Zuordnung
steht deshalb einmal hier; Seiten nutzen das Makro `status_badge` aus `_components.html`, und
`docs/glossar.md` erklärt dieselben Begriffe (ein Test hält beide gleich).
"""
from __future__ import annotations

STATUS_BADGES: dict[str, tuple[str, str]] = {
    "archive": ("Sammlung", "badge-ok"),
    "adopted": ("übernommen", "badge-ok"),
    "inbox": ("Inbox", "badge-info"),
    "rejected": ("Quarantäne", "badge-err"),
    "gate": ("Gate", "badge-warn"),
    "store": ("Store", "badge-purple"),
    "open": ("offen", "badge-muted"),
}


def badge_for(key: str | None) -> tuple[str, str]:
    """(Wort, CSS-Klasse) für einen Status; Unbekanntes neutral grau statt eines Fehlers."""
    if key is None or key == "":
        return ("—", "badge-muted")
    text = str(key)
    return STATUS_BADGES.get(text.lower(), (text, "badge-muted"))


GLOSSAR: dict[str, tuple[str, tuple[str, ...]]] = {
    "Sammlung": ("Die bestehende Musiksammlung unter /music/Schranz; wird nie verändert.", ("Archiv",)),
    "Inbox": ("Neu geladene, geprüfte Tracks, sortiert nach BPM und Key.", ("Eingang",)),
    "Eingang": ("Ordner _eingang für manuell geladene Dateien (Upload, Gates).", ("Intake",)),
    "Quarantäne": ("Abgewiesene Downloads (Fake, Clipping, defekt) unter _rejected.", ("abgelehnt", "abgewiesen", "aussortiert")),
    "Duplikat": ("Gleiche Aufnahme in Sammlung und Inbox, erkannt per Fingerprint.", ()),
    "Dublette": ("Mehrere Dateien derselben SoundCloud-ID in der Inbox.", ()),
    "Gate": ("Download nur über ein Fangate (Hypeddit, Droploud); wird verlinkt, nie umgangen.", ("Fangate",)),
    "Store": ("Track nur zum Kauf (Bandcamp, Beatport); steht auf der Kaufliste.", ()),
    "übernommen": ("Inbox-Track, der inzwischen in der Sammlung liegt.", ("adoptiert",)),
    "Artist": ("Urheber eines Tracks.", ("Künstler",)),
    "Lauf": ("Ein Durchgang eines geplanten Jobs, z. B. der tägliche Discover-Lauf.", ("Run",)),
}
