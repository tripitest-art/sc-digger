# sc-digger

<img width="1024" height="559" alt="image" src="https://github.com/user-attachments/assets/ef5fa4ec-e35d-4617-96e8-b50e5fd006c3" />

[![tests](https://github.com/tripitest-art/sc-digger/actions/workflows/tests.yml/badge.svg)](https://github.com/tripitest-art/sc-digger/actions/workflows/tests.yml)

**Crate-Digging-Assistent für Schranz und Hard Techno.** sc-digger durchsucht SoundCloud täglich nach neuen Tracks, bewertet sie im Verhältnis zum Genre statt nach absoluten Zahlen, gleicht sie mit deiner Sammlung ab, lädt freigegebene Originale, prüft deren echte Audioqualität, gleicht den Pegel an, erkennt BPM und Tonart, taggt und sortiert sie in eine Inbox und schickt dir einen Telegram-Digest. Ein Telegram-Bot prüft Playlists und Tracks auf Zuruf.

> **In English:** A self-hosted SoundCloud discovery tool for Schranz/Hard Techno DJs. It finds new tracks, ranks them relative to the genre, checks them against your collection, downloads only what artists offer for free, detects fake upscales, levels loudness, analyses BPM/key, tags and sorts files, and reports via Telegram. Docs and code comments are in German.

Läuft als Docker-Container auf einem Heimserver. Es ist ein Hobbyprojekt für eine konkrete Sammlung, aber Genres, Pfade und Schwellwerte stehen alle in `config.yaml`.

## Grundsätze

- **Fair zu Artists.** Geladen wird nur, was der Artist selbst freigibt: native SoundCloud-Downloads als Original-Datei, nie Stream-Rips. Download-Gates (Hypeddit, Droploud, …) werden erkannt und verlinkt, aber nie umgangen.
- **Die Sammlung bleibt unangetastet.** Der Sammlungsordner wird nur lesend eingebunden. DJ-Software wie Rekordbox verknüpft Cues über Dateipfade; Verschieben, Umbenennen oder Audio-Bearbeitung würde sie zerstören. Sortiert, getaggt und im Pegel angeglichen wird nur in der Inbox.
- **Messen, bevor verändert wird.** Lautheit, BPM und Tonart werden gemessen und als Metadaten gespeichert. Das einzige, was sc-digger am Audio neuer Downloads ändert, ist der Pegel: linear, samplegenau, ohne Limiter (siehe [Pegel-Angleichung](#pegel-angleichung)).
- **Laut scheitern.** sc-digger nutzt die inoffizielle SoundCloud-API, die irgendwann brechen wird. Ausfälle kommen als Alarm oder Hinweis im Digest an, nicht als stiller leerer Digest.

## Was ein Lauf macht

Der Standardmodus heißt `discover` und läuft im Container täglich um 07:30 per Cron (`30 7 * * *`). `sc_digger/main.py` führt die Schritte in dieser Reihenfolge aus:

1. **Retry und Eingang** (ohne `--dry-run`): Gescheiterte Original-Downloads aus der Retry-Queue erneut versuchen (höchstens `retry.max_attempts` Versuche), danach Dateien aus `download.intake_dir` verarbeiten.
2. **Quellen abfragen** (`collect_sources`): Tags aus `search.tags`, dann die mit `search.exploration_probability` gewählten Explorations-Tags, dann `search.followed_users`, dann `search.reference_accounts` (Reposts und Likes). Jede Quelle läuft in einem eigenen `try/except`; Fehler werden gesammelt, die nächste Quelle wird weiter versucht. Nach `ClientIdError` oder `RateLimitError` bricht der Lauf die weiteren Quellen ab. Referenz-Treffer werden zusätzlich über `genre_relevant` gegen die Tags geprüft.
3. **Deduplizieren**: Treffer über SoundCloud-`id` entdoppeln, Referenz-Treffer als `reference_hit` markieren.
4. **Filtern**: `filter_sets` entfernt Tracks länger als `search.max_duration_min` (Standard 12 min) und Titel, die auf `search.set_title_patterns` passen. `filter_bpm` schätzt BPM aus Titel, Tags und Beschreibung; Tracks ohne BPM bleiben bei `search.bpm_unknown_policy: keep` drin.
5. **Bewerten** (`score_tracks`): Uploads aus `scoring.blocked_accounts` aussortieren, dann Perzentil-Score aus Like-, Repost- und Kommentarverhältnis plus Engagement-Tempo und Aktualität; Bonus für Referenz-Hits, Malus bei Promo-Verdacht, Bonus/Malus aus der Artist-Reputation.
6. **State-Check**: Tracks, die in `state.db_path` (Tabelle `seen`) stehen, entfallen.
7. **Verarbeiten** (`process()`): Duplikat-Abgleich mit der Sammlung, Download-Weg klassifizieren, herunterladen. Danach `finish_file()` pro Datei:
   - `finalize_quality()` prüft Bitrate und Spektrum (`quality.check_file`). Fakes landen in `inbox/_rejected/`, Brickwall-Master in `inbox/_rejected/clipped/`, per Fingerprint erkannte Doppelaufnahmen in `inbox/_rejected/duplicate/` (wenn `fingerprint.check_downloads` aktiv).
   - `loudness.normalize_inbox_file()` gleicht den Pegel an, sofern `loudness.normalize_inbox` aktiv.
   - `analyze_track` und `resolve_bpm` bestimmen BPM (mit Oktav-Korrektur) und Camelot-Key.
   - `organize.write_tags` schreibt Tags, `organize.organize` verschiebt die Datei nach `inbox/<BPM-Bucket>/<Camelot-Key>/`.
8. **Ausliefern** (`deliver`): Telegram-Digest gruppiert nach Download-Weg, mit Feedback-Buttons, plus Export-Datei als Anhang. Store-Tracks werden per `upsert_store_item` eingetragen.
9. **State fortschreiben**: Nach dem Versand wird zuerst die Exploration-Statistik aktualisiert, dann werden die gelieferten Tracks in `seen` markiert (`state.mark_one`).
10. **Rekordbox-XML** neu schreiben (`write_rekordbox_xml`).
11. **Sonntags-Digest**: Ist der Tag ein Sonntag und `digest.sunday_summary` aktiv, sendet `stats.send_weekly_digest` die Wochenstatistik.

Jeder nicht abgebrochene `discover`-Lauf wird in der `runs`-Tabelle protokolliert (Rohtreffer vor Filtern, Fehlerstatus); Dry-Runs werden nicht protokolliert (`health`).

## Modi

Alle Modi verstehen `--config <datei>` (Standard `config.yaml`), `--dry-run` (nichts laden, nichts senden, State nicht schreiben), `--no-telegram` (Ausgabe auf der Konsole) und `-v` / `--verbose`. Im Container:

```bash
docker compose exec sc-digger python -m sc_digger.main <modus> [...]
```

| Modus | Was er tut |
|---|---|
| `discover` | Standardmodus, auch ohne Subcommand aufrufbar. Durchsucht die konfigurierten Quellen und führt die Pipeline wie oben beschrieben aus. |
| `playlist <url>` | Prüft eine Playlist oder (mit `--likes`) die Likes eines Profils gegen die Sammlung. Kein Genre-, Score- oder State-Filter, die Quelle wurde bewusst gewählt; Download, Prüfung, Analyse, Tagging und Sortieren laufen wie im Standardlauf. |
| `similar <track-url>` | Related Tracks eines Track-Links. `--radio` nutzt die Track-Station, `--filter` wendet BPM-Fenster und Scoring an, `--limit` (Standard 50) begrenzt die Zahl der Treffer. |
| `check <url>` | Verhält sich wie der Telegram-Bot zu einem Link: Playlist-Links liefern alle Tracks, Track-Links die Station dazu. Nach dem Versand werden die Treffer im State markiert, ein Perzentil-Filter läuft nicht. |
| `audit` | Prüft die bestehende Sammlung nur lesend. `--path` überschreibt `download.collection_dir`, `--report <datei.html>` schreibt einen HTML-Bericht, `--force` misst alle Dateien neu (Standard: Cache über `mtime` und Größe), `--export-xml <datei.xml>` exportiert Archiv-Tracks als Rekordbox-XML, `--check-adopted` gleicht Inbox-Tracks per Audio-Fingerprint ab und markiert sie als übernommen. Der Report darf nicht im Sammlungsordner liegen. |
| `rekordbox` | Schreibt die Rekordbox-XML der Inbox neu. |
| `intake` | Verarbeitet Dateien aus `download.intake_dir`. `--dry-run` listet sie nur auf, ohne sie zu verschieben. |
| `stats` | Zeigt die Wochenstatistik auf der Konsole. `--days` (Standard 7) setzt das Fenster, `--chart <datei.png>` speichert ein Balkendiagramm, `--send` schickt die Statistik zusätzlich per Telegram. |
| `scout` | Bandcamp-Feeds (`bandcamp.py`) und Beatport-Charts (`beatport.py`) abfragen und in `store_items` ablegen. |
| `cleanup-inbox` | Dubletten derselben SoundCloud-ID in der Inbox identifizieren; `--move` verschiebt überzählige Dateien nach `<inbox>/_rejected/duplicate/`, `--inbox` überschreibt den Standardpfad. |

Zur Strukturanalyse (erster Kick und Segmente) einer einzelnen Audiodatei dient `python -m sc_digger.structure <datei>`.

Beispiele (ohne den `docker compose exec`-Präfix):

```bash
python -m sc_digger.main playlist https://soundcloud.com/user/sets/name
python -m sc_digger.main playlist https://soundcloud.com/user --likes
python -m sc_digger.main similar https://soundcloud.com/artist/track --filter
python -m sc_digger.main check https://soundcloud.com/artist/track
python -m sc_digger.main audit --report report.html
python -m sc_digger.main audit --export-xml archiv.xml --check-adopted
python -m sc_digger.main intake --dry-run
python -m sc_digger.main stats --days 7 --chart woche.png
python -m sc_digger.main stats --send
python -m sc_digger.main scout
python -m sc_digger.main cleanup-inbox --move
```

## Telegram-Bot

Der Bot läuft dauerhaft (Hauptprozess des Containers) und antwortet nur im Chat aus `TELEGRAM_CHAT_ID` (eine Zahl, nicht der Bot-Name). Fehlen `TELEGRAM_BOT_TOKEN` oder `TELEGRAM_CHAT_ID`, pausiert der Bot; der tägliche Lauf läuft trotzdem. Links aus der App (`on.soundcloud.com/...`) werden vor dem Auflösen entkürzt.

| Nachricht / Befehl | Antwort |
|---|---|
| `/start`, `/help` | Hilfe mit Befehlsübersicht |
| Playlist- oder Set-Link | Alle Tracks der Playlist, mit Sammlungsabgleich, Download-Weg und Export-Datei |
| Track-Link | Track-Station (SoundCloud-Radio) zum Track, ebenso aufbereitet |
| `/kaufliste` | Offene Store-Tracks aus `store_items` mit Kauflink |
| `/curator_mining` (auch `/curator-mining`) | Profile, die eigene Like-Tracks auffällig oft geliked oder repostet haben, als Kandidaten für `search.reference_accounts`; die Inline-Buttons „➕ <Name>“ tragen einen Account per Klick in `search.followed_users` der lokalen Override-Datei (`SC_DIGGER_CONFIG_LOCAL`) ein |
| `/mix <Key> <BPM> [Toleranz]` | Harmonisch und tempomäßig passende Tracks aus der Track-DB, mit Camelot-Abstand; Toleranz in BPM, Standard 3.0 |
| `/stats [Tage]` | Statistik der letzten 1–365 Tage (Standard 7) |
| `/preview <Suchtext oder id>` | 20s-Sprachnachricht eines heruntergeladenen Tracks (Track-DB, seit #147) |
| `/preview sc:<SoundCloud-ID>` | 20s-Sprachnachricht direkt aus dem SoundCloud-Stream, auch für nicht heruntergeladene Tracks (Schalter `preview.stream_enabled`, Standard aus, seit #168) |
| `/sync_xml` | Schreibt die Rekordbox-XML der Inbox sofort neu (wie `python -m sc_digger.main rekordbox`) und nennt die Track-Zahl und den Zeitpunkt (seit #187) |
| `/similar <SoundCloud-Link>` | Top-5 klanglich ähnliche Aufnahmen aus der Sammlung via Chromaprint-Fingerprint (seit #280) |
| `/cleanup_inbox` | Dubletten in der Inbox auflisten (nur lesend) |

Zum Digest gehören Inline-Buttons pro Track (Like, Dislike, Später). Die Wahl landet in `sc_feedback` in `tracks.sqlite` und wird von `/mix` und `/curator_mining` ausgewertet. Die Kaufliste kommt zusätzlich sonntags um 20:00 (`0 20 * * 0`, aus `entrypoint.sh`).

## Installation

Voraussetzungen: Docker mit Compose, ein Telegram-Bot-Token und die dazugehörige Chat-ID. Optional ein SoundCloud-OAuth-Token des eigenen Accounts (`SOUNDCLOUD_AUTH_TOKEN`); ohne Token werden native Downloads nur verlinkt, weil SoundCloud Original-Dateien nur eingeloggt herausgibt.

```bash
git clone https://github.com/tripitest-art/sc-digger && cd sc-digger
cp .env.example .env
```

In `.env` gehören `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` und optional `SOUNDCLOUD_AUTH_TOKEN`. Pfade für Sammlung und Inbox stehen in `docker-compose.yml` (volumes) und in `config.yaml` (`download.collection_dir`, `download.inbox_dir`).

```bash
docker compose build
docker compose run --rm sc-digger python -m sc_digger.main --dry-run -v
docker compose up -d
```

Die Sammlung wird in `docker-compose.yml` mit `:ro` eingebunden und bleibt schreibgeschützt. Der Container startet `cron` und den Bot (`entrypoint.sh`); der Bot ist der Hauptprozess.

Auf einem Server setzt `./set-secret.sh NAME` die Zugangsdaten verdeckt in die `.env`; `./update.sh` holt den neuen Stand von `main` und baut neu. Betrieb, Mounts und Logs beschreibt `BETRIEB.md`.

## Erststart

1. `.env` aus `.env.example` kopieren und `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` sowie `SC_DIGGER_WEB_PASSWORD` (frei wählbares Passwort) eintragen.
2. `docker compose up -d --build`
3. `http://<host>:8080/` öffnen und das Passwort eingeben — Statusseite und Konfigeditor sind da.

Den Port gibt man bei Bedarf an die LAN-Adresse des Hosts frei (siehe Kommentar in `docker-compose.yml`).

Ob der Container gesund ist, prüft der Healthcheck im `Dockerfile` (`HEALTHCHECK --interval=15m`) über `sc_digger.healthcheck`. Dessen Prüfungen lassen sich auch manuell aufrufen:

```bash
docker compose exec sc-digger python -m sc_digger.healthcheck
```

## Konfiguration

Alle Optionen stehen in `config.yaml`; die folgende Liste nennt die Werte aus dieser Datei. Fehlende optionale Schlüssel fallen auf die Defaults im Code zurück.

| Schlüssel | Bedeutung | Standard |
|---|---|---|
| `search.tags` | Genre-Tags, die pro Lauf durchsucht werden | `[schranz, hardtechno, industrial techno, hard techno]` |
| `search.exploration_tags` | benachbarte Tags, die nur gelegentlich durchsucht werden | `[acid techno, warehouse techno]` |
| `search.exploration_probability` | Wahrscheinlichkeit pro Lauf, dass ein Explorations-Tag dazukommt | `0.0` |
| `search.bpm_min` / `search.bpm_max` | BPM-Fenster für Filter und BPM-Oktav-Korrektur | `150` / `165` |
| `search.bpm_unknown_policy` | Umgang mit Tracks ohne BPM-Angabe (`keep` / `drop`) | `keep` |
| `search.max_age_days` | nur Tracks der letzten N Tage | `14` |
| `search.max_duration_min` | maximale Trackdauer in Minuten (DJ-Set-Schwelle) | `12` |
| `search.set_title_patterns` | Regex-Muster (Groß/Klein egal) für kurze Mixe/Podcasts in `discover` | siehe Datei |
| `search.limit_per_tag` | maximale Treffer pro Tag und Lauf | `100` |
| `search.followed_users` | Profil-URLs, deren Uploads zusätzlich durchsucht werden | `[]` |
| `search.reference_accounts` | Profile, deren Reposts/Likes Treffer mit Bonus liefern | siehe Datei |
| `search.reference_limit` | maximale Feed-Einträge pro Referenz-Account | `50` |
| `scoring.min_plays` | Mindest-Playzahl, damit ein Track bewertet wird | `300` |
| `scoring.min_like_ratio` | harte Untergrenze für das Like-Verhältnis | `0.01` |
| `scoring.min_percentile` | Mindest-Perzentil im Kandidatenpool | `40` |
| `scoring.weights.*` | Gewichte `like_ratio`, `repost_ratio`, `velocity`, `comment_ratio`, `recency` | `0.35 / 0.25 / 0.20 / 0.10 / 0.10` |
| `scoring.reference_boost` | Bonus auf der 0–100-Skala für Referenz-Hits | `15` |
| `scoring.spam_penalty` | Malus bei Promo-Verdacht | `20` |
| `scoring.spam_phrases` | Phrasen, die auf Repost-Tausch/Promo hindeuten | siehe Datei |
| `scoring.blocked_accounts` | Uploads dieser Profile werden aussortiert | `[]` |
| `scoring.artist_reputation.*` | Reputation aus bewerteten Tracks: `enabled`, `boost_per_like`, `boost_per_download`, `max_boost`, `min_dislikes_for_penalty`, `penalty` | `true`, `5.0`, `2.0`, `20.0`, `3`, `15.0` |
| `download.collection_dir` | Pfad zur Sammlung (Duplikat-Check, Audit) | `/music/Schranz` |
| `download.inbox_dir` | Zielordner für geprüfte Downloads | `/music/inbox` |
| `download.auto_download_native_only` | nur native Originale laden; `false` schaltet Cloud-Downloads ein | `true` |
| `download.cloud_max_mb` | maximale Größe für Cloud-Downloads (MB) | `500` |
| `download.intake_dir` | Eingangsordner für manuell geladene Tracks | `/music/inbox/_eingang` |
| `download.intake_min_age_s` | Mindestalter unveränderter Dateien im Eingang | `120` |
| `organize.enabled` | Inbox nach BPM/Key einsortieren | `true` |
| `organize.bpm_bucket_size` | Breite der BPM-Ordner | `5` |
| `organize.default_genre` | Genre, das geschrieben wird, wenn der Track keines liefert | `Schranz` |
| `organize.detect_bpm` / `organize.detect_key` | BPM und Key via librosa erkennen | `true` / `true` |
| `organize.bpm_plausible_min` / `organize.bpm_plausible_max` | plausibler Genre-Bereich für die Oktav-Korrektur | `120` / `200` |
| `organize.write_tags` | Tags für Artist, Titel, BPM, Key, Genre und URL schreiben | `true` |
| `quality.accepted_lossless` | als verlustfrei akzeptierte Endungen | `[wav, aiff, aif, flac]` |
| `quality.min_mp3_bitrate_kbps` | Mindestbitrate für CBR-MP3 | `320` |
| `quality.min_cutoff_hz_for_320` | Cutoff-Grenze für MP3-Container | `19000` |
| `quality.min_cutoff_hz_for_lossless` | Cutoff-Grenze für verlustfreie Container | `19500` |
| `quality.min_loudness_range_lu` | untere LRA-Grenze (Brickwall-Verdacht) | `2.5` |
| `quality.max_true_peak_dbfs` | obere True-Peak-Grenze (Brickwall-Verdacht) | `3.0` |
| `loudness.normalize_inbox` | Pegel der neuen Inbox-Dateien anpassen | `true` |
| `loudness.target_lufs` | Ziel-Lautheit in LUFS | `-8.5` |
| `loudness.max_true_peak_dbfs` | True-Peak-Grenze beim Anheben | `-0.5` |
| `fingerprint.check_downloads` | neue Downloads gegen die Sammlung abgleichen | `true` |
| `curator_mining.min_appearances` | Mindestzahl Treffer, damit ein Profil vorgeschlagen wird | `2` |
| `curator_mining.max_likers_per_track` | maximale API-Abfragen pro Track | `50` |
| `curator_mining.min_collection_tracks` | Mindestanzahl Tracks in der Sammlung für Sammlungs-Favoriten | `3` |
| `preview.duration_s` | Dauer des Snippets in Sekunden | `20.0` |
| `preview.riser_s` | Vorlaufzeit vor Drop in Sekunden | `5.0` |
| `state.db_path` | SQLite-DB für gesehene Tracks, Läufe und Retry-Queue | `/data/seen.sqlite` |
| `state.track_db_path` | SQLite-Track-DB (WAL-Modus, nicht auf NFS) | `/data/tracks.sqlite` |
| `retry.max_attempts` | Gesamtzahl der Versuche pro Original-Download | `3` |
| `health.alert_after_bad_runs` | Alarm nach N erfolglosen Läufen in Folge | `2` |
| `health.max_hours_since_run` | Alarm, wenn der letzte Lauf älter ist als dieser Wert | `36` |
| `telegram.max_items_per_digest` | Obergrenze der Tracks im Digest (nicht in `playlist`/`check`) | `25` |
| `telegram.feedback_buttons` | Inline-Buttons für Feedback anzeigen | `true` |
| `cues.auto_queue` | Cue-Jobs für neue Inbox-Tracks in die Queue legen (GPU-Worker) | `true` |
| `rekordbox.xml_enabled` | Rekordbox-XML schreiben | `true` |
| `rekordbox.xml_path` | Zielpfad der XML | `/music/inbox/sc-digger.xml` |
| `rekordbox.path_map` | Mapping Server-Pfad zu Laptop-Pfad | `/music/inbox` → `Z:/Highres/_sc-digger-inbox` |
| `rekordbox.cues_enabled` | Memory Cue „First Kick“ aus dem Tag `SCDIGGER_FIRST_KICK` in die XML schreiben | `true` |
| `rekordbox.cues_as_hotcues` | Cues als Hot Cues A–H schreiben | `true` |
| `rekordbox.cues_as_memory` | Cues als Memory Cues schreiben | `true` |
| `rekordbox.cues_phrase_align` | Drop und Breakdown am 16/32-Beat-Phrasengitter ausrichten | `true` |
| `rekordbox.hot_cue_layout` | Pad-Layout der Hot Cues (`legacy` oder `standard_8pad`) | `legacy` |
| `rekordbox.grid_anchor` | Beatgrid-Anker `<TEMPO>` an `first_kick_s` setzen (verlässliches Beatgrid bei 150+ BPM) | `true` |
| `rekordbox.phrase_warning` | Warn-Cues an Drops/Breakdowns setzen, die außerhalb des Phrasengitters liegen | `true` |
| `rekordbox.active_loop` | Automatischen Active Loop am Outro-Start anlegen | `false` |
| `rekordbox.active_loop_beats` | Länge des Active Loops in Beats | `32` |
| `rekordbox.playlists_by_bpm` | Zusätzliche Playlists pro BPM-Bereich (Ordner BPM) | `true` |
| `rekordbox.playlists_by_key` | Zusätzliche Playlists pro Camelot-Key (Ordner Key) | `true` |
| `rekordbox.playlists_by_style` | Playlists nach Hauptclustern der Schranz-Matrix anlegen | `false` |
| `rekordbox.playlists_by_subgenre` | Playlists nach Subgenres der Schranz-Matrix anlegen | `false` |
| `rekordbox.playlists_by_version` | Playlists nach Versionstypen anlegen | `false` |
| `rekordbox.style_colors` | Track-Farbe nach Versionstyp setzen | `false` |
| `rekordbox.matrix_colors` | Track-Farbe aus Hauptclustern der Schranz-Matrix ableiten | `false` |
| `rekordbox.style_comments` | Track-Kommentar im Stil-Format überschreiben | `false` |
| `rekordbox.compact_style_comments` | Kompakte Stil-Kürzel in Track-Kommentare schreiben | `false` |
| `rekordbox.import_xml_path` | Pfad zur exportierten Rekordbox-XML als Rückkanal | `""` |
| `digest.kaufliste_max_items` | Obergrenze der Kaufliste | `30` |
| `digest.sunday_summary` | Wochenstatistik im Sonntagslauf senden | `false` |
| `digest.stats_chart` | Balkendiagramm zum Wochen-Digest | `false` |
| `digest.trend_radar` | Trend-Abschnitt (Trending Artists / Top-Tracks) im Wochen-Digest | `true` |
| `digest.trend_radar_days` | Zeitfenster der Trend-Berechnung in Tagen | `7` |
| `scout.bandcamp.feeds` | URLs für Bandcamp-RSS-Feeds von Labels und Artists | `[]` |
| `scout.beatport.charts` | URLs für Beatport-Charts | `[]` |

### Lokale Override-Datei

Die versionierte `config.yaml` bleibt die einzige Wahrheit. Änderungen ohne Git – später aus der
Weboberfläche – legt `Config.load` per Deep-Merge aus einer optionalen lokalen Override-Datei
darüber. Den Pfad nennt die Umgebungsvariable `SC_DIGGER_CONFIG_LOCAL` (im Container z. B.
`/data/config.local.yaml`). Fehlt die Datei oder ist sie leer, ändert sich nichts am Betrieb.
Kaputtes YAML scheitert laut mit Dateinamen, statt still ignoriert zu werden. Listen und Skalare
aus der Override-Datei ersetzen den Wert aus `config.yaml`, Zuordnungen werden rekursiv gemischt.
Pflegen lässt sie sich von Hand oder über den Web-Konfigeditor (siehe „Web-Konfigeditor“).

Konfiguration prüfen (Typen, Wertebereiche, Tippfehler):

```bash
python -m sc_digger.schema [--config config.yaml] [--local /data/config.local.yaml]
```

Je Problem eine Zeile `FEHLER <pfad>: <meldung>` bzw. `WARNUNG <pfad>: <meldung>`, sonst
`OK: Konfiguration gültig`. Der Exit-Code ist 1 bei mindestens einem Fehler, Warnungen allein
ändern ihn nicht. Die Prüfung verändert keine Datei.

## Funktionen im Detail

### Qualitätsprüfung

`quality.check_file` prüft jeden Download und jede Audit-Datei. `ffprobe` liefert Container, Codec, Samplerate und Bitrate; das Spektrum wird per FFT aus dem Audiosignal gemessen.

- **Lossless = Endung und Codec:** Eine Datei gilt nur als verlustfrei, wenn die Dateiendung in `quality.accepted_lossless` steht **und** der Codec verlustfrei ist (alle `pcm_*` inkl. 24-/32-Bit und Float, `flac`, `alac`, `wavpack`, `ape`, `tta`). Lossy-Streams hinter einer Lossless-Endung (z. B. AAC oder MP3 in einem `.wav`-Container) werden abgewiesen und landen in `inbox/_rejected/`.
- **Stille und unmessbares Spektrum:** Reine Stille, Signale unter dem Rauschboden (Spitze < 1e-4 bzw. ca. −80 dBFS) und Dateien unter etwa 3 s gelten als „Spektrum nicht messbar“ und werden abgewiesen (`inbox/_rejected/`). Vorher kamen sie mangels Cutoff als „echte Qualität“ durch; ohne messbares Signal erfolgt nun keine Freigabe mehr.
- **Mehrere Messfenster:** Bei Tracks ab 150 s Dauer wird das Spektrum an zwei Stellen gemessen (bei 60 s und bei 60 % der Gesamtdauer), das Maximum zählt. Damit stuft ein leises oder langes Intro einen echten Track nicht ab; ein Fake-Transcode bleibt über den gesamten Track hinweg abgeschnitten und wird weiterhin erkannt. Kürzere Tracks werden an einem Fenster gemessen (30 s ab Sekunde 60, bei sehr kurzen Dateien ab Dateianfang).
- **ALAC in `.m4a`:** `.m4a` steht standardmäßig nicht in `quality.accepted_lossless`, ALAC in `.m4a` wird deshalb abgewiesen. Wer ALAC akzeptieren möchte, ergänzt `m4a` unter `quality.accepted_lossless` (in `config.yaml` oder im Dashboard unter Konfiguration → Qualität). AAC in `.m4a` fällt auch dann weiterhin zuverlässig an der Codec-Prüfung durch.
- **CBR/VBR-MP3:** CBR-MP3 unter `quality.min_mp3_bitrate_kbps` (Standard 320) wird abgelehnt. VBR-MP3 wird an einem Xing-/VBRI-Kopf erkannt und ausschließlich über das Spektrum bewertet.
- **Spektrums-Cutoff:** Der Cutoff muss bei MP3 über `min_cutoff_hz_for_320` (Standard 19000 Hz), bei Lossless über `min_cutoff_hz_for_lossless` (19500 Hz) liegen. Liegt er darunter, gilt die Datei als Fake-Transcode.
- **Lautheit & Dynamik:** Erst nach bestandener Bitraten- und Spektrumsprüfung wird die Lautheit mit EBU R128 gemessen. Der Report enthält `integrated_lufs`, `true_peak_dbfs` und `loudness_range_lu`; `clipped` wird gesetzt, wenn die LRA unter `min_loudness_range_lu` (2.5 LU) liegt oder der True Peak `max_true_peak_dbfs` (3.0 dBFS) erreicht.
- **32-Bit/Float → 24-Bit-PCM:** Bleibt eine WAV- oder AIFF-Datei in der Inbox, prüft `quality.is_32bit_or_float` sie per `ffprobe` auf 32/64-Bit- oder Float-Samples (`pcm_f32le`, `pcm_s32le`, `sample_fmt` `flt`/`dbl`/`s32`, `bits_per_raw_sample == 32`). Solche Dateien konvertiert `quality.convert_to_pcm24` vor der Pegel-Angleichung atomar nach 24 Bit (WAV `pcm_s24le`, AIFF `pcm_s24be`, Metadaten per `-map_metadata 0`); Pioneer Rekordbox und CDJs weisen 32-Bit-WAVs sonst mit „Ungültiges Dateiformat“ ab. Der `quality_report` trägt danach den neuen Codec, `notes` den Hinweis „32-Bit-Audio für Rekordbox nach 24-Bit PCM konvertiert“. Schlägt die Konvertierung fehl, bleibt die Datei unverändert und der Lauf geht weiter.

Abgelehnte Dateien werden verschoben, nie gelöscht: Fakes und nicht messbare Tracks nach `inbox/_rejected/`, Brickwall-Master nach `inbox/_rejected/clipped/`, Duplikate nach `inbox/_rejected/duplicate/`.

### Pegel-Angleichung

`loudness.normalize_inbox_file` läuft nur bei `loudness.normalize_inbox: true` und nur für Dateien, die in der Inbox bleiben. Den Gain berechnet `plan_gain`: Absenken auf `target_lufs` (Standard −8.5 LUFS) ist immer erlaubt, Anheben ist auf den Abstand zu `max_true_peak_dbfs` (Standard −0.5 dBFS True Peak) begrenzt; ohne True-Peak-Messung wird nicht angehoben. Kein Limiter, nie.

Angepasst werden WAV (PCM 16/24/32 Bit, auch WAVE_FORMAT_EXTENSIBLE mit PCM-Subformat), AIFF (PCM 16/24/32 Bit, kein AIFC) und FLAC (PCM 16/24). Alle anderen Formate bleiben unverändert. Samplerate, Bittiefe, Kanalzahl und Sample-Anzahl bleiben gleich; WAV und AIFF ersetzen nur den `data`- bzw. `SSND`-Inhalt und lassen alle übrigen Chunks byte-identisch. FLAC wird neu kodiert, alle Metadaten-Blöcke außer der Seektable bleiben erhalten. Die Rundung nutzt TPDF-Dither, bei Übersteuerung wird gesättigt.

### Lautheits-Tags

Die Werte aus dem (nach der Normalisierung aktualisierten) `quality_report` schreibt `organize.write_tags` in die Datei:

| Tag | Inhalt | Beispiel |
|---|---|---|
| `REPLAYGAIN_TRACK_GAIN` | Anpassung von `integrated_lufs` auf die ReplayGain-Referenz −18 LUFS | `-9.50 dB` |
| `REPLAYGAIN_TRACK_PEAK` | True Peak, linear | `0.891251` |
| `SCDIGGER_LUFS` | integrierte Lautheit in LUFS | `-8.5` |
| `SCDIGGER_LRA` | LRA in LU | `5.3` |

Ablage: MP3, AIFF und WAV als ID3-`TXXX`-Frames, FLAC als Vorbis-Kommentar, M4A als iTunes-Freeform-Atom `----:com.apple.iTunes:<Schlüssel>`. WAV-Dateien bekommen zusätzlich einen `LIST`/`INFO`-Block (`INAM`, `IART`, `IGNR`, `ICMT`).

### BPM und Tonart

`analysis.detect_bpm` nutzt librosa auf einem 60-Sekunden-Ausschnitt (ab Sekunde 30, sonst von vorne). `analysis.resolve_bpm` korrigiert Oktavfehler: Kandidaten sind das erkannte Tempo und dessen Hälfte bzw. Doppeltes. Vorrang hat ein Text-BPM aus Titel, Tags oder Beschreibung (`estimate_bpm`), danach das Suchfenster `search.bpm_min/max`, dann `organize.bpm_plausible_min/max`. `analysis.detect_key` erkennt die Tonart über Chroma-CQT und Krumhansl-Kessler-Profile und gibt Camelot-Key plus Klarnamen zurück.

### Download-Wege

`pipeline.classify_download` bestimmt pro Track den Weg. Gates werden nur erkannt und verlinkt, nie automatisch durchlaufen.

| Weg | Verhalten |
|---|---|
| `NATIVE` | `downloadable` mit `has_downloads_left`: über `scdl --only-original` und `SOUNDCLOUD_AUTH_TOKEN` laden; ohne Token wird nur verlinkt, scheitert ein Download, kommt der Track in die Retry-Queue |
| `CLOUD` | Dropbox- oder Drive-Link in Beschreibung oder Kauflink; nur bei `download.auto_download_native_only: false` automatisch geladen |
| `STORE` | Bandcamp, Beatport, Traxsource, Juno: verlinkt und in `store_items` eingetragen |
| `WETRANSFER` | nur verlinkt, Links laufen ab |
| `MEGA` | nur verlinkt, clientseitig verschlüsselt |
| `HYPEDDIT`, `DROPLOUD`, `TONEDEN`, `ARTIST_UNION` | Gates, nur verlinkt |
| `NONE` | nur Stream |

`cloud.download_cloud` lädt Einzeldateien und ZIP-Archive. Umleitungen werden gegen eine Whitelist geprüft, HTML-Antworten abgelehnt, Dateinamen und ZIP-Einträge auf den letzten Pfadteil gekürzt. Aus einem ZIP gewinnt die größte Audiodatei; Rangfolge ist verlustfrei vor M4A vor MP3. Obergrenze ist `download.cloud_max_mb`. Temp-Dateien werden auch im Fehlerfall entfernt.

### Eingangsordner

`intake.find_ready_files` liefert Dateien aus `download.intake_dir`, die mindestens `intake_min_age_s` Sekunden unverändert sind, sortiert nach relativem Pfad. Versteckte Dateien und alles in Unterordnern, deren Name mit `_` beginnt, werden übersprungen. `intake.track_from_file` liest Artist und Titel aus den Tags (mutagen) oder aus dem Dateinamen `Artist - Titel`. Danach läuft dieselbe Pipeline wie bei nativen Downloads. Nicht-Audio wandert nach `_eingang/_unbekannt/`, Fehler bei der Verarbeitung nach `_eingang/_fehler/`. Der Ordner liegt in der Inbox; die Rekordbox-XML überspringt ihn.

### Sammlungs-Audit

`audit.run_audit` prüft die bestehende Sammlung nur lesend. Für jede Datei laufen `check_file`, `read_existing_tags` und `analyze_track`; die Ergebnisse landen in `tracks.sqlite`. Unveränderte Dateien (gleiche `mtime` und Größe) werden aus der DB übernommen, `--force` erzwingt eine neue Messung. Fehlende Fingerprints werden über `fill_fingerprints` ergänzt, anschließend werden Duplikat-Gruppen ermittelt. `--report <datei.html>` schreibt einen HTML-Bericht; der Pfad darf nicht im Sammlungsordner liegen. Nichts wird verschoben, umbenannt oder gelöscht.

### Health-Alarm und Container-Healthcheck

`health.record` protokolliert jeden `discover`-Lauf in `runs` (in `state.db_path`). `health.evaluate` gibt eine Alarm- oder Entwarnungsnachricht zurück: Alarm, wenn die letzten `health.alert_after_bad_runs` Läufe (Standard 2) fehlerhaft waren oder 0 Rohtreffer lieferten und der Alarm noch nicht aktiv ist; Entwarnung, sobald ein Lauf wieder Treffer hat. Die Meldung nennt möglichst eine vermutete Ursache (`client_id`, Rate Limit). Ein Tag ohne neue Tracks ist kein Alarm.

Der zweite Mechanismus ist der Container-Healthcheck (`sc_digger.healthcheck`): Cron-Prozess, letzter `discover`-Lauf (Alter gegen `health.max_hours_since_run`) und `PRAGMA quick_check` für beide SQLite-Dateien. Wechsel in den Alarm- oder Normalzustand werden per Telegram gemeldet, wenn Token und Chat-ID gesetzt sind.

### Rekordbox

`rekordbox.write_rekordbox_xml` läuft nach jedem `discover`-Lauf. `scan_inbox` liest die Inbox und überspringt alles, dessen Pfadteil mit `_rejected`, `.`, `_dl_` oder `.cloud-tmp` beginnt, sowie `_eingang`. `build_xml` erzeugt `PRODUCT`, `COLLECTION` und eine Playlist pro ISO-Kalenderwoche im Ordner `sc-digger` (neueste Woche zuerst, z. B. `KW 40/2026`). Pfade werden über `rekordbox.path_map` vom Server- auf den Laptop-Pfad umgeschrieben; Tracks ohne Mapping werden weggelassen. Die XML wird atomar geschrieben; liegt `xml_path` innerhalb `download.collection_dir`, wird der Lauf abgebrochen.

Zusätzlich zu den Wochen-Playlists können Playlists nach Tempo, Tonart und der Schranz-Matrix angelegt werden:
- `sc-digger/BPM/<Bereich>` (Breite aus `organize.bpm_bucket_size`, z. B. `150-155`) über `rekordbox.playlists_by_bpm: true`
- `sc-digger/Key/<Camelot>` (z. B. `5A`) über `rekordbox.playlists_by_key: true`
- `sc-digger/Stile/<Cluster>`, `sc-digger/Subgenres/<Subgenre>` und `sc-digger/Versionen/<Version>` über `rekordbox.playlists_by_style`, `rekordbox.playlists_by_subgenre` und `rekordbox.playlists_by_version` (jeweils Standard: `false`)

Tracks ohne BPM bzw. ohne gültigen Camelot-Key landen in `unbekannt`. Tracks können zudem anhand des Versionstyps (`style_colors`) oder des Subgenres (`matrix_colors`) farblich markiert werden. Kommentare für CDJ/XDJ-Displays lassen sich im Standard- oder Kompakt-Stil-Format schreiben (`style_comments`, `compact_style_comments`). Über `rekordbox.import_xml_path` kann die vom Laptop re-exportierte XML als Rückkanal angegeben werden.

**Vollständiges Cue-System:**
Liegen in einer Audiodatei Cue-Tags aus der Strukturanalyse (`cues.py`, `structure.py`, `stems.py`), übernimmt der XML-Export diese Marken bei aktivem `rekordbox.cues_enabled: true`:
- **Erkannte Marken:** Erster Kick (`SCDIGGER_FIRST_KICK`), erster Drop (`SCDIGGER_DROP`), zweiter Drop (`SCDIGGER_DROP2`), Breakdown-Start (`SCDIGGER_BREAKDOWN`) sowie Breakdown-Typ (`SCDIGGER_BREAKDOWN_KIND`: `silence` oder `groove`).
- **Hot Cues (Pads A–H):** Mit `rekordbox.cues_as_hotcues: true` (Standard `true`) werden Hot Cues für Performance-Pads geschrieben. Das Layout lässt sich über `hot_cue_layout` wählen (`legacy` für First Kick / Drop / Drop 2 / Breakdown oder `standard_8pad` für ein standardisiertes 8-Pad-Belegungsmuster von Intro/Main bis Outro).
- **Memory Cues:** Mit `rekordbox.cues_as_memory: true` (Standard `true`) werden die Cue-Punkte zusätzlich als Memory Cues hinterlegt.
- **Phrasen-Quantisierung:** `cues_phrase_align: true` (`phrase.py`) richtet Sektions-Cues (Drop, Breakdown) am 16/32-Beat-Phrasengitter relativ zum ersten Kick aus. Liegt ein Drop musikalisch außerhalb des Rasters, kann `phrase_warning: true` einen warnenden roten Memory Cue setzen. Der Beatgrid-Anker (`grid_anchor: true`) platziert Takt 1 / Beat 1 exakt auf den ersten Kick (`first_kick_s`), sodass das Beatgrid auch bei schnellem Techno (150+ BPM) absolut synchron sitzt.

In Rekordbox einrichten:

1. Unter **Einstellungen → Erweitert → Datenbank → rekordbox xml** den Pfad zur Datei angeben, aus Sicht des DJ-Laptops (z. B. `Z:\Highres\_sc-digger-inbox\sc-digger.xml`).
2. Die Ansicht **„rekordbox xml“** einblenden.
3. Unter „rekordbox xml → Importierte Bibliothek“ erscheinen die Wochen-Playlists.

### Schranz-Matrix

`sc_digger/style.py` ordnet Schranz- und Hard-Techno-Tracks deterministisch in ein mehrdimensionales DJ-System ein:
- **4 Hauptcluster:** *Fundament & Tools*, *Noise & Extremhärte*, *Emotion & Dramaturgie*, *Rhythmus-Crossover*.
- **13 Subgenres:** Tribal, Hardgroove, Classic, Industrial, Acid, Terror, Neo, Melodic, Bounce, Psy, Reverse Bass, EBM und Crossbreed.
- **3 stilistische Achsen:** Fluss (hypnotisch-rollend vs. impulsiv-hackernd), Metrum/Raster (straight 4/4 vs. polyrhythmisch/synkopiert) und Klangästhetik (raw/analog vs. modern/digital).
- **Versionstypen:** Erkennung von Edit, Bootleg, Remix, VIP, Mashup, Flip, Rework, Tool oder Original.
- **Energie-Klassifizierung:** Energie-Level von 1 bis 5 zur Abstimmung im Set (Warmup, Peaktime, Finale).

Die Ergebnisse steuern Rekordbox-Playlists, Farbmarkierungen und DJ-Kommentare.

### Multi-Platform-Scout

`sc_digger/scout/` erweitert die Recherche über SoundCloud hinaus auf externe Musikplattformen:
- **Bandcamp-Feeds (`bandcamp.py`):** Fragt konfigurierte RSS-Feeds von Labels und Artists (`scout.bandcamp.feeds`) ab.
- **Beatport-Charts (`beatport.py`):** Fragt Top-100- und Genre-Charts (`scout.beatport.charts`) ab.

Gefundene Tracks werden gegen die lokale Sammlung abgeglichen, Duplikate übersprungen und neue Tracks in der Tabelle `store_items` hinterlegt. Der CLI-Befehl `python -m sc_digger.main scout` stößt den Abruf manuell an. Offene Kauflinks sind im Web-Dashboard unter `/kaufliste` sowie im Telegram-Bot mit `/kaufliste` einsehbar.

## Architektur

| Modul in `sc_digger/` | Aufgabe |
|---|---|
| `main.py` | CLI und Modi, gemeinsame Pipeline `process()` und Auslieferung `deliver()` |
| `bot.py` | Telegram-Listener; ruft `main.run_link()`; beendet sich nie selbst |
| `soundcloud.py` | Inoffizielle api-v2 (client_id aus dem Frontend), Playlists inkl. Stub-Nachladen, Station/Related, Referenz-Accounts |
| `scout/` | Multi-Platform-Scout für externe Feeds und Charts (`bandcamp.py`, `beatport.py`) |
| `pipeline.py` | Text-BPM, Genre-Relevanz, Perzentil-Scoring, Download-Klassifizierung |
| `collection.py` | Duplikat-Abgleich mit der Sammlung (Fuzzy-Match, Remixer beachten) |
| `linksource.py` | Zuordnungslogik: Sammlungsdateien mit SoundCloud-Tracks verknüpfen (Teil 1) |
| `cleanup.py` | Inbox-Dubletten per SoundCloud-ID identifizieren und auflisten/verschieben |
| `output.py` | State-DB, Original-Download (scdl), `finalize_quality`, Telegram-Digest, Export-Datei, `telegram_call`, `send_telegram_photo` |
| `retry.py` | Retry-Queue für fehlgeschlagene Original-Downloads |
| `cloud.py` | Cloud-Downloads (Dropbox, Google Drive) inkl. ZIP-Auswahl |
| `quality.py` | ffprobe, Spektrum-Cutoff (Fake-Erkennung), EBU R128 / LRA (Brickwall) |
| `loudness.py` | Pegel-Normalisierung neuer Inbox-Downloads (samplegenau, Metadaten-Erhalt) |
| `analysis.py` | BPM/Key per librosa, BPM-Oktav-Korrektur `resolve_bpm` |
| `structure.py` | Strukturanalyse: Kick-Erkennung, Segmente (`intro`, `main`, `breakdown`, `outro`), erster Kick, Drop-Start und Breakdown-Anteil |
| `phrase.py` | Phrasen-Quantisierung (16/32-Beat Alignment, Quantisierung auf Phrasengitter und Downbeat-Fallback) |
| `cues.py` | Cue-Tags (`SCDIGGER_FIRST_KICK`, `SCDIGGER_DROP`, `SCDIGGER_BREAKDOWN`, `SCDIGGER_BREAKDOWN_KIND`) in Dateien lesen und schreiben |
| `stems.py` | Stem-basierte Subbass- und Breakdown-Analyse (Demucs-Stems `bass.wav`/`drums.wav`, Fake-Drop-Validierung, Breakdown-Typisierung `silence`/`groove`) |
| `style.py` | Schranz-Matrix (4 Cluster, 13 Subgenres, 3 Achsen, Versionstypen, Energie-Klassifizierung 1–5) |
| `fingerprint.py` | Audio-Fingerprints (Chromaprint/fpcalc), gleiche Aufnahme erkennen |
| `similarity.py` | Ähnlichkeitssuche über Chromaprint-Fingerprints gegen die Track-DB („Mehr davon“ im Bot per Audio-Datei-Upload) |
| `harmonic.py` | Harmonische Kompatibilität (Camelot-Wheel), Suche nach Key und BPM (`/mix`) |
| `dj_export.py` | Harmonischer DJ-Export: M3U8-Playlists, CSV und Set-Reports nach Camelot-Nachbarschaft und ΔBPM |
| `organize.py` | Inbox-Sortierung `<BPM>/<Camelot>/`, Tags schreiben (inkl. RIFF-INFO für WAV) |
| `rekordbox.py` | Rekordbox-XML mit einer Playlist pro Woche aus der Inbox |
| `embeddings.py` | Audio-Embeddings: Vektorspeicher in Track-DB, Queue-Job und Ergebnisverarbeitung |
| `similarity.py` | Ähnlichkeitssuche: Chromaprint-Fingerprints („Mehr davon“) und Kosinus-Ähnlichkeit auf Embeddings mit Fallback |
| `taste.py` | Taste-Score: Logistische Regression auf Embeddings und Feedback zur Vorhersage des Gefallens |
| `db.py` | Zentrale Track-DB: Metadaten, Fingerprints, Jobs-Queue, versionierte Migrationen |
| `audit.py` | Read-only Library-Audit, Fake-Erkennung, HTML-Dashboard |
| `intake.py` | Eingangsordner: bereite Dateien finden und als `Track` vorbereiten |
| `health.py` | Laufprotokoll, Alarm bei wiederholt leeren oder fehlerhaften Läufen |
| `healthcheck.py` | Container-Healthcheck: Cron, letzter Lauf, DB-Integrität |
| `autodeploy.py` | Host-seitiger systemd-Auto-Deployer & Rollback-Runner für `update.sh` |
| `schema.py` | Konfigurationsvalidierung und Prüfbefehl `python -m sc_digger.schema` |
| `issuecheck.py` | Planer-Preflight zur Validierung von Issue-Texten und Akzeptanztests |
| `stats.py` | Statistikberechnung für Digest und `/stats`, Vorperioden-Vergleich der Kernkennzahlen, Diagramm (`render_stats_chart`), Wochen-Digest inkl. Trend-Abschnitt |
| `trends.py` | Trend-Radar: Wachstumsanalyse historischer Engagement-Snapshots; im Wochen-Digest über `stats.send_weekly_digest` eingebunden. |
| `preview.py` | Live-Preview: energiereichstes Segment per ffmpeg, OGG-Opus-Snippet (`find_loudest_segment`, `extract_preview`). Eingebunden über den Bot-Befehl `/preview` (`bot.py`, seit #147). Seit #168 zusätzlich `extract_preview_from_url` für `/preview sc:<SoundCloud-ID>` direkt aus dem Stream. |
| `redact.py` | Zugangsdaten aus Texten, Logs und Fehlermeldungen entfernen |
| `models.py` | Datenmodelle (`Track`, `Config`) und Config-Loader |
| `worker.py` | Standalone-Worker-Client für externe GPU-Workstations (Audio-Embeddings, Cue-Berechnung) |

### Web-Dashboard (Live-Cockpit, Digest-Explorer, Gate-Assistent)

Setzt man in `.env` ein Passwort (`SC_DIGGER_WEB_PASSWORD`, siehe `.env.example`), startet der
Container zusätzlich einen FastAPI-Webserver (`python -m sc_digger.web`) auf Port `8080`. Ohne
Passwort startet er nicht (Standard aus), es wird kein zusätzlicher Port geöffnet. Die Seiten sind
im modernen Dark-Theme gestaltet und per HTTP-Basic-Auth geschützt:

- **Status (`/` & `/api/status`):** Ampel gesund/Achtung, letzter `discover`-Lauf mit Alter und Rohtreffern, aktiver Health-Alarm, Inbox-Größe, die letzten 10 Läufe und die Wochenstatistik.
- **Digest-Explorer (`/digest` & `/api/digest`):** Historische Digest-Tracks aus `seen.sqlite` durchsuchen und nach Download-Weg (`Alle`, `Geladen`, `Gate`, `Store`, `Stream`), Zeitraum (`7d`, `14d`, `30d`, `Alle`) oder Text filtern. Ermöglicht direktes Track-Feedback (👍/👎/⏳) und enthält den direkten Link `[🌐 Gate öffnen ↗]` für Fangates (Hypeddit, Droploud etc.).
- **Kaufliste (`/kaufliste`):** Übersicht aller Store-Tracks (Bandcamp, Beatport etc.) mit Filter auf gelikte Tracks.
- **Track-Explorer mit Vorhören (`/tracks` & `GET /api/audio/{id}`):** durchsucht die Track-DB und spielt Tracks aus Inbox und Sammlung direkt im Browser ab (HTML5-Audio mit Range-Requests, Springen im Track). Die Dateien werden nur gelesen und ausschließlich aus `download.inbox_dir` und `download.collection_dir` ausgeliefert; fehlende Dateien, Pfade außerhalb und Symlinks aus den Ordnern heraus ergeben 404.
- **Gate-Assistent & Drag & Drop Upload (`/intake` & `POST /api/upload-intake`):** Manuell am PC gelöste Gate-Tracks (WAV, FLAC, AIFF, MP3) einfach per Drag & Drop im Browser hochladen. Der Server übernimmt die Datei, prüft Audioqualität/Clipping, normalisiert den Pegel, analysiert BPM/Key, schreibt ID3/Vorbis-Tags und sortiert sie in die Rekordbox-Inbox ein.
- **Mix & URL-Scanner (`/mix` & `POST /api/scan`):** Harmonic Mix Finder nach Camelot-Key und BPM sowie On-Demand-Analyse beliebiger SoundCloud-Links (Tracks, Playlists, Artist-Likes) direkt im Browser.
- **Konfigeditor (`/config`):** Formular für Einstellungen aus `config.yaml` mit Diff-Vorschau (`/config/preview`) und sicherem Schreiben in die lokale Override-Datei (`SC_DIGGER_CONFIG_LOCAL`, z. B. `/data/config.local.yaml`).

Für den Zugriff aus dem LAN ist in `docker-compose.yml` eine Port-Freigabe vorbereitet.

### Wartung: Gate-Backfill

Wurden in früheren Läufen Gated Tracks im Telegram-Digest gemeldet, bevor die Tabelle `gate_items` existierte, können deren Gate-Links mit dem Wartungsskript nachträglich per SoundCloud-Batch-Abfrage in wenigen Sekunden nachgeladen werden:

```bash
docker compose exec -T sc-digger python - < scripts/backfill_gates.py
```
*(Mit `--dry-run` kann der Lauf vorab ohne Schreibzugriff simuliert werden).*

### Wartung: 32-Bit-WAVs in 24-Bit wandeln

Dateien, die vor dieser Prüfung in der Inbox gelandet sind, räumt `scripts/fix_32bit_wavs.py` rekursiv auf. Es werden nur `.wav`, `.aif` und `.aiff` angefasst, komprimierte Formate nie:

```bash
docker compose exec sc-digger python scripts/fix_32bit_wavs.py /music/inbox --dry-run
docker compose exec sc-digger python scripts/fix_32bit_wavs.py /music/inbox
```

*(`--dry-run` zeigt nur, was konvertiert würde. Die Sammlung ist schreibgeschützt und gehört nicht in den Aufruf.)*

## Grenzen

- **Inoffizielle SoundCloud-API.** `sc_digger/soundcloud.py` nutzt api-v2 mit einer aus dem Web-Frontend ermittelten `client_id`. Bricht das, ist das die einzige Stelle, die angepasst werden muss. Nutzung auf eigene Verantwortung und im Rahmen der SoundCloud-Nutzungsbedingungen.
- **Kein BPM-Feld bei SoundCloud.** Der Vorfilter kennt nur BPM-Angaben aus dem Freitext; Tracks ohne Angabe bleiben bei `bpm_unknown_policy: keep` drin. Echte BPM gibt es erst nach dem Download.
- **Perzentile brauchen Masse.** `score_tracks` warnt, wenn weniger als 10 Kandidaten den `min_plays`-Filter passieren; das Scoring ist dann wenig aussagekräftig. Abhilfe: `search.max_age_days` oder `search.limit_per_tag` erhöhen.
- **Pegel nur für WAV, AIFF und FLAC.** MP3, M4A, AIFC, Float-WAV, WAV/AIFF mit 8 Bit und kaputte Container bleiben unverändert.
- **Cloud-Downloads nur Dropbox und Google Drive.** WeTransfer (ablaufend) und Mega (clientseitig verschlüsselt) werden nur verlinkt.
- **Gates werden nicht umgangen.** Hypeddit, Droploud, ToneDen und Artist Union werden nur erkannt und verlinkt; Stream-Rips werden nicht geladen.
- **Track-DB nicht auf NFS.** `state.track_db_path` läuft im WAL-Modus und liegt daher unter `/data/`.
- **App-Playlists liefern Stubs.** `soundcloud.playlist_tracks` lädt Tracks ohne Titel separat nach (`_hydrate_stubs`); schlägt das fehl, fehlen sie im Ergebnis.

## Projektprüfung (Trackanalyse & DJ-Workflow)

Die vollständige Prüfung der Analyse-Pipeline mit Messwerten, gefundenen Fehlern und
priorisierten Änderungsvorschlägen liegt in [`_eval/REVIEW-2026-10-14.md`](_eval/REVIEW-2026-10-14.md).
Kernpunkte:

- BPM-Erkennung auf einem Ground-Truth-Korpus (29 bekannte Tempo-/Tonart-Fälle)
  **0/29 → 29/29** exakt, Key **83 % → 100 %** (Kandidat in `_eval/analysis_v2.py`).
- Ein Decode statt zwei, Chroma über oktav-gemittelte Halbton-Energie statt FFT-Bin-Summen,
  Anti-Alias-Resampling, Konfidenzwerte je BPM und Key.
- Harmonisch sortierte Playlists sind gebaut: `_eval/dj_export.py` erzeugt M3U8 + CSV + Report.

```powershell
# Set aus einem Ordner bauen (Tempo-Fenster wie in config.yaml)
python _eval\dj_export.py --folder "$env:USERPROFILE\Music\Schranz" --out .\sets --name peaktime --bpm 150 165

# Messungen reproduzieren
python _eval\gen_corpus.py _eval\corpus
python _eval\bench.py mix
```

## Mitentwickeln

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

Die Befehle brauchen `ffmpeg` im `PATH` (Qualitätsprüfung und Analyse); `fpcalc`/Chromaprint ist optional für Fingerprints. Tests laufen ohne Netzwerk (Fakes statt SoundCloud und Telegram, synthetisches Audio per ffmpeg). Ein Pull Request durchläuft die Checks `tests`, `acceptance-guard` und CodeQL. Der `acceptance-guard` vergleicht Akzeptanztests mit dem Issue und schützt `.github/` sowie die Test-Konfiguration.

Ein Probelauf ohne Telegram und ohne Downloads:

```bash
python -m sc_digger.main --dry-run --no-telegram -v
```

Ein Thema pro Pull Request, nach `main` nur per Pull Request, Tests grün, die Sammlung nie verändern. Branch-Namen: `feature/<kurz>`, `fix/<kurz>`, `docs/<kurz>`. Die Regeln und der Arbeitsablauf stehen in [`AGENTS.md`](AGENTS.md), Architektur und Stolperfallen in [`ENTWICKLUNG.md`](ENTWICKLUNG.md), der Betrieb in [`BETRIEB.md`](BETRIEB.md). Was seit dem letzten Deploy neu ist, steht in [`CHANGELOG.md`](CHANGELOG.md); dort kommt zu jedem PR eine Zeile unter „Unreleased“. Aufgaben mit dem Label `worker-task` sind vollständig spezifiziert. Was als Nächstes geplant ist: [`ROADMAP.md`](ROADMAP.md).

## Lizenz

sc-digger steht unter der **GNU General Public License v3.0 oder später** (`GPL-3.0-or-later`), siehe [`LICENSE`](LICENSE). Du darfst den Code nutzen, ändern und weitergeben; wer eine veränderte Fassung weitergibt, muss sie unter derselben Lizenz offenlegen.

Sicherheitslücken bitte vertraulich melden, nicht in einem öffentlichen Issue; den Meldeweg beschreibt [`SECURITY.md`](SECURITY.md).
