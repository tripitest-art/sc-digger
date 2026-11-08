# sc-digger

<img width="1024" height="559" alt="image" src="https://github.com/user-attachments/assets/ef5fa4ec-e35d-4617-96e8-b50e5fd006c3" />

[![tests](https://github.com/tripitest-art/sc-digger/actions/workflows/tests.yml/badge.svg)](https://github.com/tripitest-art/sc-digger/actions/workflows/tests.yml)

**Crate-Digging-Assistent für Schranz und Hard Techno.** sc-digger durchsucht SoundCloud täglich nach neuen Tracks, bewertet sie im Verhältnis zum Genre statt nach absoluten Zahlen, gleicht sie mit deiner Sammlung ab, lädt freigegebene Originale, prüft deren echte Audioqualität, gleicht den Pegel an, erkennt BPM und Tonart und taggt und sortiert sie in eine Inbox. Ein Telegram-Bot prüft Playlists und Tracks auf Zuruf, ein Web-Dashboard zeigt Status, Digest und Track-DB. Es läuft als Docker-Container auf einem Heimserver; Genres, Pfade und Schwellwerte stehen in `config.yaml`.

> **In English:** A self-hosted SoundCloud discovery tool for Schranz/Hard Techno DJs. It finds new tracks, ranks them relative to the genre, checks them against your collection, downloads only what artists offer for free, detects fake upscales, levels loudness, analyses BPM/key, tags and sorts files, and reports via Telegram. Docs and code comments are in German.

## Grundsätze

- **Fair zu Artists.** Geladen wird nur, was der Artist selbst freigibt: native SoundCloud-Downloads als Original-Datei, nie Stream-Rips. Download-Gates (Hypeddit, Droploud, …) werden erkannt und verlinkt, aber nie umgangen.
- **Die Sammlung bleibt unangetastet.** Der Sammlungsordner wird nur lesend eingebunden. DJ-Software wie Rekordbox verknüpft Cues über Dateipfade; Verschieben, Umbenennen oder Audio-Bearbeitung würde sie zerstören. Sortiert, getaggt und im Pegel angeglichen wird nur in der Inbox.
- **Messen, bevor verändert wird.** Lautheit, BPM und Tonart werden gemessen und als Metadaten gespeichert. Das einzige, was sc-digger am Audio neuer Downloads ändert, ist der Pegel: linear, samplegenau, ohne Limiter (siehe [Pegel-Angleichung](docs/funktionen.md#pegel-angleichung)).
- **Laut scheitern.** sc-digger nutzt die inoffizielle SoundCloud-API, die irgendwann brechen wird. Ausfälle kommen als Alarm oder Hinweis im Digest an, nicht als stiller leerer Digest.

## Features

`vorhanden` heißt: in diesem Stand umgesetzt und über das verlinkte Dokument im Code auffindbar. `geplant` heißt: steht ausschließlich in [`ROADMAP.md`](ROADMAP.md) und ist in diesem Stand nicht fertig.

| Bereich | Feature | Status | Doku |
|---|---|---|---|
| Suche | Genre-Tags, Explorations-Tags, gefolgte und Referenz-Accounts | vorhanden | [Discovery](docs/funktionen.md#discovery-und-scoring) |
| Suche | Perzentil-Scoring mit Referenz-Bonus, Promo-Abzug und Artist-Reputation | vorhanden | [Discovery](docs/funktionen.md#discovery-und-scoring) |
| Suche | Filter für Dauer, BPM und Set-Titel; Dedupe über SoundCloud-ID | vorhanden | [Discovery](docs/funktionen.md#discovery-und-scoring) |
| Suche | Sammlungsabgleich per Name, Fuzzy-Match und Dauer | vorhanden | [Duplikate](docs/funktionen.md#duplikate-und-fingerprints) |
| Download | Native Original-Downloads (`scdl --only-original`) mit Retry-Queue | vorhanden | [Download-Wege](docs/funktionen.md#download-wege) |
| Download | Cloud-Downloads (Dropbox, Google Drive, ZIP-Archive) | vorhanden | [Download-Wege](docs/funktionen.md#download-wege) |
| Download | Gate- und Store-Erkennung; Gates werden nur verlinkt | vorhanden | [Download-Wege](docs/funktionen.md#download-wege) |
| Download | Eingangsordner für manuell geladene Tracks | vorhanden | [Eingangsordner](docs/funktionen.md#eingangsordner) |
| Download | Store-Tracks, Kaufliste und Scout für Bandcamp/Beatport | vorhanden | [Scout](docs/funktionen.md#multi-platform-scout) |
| Qualität | Fake-Transcode-Erkennung per Spektrum-Cutoff (Multi-Window) | vorhanden | [Qualitätsprüfung](docs/funktionen.md#qualitätsprüfung) |
| Qualität | Brickwall-/LRA-Prüfung und 32-Bit-zu-24-Bit-Konvertierung | vorhanden | [Qualitätsprüfung](docs/funktionen.md#qualitätsprüfung) |
| Qualität | Pegel-Angleichung auf −8,5 LUFS ohne Limiter, ReplayGain-Tags | vorhanden | [Pegel-Angleichung](docs/funktionen.md#pegel-angleichung) |
| Qualität | Duplikaterkennung per Chromaprint-Fingerprint | vorhanden | [Duplikate](docs/funktionen.md#duplikate-und-fingerprints) |
| Analyse | BPM- und Camelot-Key-Erkennung mit Oktav-Korrektur | vorhanden | [BPM und Tonart](docs/funktionen.md#bpm-und-tonart) |
| Analyse | Tagging (ID3/FLAC/Vorbis) und Inbox-Sortierung nach BPM/Key | vorhanden | [BPM und Tonart](docs/funktionen.md#bpm-und-tonart) |
| Analyse | Strukturanalyse: erster Kick, Drop, Breakdown-Anteil | vorhanden | [Rekordbox](docs/funktionen.md#rekordbox) |
| Analyse | Schranz-Matrix: Cluster, Subgenres, Achsen, Versionstyp, Energie | vorhanden | [Schranz-Matrix](docs/funktionen.md#schranz-matrix) |
| Rekordbox | Wochen-, BPM-, Key- und Stil-Playlists, Beatgrid-Anker, Hot/Memory Cues | vorhanden | [Rekordbox](docs/funktionen.md#rekordbox) |
| Rekordbox | Rückkanal aus Rekordbox-XML (Rating, PlayCount, BPM, Key, Cues) | vorhanden | [Rekordbox](docs/funktionen.md#rekordbox) |
| Rekordbox | Cue-Jobs auf GPU-Workstation inkl. Wake-on-LAN | vorhanden | [GPU-Worker](docs/funktionen.md#gpu-worker-und-job-queue) |
| Rekordbox | My-Tags direkt in Rekordbox synchronisieren | vorhanden | [My-Tags](docs/funktionen.md#my-tags-direkt-in-rekordbox-synchronisieren) |
| Ausgabe | Telegram-Digest mit Feedback-Buttons und Export-Datei | vorhanden | [Telegram-Bot](docs/funktionen.md#telegram-bot) |
| Ausgabe | Telegram-Bot für Links, `/mix`, `/stats`, `/preview`, `/similar`, `/curator_mining` | vorhanden | [Telegram-Bot](docs/funktionen.md#telegram-bot) |
| Ausgabe | Wochenstatistik, Sonntags-Digest und Trend-Abschnitt im Wochen-Digest | vorhanden | [Statistik](docs/funktionen.md#statistik-und-trend-radar) |
| Ausgabe | Verfügbarkeits-Wächter für gelikte Stream-Tracks | vorhanden | [Wächter](docs/funktionen.md#verfügbarkeits-wächter) |
| Betrieb | Health-Alarm, Container-Healthcheck, API-Kanarienvogel, Speicherplatz-Wächter | vorhanden | [Health](docs/funktionen.md#health-alarm-und-container-healthcheck) |
| Betrieb | Lauf-Sperre gegen parallele schreibende Modi | vorhanden | [Health](docs/funktionen.md#health-alarm-und-container-healthcheck) |
| Betrieb | Tägliche WAL-konsistente Sicherung und Restore | vorhanden | [Sicherung](docs/funktionen.md#sicherung) |
| Betrieb | Übernommen-Erkennung per Fingerprint (`audit --check-adopted`) | vorhanden | [Sammlungs-Audit](docs/funktionen.md#sammlungs-audit) |
| Web | Statusseite, Digest-Explorer, Kaufliste, Quarantäne, Konfigeditor | vorhanden | [Web-Dashboard](docs/funktionen.md#web-dashboard) |
| Web | Track-Explorer mit Vorhören, Ähnlichkeitssuche und Taste-Filter | vorhanden | [Web-Dashboard](docs/funktionen.md#web-dashboard) |
| Web | Harmonic Mix Finder, Set-Builder (M3U8/Rekordbox-XML), URL-Scanner | vorhanden | [Web-Dashboard](docs/funktionen.md#web-dashboard) |
| Web | Upload & Intake, Duplikat- und Inbox-Cleanup | vorhanden | [Web-Dashboard](docs/funktionen.md#web-dashboard) |
| KI | Taste-Score aus Audio-Embeddings und Feedback | vorhanden | [Taste-Score](docs/funktionen.md#taste-score) |
| KI | GPU-Worker-Job-Queue für Cues und Embeddings | vorhanden | [GPU-Worker](docs/funktionen.md#gpu-worker-und-job-queue) |
| Geplant | Next-Track-Recommender per Embedding-Ähnlichkeit | geplant | [ROADMAP Phase 5.5](ROADMAP.md) |
| Geplant | Stem-Extraktion via Demucs für 3-Deck-Tools | geplant | [ROADMAP Phase 5.5](ROADMAP.md) |
| Geplant | Qwen als DJ-Copilot per Tool-Calling | geplant | [ROADMAP 4.3](ROADMAP.md#43-qwen-als-dj-copilot) |
| Geplant | Wöchentlicher automatischer Curator-Mining-Versand | geplant | [ROADMAP 3.5](ROADMAP.md#35-curator-mining--implementiert-64-359-361) |

## Was ein Lauf macht

Der Standardmodus heißt `discover` und läuft im Container täglich um 07:30 per Cron (`30 7 * * *`). Vorher führt der Container `audit --check-adopted` (`45 6 * * *`) aus, damit übernommene Tracks als Feedback ankommen. `sc_digger/main.py` führt dann neun Schritte aus (Details: [Discovery und Scoring](docs/funktionen.md#discovery-und-scoring)):

1. **Retry und Eingang:** gescheiterte Original-Downloads erneut versuchen, dann Dateien aus dem Eingangsordner übernehmen.
2. **Quellen abfragen:** Genre-Tags, Explorations-Tags, gefolgte und Referenz-Accounts; Fehler einer Quelle stoppen den Lauf nicht.
3. **Deduplizieren und filtern:** Treffer über SoundCloud-ID entdoppeln, DJ-Sets, zu lange Tracks und unpassende BPM aussortieren.
4. **Bewerten:** Perzentil-Score aus Engagement und Aktualität, Referenz-Bonus, Promo-Abzug, Artist-Reputation.
5. **State-Check:** bereits gesehene Tracks aus `seen.sqlite` entfernen.
6. **Verarbeiten:** Sammlungsabgleich, Download-Weg klassifizieren, freigegebene Originale laden.
7. **Nachbearbeiten:** Qualität prüfen, Pegel angleichen, BPM/Key analysieren, taggen und nach `<BPM>/<Camelot>` sortieren.
8. **Ausliefern:** Telegram-Digest mit Feedback-Buttons und Export-Datei, Store-Tracks in die Kaufliste eintragen.
9. **Fortschreiben:** Exploration-Statistik und `seen` aktualisieren, Rekordbox-XML neu schreiben, sonntags den Wochen-Digest senden.

## Quickstart

Voraussetzungen: Docker mit Compose, ein Telegram-Bot-Token und die dazugehörige Chat-ID. Optional ein SoundCloud-OAuth-Token des eigenen Accounts (`SOUNDCLOUD_AUTH_TOKEN`); ohne Token werden native Downloads nur verlinkt.

```bash
git clone https://github.com/tripitest-art/sc-digger && cd sc-digger
cp .env.example .env
docker compose build
docker compose run --rm sc-digger python -m sc_digger.main --dry-run -v
docker compose up -d
```

Für den **Erststart** in `.env` eintragen: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, optional `SOUNDCLOUD_AUTH_TOKEN` und `SC_DIGGER_WEB_PASSWORD` für das Dashboard. Danach `http://<host>:8080/` öffnen und das Passwort eingeben. Sammlung und Inbox werden in `docker-compose.yml` als Volumes eingebunden (die Sammlung mit `:ro`), die Pfade stehen zusätzlich in `config.yaml` unter `download.collection_dir` und `download.inbox_dir`. Der Dienst läuft mit `cap_drop: ALL` und `no-new-privileges`; der Healthcheck im `Dockerfile` prüft Cron, letzten Lauf und Datenbank-Integrität.

Minimal ist das schon lauffähig; alle weiteren Schlüssel und die lokale Override-Datei beschreibt die [Konfigurationsreferenz](docs/konfiguration.md). Betrieb, Mounts, Backup, Auto-Deploy und Logs stehen in [`BETRIEB.md`](BETRIEB.md).

## Modi

Alle Modi verstehen `--config <datei>` (Standard `config.yaml`), `--dry-run`, `--no-telegram` und `-v`/`--verbose`. Aufruf im Container: `docker compose exec sc-digger python -m sc_digger.main <modus> [...]`.

| Modus | Was er tut |
|---|---|
| `discover` | Standardmodus: durchsucht die konfigurierten Quellen und führt die Pipeline oben aus. |
| `playlist <url>` | Prüft eine Playlist oder mit `--likes` die Likes eines Profils gegen die Sammlung. |
| `similar <url>` | Related Tracks eines Track-Links; `--radio` nutzt die Track-Station, `--filter` BPM-Fenster und Scoring, `--limit` begrenzt die Treffer (Standard 50). |
| `check <url>` | Verhält sich wie der Telegram-Bot: Playlist-Links liefern alle Tracks, Track-Links die Station dazu. |
| `audit` | Prüft die Sammlung read-only; `--report`, `--force`, `--analyze-structure`, `--export-xml`, `--check-adopted` und `--write-tags` erweitern den Lauf. |
| `rekordbox` | Schreibt die Rekordbox-XML der Inbox neu. |
| `intake` | Verarbeitet Dateien aus `download.intake_dir`. |
| `stats` | Wochenstatistik auf der Konsole; `--days`, `--chart` und `--send`. |
| `scout` | Fragt Bandcamp-Feeds und Beatport-Charts ab und legt Store-Tracks an. |
| `cleanup-inbox` | Listet Inbox-Dubletten derselben SoundCloud-ID; `--move` verschiebt sie nach `_rejected/duplicate/`. |
| `embed-backfill` | Reiht Embedding-Jobs für vorhandene Tracks ein; `--all` und `--limit`. |
| `taste` | Trainiert den Taste-Score und schreibt ihn in die Track-DB. |

Beispiele für alle Modi und die Strukturanalyse einer einzelnen Datei stehen in [docs/funktionen.md](docs/funktionen.md#modi-beispiele).

## Web-Dashboard

Mit gesetztem `SC_DIGGER_WEB_PASSWORD` startet zusätzlich ein FastAPI-Dashboard auf Port `8080` (HTTP-Basic-Auth, ohne Passwort aus). Es zeigt auf der Statusseite Health-Alarme, Läufe, Wochenstatistik und die Betriebskarte, durchsucht Digest, Kaufliste, Quarantäne und Track-DB, spielt Tracks im Browser vor, berechnet harmonische Mixe und Sets, nimmt Uploads in den Eingangsordner an und schreibt Einstellungen in die lokale Override-Datei. Einzelheiten: [Web-Dashboard](docs/funktionen.md#web-dashboard).

## Architektur

`main.py` trägt CLI und gemeinsame Pipeline, `soundcloud.py` die inoffizielle api-v2, `pipeline.py` Scoring und Download-Klassifizierung, `quality.py`/`loudness.py`/`analysis.py` die Audio-Nachbearbeitung, `organize.py`/`rekordbox.py` Tags und Export, `db.py` die Track-DB und Job-Queue, `output.py`/`bot.py` Telegram, `web/` das Dashboard. Die vollständige Modultabelle, Zuständigkeiten und bekannte Stolperfallen stehen in [`ENTWICKLUNG.md`](ENTWICKLUNG.md).

## Grenzen und bewusste Nicht-Ziele

- **Inoffizielle SoundCloud-API.** `sc_digger/soundcloud.py` nutzt api-v2 mit einer aus dem Web-Frontend ermittelten `client_id`. Bricht das, ist das die einzige Stelle, die angepasst werden muss. Nutzung auf eigene Verantwortung und im Rahmen der SoundCloud-Nutzungsbedingungen.
- **Kein BPM-Feld bei SoundCloud.** Der Vorfilter kennt nur BPM-Angaben aus dem Freitext; Tracks ohne Angabe bleiben bei `bpm_unknown_policy: keep` drin. Echte BPM gibt es erst nach dem Download.
- **Perzentile brauchen Masse.** `score_tracks` warnt, wenn weniger als 10 Kandidaten den `min_plays`-Filter passieren; das Scoring ist dann wenig aussagekräftig. Abhilfe: `search.max_age_days` oder `search.limit_per_tag` erhöhen.
- **Pegel nur für WAV, AIFF und FLAC.** MP3, M4A, AIFC, Float-WAV, WAV/AIFF mit 8 Bit und kaputte Container bleiben unverändert.
- **Cloud-Downloads nur Dropbox und Google Drive.** WeTransfer (ablaufend) und Mega (clientseitig verschlüsselt) werden nur verlinkt.
- **Gates werden nicht umgangen.** Hypeddit, Droploud, ToneDen und Artist Union werden nur erkannt und verlinkt; Stream-Rips werden nicht geladen.
- **Track-DB nicht auf NFS.** `state.track_db_path` läuft im WAL-Modus und liegt daher unter `/data/`.
- **App-Playlists liefern Stubs.** `soundcloud.playlist_tracks` lädt Tracks ohne Titel separat nach (`_hydrate_stubs`); schlägt das fehl, fehlen sie im Ergebnis.

## Mitentwickeln und Lizenz

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

Die Befehle brauchen `ffmpeg` im `PATH`; `fpcalc`/Chromaprint ist optional. Tests laufen ohne Netzwerk. Neue Logik bekommt Tests, ein behobener Fehler einen Test, der ihn reproduziert. Abhängigkeiten stehen in `requirements.txt` (Untergrenzen, Dependabot) und `requirements.lock` (exakte Versionen mit Hashes); nach Änderungen `scripts/update-lock.sh` ausführen. Ein Pull Request durchläuft die Checks `tests`, `acceptance-guard` und CodeQL.

Ein Thema pro Pull Request, nach `main` nur per Pull Request, die Sammlung nie verändern. Branch-Namen: `feature/<kurz>`, `fix/<kurz>`, `docs/<kurz>`. Die Regeln und der Arbeitsablauf stehen in [`AGENTS.md`](AGENTS.md), Architektur und Stolperfallen in [`ENTWICKLUNG.md`](ENTWICKLUNG.md), der Betrieb in [`BETRIEB.md`](BETRIEB.md). Was seit dem letzten Deploy neu ist, steht in [`CHANGELOG.md`](CHANGELOG.md); Aufgaben mit dem Label `worker-task` sind vollständig spezifiziert, geplante Arbeiten in [`ROADMAP.md`](ROADMAP.md).

sc-digger steht unter der **GNU General Public License v3.0 oder später** (`GPL-3.0-or-later`), siehe [`LICENSE`](LICENSE). Sicherheitslücken bitte vertraulich melden, nicht in einem öffentlichen Issue; den Meldeweg beschreibt [`SECURITY.md`](SECURITY.md).
