# Funktionen im Detail

Diese Seite beschreibt die Funktionen, die früher im README standen. Einstieg, Quickstart und
die Feature-Übersicht stehen in der [README](../README.md).

## Discovery und Scoring

Der Standardmodus heißt `discover` und läuft im Container täglich um 07:30 per Cron
(`30 7 * * *`). `sc_digger/main.py` führt die Schritte in dieser Reihenfolge aus:

### Pipeline im Detail

1. **Retry und Eingang** (ohne `--dry-run`): Gescheiterte Original-Downloads aus der Retry-Queue erneut versuchen (höchstens `retry.max_attempts` Versuche), danach Dateien aus `download.intake_dir` verarbeiten.
2. **Quellen abfragen** (`collect_sources`): Tags aus `search.tags`, dann die mit `search.exploration_probability` gewählten Explorations-Tags, dann `search.followed_users`, dann `search.reference_accounts` (Reposts und Likes). Jede Quelle läuft in einem eigenen `try/except`; Fehler werden gesammelt, die nächste Quelle wird weiter versucht. Nach `ClientIdError` oder `RateLimitError` bricht der Lauf die weiteren Quellen ab. Referenz-Treffer werden zusätzlich über `genre_relevant` gegen die Tags geprüft.
3. **Deduplizieren**: Treffer über SoundCloud-`id` entdoppeln, Referenz-Treffer als `reference_hit` markieren.
4. **Filtern**: `filter_sets` entfernt Tracks länger als `search.max_duration_min` (Standard 12 min) und Titel, die auf `search.set_title_patterns` passen. `filter_bpm` schätzt BPM aus Titel, Tags und Beschreibung; Tracks ohne BPM bleiben bei `search.bpm_unknown_policy: keep` drin.
5. **Bewerten** (`score_tracks`): Uploads aus `scoring.blocked_accounts` aussortieren, dann Perzentil-Score aus Like-, Repost- und Kommentarverhältnis plus Engagement-Tempo und Aktualität; Bonus für Referenz-Hits, Malus bei Promo-Verdacht, Bonus/Malus aus der Artist-Reputation.
6. **State-Check**: Tracks, die in `state.db_path` (Tabelle `seen`) stehen, entfallen.
7. **Verarbeiten** (`process()`): Duplikat-Abgleich mit der Sammlung, Download-Weg klassifizieren, herunterladen. Danach `finish_file()` pro Datei:
   - `finalize_quality()` prüft Bitrate und Spektrum (`quality.check_file`). Fakes landen in `inbox/_rejected/`, nicht messbare Dateien in `inbox/_rejected/unmeasurable/`, Brickwall-Master in `inbox/_rejected/clipped/`, per Fingerprint erkannte Doppelaufnahmen in `inbox/_rejected/duplicate/` (wenn `fingerprint.check_downloads` aktiv).
   - `loudness.normalize_inbox_file()` gleicht den Pegel an, sofern `loudness.normalize_inbox` aktiv.
   - `analyze_track` und `resolve_bpm` bestimmen BPM (mit Oktav-Korrektur) und Camelot-Key.
   - `organize.write_tags` schreibt Tags, `organize.organize` verschiebt die Datei nach `inbox/<BPM-Bucket>/<Camelot-Key>/`.
8. **Ausliefern** (`deliver`): Telegram-Digest gruppiert nach Download-Weg, mit Feedback-Buttons, plus Export-Datei als Anhang. Store-Tracks werden per `upsert_store_item` eingetragen.
9. **State fortschreiben**: Nach dem Versand wird zuerst die Exploration-Statistik aktualisiert, dann werden die gelieferten Tracks in `seen` markiert (`state.mark_one`).
10. **Rekordbox-XML** neu schreiben (`write_rekordbox_xml`).
11. **Sonntags-Digest**: Ist der Tag ein Sonntag und `digest.sunday_summary` aktiv, sendet `stats.send_weekly_digest` die Wochenstatistik.

Jeder nicht abgebrochene `discover`-Lauf wird in der `runs`-Tabelle protokolliert (Rohtreffer vor Filtern, Fehlerstatus); Dry-Runs werden nicht protokolliert (`health`).

## Modi: Beispiele

Alle Modi verstehen `--config <datei>` (Standard `config.yaml`), `--dry-run` (nichts laden, nichts
senden, State nicht schreiben), `--no-telegram` (Ausgabe auf der Konsole) und `-v` / `--verbose`.
Im Container:

