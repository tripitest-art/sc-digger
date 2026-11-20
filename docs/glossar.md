# Glossar

Ein Wort je Sache, gleich in Dashboard, Bot und Doku. Die Plaketten im Dashboard nutzen dieselben
Wörter (`sc_digger/web/glossary.py`).

- **Sammlung**: Die bestehende Musiksammlung unter /music/Schranz; wird nie verändert. Nicht: Archiv.
- **Inbox**: Neu geladene, geprüfte Tracks, sortiert nach BPM und Key. Nicht: Eingang.
- **Eingang**: Ordner _eingang für manuell geladene Dateien (Upload, Gates). Nicht: Intake.
- **Quarantäne**: Abgewiesene Downloads (Fake, Clipping, defekt) unter _rejected. Nicht: abgelehnt, abgewiesen, aussortiert.
- **Duplikat**: Gleiche Aufnahme in Sammlung und Inbox, erkannt per Fingerprint.
- **Dublette**: Mehrere Dateien derselben SoundCloud-ID in der Inbox.
- **Gate**: Download nur über ein Fangate (Hypeddit, Droploud); wird verlinkt, nie umgangen. Nicht: Fangate.
- **Store**: Track nur zum Kauf (Bandcamp, Beatport); steht auf der Kaufliste.
- **übernommen**: Inbox-Track, der inzwischen in der Sammlung liegt. Nicht: adoptiert.
- **Artist**: Urheber eines Tracks. Nicht: Künstler.
- **Set-Tracklist**: Einträge eines fremden DJ-Sets aus MixesDB, mit Position, Label und Katalog. Nicht: Playlist, Trackliste.
- **Offene Stelle**: Eintrag `?` in einer Set-Tracklist — auch das Wiki kennt den Track nicht. Nicht: Lücke, Unknown.
- **Lauf**: Ein Durchgang eines geplanten Jobs, z. B. der tägliche Discover-Lauf. Nicht: Run.

Die Bereiche der Navigation (Start, Entdecken, Eingang, Bibliothek, Auflegen) sind Arbeitsschritte, keine
Speicherorte: „Bibliothek“ umfasst Track-Explorer und Duplikate, also Inbox und Sammlung zusammen.
