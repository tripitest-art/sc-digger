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
8. **Ausliefern** (`deliver`): Telegram-Digest in fester Form – Kopf mit Titel und Verteilung (`1 ✅ · 2 🚪`), pro Track eine eingerückte Datenzeile in fester Reihenfolge (BPM, Key, Qualität, Set, Stern, Reichweite) und darunter die Hinweise; gruppiert nach Download-Weg, mit Feedback-Buttons, plus Export-Datei als Anhang. Store-Tracks werden per `upsert_store_item` eingetragen.
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

Im Web-Dashboard bietet die Seite `/audit` eine Übersicht über den Sammlungs-Audit: Sie erklärt die Prüfschritte und Optionen (`check-adopted`, `analyze-structure`, `write-tags`, `export-xml`), startet den Audit mit `--check-adopted` über das Wartungsgerüst und verlinkt auf den zuletzt geschriebenen HTML-Bericht (`/api/audit/report`). Die schreibenden Optionen (`--write-tags`, `--export-xml`) bleiben aus Sicherheitsgründen der CLI vorbehalten.

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

## MixesDB-Abgleich

`sc_digger/mixesdb.py` liest Set-Tracklisten aus dem Szene-Wiki MixesDB (Schranz und Hard Techno sind dort vollständig erfasst, bei 1001tracklists praktisch nicht) und vergleicht sie mit der Sammlung:
- **Quelle:** MediaWiki-API (`action=parse`, `prop=wikitext`) — stabiler als HTML, keine Anmeldung nötig.
- **Einträge:** Position im Set, Artist, Titel, Label und Katalognummer; offene Stellen (`?`) bleiben als solche markiert. Damit lassen sich Promos und Edits benennen, die keine Audio-Erkennung findet.
- **Abgleich:** über `collection.name_key`/`NameIndex`; ein Remix in der Sammlung deckt den Original-Eintrag nicht ab.

Im Dashboard liegt die Seite unter `/mixesdb` (Einstieg über die Karte auf der Mix-Seite `/mix`):
Suchfeld für Set-Seiten, Trefferliste und die Tracklist als Tabelle mit Position, Marke
(✓ vorhanden, ✗ fehlt, ? im Wiki nicht benannt), Label und Katalog. Anmeldung wie das übrige
Dashboard, rein lesend, keine Zwischenspeicherung der MixesDB-Antworten.
Im Telegram-Bot steht dieselbe Abfrage über `/set <Suchtext>` (Suche) und `/set <Nummer>` (fehlende Tracks des gewählten Treffers mit Label und Katalognummer) bereit.

```bash
# Seiten zu einem Set suchen:
python scripts/mixesdb_set.py --suche "Robert Natus Schranz Total"

# Tracklist mit Sammlungsabgleich (✓ vorhanden, ✗ fehlt, ? offen):
python scripts/mixesdb_set.py --seite "2006-03-03 - Robert Natus @ Schranz Total, PPC, Graz"

# Nur die fehlenden Tracks als kopierbare Liste:
python scripts/mixesdb_set.py --seite "..." --nur-fehlen
```

## Multi-Platform-Scout

`sc_digger/scout/` erweitert die Recherche über SoundCloud hinaus auf externe Musikplattformen:
- **Bandcamp-Feeds (`bandcamp.py`):** Fragt konfigurierte RSS-Feeds von Labels und Artists (`scout.bandcamp.feeds`) ab.
- **Beatport-Charts (`beatport.py`):** Fragt Top-100- und Genre-Charts (`scout.beatport.charts`) ab.

Gefundene Tracks werden gegen die lokale Sammlung abgeglichen, Duplikate übersprungen und neue Tracks in der Tabelle `store_items` hinterlegt. Der CLI-Befehl `python -m sc_digger.main scout` stößt den Abruf manuell an. Ohne eingetragene Feeds oder Charts startet der Wartungslauf im Dashboard nicht und meldet „keine Quellen eingetragen“; sonst nennt er neue und schon bekannte Kaufempfehlungen getrennt. Offene Kauflinks sind im Web-Dashboard unter `/kaufliste` sowie im Telegram-Bot mit `/kaufliste` einsehbar. Gates und Käufe lassen sich als erledigt markieren (`POST /api/item-done`, Spalte `done_at`); erledigt bleibt erledigt, bis der Track neu gefunden wird.

## Telegram-Bot

Der Bot läuft dauerhaft (Hauptprozess des Containers) und antwortet nur im Chat aus `TELEGRAM_CHAT_ID` (eine Zahl, nicht der Bot-Name). Fehlen `TELEGRAM_BOT_TOKEN` oder `TELEGRAM_CHAT_ID`, pausiert der Bot; der tägliche Lauf läuft trotzdem. Links aus der App (`on.soundcloud.com/...`) werden vor dem Auflösen entkürzt.

| Nachricht / Befehl | Antwort |
|---|---|
| `/start`, `/help` | Hilfe mit Befehlsübersicht, gegliedert nach den Bereichen des Dashboards (Start, Entdecken, Eingang, Bibliothek, Auflegen) |
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