```bash
docker compose exec sc-digger python -m sc_digger.main <modus> [...]
```

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
python -m sc_digger.main embed-backfill --limit 200
python -m sc_digger.main taste --dry-run
python -m sc_digger.main cleanup-inbox --move
```

Zur Strukturanalyse (erster Kick und Segmente) einer einzelnen Audiodatei dient
`python -m sc_digger.structure <datei>`.

## Download-Wege

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

## Eingangsordner

`intake.find_ready_files` liefert Dateien aus `download.intake_dir`, die mindestens `intake_min_age_s` Sekunden unverändert sind, sortiert nach relativem Pfad. Versteckte Dateien und alles in Unterordnern, deren Name mit `_` beginnt, werden übersprungen. `intake.track_from_file` liest Artist und Titel aus den Tags (mutagen) oder aus dem Dateinamen `Artist - Titel`. Danach läuft dieselbe Pipeline wie bei nativen Downloads. Nicht-Audio wandert nach `_eingang/_unbekannt/`, Fehler bei der Verarbeitung nach `_eingang/_fehler/`. Der Ordner liegt in der Inbox; die Rekordbox-XML überspringt ihn.

## Qualitätsprüfung

`quality.check_file` prüft jeden Download und jede Audit-Datei. `ffprobe` liefert Container, Codec, Samplerate und Bitrate; das Spektrum wird per FFT aus dem Audiosignal gemessen.

- **Lossless = Endung und Codec:** Eine Datei gilt nur als verlustfrei, wenn die Dateiendung in `quality.accepted_lossless` steht **und** der Codec verlustfrei ist (alle `pcm_*` inkl. 24-/32-Bit und Float, `flac`, `alac`, `wavpack`, `ape`, `tta`). Lossy-Streams hinter einer Lossless-Endung (z. B. AAC oder MP3 in einem `.wav`-Container) werden abgewiesen und landen in `inbox/_rejected/`.
- **Stille und unmessbares Spektrum:** Reine Stille, Signale unter dem Rauschboden (Spitze < 1e-4 bzw. ca. −80 dBFS) und Dateien unter etwa 3 s gelten als „Spektrum nicht messbar“ und werden abgewiesen (`inbox/_rejected/unmeasurable/`, im Report `unmeasurable: true`; getrennt von echten Fakes, weil kein Urteil möglich war). Vorher kamen sie mangels Cutoff als „echte Qualität“ durch; ohne messbares Signal erfolgt nun keine Freigabe mehr.
- **Mehrere Messfenster:** Bei Tracks ab 150 s Dauer wird das Spektrum an zwei Stellen gemessen (bei 60 s und bei 60 % der Gesamtdauer), das Maximum zählt. Damit stuft ein leises oder langes Intro einen echten Track nicht ab; ein Fake-Transcode bleibt über den gesamten Track hinweg abgeschnitten und wird weiterhin erkannt. Kürzere Tracks werden an einem Fenster gemessen (30 s ab Sekunde 60, bei sehr kurzen Dateien ab Dateianfang).
- **ALAC in `.m4a`:** ALAC in `.m4a` wird standardmäßig als verlustfrei akzeptiert, weil `m4a` in `quality.accepted_lossless` steht. Wer das abschalten möchte, nimmt `m4a` aus der Liste (in `config.yaml` oder im Dashboard unter Konfiguration → Qualität). AAC in `.m4a` fällt unabhängig davon weiterhin zuverlässig an der Codec-Prüfung durch.
- **CBR/VBR-MP3:** CBR-MP3 unter `quality.min_mp3_bitrate_kbps` (Standard 320) wird abgelehnt. VBR-MP3 wird an einem Xing-/VBRI-Kopf erkannt und ausschließlich über das Spektrum bewertet.
- **Spektrums-Cutoff:** Der Cutoff muss bei MP3 über `min_cutoff_hz_for_320` (Standard 19000 Hz), bei Lossless über `min_cutoff_hz_for_lossless` (19500 Hz) liegen. Liegt er darunter, gilt die Datei als Fake-Transcode.
- **Lautheit & Dynamik:** Erst nach bestandener Bitraten- und Spektrumsprüfung wird die Lautheit mit EBU R128 gemessen. Der Report enthält `integrated_lufs`, `true_peak_dbfs` und `loudness_range_lu`; `clipped` wird gesetzt, wenn die LRA unter `min_loudness_range_lu` (2.5 LU) liegt oder der True Peak `max_true_peak_dbfs` (3.0 dBFS) erreicht.
- **32-Bit/Float → 24-Bit-PCM:** Bleibt eine WAV- oder AIFF-Datei in der Inbox, prüft `quality.is_32bit_or_float` sie per `ffprobe` auf 32/64-Bit- oder Float-Samples (`pcm_f32le`, `pcm_s32le`, `sample_fmt` `flt`/`dbl`/`s32`, `bits_per_raw_sample == 32`). Solche Dateien konvertiert `quality.convert_to_pcm24` vor der Pegel-Angleichung atomar nach 24 Bit (WAV `pcm_s24le`, AIFF `pcm_s24be`, Metadaten per `-map_metadata 0`); Pioneer Rekordbox und CDJs weisen 32-Bit-WAVs sonst mit „Ungültiges Dateiformat“ ab. Der `quality_report` trägt danach den neuen Codec, `notes` den Hinweis „32-Bit-Audio für Rekordbox nach 24-Bit PCM konvertiert“. Schlägt die Konvertierung fehl, bleibt die Datei unverändert und der Lauf geht weiter.

Abgelehnte Dateien werden verschoben, nie gelöscht: Fakes nach `inbox/_rejected/`, nicht messbare Tracks nach `inbox/_rejected/unmeasurable/`, Brickwall-Master nach `inbox/_rejected/clipped/`, Duplikate nach `inbox/_rejected/duplicate/`.

## Pegel-Angleichung

`loudness.normalize_inbox_file` läuft nur bei `loudness.normalize_inbox: true` und nur für Dateien, die in der Inbox bleiben. Den Gain berechnet `plan_gain`: Absenken auf `target_lufs` (Standard −8.5 LUFS) ist immer erlaubt, Anheben ist auf den Abstand zu `max_true_peak_dbfs` (Standard −0.5 dBFS True Peak) begrenzt; ohne True-Peak-Messung wird nicht angehoben. Kein Limiter, nie.

Angepasst werden WAV (PCM 16/24/32 Bit, auch WAVE_FORMAT_EXTENSIBLE mit PCM-Subformat), AIFF (PCM 16/24/32 Bit, kein AIFC) und FLAC (PCM 16/24). Alle anderen Formate bleiben unverändert. Samplerate, Bittiefe, Kanalzahl und Sample-Anzahl bleiben gleich; WAV und AIFF ersetzen nur den `data`- bzw. `SSND`-Inhalt und lassen alle übrigen Chunks byte-identisch. FLAC wird neu kodiert, alle Metadaten-Blöcke außer der Seektable bleiben erhalten. Die Rundung nutzt TPDF-Dither, bei Übersteuerung wird gesättigt.

## Lautheits-Tags

Die Werte aus dem (nach der Normalisierung aktualisierten) `quality_report` schreibt `organize.write_tags` in die Datei:

| Tag | Inhalt | Beispiel |
|---|---|---|
| `REPLAYGAIN_TRACK_GAIN` | Anpassung von `integrated_lufs` auf die ReplayGain-Referenz −18 LUFS | `-9.50 dB` |
| `REPLAYGAIN_TRACK_PEAK` | True Peak, linear | `0.891251` |
| `SCDIGGER_LUFS` | integrierte Lautheit in LUFS | `-8.5` |
| `SCDIGGER_LRA` | LRA in LU | `5.3` |

Ablage: MP3, AIFF und WAV als ID3-`TXXX`-Frames, FLAC als Vorbis-Kommentar, M4A als iTunes-Freeform-Atom `----:com.apple.iTunes:<Schlüssel>`. WAV-Dateien bekommen zusätzlich einen `LIST`/`INFO`-Block (`INAM`, `IART`, `IGNR`, `ICMT`).

## BPM und Tonart

`analysis.analyze_track` dekodiert die Datei einmal (Mono, 44,1 kHz) und liefert daraus BPM mit Konfidenz (`detect_tempo`, harmonische Autokorrelation, ohne librosa/numba) und Key (`chroma`, oktav-gemittelte Halbton-Energie, Krumhansl-Kessler-Profile) samt Marge. `analysis.resolve_bpm` korrigiert Oktavfehler: Kandidaten sind das erkannte Tempo und dessen Hälfte bzw. Doppeltes. Vorrang hat ein Text-BPM aus Titel, Tags oder Beschreibung (`estimate_bpm`), danach das Suchfenster `search.bpm_min/max`, dann `organize.bpm_plausible_min/max`. Ausnahme: Ist die Audio-Erkennung sicher (`organize.bpm_audio_min_confidence`, Standard 0.6) und weicht ohne Oktavbezug von der Uploader-Angabe ab, gewinnt das Audio. Die Uploader-BPM bleibt dann als `TXXX:TEXTBPM` (FLAC `textbpm`) im Tag, und der Digest markiert den Track mit `⚠️ Uploader: <BPM>` (Abweichung über 2 BPM). Wurde die Oktave korrigiert, steht im Kommentar-Tag `BPM: 158 (Audio roh 79.0)`. Der Key kommt als Camelot-Key (`5A`) in `TKEY` bzw. FLAC-`initialkey`, der Klarname (`Cm`) in `TXXX:INITIALKEY`.

## Duplikate und Fingerprints

`fingerprint.py` erzeugt Audio-Fingerprints über Chromaprint (`fpcalc`, optional). Neue Downloads
werden bei `fingerprint.check_downloads: true` gegen die Sammlung abgeglichen und bei einem Treffer
nach `inbox/_rejected/duplicate/` verschoben. Der Namensabgleich davor (`collection.NameKey`,
`names_match`) faltet Sonderzeichen, beachtet Remixer und prüft bei Namens-Treffern die Dauer
(`DURATION_TOLERANCE_S` = 5 s). Der Audit füllt fehlende Fingerprints der Sammlung nach und meldet
Duplikat-Gruppen; die Inbox-Dubletten derselben SoundCloud-ID findet `cleanup.py` (CLI
`cleanup-inbox`, Web-Seite `/duplicates`). Gelöscht wird nie automatisch.

## Sammlungs-Audit

`audit.run_audit` prüft die bestehende Sammlung (standardmäßig read-only). Für jede Datei laufen `check_file`, `read_existing_tags` und `analyze_track` (mit Oktaven-Snap aus `search.bpm_min/max` und Plausibilitätskorrektur unpassender Tag-BPMs); die Ergebnisse landen in `tracks.sqlite`. Unveränderte Dateien (gleiche `mtime` und Größe) werden aus der DB übernommen, `--force` erzwingt eine neue Messung. Mit `--write-tags` können die ermittelten Metadaten (BPM, Camelot-Key) direkt in die Dateien geschrieben werden (z. B. für ungetaggte WAV-Sammlungen). Fehlende Fingerprints werden über `fill_fingerprints` ergänzt, anschließend werden Duplikat-Gruppen ermittelt. `--report <datei.html>` schreibt einen HTML-Bericht; der Pfad darf nicht im Sammlungsordner liegen. Nichts wird verschoben, umbenannt oder gelöscht.

Mit `--check-adopted` gleicht der Audit die Inbox per Audio-Fingerprint mit der Sammlung ab und
markiert übernommene Tracks mit Datum (`adopted_at`, Feedback `like`; ein 👎 bleibt stehen). Der
Container führt das täglich per Cron aus (`45 6 * * *`, vor dem `discover`-Lauf).

## Rekordbox

`rekordbox.write_rekordbox_xml` läuft nach jedem `discover`-Lauf. `scan_inbox` liest die Inbox und überspringt alles, dessen Pfadteil mit `_rejected`, `.`, `_dl_` oder `.cloud-tmp` beginnt, sowie `_eingang`. `build_xml` erzeugt `PRODUCT`, `COLLECTION` und eine Playlist pro ISO-Kalenderwoche im Ordner `sc-digger` (neueste Woche zuerst, z. B. `KW 40/2026`). Pfade werden über `rekordbox.path_map` vom Server- auf den Laptop-Pfad umgeschrieben; Tracks ohne Mapping werden weggelassen. Die XML wird atomar geschrieben; liegt `xml_path` innerhalb `download.collection_dir`, wird der Lauf abgebrochen. Auf der Statusseite des Dashboards schreibt der Button „Rekordbox-XML neu schreiben“ die Inbox-XML per `POST /api/rekordbox/sync` neu (Anmeldung wie alle Dashboard-Routen, `Content-Type: application/json`) und nennt die Zahl der Tracks; die XML-Erzeugung ist dieselbe wie beim täglichen Lauf und beim Bot-Befehl `/sync_xml`.

Zusätzlich zu den Wochen-Playlists können Playlists nach Tempo, Tonart und der Schranz-Matrix angelegt werden:
- `sc-digger/BPM/<Bereich>` (Breite aus `organize.bpm_bucket_size`, z. B. `150-155`) über `rekordbox.playlists_by_bpm: true`
- `sc-digger/Key/<Camelot>` (z. B. `5A`) über `rekordbox.playlists_by_key: true`
- `sc-digger/Stile/<Cluster>`, `sc-digger/Subgenres/<Subgenre>` und `sc-digger/Versionen/<Version>` über `rekordbox.playlists_by_style`, `rekordbox.playlists_by_subgenre` und `rekordbox.playlists_by_version` (jeweils Standard: `false`)

Tracks ohne BPM bzw. ohne gültigen Camelot-Key landen in `unbekannt`. Tracks können zudem anhand des Versionstyps (`style_colors`) oder des Subgenres (`matrix_colors`) farblich markiert werden. Kommentare für CDJ/XDJ-Displays lassen sich im Standard- oder Kompakt-Stil-Format schreiben (`style_comments`, `compact_style_comments`). Der **Rückkanal** läuft über `rekordbox.import_xml_path`: Zeigt der Pfad (in der lokalen Override-Datei) auf die vom DJ-Laptop zurückgelieferte XML, importiert der tägliche Lauf (`discover`, nicht Dry-Run) sie direkt nach dem XML-Export. Übernommen werden Rating, PlayCount, BPM, Key und Cues in `tracks.sqlite`; ein noch leeres Feedback wird bei Rating ≥ 4 oder ≥ 2 Plays auf `like` gesetzt. Dieselbe `rekordbox.path_map` wie beim Export ordnet Laptop-Pfade den Server-Pfaden zu. Audiodateien und Sammlung bleiben unberührt. Fehlt die Datei, wird der Import übersprungen (Warnung im Log). Ein Importfehler, eine XML ohne einen einzigen Treffer oder ein Lauf mit reinen Dateinamen-Treffern ohne Pfad-Treffer (Hinweis auf falsche `path_map`) bricht den Lauf nicht ab, erscheint aber als Health-Modus `rekordbox_import` und löst nach `health.alert_after_bad_runs` schlechten Läufen den Telegram-Alarm aus. Über `rekordbox.import_match_by_name: false` lässt sich der Namens-Fallback abschalten, sodass nur exakte Pfad-Treffer zugeordnet werden.

**Vollständiges Cue-System:**
Liegen in einer Audiodatei Cue-Tags aus der Strukturanalyse (`cues.py`, `structure.py`, `stems.py`), übernimmt der XML-Export diese Marken bei aktivem `rekordbox.cues_enabled: true`:
- **Erkannte Marken:** Erster Kick (`SCDIGGER_FIRST_KICK`), erster Drop (`SCDIGGER_DROP`), zweiter Drop (`SCDIGGER_DROP2`), Breakdown-Start (`SCDIGGER_BREAKDOWN`) sowie Breakdown-Typ (`SCDIGGER_BREAKDOWN_KIND`: `silence` oder `groove`).
- **Hot Cues (Pads A–H):** Mit `rekordbox.cues_as_hotcues: true` (Standard `true`) werden Hot Cues für Performance-Pads geschrieben. Das Layout lässt sich über `hot_cue_layout` wählen (`legacy` für First Kick / Drop / Drop 2 / Breakdown oder `standard_8pad` für ein standardisiertes 8-Pad-Belegungsmuster von Intro/Main bis Outro).
- **Memory Cues:** Mit `rekordbox.cues_as_memory: true` (Standard `true`) werden die Cue-Punkte zusätzlich als Memory Cues hinterlegt.
- **Phrasen-Quantisierung:** `cues_phrase_align: true` (`phrase.py`) richtet Sektions-Cues (Drop, Breakdown) am 16/32-Beat-Phrasengitter relativ zum ersten Kick aus. Liegt ein Drop musikalisch außerhalb des Rasters, kann `phrase_warning: true` einen warnenden roten Memory Cue setzen. Der Beatgrid-Anker (`grid_anchor: true`) platziert Takt 1 / Beat 1 exakt auf den ersten Kick (`first_kick_s`), sodass das Beatgrid auch bei schnellem Techno (150+ BPM) absolut synchron sitzt.

`audit --export-xml` exportiert die Bibliothek mit denselben Optionen wie der Inbox-Export: `rekordbox.hot_cue_layout`, `grid_anchor`, `cues_phrase_align`, `cues_as_hotcues`, `cues_as_memory` und `compact_style_comments` werden aus der Konfiguration übernommen (fehlende Schlüssel: Standardwert wie bei `write_rekordbox_xml`, also `legacy`, Anker an, Phrasen-Ausrichtung und Kompakt-Kommentar aus, Hot- und Memory-Cues an). `TotalTime` trägt die in der TrackDB gespeicherte Dauer (fehlt sie: `0`). Cue-Zeiten stammen weiterhin nur aus den Cue-Tags der Dateien; die Sammlung selbst bleibt unverändert und wird nicht getaggt.

In Rekordbox einrichten:

1. Unter **Einstellungen → Erweitert → Datenbank → rekordbox xml** den Pfad zur Datei angeben, aus Sicht des DJ-Laptops (z. B. `Z:\Highres\_sc-digger-inbox\sc-digger.xml`).
2. Die Ansicht **„rekordbox xml“** einblenden.
3. Unter „rekordbox xml → Importierte Bibliothek“ erscheinen die Wochen-Playlists.

### My-Tags direkt in Rekordbox synchronisieren

Pioneers offizieller XML-Standard unterstützt technisch keine My-Tags. Das Wartungsskript `scripts/sync_rekordbox_mytags.py` liest die analysierten Metadaten (BPM mit Oktav-Snap, Versionstyp) der Sammlung und weist My-Tags direkt in Rekordbox' SQLite/SQLCipher-Datenbank (`master.db`) zu:

```bash
# Probelauf (zeigt geplante Änderungen, sicher und schreibgeschützt):
python scripts/sync_rekordbox_mytags.py --dry-run -v

