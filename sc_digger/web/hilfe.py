"""Inhalt der Hilfe-Seite „So funktioniert sc-digger“ (Issue #711).

Reine Daten ohne Webframework: Die Seite zeigt den Weg eines Tracks in sieben Stationen und
den Tagesablauf. Die Texte nutzen nur Begriffe aus `glossary.GLOSSAR` (ein Test prüft, dass
keine der dort verworfenen Alternativen auftaucht), damit Hilfe und Dashboard dieselben Wörter
sprechen.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    key: str     # stabile Kennung, z. B. "entdecken" (Anker im Template und in Tests)
    titel: str   # Überschrift der Station
    text: str    # ein bis zwei Sätze, du-Form
    href: str    # Seite, auf der man die Station sieht
    ziel: str    # Linktext zu dieser Seite


STATIONEN: tuple[Station, ...] = (
    Station(
        "entdecken", "Entdecken",
        "Jeden Tag sucht sc-digger neue Schranz- und Hard-Techno-Tracks auf SoundCloud "
        "und schickt dir den Digest.",
        "/digest", "Zum Digest",
    ),
    Station(
        "bewerten", "Bewerten",
        "Hör die Tracks an und gib 👍 oder 👎. Daraus lernt die Bewertung, was dir gefällt.",
        "/digest/durchhoeren", "Zum Durchhören",
    ),
    Station(
        "laden", "Laden",
        "Freie Originale lädt sc-digger selbst. Liegt ein Gate davor, lädst du von Hand "
        "und legst die Datei im Upload ab; Tracks nur zum Kauf stehen auf der Kaufliste.",
        "/intake", "Zum Upload",
    ),
    Station(
        "pruefen", "Prüfen",
        "Jede Datei wird auf Fakes, Clipping und Duplikate geprüft. Wer durchfällt, "
        "landet in der Quarantäne.",
        "/rejected", "Zur Quarantäne",
    ),
    Station(
        "inbox", "Inbox",
        "Bestandene Tracks werden auf −8,5 LUFS gebracht, bekommen BPM, Key und Cues "
        "und liegen sortiert in der Inbox.",
        "/tracks", "Zu den Tracks",
    ),
    Station(
        "rekordbox", "Rekordbox",
        "Die Inbox gibt es als Rekordbox-XML. Auf der Startseite schreibst du sie neu, "
        "wenn neue Tracks dazugekommen sind.",
        "/", "Zur Startseite",
    ),
    Station(
        "sammlung", "Sammlung",
        "Tracks, die du in die Sammlung übernommen hast, erkennt sc-digger wieder. "
        "Die Sammlung selbst verändert es nie.",
        "/audit", "Zum Audit",
    ),
)

# (Text, Ziel, Linktext) – die drei Handgriffe, die an einem Tag anfallen.
TAG_SCHRITTE: tuple[tuple[str, str, str], ...] = (
    ("Neue Tracks im Digest anhören und bewerten.", "/digest/durchhoeren", "Zum Durchhören"),
    ("Gates von Hand laden und die Dateien im Upload ablegen.", "/intake", "Zum Upload"),
    ("Die Inbox in Rekordbox übernehmen.", "/tracks", "Zu den Tracks"),
)
