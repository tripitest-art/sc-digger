# sc-digger

[![tests](https://github.com/tripitest-art/sc-digger/actions/workflows/tests.yml/badge.svg)](https://github.com/tripitest-art/sc-digger/actions/workflows/tests.yml)

**Crate-Digging-Assistent für Schranz und Hard Techno.** sc-digger durchsucht SoundCloud täglich
nach neuen Tracks, bewertet sie im Verhältnis zum Genre statt nach absoluten Zahlen, gleicht sie
mit deiner Sammlung ab, lädt freigegebene Originale, prüft deren echte Audioqualität, erkennt
BPM und Tonart, taggt und sortiert sie in eine Inbox und schickt dir einen Telegram-Digest.
Ein Telegram-Bot prüft Playlists und Tracks auf Zuruf.

> **In English:** A self-hosted SoundCloud discovery tool for Schranz/Hard Techno DJs. It finds new
> tracks, ranks them relative to the genre, checks them against your collection, downloads only
> what artists offer for free, detects fake upscales, analyses BPM/key, tags and sorts files, and
> reports via Telegram. Docs and code comments are in German.

Läuft als Docker-Container auf einem Heimserver. Es ist ein Hobbyprojekt für eine konkrete
Sammlung, aber die Genres, Pfade und Schwellwerte stehen alle in `config.yaml`.

## Grundsätze

- **Fair zu Artists.** Geladen wird nur, was der Artist selbst freigibt: native SoundCloud-Downloads
  als Original-Datei, nie Stream-Rips. Download-Gates (Hypeddit, Droploud, …) werden erkannt und
  verlinkt, aber nie umgangen.
- **Die Sammlung bleibt unangetastet.** Der Sammlungsordner wird nur lesend eingebunden. DJ-Software
  wie Rekordbox verknüpft Cues über Dateipfade; Verschieben, Umbenennen oder Audio-Bearbeitung würde
  sie zerstören. Sortiert und getaggt wird nur in der Inbox.
- **Messen statt verändern.** Lautheit, BPM und Tonart werden gemessen und als Metadaten gespeichert.
  Das Audio bleibt bitgenau, wie es war.
- **Laut scheitern.** sc-digger nutzt die inoffizielle SoundCloud-API, die irgendwann brechen wird.
  Ausfälle kommen als Alarm an, nicht als stiller leerer Digest.

## Was ein Lauf macht

1. **Suchen:** Genre-Tags, Uploads gefolgter Artists und Reposts/Likes von Referenz-Accounts
   (DJs, deren Geschmack du vertraust; ihre Treffer bekommen einen Bonus)
2. **BPM-Fenster:** BPM aus Titel, Tags und Beschreibung geschätzt; unbekannt bleibt drin
3. **Bewerten:** Perzentil-Score aus Likes, Reposts und Kommentaren pro Play plus Aktualität
4. **Aussortieren:** schon gemeldet (SQLite) oder schon in deiner Sammlung (Fuzzy-Match, Remixe zählen extra)
5. **Download-Weg bestimmen:** nativ, Gate, Store, Cloud-Link oder nur Stream
6. **Laden und prüfen:** Original über `scdl --only-original`, dann ffprobe, Spektrum-Check gegen
   hochkonvertierte Fakes und EBU-R128-Messung gegen Brickwall-Master (siehe unten)
7. **Analysieren:** BPM per Beat-Tracking mit Oktav-Korrektur, Tonart als Camelot-Key
8. **Taggen:** Artist, Titel, BPM, Key, Genre, SoundCloud-URL und Lautheit (ReplayGain)
9. **Sortieren:** in `inbox/<BPM-Bereich>/<Camelot-Key>/`
10. **Melden:** Telegram-Digest nach Download-Weg gruppiert, dazu eine Export-Datei

## Modi

```bash
python -m sc_digger.main                                            # discover (Standard, täglich per Cron)
python -m sc_digger.main playlist https://soundcloud.com/user/sets/name
python -m sc_digger.main playlist https://soundcloud.com/user --likes    # Likes eines Profils
python -m sc_digger.main check https://soundcloud.com/artist/track      # wie der Bot: Playlist oder Track-Station
python -m sc_digger.main similar https://soundcloud.com/artist/track    # Related Tracks
python -m sc_digger.main similar https://soundcloud.com/artist/track --radio    # Track-Radio
python -m sc_digger.main similar https://soundcloud.com/artist/track --filter   # mit BPM-Fenster und Scoring
```

Für jeden Modus: `--dry-run` (nichts laden, senden oder speichern), `--no-telegram` (Ausgabe in
der Konsole), `-v`, `--config <datei>`.

| | discover | playlist / check | similar |
|---|---|---|---|
| BPM- und Score-Filter | ja | **nein**, du hast die Quelle bewusst gewählt | nur mit `--filter` |
| „schon gemeldet“ | ja | nein, beliebig oft prüfbar | nein |
| Sammlungsabgleich, Download, Analyse, Tags, Sortieren | ja | ja | ja |
| Digest gekürzt auf `max_items_per_digest` | ja | nein, zeigt alles | ja |

### Telegram-Bot

Der Container startet einen Bot. Schick ihm einen SoundCloud-Link (auch `on.soundcloud.com`-Kurzlinks
aus der App): Bei einer Playlist bekommst du alle Tracks, bei einem Track die Station dazu, jeweils
mit Sammlungsabgleich, Download-Einordnung und Export-Datei. Er antwortet nur im Chat aus
`TELEGRAM_CHAT_ID`. Fehlen die Telegram-Daten, pausiert der Bot; der tägliche Lauf läuft trotzdem.

## Installation

**Voraussetzungen:** Docker mit Compose, ein Telegram-Bot (Token von [@BotFather](https://t.me/BotFather))
und deine Chat-ID. Optional ein SoundCloud-OAuth-Token deines eigenen Accounts: SoundCloud gibt
Original-Dateien nur eingeloggt heraus. Ohne Token werden native Downloads nur verlinkt.

```bash
git clone https://github.com/tripitest-art/sc-digger && cd sc-digger
cp .env.example .env              # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, optional SOUNDCLOUD_AUTH_TOKEN
# Pfade anpassen: Sammlung und Inbox in docker-compose.yml (volumes) und config.yaml (download)
docker compose build
docker compose run --rm sc-digger python -m sc_digger.main --dry-run -v   # erreicht SoundCloud?
docker compose up -d              # täglicher Lauf um 07:30, Bot läuft dauerhaft
```

Die Sammlung wird **nur lesend** eingebunden (`:ro` in `docker-compose.yml`), bitte so lassen.
Auf einem Server setzt `./set-secret.sh NAME` Zugangsdaten mit verdeckter Eingabe, und
`./update.sh` holt einen neuen Stand und baut neu.

### Konfiguration

Alles steht kommentiert in `config.yaml`. Die wichtigsten Stellschrauben:

| Abschnitt | Wofür |
|---|---|
| `search` | Genre-Tags, BPM-Fenster, Zeitraum, Referenz-Accounts |
| `scoring` | Mindest-Plays, Perzentil-Schwelle, Gewichte, Bonus für Referenz-Accounts |
| `download` | Sammlungs- und Inbox-Ordner |
| `quality` | Mindest-Bitrate, Spektrum-Grenzen, Brickwall-Schwellen |
| `organize` | Ordnerstruktur, BPM-/Key-Erkennung, Tags schreiben |
| `health` | ab wie vielen schlechten Läufen ein Alarm kommt |

## Details

### Qualitätsprüfung

- **Fakes:** Ein 128-kbps-MP3, das als WAV oder 320er neu verpackt wurde, hat im Spektrum trotzdem
  eine harte Kante bei etwa 16 kHz. Solche Dateien landen in `inbox/_rejected/`.
- **Brickwall-Master:** Viele Free-Downloads sind so laut gemastert, dass keine Dynamik übrig ist.
  Erkannt am Dynamikumfang (EBU R128 Loudness Range), nicht an der Lautheit allein, weil laute
  Master im Genre normal sind. Solche Dateien landen in `inbox/_rejected/clipped/`.

### BPM-Oktav-Korrektur

Beat-Tracking erkennt gern das halbe oder doppelte Tempo. Korrigiert wird per ×2/÷2. Vorrang hat eine
BPM-Angabe des Uploaders im Text, danach das Suchfenster (`search.bpm_min/max`), danach der plausible
Bereich (`organize.bpm_plausible_min/max`). Echte Tempi außerhalb des Fensters (z. B. 140) bleiben.

### Lautheits-Tags

Die gemessenen Werte werden in die Datei geschrieben, das Audio bleibt unverändert:

| Tag | Inhalt | Beispiel |
|---|---|---|
| `REPLAYGAIN_TRACK_GAIN` | Anpassung auf −18 LUFS (ReplayGain 2.0) | `-12.00 dB` |
| `REPLAYGAIN_TRACK_PEAK` | True Peak, linear | `1.122018` |
| `SCDIGGER_LUFS` | integrierte Lautheit | `-6.0` |
| `SCDIGGER_LRA` | Loudness Range in LU | `5.3` |

MP3, AIFF und WAV als ID3-`TXXX`-Frames, FLAC als Vorbis-Kommentar, M4A als iTunes-Freeform-Atom.
WAV-Dateien bekommen dabei auch die normalen Tags (Artist, Titel, BPM, Key, Genre, URL) und zusätzlich einen RIFF-INFO-Block (Titel, Artist, Genre, Kommentar), da Rekordbox bei WAV ausschließlich RIFF-INFO auswertet.

### Cloud-Downloads

Öffentlich verlinkte Downloads aus Track-Beschreibungen (Dropbox, Google Drive) können automatisch geladen und durch dieselbe Qualitätsprüfung und Inbox-Sortierung geschickt werden wie native Downloads.

- **Was geladen wird:** Einzeldateien und ZIP-Archive von `dropbox.com` und `drive.google.com`. Aus ZIPs wird genau eine Datei gewählt (bevorzugt verlustfrei vor M4A vor MP3, dann die größte).
- **Was nicht geladen wird:** Download-Gates (Hypeddit, Droploud usw. bleiben als Gate verlinkt, Goldene Regel 6), Ordner-Links, clientseitig verschlüsselte Links (Mega) oder zeitlich ablaufende Links (WeTransfer). Diese werden im Digest zum manuellen Download markiert.
- **Sicherheit:** Strikte Beschränkung auf erlaubte Download-Hosts (auch nach HTTP-Umleitungen), Dateinamen und entpackte ZIP-Inhalte können nie aus der Inbox ausbrechen, konfigurierbare Maximalgröße (`cloud_max_mb`).
- **Einschalten:** In `config.yaml` unter `download`:
  ```yaml
  download:
    auto_download_native_only: false   # false schaltet Cloud-Downloads ein (Standard: true)
    cloud_max_mb: 500                  # Maximalgröße in MB (Standard: 500)
  ```

### Health-Alarm

Jeder `discover`-Lauf wird protokolliert. Liefern `health.alert_after_bad_runs` Läufe in Folge keine
Rohtreffer oder brechen ab, kommt einmal ein 🚨-Alarm per Telegram und nach der Erholung eine
✅-Entwarnung. Ein Tag ohne *neue* Tracks ist kein Alarm.

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
pip install -r requirements.txt pytest     # braucht ffmpeg im PATH
python -m pytest -q
```

Tests laufen ohne Netzwerk (Fakes statt SoundCloud, synthetisches Audio per ffmpeg). Jeder Pull
Request durchläuft die Tests, CodeQL und den Check `acceptance-guard`.

Das Projekt wird zu großen Teilen mit KI-Agenten entwickelt: Ein Agent schreibt Issues mit
festen Schnittstellen und Akzeptanztests, ein anderer setzt um, ein dritter prüft. Die Regeln dafür
stehen in [`AGENTS.md`](AGENTS.md) und gelten genauso für Menschen: ein Thema pro Pull Request, nach
`main` nur per PR, Tests grün, die Sammlung nie verändern. Aufgaben mit dem Label `worker-task` sind
fertig spezifiziert.

## Roadmap

Track-Datenbank und Sammlungs-Audit, Duplikate per Audio-Fingerprint, Feedback-Buttons im Digest,
Cloud-Downloads, ein persönliches Geschmacksmodell aus Audio-Embeddings, Rekordbox-Export:
[`ROADMAP.md`](ROADMAP.md).

## Lizenz

sc-digger steht unter der **GNU General Public License v3.0 oder später** (`GPL-3.0-or-later`),
siehe [`LICENSE`](LICENSE). Du darfst den Code nutzen, ändern und weitergeben; wer eine veränderte
Fassung weitergibt, muss sie unter derselben Lizenz offenlegen. Das passt zu den Abhängigkeiten:
`mutagen` steht unter GPL-2.0-or-later.