# Zuweisung in master.db (erfordert beendetes Rekordbox, erstellt automatisches Backup):
python scripts/sync_rekordbox_mytags.py -v
```

## Schranz-Matrix

`sc_digger/style.py` ordnet Schranz- und Hard-Techno-Tracks deterministisch in ein mehrdimensionales DJ-System ein:
- **4 Hauptcluster:** *Fundament & Tools*, *Noise & Extremhärte*, *Emotion & Dramaturgie*, *Rhythmus-Crossover*.
- **13 Subgenres:** Tribal, Hardgroove, Classic, Industrial, Acid, Terror, Neo, Melodic, Bounce, Psy, Reverse Bass, EBM und Crossbreed.
- **3 stilistische Achsen:** Fluss (hypnotisch-rollend vs. impulsiv-hackernd), Metrum/Raster (straight 4/4 vs. polyrhythmisch/synkopiert) und Klangästhetik (raw/analog vs. modern/digital).
- **Versionstypen:** Erkennung von Edit, Bootleg, Remix, VIP, Mashup, Flip, Rework, Tool oder Original.
- **Energie-Klassifizierung:** Energie-Level von 1 bis 5 zur Abstimmung im Set (Warmup, Peaktime, Finale).

Die Ergebnisse steuern Rekordbox-Playlists, Farbmarkierungen und DJ-Kommentare.

## Multi-Platform-Scout

`sc_digger/scout/` erweitert die Recherche über SoundCloud hinaus auf externe Musikplattformen:
- **Bandcamp-Feeds (`bandcamp.py`):** Fragt konfigurierte RSS-Feeds von Labels und Artists (`scout.bandcamp.feeds`) ab.
- **Beatport-Charts (`beatport.py`):** Fragt Top-100- und Genre-Charts (`scout.beatport.charts`) ab.

Gefundene Tracks werden gegen die lokale Sammlung abgeglichen, Duplikate übersprungen und neue Tracks in der Tabelle `store_items` hinterlegt. Der CLI-Befehl `python -m sc_digger.main scout` stößt den Abruf manuell an. Offene Kauflinks sind im Web-Dashboard unter `/kaufliste` sowie im Telegram-Bot mit `/kaufliste` einsehbar.

## Telegram-Bot

Der Bot läuft dauerhaft (Hauptprozess des Containers) und antwortet nur im Chat aus `TELEGRAM_CHAT_ID` (eine Zahl, nicht der Bot-Name). Fehlen `TELEGRAM_BOT_TOKEN` oder `TELEGRAM_CHAT_ID`, pausiert der Bot; der tägliche Lauf läuft trotzdem. Links aus der App (`on.soundcloud.com/...`) werden vor dem Auflösen entkürzt.

| Nachricht / Befehl | Antwort |
|---|---|
| `/start`, `/help` | Hilfe mit Befehlsübersicht |
| Playlist- oder Set-Link | Alle Tracks der Playlist, mit Sammlungsabgleich, Download-Weg und Export-Datei |
| Track-Link | Der verlinkte Track selbst (Download falls freigegeben) plus seine Track-Station (SoundCloud-Radio), ebenso aufbereitet |
| `/kaufliste` | Offene Store-Tracks aus `store_items` mit Kauflink |
| `/curator_mining` (auch `/curator-mining`) | Profile, die eigene Like-Tracks auffällig oft geliked oder repostet haben, als Kandidaten für `search.reference_accounts`; die Inline-Buttons „➕ <Name>“ tragen einen Account per Klick in `search.followed_users` der lokalen Override-Datei (`SC_DIGGER_CONFIG_LOCAL`) ein |
| `/mix <Key> <BPM> [Toleranz]` | Harmonisch und tempomäßig passende Tracks aus der Track-DB, mit Camelot-Abstand; Toleranz in BPM, Standard 3.0 |
| `/stats [Tage]` | Statistik der letzten 1–365 Tage (Standard 7) |
| `/preview <Suchtext oder id>` | 20s-Sprachnachricht eines heruntergeladenen Tracks (Track-DB, seit #147) |
| `/preview sc:<SoundCloud-ID>` | 20s-Sprachnachricht direkt aus dem SoundCloud-Stream, auch für nicht heruntergeladene Tracks (Schalter `preview.stream_enabled`, Standard aus, seit #168) |
| `/sync_xml` | Schreibt die Rekordbox-XML der Inbox sofort neu (wie `python -m sc_digger.main rekordbox`) und nennt die Track-Zahl und den Zeitpunkt (seit #187) |
| `/similar <SoundCloud-Link | Track-ID>` | Top-5 klanglich ähnliche Aufnahmen aus der Sammlung, derzeit per Chromaprint-Fingerprint; die Embedding-Suche ist gebaut, bekommt aber noch keine Daten (seit #280, #378) |
| `/cleanup_inbox` | Dubletten in der Inbox auflisten (nur lesend) |

Zum Digest gehören Inline-Buttons pro Track (Like, Dislike, Später). Die Wahl landet in `sc_feedback` in `tracks.sqlite` und wird von `/mix` und `/curator_mining` ausgewertet. Die Kaufliste kommt zusätzlich sonntags um 20:00 (`0 20 * * 0`, aus `entrypoint.sh`).

## Statistik und Trend-Radar

`stats.py` berechnet die Wochenstatistik für den Digest und `/stats`, inklusive Vergleich zur
Vorperiode (`prev_*`). `render_stats_chart` zeichnet das Balkendiagramm. Ist
`digest.sunday_summary` aktiv, sendet der Sonntagslauf `send_weekly_digest`; der Abschnitt
Trend-Radar (`digest.trend_radar`, Standard an) nutzt die Engagement-Snapshots aus `trends.py`
(Trending Artists und Top-Tracks über `digest.trend_radar_days`).

## Taste-Score

`python -m sc_digger.main taste` trainiert täglich um 07:05 Uhr (Cron, vor dem `discover`-Lauf)
eine logistische Regression auf den Audio-Embeddings und schreibt den Score als
`tracks.taste_score` (0..1) für jeden Track mit Embedding. Trainingsquellen sind alle
Bewertungssignale: `feedback` (👍/👎), die Digest-Buttons in `sc_feedback` über die
SoundCloud-ID, übernommene Tracks (`adopted_at`, auch die Archivzeile über dieselbe
SoundCloud-ID) und Rekordbox-Ratings ab 4 Sternen. Ein positives Signal gewinnt immer;
`later` und Unbewertete fließen nicht ein. Unter fünf Beispielen je Klasse bleibt der Lauf
ohne Fehler und ohne Änderung: Das alte Modell und die alten Scores bleiben stehen. Der Score
dient zunächst nur der Anzeige und beeinflusst die tägliche Auswahl nicht; SoundCloud-
Kandidaten haben ohne Download kein Embedding. `--dry-run` zeigt die Zahl der bewertbaren
Tracks, ohne Modell oder Scores zu schreiben.

## Verfügbarkeits-Wächter

`sc_digger.watch` läuft sonntags um 19:00 Uhr per Cron (`entrypoint.sh`) oder manuell:

```bash
python -m sc_digger.watch [--config config.yaml] [--dry-run]
```

Der Wächter prüft alle mit 👍 bewerteten Tracks (`sc_feedback.value = 'like'`), die nicht lokal in der Track-DB vorliegen (`sc_id NOT IN tracks`), auf neu freigeschaltete Original-Downloads oder Free-Download-Gates.

- **Status-Erfassung (Migration 17):** In `availability_watch` wird der Verlauf (`first_checked`, `last_checked`, `last_kind`, `available_at`) festgehalten.
- **Benachrichtigung (Regel 7):** Sobald ein bisher nicht ladbarer Track verfügbar wird (`NONE` → `DIRECT` / `HYPEDDIT` / etc.), sendet der Wächter eine Telegram-Nachricht („🔓 Jetzt verfügbar“) mit Track-Link und ggf. Gate-Link.
- **Altersgrenze:** `watch.max_age_days` (Standard `180` Tage) begrenzt die Kandidatenmenge; Tracks, die beim ersten Prüflauf bereits ladbar waren, werden erfasst, aber nicht als Neuerung gemeldet.

## Health-Alarm und Container-Healthcheck

`health.record` protokolliert jeden `discover`-Lauf in `runs` (in `state.db_path`). `health.evaluate` gibt eine Alarm- oder Entwarnungsnachricht zurück: Alarm, wenn die letzten `health.alert_after_bad_runs` Läufe (Standard 2) fehlerhaft waren oder 0 Rohtreffer lieferten und der Alarm noch nicht aktiv ist; Entwarnung, sobald ein Lauf wieder Treffer hat. Die Meldung nennt möglichst eine vermutete Ursache (`client_id`, Rate Limit). Ein Tag ohne neue Tracks ist kein Alarm.

Der zweite Mechanismus ist der Container-Healthcheck (`sc_digger.healthcheck`): Cron-Prozess, letzter `discover`-Lauf (Alter gegen `health.max_hours_since_run`) und `PRAGMA quick_check` für beide SQLite-Dateien. Wechsel in den Alarm- oder Normalzustand werden per Telegram gemeldet, wenn Token und Chat-ID gesetzt sind.

**SoundCloud-Login-Prüfung:** Zu Beginn jedes echten `discover`-Laufs prüft `guard_auth_token` den hinterlegten `SOUNDCLOUD_AUTH_TOKEN` gegen `GET https://api-v2.soundcloud.com/me`. Wird der Token von SoundCloud abgelehnt (401/403), löst das Health-System einmalig einen Alarm (`sc_login`) mit Handlungsanweisung (`./set-secret.sh`) aus. Der Lauf bricht nicht ab, sondern arbeitet mit ausgeblendetem Token weiter: Original-Downloads werden wie ohne Token nur verlinkt und die Retry-Queue verbraucht keine Fehlversuche. Sobald der Token erneuert wurde, folgt automatisch eine Entwarnung.

