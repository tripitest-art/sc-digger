# sc-digger

<img width="1024" height="559" alt="image" src="https://github.com/user-attachments/assets/ef5fa4ec-e35d-4617-96e8-b50e5fd006c3" />

[![tests](https://github.com/tripitest-art/sc-digger/actions/workflows/tests.yml/badge.svg)](https://github.com/tripitest-art/sc-digger/actions/workflows/tests.yml)

**Crate-Digging-Assistent für Schranz und Hard Techno.** sc-digger durchsucht SoundCloud täglich
nach neuen Tracks, bewertet sie im Verhältnis zum Genre statt nach absoluten Zahlen, gleicht sie
mit deiner Sammlung ab, lädt freigegebene Originale, prüft deren echte Audioqualität, gleicht den
Pegel an, erkennt BPM und Tonart, taggt und sortiert sie in eine Inbox und schickt dir einen
Telegram-Digest. Ein Telegram-Bot prüft Playlists und Tracks auf Zuruf.

> **In English:** A self-hosted SoundCloud discovery tool for Schranz/Hard Techno DJs. It finds new
> tracks, ranks them relative to the genre, checks them against your collection, downloads only
> what artists offer for free, detects fake upscales, levels loudness, analyses BPM/key, tags and
> sorts files, and reports via Telegram. Docs and code comments are in German.

Läuft als Docker-Container auf einem Heimserver. Es ist ein Hobbyprojekt für eine konkrete
Sammlung, aber Genres, Pfade und Schwellwerte stehen alle in `config.yaml`.

**Inhalt:** [Grundsätze](#grundsätze) · [Was ein Lauf macht](#was-ein-lauf-macht) ·
[Modi](#modi) · [Telegram](#telegram) · [Installation](#installation) ·
[Konfiguration](#konfiguration) · [Details](#details) · [Grenzen](#grenzen) ·
[Mitentwickeln](#mitentwickeln)

## Grundsätze

- **Fair zu Artists.** Geladen wird nur, was der Artist selbst freigibt: native SoundCloud-Downloads
  als Original-Datei, nie Stream-Rips. Download-Gates (Hypeddit, Droploud, …) werden erkannt und
  verlinkt, aber nie umgangen.
- **Die Sammlung bleibt unangetastet.** Der Sammlungsordner wird nur lesend eingebunden. DJ-Software
  wie Rekordbox verknüpft Cues über Dateipfade; Verschieben, Umbenennen oder Audio-Bearbeitung würde
  sie zerstören. Sortiert, getaggt und im Pegel angeglichen wird nur in der Inbox.
- **Messen, bevor verändert wird.** Lautheit, BPM und Tonart werden gemessen und als Metadaten
  gespeichert. Das einzige, was sc-digger am Audio neuer Downloads ändert, ist der Pegel: linear,
  samplegenau, ohne Limiter (siehe [Pegel-Angleichung](#pegel-angleichung)).
- **Laut scheitern.** sc-digger nutzt die inoffizielle SoundCloud-API, die irgendwann brechen wird.
  Ausfälle kommen als Alarm oder Hinweis im Digest an, nicht als stiller leerer Digest.

## Was ein Lauf macht

Der tägliche `discover`-Lauf (07:30 per Cron):

1. **Nachholen:** Original-Downloads, die beim letzten Mal gescheitert sind, erneut versuchen
   (bis zu 3 Versuche insgesamt)
2. **Suchen:** Genre-Tags, Uploads gefolgter Artists und Reposts/Likes von Referenz-Accounts
   (DJs, deren Geschmack du vertraust; ihre Treffer bekommen einen Bonus). Scheitert eine einzelne
   Quelle, läuft der Rest weiter und der Digest sagt, welche fehlte.
3. **Vorfiltern:** DJ-Sets (länger als 12 Minuten) raus; BPM aus Titel, Tags und Beschreibung
   geschätzt, unbekannt bleibt drin
4. **Bewerten:** Perzentil-Score aus Likes, Reposts und Kommentaren pro Play plus Aktualität;
   Abzug bei Promo-Netzwerk-Verdacht (Repost-Tausch, „send your tracks“)
5. **Aussortieren:** schon gemeldet (SQLite) oder schon in deiner Sammlung (Fuzzy-Match, Remixe zählen extra)
6. **Download-Weg bestimmen:** nativ, Gate, Store, Cloud-Link oder nur Stream
7. **Laden und prüfen:** Original über `scdl --only-original`, dann ffprobe, Spektrum-Check gegen
   hochkonvertierte Fakes, EBU-R128-Messung gegen Brickwall-Master und Audio-Fingerprint gegen
   Aufnahmen, die schon in der Sammlung liegen
8. **Pegel angleichen:** WAV/AIFF/FLAC auf −8,5 LUFS, samplegenau, Metadaten unverändert
9. **Analysieren:** BPM per Beat-Tracking mit Oktav-Korrektur, Tonart als Camelot-Key
10. **Taggen:** Artist, Titel, BPM, Key, Genre, SoundCloud-URL und Lautheit (ReplayGain)
11. **Sortieren:** in `inbox/<BPM-Bereich>/<Camelot-Key>/`
12. **Melden:** Telegram-Digest nach Download-Weg gruppiert, mit 👍/👎/⏳-Buttons und einer
    Export-Datei; Store-Tracks landen zusätzlich auf der Kaufliste
13. **Rekordbox:** `sc-digger.xml` mit einer Playlist pro Kalenderwoche neu schreiben

## Modi

```bash
python -m sc_digger.main                                            # discover (Standard, täglich per Cron)
python -m sc_digger.main playlist https://soundcloud.com/user/sets/name
python -m sc_digger.main playlist https://soundcloud.com/user --likes    # Likes eines Profils
python -m sc_digger.main check https://soundcloud.com/artist/track      # wie der Bot: Playlist oder Track-Station
python -m sc_digger.main similar https://soundcloud.com/artist/track    # Related Tracks
python -m sc_digger.main similar https://soundcloud.com/artist/track --radio    # Track-Radio
python -m sc_digger.main similar https://soundcloud.com/artist/track --filter   # mit BPM-Fenster und Scoring
python -m sc_digger.main rekordbox                                  # Rekordbox-XML der Inbox neu schreiben
python -m sc_digger.main intake                                     # Manuell abgelegte Tracks verarbeiten
python -m sc_digger.main audit --report report.html                 # Sammlung prüfen, nur lesend (siehe unten)
```

Für jeden Modus: `--dry-run` (nichts laden, senden oder speichern), `--no-telegram` (Ausgabe in
der Konsole), `-v`, `--config <datei>`. Im Container: `docker exec sc-digger-sc-digger-1 python -m sc_digger.main …`

| | discover | playlist / check | similar |
|---|---|---|---|
| DJ-Set-, BPM- und Score-Filter | ja | **nein**, du hast die Quelle bewusst gewählt | nur mit `--filter` |
| „schon gemeldet“ | ja | nein, beliebig oft prüfbar | nein |
| Sammlungsabgleich, Download, Prüfung, Pegel, Analyse, Tags, Sortieren | ja | ja | ja |
| Digest gekürzt auf `max_items_per_digest` | ja | nein, zeigt alles | ja |

## Telegram

**Digest.** Jeder Lauf schickt eine Nachricht, gruppiert nach Download-Weg (direkt geladen, Gate,
Store/Cloud, nur Stream), dazu eine Textdatei für Download-Tools. Unter jedem Track stehen Buttons:
👍 und 👎 werden in der Track-Datenbank gespeichert (Grundlage für Curator-Mining und das geplante
Geschmacksmodell), ⏳ merkt ein Gate für später vor.

**Bot.** Der Container startet einen Bot, der nur im Chat aus `TELEGRAM_CHAT_ID` antwortet:

| Nachricht | Antwort |
|---|---|
| Playlist-Link | alle Tracks der Playlist mit Sammlungsabgleich und Download-Einordnung |
| Track-Link | die „Station“ zum Track (SoundCloud-Radio), ebenso aufbereitet |
| `/kaufliste` | offene Store-Tracks (Bandcamp, Beatport, …) mit Kauflink |
| `/curator_mining` | SoundCloud-Profile, die deine 👍-Tracks auffällig oft geliked oder repostet haben, als Kandidaten für `reference_accounts` |
| `/mix <Key> <BPM> [Tol]` | harmonisch und tempomäßig passende Tracks aus der Track-DB (Sammlung) |

Links aus der App (`on.soundcloud.com/…`) funktionieren auch. Die Kaufliste kommt zusätzlich jeden
Sonntag um 20:00 von selbst. Fehlen die Telegram-Daten, pausiert der Bot; der tägliche Lauf läuft trotzdem.

## Installation

**Voraussetzungen:** Docker mit Compose, ein Telegram-Bot (Token von [@BotFather](https://t.me/BotFather))
und deine Chat-ID (eine Zahl, nicht der Bot-Name). Optional ein SoundCloud-OAuth-Token deines eigenen
Accounts: SoundCloud gibt Original-Dateien nur eingeloggt heraus. Ohne Token werden native Downloads
nur verlinkt.

```bash
git clone https://github.com/tripitest-art/sc-digger && cd sc-digger
cp .env.example .env              # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, optional SOUNDCLOUD_AUTH_TOKEN
# Pfade anpassen: Sammlung und Inbox in docker-compose.yml (volumes) und config.yaml (download)
docker compose build
docker compose run --rm sc-digger python -m sc_digger.main --dry-run -v   # erreicht SoundCloud?
docker compose up -d              # täglicher Lauf um 07:30, Kaufliste sonntags 20:00, Bot läuft dauerhaft
```

Die Sammlung wird **nur lesend** eingebunden (`:ro` in `docker-compose.yml`), bitte so lassen.
Auf einem Server setzt `./set-secret.sh NAME` Zugangsdaten mit verdeckter Eingabe, und
`./update.sh` holt den neuen Stand von `main` und baut neu. Betrieb, Mounts und Logs: [`BETRIEB.md`](BETRIEB.md).

**Läuft alles?** `docker ps` zeigt `healthy`, wenn Cron läuft, der letzte Lauf höchstens 36 Stunden
her ist und beide Datenbanken lesbar sind. `docker exec sc-digger-sc-digger-1 crontab -l` zeigt
die zwei Cron-Einträge.

## Konfiguration

Alles steht kommentiert in `config.yaml`:

| Abschnitt | Wofür |
|---|---|
| `search` | Genre-Tags, BPM-Fenster, Zeitraum, maximale Track-Dauer, gefolgte Artists, Referenz-Accounts |
| `scoring` | Mindest-Plays, Perzentil-Schwelle, Gewichte, Bonus für Referenz-Accounts, Abzug und Phrasen für Promo-Verdacht, Sperrliste |
| `download` | Sammlungs- und Inbox-Ordner, Cloud-Downloads ein/aus, Maximalgröße |
| `organize` | Ordnerstruktur, BPM-/Key-Erkennung, Tags schreiben |
| `quality` | Mindest-Bitrate, Spektrum-Grenzen, Brickwall-Schwellen |
| `loudness` | Pegel-Angleichung der Inbox ein/aus, Ziel-LUFS, True-Peak-Grenze |
| `fingerprint` | Klangabgleich neuer Downloads mit der Sammlung ein/aus |
| `retry` | wie oft ein gescheiterter Original-Download insgesamt versucht wird |
| `health` | ab wie vielen schlechten Läufen ein Alarm kommt |
| `telegram` | Digest-Länge, Feedback-Buttons |
| `digest` | Länge der Kaufliste |
| `curator_mining` | ab wie vielen Treffern ein Profil vorgeschlagen wird |
| `rekordbox` | XML-Export, Wochen-Playlists, Pfad-Mapping Server → DJ-Laptop |
| `state` | Pfade der beiden SQLite-Datenbanken |

## Details

### Qualitätsprüfung

Jeder Download wird geprüft, bevor er in der Inbox bleibt:

| Befund | Wie erkannt | Wohin |
|---|---|---|
| **Fake** (128er MP3 als WAV/320er neu verpackt) | harte Kante im Spektrum bei etwa 16 kHz; VBR-MP3 nach Spektrum, nicht nach Durchschnittsbitrate | `inbox/_rejected/` |
| **Brickwall-Master** | zu kleiner Dynamikumfang (EBU R128 Loudness Range), nicht die Lautheit allein, weil laute Master im Genre normal sind | `inbox/_rejected/clipped/` |
| **Schon in der Sammlung**, nur anders benannt | Audio-Fingerprint (Chromaprint) | `inbox/_rejected/duplicate/` |

Abgelehnte Dateien werden nicht gelöscht, du kannst sie dir anhören und selbst entscheiden.

### Pegel-Angleichung

Neue Downloads in der Inbox werden auf **−8,5 LUFS** gebracht. Zu laute Tracks werden abgesenkt,
zu leise nur so weit angehoben, wie der Abstand zu −0,5 dBTP True Peak es **ohne Limiter** erlaubt.

- **Samplegenau:** gleiche Sampleanzahl, Rate und Bittiefe, kein Versatz. Cues und Beatgrids, die
  Rekordbox oder Engine später setzen, stimmen damit.
- **Metadaten unverändert:** Bei WAV und AIFF werden nur die Audiodaten ersetzt, alle anderen Chunks
  (ID3, Cue, Serato, Traktor) bleiben byte-identisch. FLAC wird neu kodiert, alle Metadaten-Blöcke
  außer der Seektable bleiben erhalten.
- **Nur WAV, AIFF und FLAC.** MP3 und M4A bleiben, wie sie sind.
- **Nie in der Sammlung.** Für ältere Tracks bleibt Auto-Gain in der DJ-Software zuständig.
- Abschalten: `loudness.normalize_inbox: false`.

### Lautheits-Tags

Die gemessenen Werte (nach der Angleichung) werden in die Datei geschrieben:

| Tag | Inhalt | Beispiel |
|---|---|---|
| `REPLAYGAIN_TRACK_GAIN` | Anpassung auf −18 LUFS (ReplayGain 2.0) | `-9.50 dB` |
| `REPLAYGAIN_TRACK_PEAK` | True Peak, linear | `0.891251` |
| `SCDIGGER_LUFS` | integrierte Lautheit | `-8.5` |
| `SCDIGGER_LRA` | Loudness Range in LU | `5.3` |

MP3, AIFF und WAV als ID3-`TXXX`-Frames, FLAC als Vorbis-Kommentar, M4A als iTunes-Freeform-Atom.
WAV-Dateien bekommen zusätzlich einen RIFF-INFO-Block (Titel, Artist, Genre, Kommentar), weil
Rekordbox bei WAV nur RIFF-INFO auswertet.

### BPM-Oktav-Korrektur

Beat-Tracking erkennt gern das halbe oder doppelte Tempo. Korrigiert wird per ×2/÷2. Vorrang hat eine
BPM-Angabe des Uploaders im Text, danach das Suchfenster (`search.bpm_min/max`), danach der plausible
Bereich (`organize.bpm_plausible_min/max`). Echte Tempi außerhalb des Fensters (z. B. 140) bleiben.

### Download-Wege

| Einordnung | Was passiert |
|---|---|
| ✅ Direkt | Original über `scdl`, nur mit `SOUNDCLOUD_AUTH_TOKEN`; scheitert es, wird es beim nächsten Lauf erneut versucht |
| 🚪 Gate (Hypeddit, Droploud, ToneDen, Artist Union) | nur verlinkt, du klickst selbst durch |
| ☁️ Cloud (Dropbox, Google Drive) | optional automatisch geladen, siehe unten |
| ⏳ WeTransfer / 🔒 Mega | nur verlinkt: WeTransfer läuft nach 7 Tagen ab, Mega ist clientseitig verschlüsselt |
| 🛒 Store (Bandcamp, Beatport, …) | verlinkt und auf die Kaufliste gesetzt |

**Cloud-Downloads** sind standardmäßig aus. Eingeschaltet (`download.auto_download_native_only: false`)
lädt sc-digger Einzeldateien und ZIP-Archive von `dropbox.com` und `drive.google.com` und schickt
sie durch dieselbe Prüfung wie native Downloads. Aus ZIPs wird genau eine Datei gewählt (verlustfrei
vor M4A vor MP3, dann die größte). Nur erlaubte Hosts, auch nach Umleitungen; Dateinamen und
ZIP-Inhalte können nicht aus der Inbox ausbrechen; Maximalgröße `download.cloud_max_mb` (500 MB).

### Manuell geladene Tracks

Dateien, die manuell in den Eingangsordner gelegt werden (z. B. nach einem Gate-Download über
Hypeddit oder Droploud), durchlaufen dieselbe Pipeline wie automatisch geladene Originale:

1. **Warten:** Dateien müssen mindestens `intake_min_age_s` Sekunden unverändert sein (Standard: 120 s),
   damit laufende Kopiervorgänge nicht gestört werden.
2. **Qualitätsprüfung:** Fake-Erkennung und Brickwall-Check. Fakes → `inbox/_rejected/`.
3. **Analyse:** BPM und Tonart per Beat-Tracking und Chroma-Analyse.
4. **Tagging:** Artist, Titel, BPM, Key, Genre und Lautheits-Tags.
5. **Sortieren:** In die BPM/Key-Ordnerstruktur der Inbox (`inbox/<BPM>/<Key>/`).

| Dateityp | Ziel |
|---|---|
| Echtes Audio (WAV, FLAC, MP3, AIFF, M4A) | `inbox/<BPM>/<Key>/` |
| Fake | `inbox/_rejected/` |
| Brickwall-Master | `inbox/_rejected/clipped/` |
| Duplikat | `inbox/_rejected/duplicate/` |
| Nicht-Audio (ZIP, TXT, …) | `_eingang/_unbekannt/` |
| Prüfung fehlgeschlagen | `_eingang/_fehler/` |

Der Eingangsordner liegt standardmäßig unter `inbox/_eingang/` und wird von der Rekordbox-XML
ignoriert. Im täglichen `discover`-Lauf wird er automatisch vor den Downloads abgearbeitet.
Alternativ manuell:

```bash
python -m sc_digger.main intake              # sofort verarbeiten
python -m sc_digger.main intake --dry-run    # nur auflisten, nichts verschieben
```

Konfiguration in `config.yaml`:
```yaml
download:
  intake_dir: /music/inbox/_eingang    # Eingangsordner
  intake_min_age_s: 120                # Wartezeit in Sekunden
```

### Rekordbox

Nach jedem `discover`-Lauf schreibt sc-digger `sc-digger.xml` in die Inbox: alle geprüften Tracks mit
Titel, Artist, Genre, BPM, Tonart und Kommentar, im Ordner **„sc-digger“** mit **einer Playlist pro
Kalenderwoche** (z. B. `KW 40/2026`, neueste zuerst).

1. In Rekordbox unter **Einstellungen → Erweitert → Datenbank → rekordbox xml** den Pfad zur Datei
   angeben, aus Sicht des DJ-Laptops (z. B. `Z:\Highres\_sc-digger-inbox\sc-digger.xml`).
   Die Umrechnung der Pfade steht in `rekordbox.path_map`.
2. Die Ansicht **„rekordbox xml“** einblenden.
3. Unter „rekordbox xml → Importierte Bibliothek“ erscheinen die Wochen-Playlists.

### Sammlungs-Audit

`python -m sc_digger.main audit --report report.html` prüft die ganze Sammlung **nur lesend**:
Fakes, Brickwall-Master, doppelte Aufnahmen (Fingerprint) und alle Messwerte (BPM, Key, Bitrate,
Lautheit). Das Ergebnis landet in der Track-Datenbank und als HTML-Report. Nichts wird verschoben,
umbenannt oder gelöscht. Unveränderte Dateien werden beim nächsten Audit aus dem Cache übernommen
(`--force` misst alles neu). Der Report darf nicht im Sammlungsordner liegen.

### Health-Alarm

Jeder `discover`-Lauf wird protokolliert. Liefern `health.alert_after_bad_runs` Läufe in Folge keine
Rohtreffer oder brechen ab, kommt einmal ein 🚨-Alarm per Telegram mit der vermuteten Ursache
(z. B. geänderte client_id, Drosselung durch SoundCloud) und nach der Erholung eine ✅-Entwarnung.
Ein Tag ohne *neue* Tracks ist kein Alarm. Unabhängig davon prüft der Docker-Healthcheck alle
15 Minuten Cron, Alter des letzten Laufs und die Datenbanken.

## Grenzen

- **Inoffizielle API.** `soundcloud.py` nutzt SoundCloud api-v2 mit einer aus dem Web-Frontend
  ermittelten client_id. Bricht das, ist `soundcloud.py` die einzige Datei, die angepasst werden muss.
  Nutzung auf eigene Verantwortung und im Rahmen der SoundCloud-Nutzungsbedingungen.
- **Kein BPM-Feld bei SoundCloud.** Der Vorfilter kennt nur BPM-Angaben aus dem Freitext, deshalb
  bleiben Tracks ohne Angabe drin (`bpm_unknown_policy: keep`). Echte BPM gibt es erst nach dem Download.
- **Perzentile brauchen Masse.** Unter etwa 30 Kandidaten wird das Scoring ungenau; dann
  `max_age_days` oder `limit_per_tag` erhöhen.

## Mitentwickeln

```bash
pip install -r requirements.txt pytest     # braucht ffmpeg im PATH (fpcalc/Chromaprint optional, Tests kommen ohne aus)
python -m pytest -q                         # alle Tests ohne Netzwerk, ~15 s
```

Tests laufen ohne Netzwerk (Fakes statt SoundCloud und Telegram, synthetisches Audio per ffmpeg).
Jeder Pull Request durchläuft die Tests, CodeQL und den Check `acceptance-guard`.

Das Projekt wird zu großen Teilen mit KI-Agenten entwickelt: Ein Agent schreibt Issues mit
festen Schnittstellen und Akzeptanztests, ein anderer setzt um, ein dritter prüft. Die Regeln dafür
stehen in [`AGENTS.md`](AGENTS.md) und gelten genauso für Menschen: ein Thema pro Pull Request, nach
`main` nur per PR, Tests grün, die Sammlung nie verändern. Aufgaben mit dem Label `worker-task` sind
fertig spezifiziert. Was als Nächstes kommt: [`ROADMAP.md`](ROADMAP.md).

## Lizenz

sc-digger steht unter der **GNU General Public License v3.0 oder später** (`GPL-3.0-or-later`),
siehe [`LICENSE`](LICENSE). Du darfst den Code nutzen, ändern und weitergeben; wer eine veränderte
Fassung weitergibt, muss sie unter derselben Lizenz offenlegen. Das passt zu den Abhängigkeiten:
`mutagen` steht unter GPL-2.0-or-later.