Zum Digest gehören Inline-Buttons pro Track (Like, Dislike, Später). Die Wahl landet in `sc_feedback` in `tracks.sqlite` und wird von `/mix` und `/curator_mining` ausgewertet. Die Kaufliste kommt zusätzlich sonntags um 20:00 (`0 20 * * 0`, aus `entrypoint.sh`) über den CLI-Modus `python -m sc_digger.main kaufliste`. Die Cron-Läufe `audit`, `taste`, `scout` und `kaufliste` werden in `runs` protokolliert (`main.run_logged`); wiederholte Fehler lösen einen Health-Alarm aus.

Ist `web.public_url` gesetzt, führt der Titel jedes Digest-Tracks auf die Track-Detailseite des Dashboards (`<public_url>/track/sc/<sc_id>`, dort stehen SoundCloud-Link, Lebenslauf und Aktionen), und `/heute` nennt volle, antippbare Links. Leer bleibt alles wie bisher (`output.dashboard_url`, `output.track_detail_url`).

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
ohne Fehler und ohne Änderung: Das alte Modell und die alten Scores bleiben stehen. Trifft
das Ergebnis eines Embedding-Jobs ein (`apply_embedding_result`), wird der Score des Tracks
sofort mit dem gespeicherten Modell nachgezogen (`taste.load_and_score`), ohne auf den
nächtlichen Lauf zu warten; Fehler dabei werden nur protokolliert. Der Score dient zunächst
nur der Anzeige und beeinflusst die tägliche Auswahl nicht — er steht aber schon beim
Eintreffen des Embeddings im Dashboard bereit, statt erst am nächsten Morgen. SoundCloud-
Kandidaten haben ohne Download kein Embedding. `--dry-run` zeigt die Zahl der bewertbaren
Tracks, ohne Modell oder Scores zu schreiben. Damit die Digest-Bewertungen geladene Tracks
erreichen, speichert die Inbox jeden Download mit seiner SoundCloud-ID (`tracks.sc_id`); ältere
Zeilen ohne ID bekommen sie beim Embedding-Backfill aus dem Dateinamen-Präfix `[<id>]` (#749).

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

**Workstation-Alarm:** Derselbe Kanarienvogel prüft vor der SoundCloud-Abfrage die Job-Queue der KI-Workstation (`sc_digger/queue_watch.py`). Wartet ein offener Job seit mindestens `gpu_wake.stale_alert_days` Tagen (Standard 3, im Konfigeditor einstellbar), kommt einmal ein Telegram-Alarm und die Statusseite führt „KI-Workstation“ in der Alarmliste; sobald die Queue abgearbeitet wird, folgt eine Entwarnung. Die Prüfung läuft bewusst nicht im Weck-Timer, damit der Alarm auch kommt, wenn der Timer fehlt.

**Lauf-Sperre:** Schreibende Modi von `python -m sc_digger.main` (alle außer `stats`) laufen nie gleichzeitig. Die Sperre ist ein `flock` auf `pipeline.lock` neben der State-DB (Verzeichnis von `state.db_path`); die Datei nennt den laufenden Modus und die Startzeit. Ist sie belegt, wartet ein Lauf höchstens `state.lock_wait_s` Sekunden (Standard 1800) und bricht dann mit Exit-Code 75 ab; ein so ausgefallener `discover` zählt im Health-Protokoll als gescheiterter Lauf. Jeder andere übersprungene Modus (z. B. `scout`, `audit`, `taste`, `kaufliste`, `intake`) wird ebenso als fehlgeschlagener Lauf dieses Modus in `runs` protokolliert, mit Health-Alarm nach `health.alert_after_bad_runs` Fehlschlägen in Folge. Echte Läufe von `playlist`, `similar` und `intake` erscheinen als Läufe in `runs` (Treffer = fehlende Tracks bzw. gemeldete Eingangszeilen); Probeläufe mit `--dry-run` nicht. Lässt sich die Sperre nicht anlegen (kein beschreibbares Verzeichnis), läuft der Modus mit Warnung ohne Sperre. `seen.sqlite` läuft wie die Track-DB im WAL-Modus mit 30 s Wartezeit.

## Sicherung

`sc_digger.backup` sichert täglich um 06:30 Uhr (vor dem Übernommen-Abgleich und dem täglichen `discover`-Lauf) die SQLite-Datenbanken (`seen.sqlite`, `tracks.sqlite`) sowie die lokale Override-Datei (`config.local.yaml`, falls über `SC_DIGGER_CONFIG_LOCAL` gesetzt).

- **Konsistente WAL-Sicherung:** Da `tracks.sqlite` und `seen.sqlite` im WAL-Modus laufen, werden Datenbanken über die SQLite Backup-API gesichert und im Ziel auf `PRAGMA journal_mode=DELETE` umgestellt. So gehen ungeschriebene Transaktionen aus dem `-wal` nicht verloren, und die Kopie ist eine in sich geschlossene Einzeldatei ohne begleitende `-wal`- oder `-shm`-Dateien.
- **Integritätsprüfung:** Jede Kopie wird mit `PRAGMA integrity_check` geprüft. Schlägt die Prüfung fehl, wird die Datei entfernt und der Lauf bricht laut mit `BackupError` ab.
- **Atomarer Tagesordner & Rotation:** Sicherungen liegen unter `backup.target_dir/<JJJJ-MM-TT>/` (zuerst in `.<JJJJ-MM-TT>.tmp` geschrieben, dann atomar umbenannt). Alte Stände werden rotiert (`keep_daily`: Standard 7 tägliche, `keep_weekly`: Standard 4 wöchentliche Stände).
- **Alarmierung (Regel 7):** Bei Fehlern schlägt der Lauf fehl (Exit 1) und schickt einen Alarm per Telegram (`🚨 sc-digger: Sicherung fehlgeschlagen ...`). Jeder Lauf wird in `runs` (Modus `backup`) protokolliert; bei wiederholtem Fehler gibt es einmal einen Health-Alarm, bei Besserung eine Entwarnung. Ist `backup.target_dir` leer (Exit 2), zählt das als Fehler. Standardziel ist `/data/backup` (Daten-Volume); ein NAS wird auf dem Host unter `data/backup` eingebunden (BETRIEB.md).
- **Wiederherstellung:** `python -m sc_digger.backup restore <sicherung.sqlite> <ziel.sqlite> [--force]` prüft die Sicherung vor dem Schreiben und stellt sie wieder her. Mit `--force` wird die bisherige Datei vorher als `.vor-restore` gesichert und alte `-wal`/`-shm`-Dateien werden entfernt.

## GPU-Worker und Job-Queue

Die Track-DB (`db.py`) enthält eine versionierte `jobs`-Tabelle für asynchrone Aufgaben auf einer
externen GPU-Workstation (Cues und Embeddings). Der Server bietet dafür
`POST /api/jobs/claim`, `POST /api/jobs/{id}/heartbeat`, `POST /api/jobs/{id}/complete` und
`GET /api/jobs/{id}/audio`; `worker.py` ist der Standalone-Client mit Heartbeat,
Wiederholungs-Backoff und `--idle-exit`. `cue_infer.py` rechnet auf der Workstation Cues
(all-in-one mit `beat_this`-Beats oder lokale `librosa`-Analyse als Rückfall), `embeddings.py`
liefert den Vektorspeicher und die Embedding-Jobs. `gpu_wake.py` weckt die Workstation bei
offenen Cue- oder Embedding-Jobs per Wake-on-LAN-Proxy (andere Job-Typen wecken nicht). Die Unit auf der Workstation holt beide Typen; ohne `transformers` in `allin1_env` rechnet sie nur Cues. Im Web-Dashboard kann dieser Weckruf über den Knopf „Workstation-Lauf starten" auch von Hand angestoßen werden. Einrichtung und Betrieb stehen in
[BETRIEB.md](../BETRIEB.md).

## Web-Dashboard

Jede Zeile in Digest, Track-Explorer, Quarantäne und Kaufliste verlinkt mit Künstler und Titel auf die Detailseite `/track/sc/<sc_id>` (Lebenslauf des Tracks); ohne SoundCloud-ID bleibt die Zelle Text.

Setzt man in `.env` ein Passwort (`SC_DIGGER_WEB_PASSWORD`, siehe `.env.example`), startet der
Container zusätzlich einen FastAPI-Webserver (`python -m sc_digger.web`) auf Port `8080`. Ohne
Passwort startet er nicht (Standard aus), es wird kein zusätzlicher Port geöffnet. Die Seiten sind
im modernen Dark-Theme gestaltet (mit `color-scheme: dark` für einheitliche Scrollbars und Formularfelder,
barrierefreien SVG-Icons statt Emoji für Header, Chips und Feedback, WCAG-konformen Textkontrasten ab 4,5:1
sowie sichtbarem Tastaturfokus per `:focus-visible` auf allen interaktiven Bedienelementen) und per HTTP-Basic-Auth geschützt:

- **Status (`/` & `/api/status`):** Ampel gesund/Achtung, letzter `discover`-Lauf mit Alter und Rohtreffern, aktiver Health-Alarm, Inbox-Größe, die Karte „Tagesablauf“ mit allen geplanten Jobs, Uhrzeit und letztem Ergebnis, die letzten 10 Läufe, die Wochenstatistik mit Vergleich zur Vorperiode (▲/▼ mit Prozent oder „–“ ohne Vergleichswert) sowie die Betriebskarte (Zustand von Speicherplatz-Wächter, täglicher Sicherung und API-Kanarienvogel); steht ein Punkt auf „Aus“ oder „Warnung“, führt „Einstellen →“ in den passenden Abschnitt des Konfigeditors (nur mit Editor), bei der Sicherung verweist ein Hinweis auf `BETRIEB.md`. Zeigt die Kachel „übernommen“ eine 0, erklärt ein Hinweis den täglichen Abgleich um 06:45 und verlinkt das Sammlungs-Audit. Die leeren Seiten `/kuratoren` und `/smartlists` nennen den nächsten Schritt (Wartung „Artist aus Dateinamen ergänzen“, Track-Explorer) (Issue #733).
- **Statistik & Trend-Radar (`/statistik` & `GET /api/statistik`):** Ausführliche Statistik mit wählbarem Zeitfenster (1 bis 365 Tage, Standard 7 Tage). Zeigt den Pipeline-Funnel (Läufe, gescannte Tracks, Inbox, Fakes, Feedback, Top-Artists) und das SoundCloud-Trend-Radar (Trending Artists, Top-Tracks nach Like-Wachstum, Trending Tags). Erreichbar über den Link „Alle Zeitfenster →“ in der Wochenstatistik-Karte auf der Startseite.
- **Digest-Explorer (`/digest` & `/api/digest`):** Historische Digest-Tracks aus `seen.sqlite` durchsuchen und nach Download-Weg (`Alle`, `Geladen`, `Gate`, `Store`, `Stream`), Zeitraum (`7d`, `14d`, `30d`, `Alle`) oder Text filtern; Tabellensortierung per Klick auf die Spaltenköpfe, 1-Klick Quick-Filter-Chips und wählbare Zeilenmenge (25/50/100/200). Ermöglicht direktes Track-Feedback (👍/👎/⏳), Matcher-Korrekturen per Button („Schon in Sammlung“, „Kein Duplikat“, „Zurücknehmen“) und enthält den direkten Link `[🌐 Gate öffnen ↗]` für Fangates (Hypeddit, Droploud etc.); Gates lassen sich per „erledigt“ / „wieder öffnen“ abhaken (`gate_items.done_at`), erledigte Zeilen bleiben sichtbar und sind nur markiert. Der Durchhör-Modus (`/digest/durchhoeren`, Idee #592) zeigt jeweils einen offenen Vorschlag bildschirmfüllend mit Tastatursteuerung (`1`/`2`/`3` für Feedback, `↓`/`j` weiter, `↑`/`k` zurück, `Enter` Details) und schaltet nach jeder vom Server bestätigten Bewertung direkt weiter (bei Fehler bleibt der Vorschlag stehen und ein Hinweis erscheint). Er zeigt nur offene, unbewertete Vorschläge. Jeder Vorschlag spielt die Hörprobe vom eigenen Server (`/api/preview/sc/<id>`, `web.stream_preview`, ab Werk an; Schnipsel liegen im Zwischenspeicher `preview_cache` bis `web.preview_cache_mb`); nach dem ersten Klick oder Tastendruck startet jeder neue Vorschlag von selbst, die Leertaste schaltet Abspielen und Pause, der nächste Schnipsel wird vorgeladen. Je Karte führt „Auf SoundCloud öffnen“ zum Original.
- **Kaufliste (`/kaufliste` & `GET /api/watch`):** Übersicht aller Store-Tracks (Bandcamp, Beatport etc.) mit Filter auf gelikte Tracks und Quick-Filter-Chips, je Zeile ein Umschalter „erledigt“/„wieder öffnen“ (`POST /api/item-done`; erledigte Zeilen bleiben sichtbar, nur abgesetzt) sowie Verfügbarkeits-Wächter-Ansicht (`data-watch-section`) für überwachte 👍-Stream-Tracks ohne Original (inkl. Markierung „Jetzt verfügbar“).
- **Track-Explorer mit Vorhören (`/tracks` & `GET /api/audio/{id}`):** durchsucht die Track-DB und spielt Tracks aus Inbox und Sammlung direkt im Browser ab (HTML5-Audio mit Range-Requests, Springen im Track); Tabellensortierung per Klick auf Spaltenköpfe und wählbare Zeilenmenge (25/50/100/200); Daten-Spalten lassen sich über „Spalten auswählen“ ein- und ausblenden, die Auswahl bleibt im Browser gespeichert (#553). Filtern, Sortieren und Blättern tauschen nur den Ergebnisbereich aus, statt die Seite neu zu laden (#554). Aktive Filter erscheinen über der Tabelle als entfernbare Filter-Chips, und beim horizontalen Scrollen bleiben Artist und Titel als fixierte Spalten sichtbar. Die Sticky Bottom DJ-Deck-Bar öffnet sich beim Abspielen automatisch (verhindert überlappende Player, `-15s`/`+30s` Drop-Check-Buttons, Vor/Zurück `⏮`/`⏭` und Tastatur-Shortcuts `Space`, `←`/`→`, `J`/`K`). Ein Klick auf `⚡ Match` in der Spalte *Aktionen* öffnet direkt den Harmonic Mix Finder (`/mix?key=...&bpm=...`) für harmonisch und tempomäßig passende Anschluss-Tracks; ein Klick auf den Button *Ähnlich* lädt per `GET /api/tracks/{track_id}/similar` die Top-N ähnlichsten Tracks der Sammlung (Audio-Embeddings mit Chromaprint-Fallback) in ein einblendbares Panel (`#similar-panel`). Die Spalte *Taste* zeigt den täglich berechneten Geschmacks-Score in Prozent und lässt sich sortieren; `Min. Taste` filtert darauf, die Schnellwahl `💚 Mein Geschmack (≥70%)` zeigt passende Inbox-Tracks, und eine Zeile nennt Stand und Beispielzahlen des Taste-Modells.
- **Upload & Intake (`/intake`, Endpunkt `POST /api/upload-intake`):** Eigene Seite für Mehrfach-Uploads per Dateiauswahl oder Drag & Drop. Mehrere Gate- oder Kauf-Tracks (WAV, FLAC, AIFF, MP3) können gleichzeitig abgelegt werden und laufen nacheinander durch dieselbe Pipeline (Audioqualität/Fake-Check, Pegel-Normalisierung, BPM/Key-Analyse, Tagging und Sortierung in die Inbox). Das Limit beträgt 1 GiB pro Datei bzw. Summe (sonst HTTP 413). In einer Gate-Zeile des Digests lässt sich die Datei direkt über „Datei ablegen" hochladen: Der Upload geht mit der `sc_id` der Zeile an `POST /api/upload-intake`, und nur bei erfolgreichem Einordnen wird das Gate als erledigt markiert (`gate_done`, #664).
- **Mix & URL-Scanner (`/mix` & `POST /api/scan`):** Harmonic Mix Finder nach Camelot-Key und BPM mit interaktivem Camelot-Rad-Picker und Hervorhebung harmonischer Nachbar-Tonarten sowie On-Demand-Analyse beliebiger SoundCloud-Links (Tracks, Playlists, Artist-Likes) direkt im Browser. Seit #542 lässt sich die Auswahl zusätzlich nach Helligkeit eingrenzen (`cmin`/`cmax`, Spektral-Schwerpunkt in Hz; ohne Angabe bleibt die Suche wie bisher). Der harmonische Set-Builder berechnet optimale Track-Ketten und exportiert sie als M3U8 oder Rekordbox-XML (`POST /api/mix/export-xml`).
- **Quarantäne-Manager (`/rejected` & `GET /api/rejected`):** Abgewiesene Downloads (Grund, Spektrum-Cutoff) durchsuchen und per Klick zur Wiederholung freigeben; Die Gruppen kommen aus einem Zwischenspeicher (`duplicate_groups.json` neben der State-DB, `web/duplicates_cache.py`): Solange sich keine Fingerprints ändern, antwortet die Seite sofort und zeigt „Stand: …“. Ändern sich Fingerprints, erscheint der letzte Stand mit dem Hinweis „Wird gerade neu berechnet“ und die Neuberechnung läuft im Hintergrund; nur der allererste Aufruf wartet. `GET /api/duplicates` liefert dazu `computed_at` und `stale`. Erreichbar über das „Mehr“-Menü.
- **Wartung (Karte auf der Statusseite, `POST /api/maintenance/{action}` & `GET /api/maintenance`, Issue #655, #656):** Knöpfe für `scout`, `watch` (Verfügbarkeits-Wächter), `taste` (Taste-Score trainieren), `backfill-energy` (Energie-Backfill), `backfill-artist` (Artist-Backfill), `backfill-names` („Artist aus Dateinamen ergänzen“: füllt leere Artist-/Titel-Felder der Track-DB aus Tags oder Dateinamen, Issue #702; Dateien bleiben unverändert), `backfill-bpm` („Alte BPM-Werte neu messen“: misst Sammlungs-Tracks mit Audio-BPM ohne Konfidenz, also die Rasterwerte 161,5/152,0 der alten Analyse vor #287, neu und speichert BPM samt Konfidenz nur in der Track-DB, Issue #703; fehlende Dateien bleiben unverändert, Dateien werden nie geschrieben; `audit` speichert die Konfidenz jetzt mit), `backfill-embeddings` (Embedding-Backfill), `requeue` (hängende Jobs zurücksetzen) und `backup` (Sicherung auf Knopfdruck); der Auftrag läuft im Hintergrund, die Cron-Jobs bleiben. Je Aktion gibt es eine Sperre: läuft dieselbe Aktion schon, antwortet die Route mit 409, andere Aktionen laufen parallel; unbekannte Aktionen liefern 404. Der Zustand (`running`/`done`/`error`, Start, Ende, Ergebnis- oder Fehlerzeile ohne Ausnahmetext) steht in `web-tasks.json` neben der State-DB und überlebt einen Neustart. Anmeldung, `Content-Type: application/json` und Origin-Prüfung wie bei `/api/gpu-wake`.
- **Track-Lebenslauf (`/track/sc/<id>`):** Zeigt für eine SoundCloud-ID, welche Stationen der Track erreicht hat – Entdeckt, Bewertet, Gate gefunden, Im Store, Quarantäne, In der Inbox, Analysiert, In der Sammlung, Übernommen, Handkorrektur – jeweils mit Zeitpunkt (falls bekannt) und Detail (Bewertung, Gate-/Kauflink, Grund, Pfad, BPM · Key, Korrektur); die letzte Station ist hervorgehoben. Nur `http(s)`-Adressen werden verlinkt. Die Seite liest nur und gehört zu keinem Navigationsbereich; eine unbekannte ID ergibt 404 mit Link zum Digest. Darunter stehen die Aktionen: bei vorliegendem Fangate „Gate öffnen ↗“, Bewertung (👍/👎/🕒), Matcher-Korrektur („Bereits in der Sammlung“, „Kein Duplikat“, „Zurücksetzen“), bei Quarantäne „Erneut prüfen“, Dateifeld („Datei ablegen“) zum direkten Hochladen per `POST /api/upload-intake` und ein Rückfall-Link zum Upload auf `/intake`; sie nutzen dieselben Endpunkte wie Bot und Tabellen (`/api/feedback`, `/api/match-override`, `/api/retry`, `/api/upload-intake`) und zeigen das Ergebnis in einer Statuszeile (Teil 2 von #590, #708). Ist `web.stream_preview` eingeschaltet (ab Werk an), steht über dem Lebenslauf ein Audio-Player, der die Hörprobe vom eigenen Server abspielt (`/api/preview/sc/<id>`, erst beim Klick geladen); bei ausgeschaltetem Schalter erscheint kein Player.
- **Duplikat-Kandidaten & Inbox-Cleanup (`/duplicates`, `GET /api/duplicates`, `GET /api/inbox-duplicates` & `POST /api/inbox-duplicates/move`):** Gegenüberstellung aller erkannten Audio-Duplikate per Chromaprint-Fingerprint aus Inbox und Sammlung sowie Ansicht und Bereinigung von Inbox-Dubletten derselben SoundCloud-ID (überzählige Dateien werden per Klick nach `_rejected/duplicate/` verschoben, nie gelöscht; Sammlung bleibt unberührt); erreichbar über das „Mehr“-Menü.
- **Konfigeditor (`/config`):** Formular für Einstellungen aus `config.yaml` mit Diff-Vorschau (`/config/preview`) und sicherem Schreiben in die lokale Override-Datei (`SC_DIGGER_CONFIG_LOCAL`, z. B. `/data/config.local.yaml`), erreichbar über das „Mehr“-Menü. Ein clientseitiges Suchfeld (`#config-suche`) filtert Felder sofort nach Bezeichnung, Erklärung und Punkt-Pfad. Je Abschnitt zeigt die Ansicht „Wichtig“ zuerst die Felder, die man im Alltag anfasst (`schema.IMPORTANT_FIELDS`), „Alle“ den Rest; lokal geänderte Felder bleiben immer sichtbar. Unter jedem Feld steht, welcher Lauf oder welche Funktion es liest („Wirkt auf: …“, `schema.USED_BY`). Über den Link „Verlauf“ (`/config/verlauf`) lassen sich frühere Stände der Sicherungen (`*.bak-*`) einsehen und per Knopfdruck wiederherstellen (`POST /api/config/restore`).

In allen Tabellenansichten (Track-Explorer, Digest, Kaufliste, Quarantäne) erleichtern farbcodierte Camelot-Pills (`1A`–`12B`) und 1-Klick Quick-Filter-Chips die DJ-Navigation; leere Filterergebnisse bieten über einen einheitlichen Leerzustand einen direkten Link zum Zurücksetzen der Filter. Interaktive Aktionen wie Track-Feedback (👍/👎/⏳) auf `/tracks` und `/kaufliste` sowie das erneute Freigeben von Quarantäne-Tracks (`/rejected`) geben sofortige optische Rückmeldung über eine globale, barrierefreie Toast-Meldungsleiste (`#toast-region` mit `role="status"`).

Für den Zugriff aus dem LAN ist in `docker-compose.yml` eine Port-Freigabe vorbereitet. Die Kopfzeile gliedert sich in fünf Haupteinträge (Status, Tracks, Digest, Mix, Kaufliste) sowie ein aufklappbares „Mehr“-Menü (`<details>`) für Upload, Duplikate, Quarantäne und Konfiguration. Auf schmalen Bildschirmen und Laptops passen sich Navigation und Filterformulare responsive an; breite Tabellen erhalten einen horizontalen Scrollbereich.

Die Navigation gliedert sich seit #607 in fünf Arbeitsbereiche, denen jede Seite zugeordnet ist: **Start** zeigt die Statusübersicht mit Wochenstatistik und Betriebskarte, **Entdecken** bündelt Digest, Kuratoren und Kaufliste, **Eingang** fasst Upload und Quarantäne zusammen, **Bibliothek** enthält Track-Explorer und Duplikat-Kandidaten, **Auflegen** führt zum Mix-Finder. Die Reiter folgen in dieser Reihenfolge der Kopfzeile, die Seiten des aktiven Bereichs stehen darunter in einer Unterleiste, und die Konfiguration erreicht man über das Zahnrad.

Oben auf der Startseite fasst eine Karte „Heute zu tun“ zusammen, was aktuell zu erledigen ist (#640): unbewertete Vorschläge im Digest, offene Gates, Tracks, die der Verfügbarkeits-Wächter als neu verfügbar meldet, und offene Vorschläge, die du im Telegram-Digest mit ⏳ für später gemerkt hast (Link auf den Digest-Filter „später“; `/heute` zeigt denselben Eintrag). Es erscheinen nur Einträge mit einem Zähler größer null, jeder Eintrag ist ein Direktlink auf die passende Ansicht. Die Zahlen ermittelt `collect_today` nur lesend aus denselben Datenbanken, die auch der Bot nutzt; der Bot-Befehl `/heute` schickt dieselbe Liste als Nachricht (#650). Hinter jedem Eintrag steht, wie viel davon seit dem letzten Besuch neu ist („· 12 neu seit deinem letzten Besuch“, #710): Der Browser merkt sich den Besuch im Cookie `scd_besuch`, ein Besuch endet nach 30 Minuten ohne Aufruf der Startseite, damit die Zahl beim Auto-Refresh stehen bleibt. Beim ersten Besuch fehlt die Angabe.

Gates und Käufe lassen sich im Digest und in der Kaufliste abhaken (#642, #645, #649): Ein Umschalter „erledigt“ / „wieder öffnen“ setzt oder entfernt die Marke `done_at` über `POST /api/item-done`. Die Zeile bleibt stehen und ist nur markiert, nichts wird gelöscht; findet ein Lauf den Track neu, öffnet sich der Eintrag wieder und erscheint erneut als offen.

**Login-Sperre:** Fünf Fehlversuche (falsche oder fehlende Zugangsdaten) innerhalb von fünf
Minuten sperren die Client-IP für 15 Minuten; die Antwort ist `429` mit `Retry-After`. Die Sperre
gilt auch für das richtige Passwort, ein erfolgreicher Login setzt den Zähler zurück, `/healthz`
bleibt davon unberührt. Der Zähler liegt nur im Arbeitsspeicher. `SC_DIGGER_WEB_RATE_LIMIT=0`
schaltet die Sperre ab. Hinter einem Proxy (z. B. Caddy) sähe der Server sonst für alle Clients
dieselbe Adresse: `SC_DIGGER_WEB_TRUST_PROXY=1` zählt stattdessen die letzte Station aus
`X-Forwarded-For`. Nur setzen, wenn der Webserver ausschließlich über den Proxy erreichbar ist,
sonst kann ein Client den Header selbst setzen.
Die Taste `/` öffnet auf jeder Seite eine Schnellsuche über alle Seiten der Navigation (bei aktivem Konfigeditor auch die Konfiguration) und liefert ab zwei Zeichen direkte Treffer aus der Track-Datenbank (Gruppe „Tracks", verlinkt auf die Detailseite oder Explorer-Suche); Freitext führt zusätzlich zur Suche in `/tracks` und `/digest`, `↑`/`↓` wählen, `Enter` öffnet, `Esc` schließt. Passende Aktionen (Rekordbox-XML neu schreiben, Workstation-Lauf starten und alle Wartungsaktionen der Statusseite) erscheinen als Treffer der Gruppe „Aktion“; `Enter` oder Klick fragt „… wirklich starten?“ und startet sie per POST wie der Knopf auf der Seite, das Ergebnis steht in der Hinweiszeile. Ein eingefügter SoundCloud-Link liefert als ersten Treffer „Link im URL-Scanner prüfen“: `/mix?url=<Link>#scan` trägt den Link ein und prüft sofort.

## Kuratoren

`GET /kuratoren` (JSON: `GET /api/kuratoren`) beantwortet die Frage, welche Artists die Sammlung tragen — die Grundlage dafür, einen Account in `search.reference_accounts` aufzunehmen. Es wird nichts geladen: alles kommt aus der Track-DB.

Gezählt werden Tracks mit Status `archive` oder `adopted`, Gruppen unter `min_tracks` (Standard 3) fallen weg. Sortiert wird nach Anzahl absteigend, bei Gleichstand alphabetisch, dann auf `limit` (Standard 30) gekürzt. Je Artist stehen Anzahl, Anteil der Tracks mit `rb_rating >= 4`, mittleres BPM (nur über Tracks mit gesetztem BPM) und die Tracks selbst — nach BPM aufsteigend, Tracks ohne BPM zuletzt.

Im Dashboard liegt die Seite unter „Mehr" → „Kuratoren".

### Kuratoren-Mining im Dashboard

Unter der Übersicht steht ein Block „Kandidaten aus dem Mining“: Profile, die die 👍-Tracks auffällig oft liken oder reposten (dieselbe Auswertung wie `/curator_mining`, Logik in `sc_digger/curator.py`: `candidates_from_counts`, höchstens `MAX_CANDIDATES` = 30, absteigend nach Treffern, bei Gleichstand alphabetisch). Der Knopf „Kuratoren-Mining starten“ löst `POST /api/maintenance/curator-mining` über das Wartungsgerüst aus (Sperre je Aktion). Die letzte Auswertung liegt in `curator-candidates.json` neben der State-DB, geschrieben vom Wartungsauftrag und vom Bot nach `/curator_mining`; `GET /api/kuratoren/kandidaten` liefert sie als `{"candidates": [{"profile", "appearances"}], "checked_at"}` und startet nie eine Netz-Abfrage. Ein Kandidat wird nicht automatisch in `search.followed_users` eingetragen.

## Smartlists

Regeln über die Bibliothek, ohne sie in `config.yaml` zu schreiben: „BPM 150–160, Camelot 8A/9A, Energie 4–5, Taste ≥ 70 %, Rating ≥ 4, Tag `peak time`". `sc_digger/smartlists.py` trennt Auswertung und Ablage.

`parse_rules(dict)` baut `Rules`. Unbekannte Schlüssel, falsche Typen und widersprüchliche Grenzen (`min > max`) scheitern mit `RuleError`, dessen Meldung Schlüssel und erlaubten Bereich nennt. Geprüfte Bereiche: `taste_min` 0..1, `rating_min` 0..5, `energy_*` 1..5, `bpm_*` und `duration_*` größer als 0, `keys` im Camelot-Muster (1..12 plus A/B; `8a` wird zu `8A` gespeichert). Ein `taste_min: 7` statt `0.7` läuft deshalb nicht mehr still auf eine leere Trefferliste.

`matches(row, rules, tags)` ist reine Auswertung ohne Datenbank: fehlt ein Wert, den eine gesetzte Regel braucht (`NULL`), gilt der Track als nicht passend; leere Regeln passen immer. `select_tracks(db, rules, limit=None)` liefert die Treffer nach BPM aufsteigend (`NULL` zuletzt), dann Key, dann ID — Tracks mit `status = 'rejected'` sind immer ausgeschlossen.

Gespeichert wird in `smartlists` je Profil (`profile_id` plus `name` als zusammengesetzter Schlüssel), derselbe Name darf also in zwei Genres existieren. `save_smartlist`, `get_smartlist` und `list_smartlists` arbeiten auf dem Profil der `TrackDB`.

Im Dashboard liegt die Seite unter `/smartlists` (Einstieg über die Karte auf der Mix-Seite `/mix`): gespeicherte Listen mit Regel- und Trefferzahl und direktem M3U8-Export (`/smartlists/{name}.m3u8`), rein lesend. Ein Rekordbox-XML-Export und das Anlegen/Bearbeiten von Regeln über die Oberfläche sind noch nicht enthalten.

## Freie Tags

Eigene Markierungen jenseits der Schranz-Matrix: „Opener", „Peak Time", „Warmup", „Fake-Drop". `track_tags` hält beliebig viele Tags je Track (Primärschlüssel `(track_id, tag)`, Fremdschlüssel mit `ON DELETE CASCADE`).

`sc_digger.tags.normalize_tag` ist der einzige Weg in die Tabelle: Kleinschreibung, Leerraum zusammengefasst und am Rand entfernt, erlaubt sind `a-z 0-9 ä ö ü ß`, Leerzeichen, `-` und `_`, höchstens 40 Zeichen. Alles andere („Opener!", leer, zu lang) wirft `ValueError` — auch beim Entfernen und Suchen, damit ein Tippfehler nicht als „nichts gefunden" durchgeht.

`TrackDB` bietet `add_track_tag` (idempotent, gibt den normalisierten Tag zurück), `remove_track_tag`, `get_track_tags`, `list_tags` (absteigend nach Anzahl) und `list_track_ids_by_tag`. Im Track-Explorer (`/tracks`) können Tags direkt in der Zeile gesetzt werden (Chips und Eingabefeld unter Artist/Titel), gefiltert wird über das Feld `tag` oder einen Klick auf einen Chip (`POST /api/tracks/{id}/tags`, `GET /tracks?tag=...`). Die Nutzung als Smartlist-Regel (`tags_all`, `tags_any`) ist der nächste Schritt; die Regel-Engine liest die Tabelle bereits.

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

## Hilfe-Seite

Die Seite `/hilfe` (Link „Hilfe“ in der Kopfzeile jeder Seite, neben „Glossar“) zeigt den Weg eines Tracks in sieben Stationen (Entdecken, Bewerten, Laden, Prüfen, Inbox, Rekordbox, Sammlung), jede mit einem Satz und einem Link auf ihre Seite, dazu „Dein Tag in 3 Schritten“. Die Texte stehen als reine Daten in `sc_digger/web/hilfe.py` und nutzen nur Begriffe aus dem Glossar.

## Glossar

Die Seite `/glossar` im Dashboard (Link „Glossar“ in der Kopfzeile) erklärt alle Begriffe mit ihren verworfenen Alternativen und zeigt die Legende der Status-Plaketten. Sie rendert direkt aus `sc_digger/web/glossary.py` (`GLOSSAR`, `STATUS_BADGES`); `docs/glossar.md` bleibt per Test gleich.