**Speicherplatz-Wächter:** Vor jedem echten `discover`-Lauf prüft `guard_disk` den freien Speicherplatz unter `download.inbox_dir` gegen `health.min_free_gb`. Ist der Wächter aktiv (> 0 GB) und der freie Platz unterschreitet die Schwelle oder das Dateisystem ist nicht lesbar, pausiert der Lauf Downloads: Tracks werden nur verlinkt (wie bei `--dry-run`), die Retry-Queue verbraucht keine Versuche, und es wird einmalig ein Health-Alarm (`disk`) mit Handlungsanweisung zum Aufräumen gesendet. Sobald wieder ausreichend Platz frei ist, folgt automatisch eine Entwarnung.

**API-Kanarienvogel:** `python -m sc_digger.canary` prüft täglich um 07:15 Uhr (vor dem `discover`-Lauf) das Rohformat der inoffiziellen SoundCloud-API. Fehlen in Such- oder Track-Antworten Felder, die `SoundCloudClient._to_track` erwartet (z. B. `playback_count`, `likes_count`, `media.transcodings`), schlägt ein Health-Alarm (Modus `api_schema`) per Telegram an und nennt die fehlenden Felder. Sobald die Antworten wieder vollständig sind, folgt automatisch eine Entwarnung. Temporäre Netzwerk- oder Verbindungsfehler werden ignoriert, da sie bereits durch den `discover`-Alarm abgedeckt sind.

**Lauf-Sperre:** Schreibende Modi von `python -m sc_digger.main` (alle außer `stats`) laufen nie gleichzeitig. Die Sperre ist ein `flock` auf `pipeline.lock` neben der State-DB (Verzeichnis von `state.db_path`); die Datei nennt den laufenden Modus und die Startzeit. Ist sie belegt, wartet ein Lauf höchstens `state.lock_wait_s` Sekunden (Standard 1800) und bricht dann mit Exit-Code 75 ab; ein so ausgefallener `discover` zählt im Health-Protokoll als gescheiterter Lauf. Lässt sich die Sperre nicht anlegen (kein beschreibbares Verzeichnis), läuft der Modus mit Warnung ohne Sperre. `seen.sqlite` läuft wie die Track-DB im WAL-Modus mit 30 s Wartezeit.

## Sicherung

`sc_digger.backup` sichert täglich um 06:30 Uhr (vor dem Übernommen-Abgleich und dem täglichen `discover`-Lauf) die SQLite-Datenbanken (`seen.sqlite`, `tracks.sqlite`) sowie die lokale Override-Datei (`config.local.yaml`, falls über `SC_DIGGER_CONFIG_LOCAL` gesetzt).

- **Konsistente WAL-Sicherung:** Da `tracks.sqlite` und `seen.sqlite` im WAL-Modus laufen, werden Datenbanken über die SQLite Backup-API gesichert und im Ziel auf `PRAGMA journal_mode=DELETE` umgestellt. So gehen ungeschriebene Transaktionen aus dem `-wal` nicht verloren, und die Kopie ist eine in sich geschlossene Einzeldatei ohne begleitende `-wal`- oder `-shm`-Dateien.
- **Integritätsprüfung:** Jede Kopie wird mit `PRAGMA integrity_check` geprüft. Schlägt die Prüfung fehl, wird die Datei entfernt und der Lauf bricht laut mit `BackupError` ab.
- **Atomarer Tagesordner & Rotation:** Sicherungen liegen unter `backup.target_dir/<JJJJ-MM-TT>/` (zuerst in `.<JJJJ-MM-TT>.tmp` geschrieben, dann atomar umbenannt). Alte Stände werden rotiert (`keep_daily`: Standard 7 tägliche, `keep_weekly`: Standard 4 wöchentliche Stände).
- **Alarmierung (Regel 7):** Bei Fehlern schlägt der Lauf fehl (Exit 1) und schickt einen Alarm per Telegram (`🚨 sc-digger: Sicherung fehlgeschlagen ...`). Ist `backup.target_dir` leer, ist die Sicherung deaktiviert (Exit 2, kein Alarm).
- **Wiederherstellung:** `python -m sc_digger.backup restore <sicherung.sqlite> <ziel.sqlite> [--force]` prüft die Sicherung vor dem Schreiben und stellt sie wieder her. Mit `--force` wird die bisherige Datei vorher als `.vor-restore` gesichert und alte `-wal`/`-shm`-Dateien werden entfernt.

## GPU-Worker und Job-Queue

Die Track-DB (`db.py`) enthält eine versionierte `jobs`-Tabelle für asynchrone Aufgaben auf einer
externen GPU-Workstation (Cues und Embeddings). Der Server bietet dafür
`POST /api/jobs/claim`, `POST /api/jobs/{id}/heartbeat`, `POST /api/jobs/{id}/complete` und
`GET /api/jobs/{id}/audio`; `worker.py` ist der Standalone-Client mit Heartbeat,
Wiederholungs-Backoff und `--idle-exit`. `cue_infer.py` rechnet auf der Workstation Cues
(all-in-one mit `beat_this`-Beats oder lokale `librosa`-Analyse als Rückfall), `embeddings.py`
liefert den Vektorspeicher und die Embedding-Jobs. `gpu_wake.py` weckt die Workstation bei
offenen Cue-Jobs per Wake-on-LAN-Proxy. Einrichtung und Betrieb stehen in
[BETRIEB.md](../BETRIEB.md).

## Web-Dashboard

Setzt man in `.env` ein Passwort (`SC_DIGGER_WEB_PASSWORD`, siehe `.env.example`), startet der
Container zusätzlich einen FastAPI-Webserver (`python -m sc_digger.web`) auf Port `8080`. Ohne
Passwort startet er nicht (Standard aus), es wird kein zusätzlicher Port geöffnet. Die Seiten sind
im modernen Dark-Theme gestaltet (mit `color-scheme: dark` für einheitliche Scrollbars und Formularfelder,
barrierefreien SVG-Icons statt Emoji für Header, Chips und Feedback, WCAG-konformen Textkontrasten ab 4,5:1
sowie sichtbarem Tastaturfokus per `:focus-visible` auf allen interaktiven Bedienelementen) und per HTTP-Basic-Auth geschützt:

- **Status (`/` & `/api/status`):** Ampel gesund/Achtung, letzter `discover`-Lauf mit Alter und Rohtreffern, aktiver Health-Alarm, Inbox-Größe, die letzten 10 Läufe, die Wochenstatistik mit Vergleich zur Vorperiode (▲/▼ mit Prozent oder „–“ ohne Vergleichswert) sowie die Betriebskarte (Zustand von Speicherplatz-Wächter, täglicher Sicherung und API-Kanarienvogel).
- **Digest-Explorer (`/digest` & `/api/digest`):** Historische Digest-Tracks aus `seen.sqlite` durchsuchen und nach Download-Weg (`Alle`, `Geladen`, `Gate`, `Store`, `Stream`), Zeitraum (`7d`, `14d`, `30d`, `Alle`) oder Text filtern; Tabellensortierung per Klick auf die Spaltenköpfe, 1-Klick Quick-Filter-Chips und wählbare Zeilenmenge (25/50/100/200). Ermöglicht direktes Track-Feedback (👍/👎/⏳), Matcher-Korrekturen per Button („Schon in Sammlung“, „Kein Duplikat“, „Zurücknehmen“) und enthält den direkten Link `[🌐 Gate öffnen ↗]` für Fangates (Hypeddit, Droploud etc.).
- **Kaufliste (`/kaufliste` & `GET /api/watch`):** Übersicht aller Store-Tracks (Bandcamp, Beatport etc.) mit Filter auf gelikte Tracks und Quick-Filter-Chips sowie Verfügbarkeits-Wächter-Ansicht (`data-watch-section`) für überwachte 👍-Stream-Tracks ohne Original (inkl. Markierung „Jetzt verfügbar“).
- **Track-Explorer mit Vorhören (`/tracks` & `GET /api/audio/{id}`):** durchsucht die Track-DB und spielt Tracks aus Inbox und Sammlung direkt im Browser ab (HTML5-Audio mit Range-Requests, Springen im Track); Tabellensortierung per Klick auf Spaltenköpfe und wählbare Zeilenmenge (25/50/100/200). Aktive Filter erscheinen über der Tabelle als entfernbare Filter-Chips, und beim horizontalen Scrollen bleiben Artist und Titel als fixierte Spalten sichtbar. Die Sticky Bottom DJ-Deck-Bar öffnet sich beim Abspielen automatisch (verhindert überlappende Player, `-15s`/`+30s` Drop-Check-Buttons, Vor/Zurück `⏮`/`⏭` und Tastatur-Shortcuts `Space`, `←`/`→`, `J`/`K`). Ein Klick auf `⚡ Match` in der Spalte *Aktionen* öffnet direkt den Harmonic Mix Finder (`/mix?key=...&bpm=...`) für harmonisch und tempomäßig passende Anschluss-Tracks; ein Klick auf den Button *Ähnlich* lädt per `GET /api/tracks/{track_id}/similar` die Top-N ähnlichsten Tracks der Sammlung (Audio-Embeddings mit Chromaprint-Fallback) in ein einblendbares Panel (`#similar-panel`). Die Spalte *Taste* zeigt den täglich berechneten Geschmacks-Score in Prozent und lässt sich sortieren; `Min. Taste` filtert darauf, die Schnellwahl `💚 Mein Geschmack (≥70%)` zeigt passende Inbox-Tracks, und eine Zeile nennt Stand und Beispielzahlen des Taste-Modells.
- **Upload & Intake (`/intake`, Endpunkt `POST /api/upload-intake`):** Eigene Seite für Mehrfach-Uploads per Dateiauswahl oder Drag & Drop. Mehrere Gate- oder Kauf-Tracks (WAV, FLAC, AIFF, MP3) können gleichzeitig abgelegt werden und laufen nacheinander durch dieselbe Pipeline (Audioqualität/Fake-Check, Pegel-Normalisierung, BPM/Key-Analyse, Tagging und Sortierung in die Inbox). Das Limit beträgt 1 GiB pro Datei bzw. Summe (sonst HTTP 413).
- **Mix & URL-Scanner (`/mix` & `POST /api/scan`):** Harmonic Mix Finder nach Camelot-Key und BPM mit interaktivem Camelot-Rad-Picker und Hervorhebung harmonischer Nachbar-Tonarten sowie On-Demand-Analyse beliebiger SoundCloud-Links (Tracks, Playlists, Artist-Likes) direkt im Browser. Der harmonische Set-Builder berechnet optimale Track-Ketten und exportiert sie als M3U8 oder Rekordbox-XML (`POST /api/mix/export-xml`).
- **Quarantäne-Manager (`/rejected` & `GET /api/rejected`):** Abgewiesene Downloads (Grund, Spektrum-Cutoff) durchsuchen und per Klick zur Wiederholung freigeben; erreichbar über das „Mehr“-Menü.
- **Duplikat-Kandidaten & Inbox-Cleanup (`/duplicates`, `GET /api/duplicates`, `GET /api/inbox-duplicates` & `POST /api/inbox-duplicates/move`):** Gegenüberstellung aller erkannten Audio-Duplikate per Chromaprint-Fingerprint aus Inbox und Sammlung sowie Ansicht und Bereinigung von Inbox-Dubletten derselben SoundCloud-ID (überzählige Dateien werden per Klick nach `_rejected/duplicate/` verschoben, nie gelöscht; Sammlung bleibt unberührt); erreichbar über das „Mehr“-Menü.
- **Konfigeditor (`/config`):** Formular für Einstellungen aus `config.yaml` mit Diff-Vorschau (`/config/preview`) und sicherem Schreiben in die lokale Override-Datei (`SC_DIGGER_CONFIG_LOCAL`, z. B. `/data/config.local.yaml`), erreichbar über das „Mehr“-Menü.

In allen Tabellenansichten (Track-Explorer, Digest, Kaufliste, Quarantäne) erleichtern farbcodierte Camelot-Pills (`1A`–`12B`) und 1-Klick Quick-Filter-Chips die DJ-Navigation; leere Filterergebnisse bieten über einen einheitlichen Leerzustand einen direkten Link zum Zurücksetzen der Filter. Interaktive Aktionen wie Track-Feedback (👍/👎/⏳) auf `/tracks` und `/kaufliste` sowie das erneute Freigeben von Quarantäne-Tracks (`/rejected`) geben sofortige optische Rückmeldung über eine globale, barrierefreie Toast-Meldungsleiste (`#toast-region` mit `role="status"`).

Für den Zugriff aus dem LAN ist in `docker-compose.yml` eine Port-Freigabe vorbereitet. Die Kopfzeile gliedert sich in fünf Haupteinträge (Status, Tracks, Digest, Mix, Kaufliste) sowie ein aufklappbares „Mehr“-Menü (`<details>`) für Upload, Duplikate, Quarantäne und Konfiguration. Auf schmalen Bildschirmen und Laptops passen sich Navigation und Filterformulare responsive an; breite Tabellen erhalten einen horizontalen Scrollbereich.

**Login-Sperre:** Fünf Fehlversuche (falsche oder fehlende Zugangsdaten) innerhalb von fünf
Minuten sperren die Client-IP für 15 Minuten; die Antwort ist `429` mit `Retry-After`. Die Sperre
gilt auch für das richtige Passwort, ein erfolgreicher Login setzt den Zähler zurück, `/healthz`
bleibt davon unberührt. Der Zähler liegt nur im Arbeitsspeicher. `SC_DIGGER_WEB_RATE_LIMIT=0`
schaltet die Sperre ab. Hinter einem Proxy (z. B. Caddy) sähe der Server sonst für alle Clients
dieselbe Adresse: `SC_DIGGER_WEB_TRUST_PROXY=1` zählt stattdessen die letzte Station aus
`X-Forwarded-For`. Nur setzen, wenn der Webserver ausschließlich über den Proxy erreichbar ist,
sonst kann ein Client den Header selbst setzen.

## Wartung: Gate-Backfill

Wurden in früheren Läufen Gated Tracks im Telegram-Digest gemeldet, bevor die Tabelle `gate_items` existierte, können deren Gate-Links mit dem Wartungsskript nachträglich per SoundCloud-Batch-Abfrage in wenigen Sekunden nachgeladen werden:

```bash
docker compose exec -T sc-digger python - < scripts/backfill_gates.py
```
*(Mit `--dry-run` kann der Lauf vorab ohne Schreibzugriff simuliert werden).*

## Wartung: 32-Bit-WAVs in 24-Bit wandeln

Dateien, die vor dieser Prüfung in der Inbox gelandet sind, räumt `scripts/fix_32bit_wavs.py` rekursiv auf. Es werden nur `.wav`, `.aif` und `.aiff` angefasst, komprimierte Formate nie:

```bash
docker compose exec sc-digger python scripts/fix_32bit_wavs.py /music/inbox --dry-run
docker compose exec sc-digger python scripts/fix_32bit_wavs.py /music/inbox
```

*(`--dry-run` zeigt nur, was konvertiert würde. Die Sammlung ist schreibgeschützt und gehört nicht in den Aufruf.)*

## Projektprüfung der Analyse

Die Analyse-Pipeline wurde im Oktober 2026 auf einem Ground-Truth-Korpus mit 29 bekannten
Tempo-/Tonart-Fällen geprüft: BPM **29/29** exakt (vorher 0/29), Key **100 %** (vorher 83 %).
Der Code dazu steht in `sc_digger/analysis.py` (BPM/Key), die harmonisch sortierten Playlists
(M3U8, CSV, Report) baut `sc_digger/dj_export.py`; abgesichert ist die Kette durch
`tests/test_dj_workflow.py`. Das frühere Auswertungswerkzeug samt Prüfbericht ist entfernt und
nur noch über die Git-Historie erreichbar.
