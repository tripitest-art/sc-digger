# Entwicklung: Architektur, Stolperfallen, Standard-Aufträge

Ergänzt `AGENTS.md`. Steht hier, damit `AGENTS.md` unter 12.000 Zeichen bleibt (Antigravity
schneidet Regeldateien dort ab). Agenten lesen diese Datei vor dem Umsetzen. Wer ein neues
Modul anlegt, trägt es in die Tabelle ein.

## Architektur

| Datei | Zuständig für |
|---|---|
| `sc_digger/main.py` | CLI und Modi (`discover`, `playlist`, `similar`, `check`), gemeinsame Pipeline `process()`, Nachbearbeitung `finish_file()` (Qualität, BPM/Key, Tags, Struktur/Cues, Organize, DB-Persistierung), Auslieferung `deliver()` |
| `sc_digger/models.py` | Zentrale Datenmodelle (`Track`, `DownloadKind`, etc.) und Hilfsklassen für Pipeline und DB |
| `sc_digger/bot.py` | Telegram-Listener; ruft `main.run_link()`; beendet sich nie selbst (sonst stirbt der Cron); Curator-Mining (`/curator_mining`) schlägt Liker/Reposter der 👍-Tracks und häufige Sammlungs-Künstler (`min_collection_tracks`) vor und schickt Inline-Buttons, deren `cf:<slug>`-Callbacks den Account über `web.configedit.save_local` in `search.followed_users` der Override-Datei eintragen; Matcher-Korrekturen per `/is_duplicate <sc_id> [Notiz]` (`already_in_collection`) und `/not_duplicate <sc_id> [Notiz]` (`not_duplicate`) in `match_overrides` (Issue #325) |
| `sc_digger/soundcloud.py` | Inoffizielle api-v2 (client_id aus dem Frontend), Playlists inkl. Stub-Nachladen, Station/Related, Referenz-Accounts (soundcloud-v2-Lib) |
| `sc_digger/pipeline.py` | Text-BPM, Genre-Relevanz, Perzentil-Scoring, Download-Klassifizierung |
| `sc_digger/intake.py` | Eingangsordner `_eingang`: manuell geladene Tracks in `download.intake_dir` finden (`find_ready_files`) und als Track-Objekt für die Weiterverarbeitung vorbereiten |
| `sc_digger/collection.py` | Duplikat-Abgleich mit der Sammlung (Fuzzy-Match, Remixer beachten, Unicode-Faltung); `NameKey`/`NameIndex` als gemeinsame Namenslogik für Sammlung und Web-Digest; Dauer-Prüfung bei Namens-Treffern (`probe_duration`, Toleranz `DURATION_TOLERANCE_S`); `open_collection` nutzt den Sammlungs-Index der Track-DB (Rückfall auf `rglob`) und lädt die Handkorrekturen aus `match_overrides`; `find_duplicate` beachtet sie vorrangig (`not_duplicate` -> frei, `already_in_collection` -> `MANUAL_DUPLICATE_LABEL`) |
| `sc_digger/linksource.py` | Zuordnungslogik: Sammlungsdateien mit SoundCloud-Tracks verknüpfen (Teil 1); reine Logik ohne Netzwerk (`propose_links`, `apply_proposals`, `normalize_url`) |
| `sc_digger/output.py` | State-DB, Original-Download (scdl), `find_in_inbox` (schon in der Inbox?), `finalize_quality`, Telegram-Digest, Export-Datei, `telegram_call`, `send_telegram_photo` |
| `sc_digger/retry.py` | Retry-Queue für fehlgeschlagene Original-Downloads |
| `sc_digger/cloud.py` | Cloud-Downloads (Dropbox, Google Drive) |
| `sc_digger/quality.py` | ffprobe, Codec-Prüfung (`is_lossless_codec`), Report-Flag `unmeasurable` (Ordner `_rejected/unmeasurable/`), `convert_wav_to_flac` (optional, `organize.wav_to_flac`), Spektrum-Cutoff (Fake-Erkennung) mit Multi-Window-Messung (`spectral_cutoff_hz(..., offsets=...)`, `_cutoff_offsets`) und Stille-Erkennung, EBU R128 / LRA (Brickwall) |
| `sc_digger/loudness.py` | Pegel-Normalisierung neuer Inbox-Downloads (-8.5 LUFS, samplegenau, Metadaten-Erhalt) |
| `sc_digger/analysis.py` | BPM/Key per librosa, BPM-Oktav-Korrektur `resolve_bpm` |
| `sc_digger/structure.py` | Strukturanalyse: Kick-Erkennung, Segmente (`intro`, `main`, `breakdown`, `outro`), erster Kick, Drop-Start (`find_drop_start`) und Breakdown-Anteil (`calculate_breakdown_ratio`) |
| `sc_digger/stems.py` | Stem-basierte Subbass- und Breakdown-Analyse (`bass.wav`/`drums.wav`), Drop-Verifikation (Subbass-Check, Fake-Drop-Korrektur) und Kick-Pausen-Typisierung (`silence`/`groove`) |
| `sc_digger/phrase.py` | Phrasen-Quantisierung: 16/32-Beat-Gitter aus `first_kick` und BPM (`calculate_phrase_grid`), Cue-Alignment mit Toleranzfenster und Taktanfang-/Downbeat-Fallback (`quantize_to_phrase`, `align_cues`); in `structure.find_drop_start`, `cues.write_stem_cues` und `rekordbox.build_xml` optional eingebunden (`phrase_align`/`phrase_align_cues`, Config `rekordbox.cues_phrase_align`) |
| `sc_digger/fingerprint.py` | Audio-Fingerprints (Chromaprint/fpcalc), gleiche Aufnahme erkennen |
| `sc_digger/similarity.py` | Ähnlichkeitssuche über Chromaprint-Fingerprints gegen die Track-DB („Mehr davon“ im Bot per Audio-Datei-Upload) |
| `sc_digger/harmonic.py` | Harmonische Kompatibilität (Camelot-Wheel), Suche passender Tracks nach Key und BPM |
| `sc_digger/dj_export.py` | Harmonischer DJ-Export (CLI `python -m sc_digger.dj_export`): M3U8-Playlists, CSV und Prüfbericht nach Camelot-Nachbarschaft und ΔBPM |
| `sc_digger/organize.py` | Inbox-Sortierung `<BPM>/<Camelot>/`, Tags schreiben (`TKEY` = Camelot, Klarname in `TXXX:INITIALKEY`, `TXXX:TEXTBPM`, ID3-Reserve `padding_kb`), Dateinamen in NFC-Namen |
| `sc_digger/rekordbox.py` | Rekordbox-XML: Playlists pro Woche aus der Inbox, Locations in NFC-Namen, Beatgrid-Anker `<TEMPO>` an `first_kick_s` (`build_xml(grid_anchor=…)`, Konfiguration `rekordbox.grid_anchor`); lesender XML-Import (`parse_rekordbox_xml`, `import_rekordbox_xml`, Migration 9) für Ratings, Plays und Cues; `import_configured_xml` liest `rekordbox.import_xml_path`/`path_map` aus der Config und wird vom täglichen Lauf über `main.run_rekordbox_import` (Health-Modus `rekordbox_import`) aufgerufen |
| `sc_digger/cleanup.py` | Inbox-Dubletten per SoundCloud-ID finden, auflisten und optional nach `_rejected/duplicate/` verschieben |
| `sc_digger/cues.py` | Cue-Tags `SCDIGGER_FIRST_KICK`, `SCDIGGER_DROP`, `SCDIGGER_BREAKDOWN`, `SCDIGGER_BREAKDOWN_KIND` (Sekunden) lesen und schreiben; `write_stem_cues` schreibt First Kick, Drop und Breakdown und richtet Drop/Breakdown optional per `phrase_align` am Phrasengitter aus; `apply_cue_result` übernimmt das Ergebnis eines `cues`-Jobs in die Track-DB (`rb_cues`, `breakdown_ratio`) und schreibt die Tags nur für Dateien in der Inbox (Sammlung bleibt unberührt) |
| `sc_digger/embeddings.py` | Audio-Embeddings: Serialisierung kompakter float32-Vektoren (`pack_vector`/`unpack_vector`), Kosinus-Ähnlichkeit (`cosine_similarity`), Jobs einreihen (`enqueue_embedding_job`), Ergebnis-Hook (`apply_embedding_result`) und Worker-Handler (`make_embedding_handler`) |
| `sc_digger/taste.py` | Taste-Score: Logistische Regression auf Audio-Embeddings und Feedback (`TasteModel`, `train_taste_model`, `build_training_set`), Wahrscheinlichkeits-Scoring (0..1) für Tracks |
| `sc_digger/cue_infer.py` | Cue-Inferenz auf der Workstation: `extract_cues_from_segments` rechnet Segmente/Beats in First Kick, Drop, Drop 2, Breakdown, Breakdown-Art und -Anteil um (rein rechnerisch, ohne Audio-Bibliotheken); `infer_cues_from_audio` nutzt die lokale Strukturanalyse; `handle_cues_job` nimmt den lokalen Pfad oder lädt die Datei per `JobWorkerClient.get_job_audio` in eine temporäre Datei (Löschung im `finally`); `build_handler_map` liefert das Standard-Mapping für `execute_job`/`run_worker_loop` |
| `sc_digger/db.py` | Zentrale Track-DB (Phase 2), Metadaten-Index, Audio-Fingerprints, Jobs-Queue (`JobType.CUES` samt `enqueue_cue_job`, `JobType.EMBEDDING`), versionierte Migrationen (Migration 13 `track_embeddings`, Migration 14 `tracks.sc_id`, Migration 15 `match_overrides`) samt Prüfung der Migrationsliste vor dem Lauf (`check_migrations`, Issue #364); Sammlungs-Index (`list_collection_tracks`), Sammlungs-Favoriten für das Curator-Mining (`get_top_collection_artists`) und Dateidauer (`duration`, Migration 12); `mark_adopted` überträgt mit `archive_track_id` die SoundCloud-ID und `source_url` auf die Sammlungszeile; Handkorrekturen des Matchers in `match_overrides` (`set_match_override`, `get_match_override`, `list_match_overrides`, `remove_match_override`, Issue #325) |
| `sc_digger/audit.py` | Read-only Library Audit (Phase 2.2), Fake-Erkennung, HTML-Dashboard, Rückmeldung „übernommen“ per Fingerprint (`mark_adopted_tracks`, `audit --check-adopted`, ROADMAP 3.12); der Treffer verknüpft die Sammlungsdatei dauerhaft über `tracks.sc_id` (Issue #324); `export_collection_xml` versteht die `rekordbox`-Optionen für Hot-Cue-Layout, Phrasen-Ausrichtung, Beatgrid-Anker und Kompakt-Kommentar (Issue #381) |
| `sc_digger/health.py` | Laufprotokoll, Alarm bei wiederholt leeren/fehlerhaften Läufen |
| `sc_digger/healthcheck.py` | Container-Healthcheck: Cron, letzter Lauf, DB-Integrität |
| `sc_digger/autodeploy.py` | Auto-Deploy per systemd-Timer auf dem Host: neuen `main`-Stand von `update.sh` ausrollen, Healthcheck prüfen, bei Fehlschlag automatisch zurückrollen; Einrichtung in `BETRIEB.md` |
| `sc_digger/redact.py` | Zugangsdaten aus Texten und Logs entfernen |
| `sc_digger/scout/bandcamp.py` | Bandcamp-RSS-Scout: liest Label- und Artist-Feeds und erzeugt Kaufempfehlungen (`DownloadKind.STORE`) in `store_items` |
| `sc_digger/scout/beatport.py` | Beatport-Chart-Scout: scraped Top-100-Genre-Charts und erzeugt Kaufempfehlungen (`DownloadKind.STORE`) in `store_items` |
| `sc_digger/web/status.py` | Statusseite (Phase 5): `collect_status` liest Läufe, Health-Alarm, Inbox-Größe und Wochenstatistik nur lesend zusammen; dazu die Job-Queue der KI-Workstation (`jobs`-Tabelle: Zähler je Status, jüngstes `updated_at` als Lebenszeichen, „Workstation aktiv“ bei laufendem Job mit Heartbeat binnen 300 s) |
| `sc_digger/web/database.py` | Multi-DB-Query für das Web-Dashboard: `query_digest` (joint `seen.sqlite` und `tracks.sqlite` für Status, Feedback, Gate-/Store-Links; Handkorrekturen aus `match_overrides` schlagen den Namensabgleich, Issue #325; Sortierung per Weißliste `DIGEST_SORT_FIELDS`, `MAX_LIMIT = 200`), `query_store_items` (Kaufliste aus `store_items` mit LEFT JOIN auf `sc_feedback`: Wortsuche über Artist/Titel, Feedback-Filter, Limit/Offset, nur lesend), `query_mix_candidates`, `query_tracks` (Track-Explorer-Filter inkl. `breakdown_ratio_max`, Sortierung per Weißliste `TRACK_SORT_FIELDS`, `MAX_LIMIT = 200`, nur lesend), `load_tracks_for_set_builder` (Kandidaten des Set-Builders: nur Tracks mit BPM und Camelot-Key, BPM-Fenster/Status, nur lesend); dazu `query_rejected_tracks` (Quarantäne-Liste aus `rejected` in `seen.sqlite`: Wortsuche über Titel/URL/Grund, Limit/Offset, nur lesend) und `retry_quarantined_track` (Freigabe wie Bot-Befehl `/retry`: entfernt den Eintrag aus `rejected` und `seen`, damit der nächste Lauf den Track erneut prüft); dazu die Job-Queue-Schreibpfade für externe Worker (`claim_job_for_worker`, `heartbeat_job`, `finish_job`), die nie werfen und fehlende DB-Dateien nicht anlegen; `finish_job` übernimmt bei `completed` und `job_type == cues` das Ergebnis per `apply_cue_result` (mit `inbox_dir` als erlaubter Schreibwurzel) |
| `sc_digger/web/audio.py` | Vorhören im Track-Explorer: sichere Pfadprüfung (`resolve_playable_path` nur innerhalb erlaubter Ordner `inbox_dir` und `collection_dir`, Symlinks/Ausbrüche abgewiesen) und HTTP-Range-Requests zum Springen im Track |
| `sc_digger/web/intake.py` | Web-Upload & Gate-Assistent: `save_intake_upload` speichert Uploads sicher im Intake-Ordner, `process_intake_file` führt die reguläre Pipeline (Qualitätsprüfung, Normalisierung, BPM/Key, Tags, Rekordbox) aus |
| `sc_digger/web/app.py` | FastAPI-App des Web-Dashboards (HTTP-Basic, Dark Theme): Status (`/`), Digest-Explorer (`/digest`, `/api/digest`, Sortierung per Klick, Auswahl der Zeilenmenge 25/50/100/200), Track-Explorer (`/tracks`, `/api/tracks`, u. a. Filter „Max. Breakdown %“, Sortierung per Spaltenklick, Zeilenmenge 25/50/100/200), Kaufliste (`/kaufliste`, `/api/kaufliste`, Bewertung über `POST /api/feedback`), Quarantäne-Liste (`/rejected`, `/api/rejected`, Freigabe über `POST /api/retry` mit Origin-Prüfung), Mix & URL-Scanner (`/mix`) inkl. harmonischem Set-Builder (`POST /api/set-builder`, M3U8-Download über `GET /api/set-builder/m3u8`), Mehrfach-Upload (`/intake`, `POST /api/upload-intake`), Konfigeditor (`/config`), Job-Queue-REST-API für externe GPU-Worker (`/api/jobs/claim`, `/api/jobs/{id}/heartbeat`, `/api/jobs/{id}/complete`, `/api/jobs/{id}/audio` zum Herunterladen der Job-Datei) |
| `sc_digger/web/configedit.py` | Reine Logik des Konfigeditors (ohne Webframework): Formularwert parsen/formatieren, minimaler Override, Diff, Schlüssel entfernen, atomares Schreiben der lokalen Datei mit Backup |
| `sc_digger/web/__main__.py` | Startbefehl `python -m sc_digger.web`; startet ohne `SC_DIGGER_WEB_PASSWORD` nicht, schaltet den Editor nur mit `SC_DIGGER_CONFIG_LOCAL` ein |
| `sc_digger/worker.py` | GPU-Worker-Client für die Job-Queue auf der Workstation: `JobWorkerClient` (HTTP-Basic, claimen/Heartbeat/abschließen, `get_job_audio` für `GET /api/jobs/{id}/audio`), `execute_job` (Handler über `handler_map`, Fehler werden zu `failed`), `run_worker_loop` (Poll-Backoff, `max_jobs`, `stop_event`, Heartbeat-Thread während der Ausführung; ohne `handler_map` automatisch `cue_infer.build_handler_map`); CLI `python -m sc_digger.worker --api-url … [--job-type …] [--once]` |
| `sc_digger/gpu_wake.py` | Wecken der GPU-Workstation (Issue #409): `count_pending_jobs` (offene Cue-Jobs, DB nur lesend), `wake_workstation` (POST an den Wake-Proxy, URL nie im Log), `run_wake`; CLI `python -m sc_digger.gpu_wake` per Timer (`deploy/sc-digger-gpu-wake.*`), Adresse nur aus `SC_DIGGER_WAKE_URL`; Gegenstück ist `worker.py --idle-exit` (`deploy/workstation/sc-digger-gpu-worker.service`) |
| `scripts/backfill_gates.py` | Wartungsskript: liest historische Digest-Tracks aus `seen.sqlite`, fragt SoundCloud api-v2 in Batches ab und trägt erkannte Gate-/Store-Links in `tracks.sqlite` ein |
| `scripts/link_sources.py` | Wartungsskript: verknüpft die Sammlung (Status archive) mit SoundCloud (Teil 2); Likes, Playlists und freie Suche, CSV-Report und `--apply` |
| `sc_digger/stats.py` | Statistikberechnung für Telegram-Digest und /stats-Bot-Befehl, Vorperioden-Vergleich der Kernkennzahlen, `render_stats_chart`, `send_weekly_digest`, `_trend_section` (Trend-Abschnitt) |
| `sc_digger/trends.py` | Trend-Radar (Phase 4): Wachstumsanalyse historischer Engagement-Snapshots (`track_snapshots`) je Track und Artist |
| `sc_digger/preview.py` | Live-Preview (Phase 3): energiereichstes Segment einer Audiodatei per ffmpeg finden und als OGG-Opus-Snippet extrahieren (`find_loudest_segment`, `extract_preview`); Drop-Preview `find_preview_start` startet `preview.riser_s` vor dem ersten Drop (Fallback `find_loudest_segment`); `extract_preview_from_url` für die Stream-Preview nicht heruntergeladener Tracks (`/preview sc:<id>`, seit #168, Schalter `preview.stream_enabled`) |
| `sc_digger/schema.py` | Config-Schema: `FIELDS`/`SECTION_TITLES`, `validate_config` (Typen, Wertebereiche, Warnungen), `deep_merge` für die lokale Override-Datei, Prüfbefehl `python -m sc_digger.schema` |
| `sc_digger/style.py` | Schranz-Matrix (4 Cluster, 13 Subgenres, 3 Achsen), Versionstyp-Erkennung, Rekordbox-Farb-/Kommentar-Empfehlung, Energie-Stufe, `classify_track` |
| `config.yaml` | Einzige Konfiguration (Tags, Referenz-Accounts, Schwellwerte). Ist die Produktivkonfiguration. |
| `entrypoint.sh` | Schreibt `cron.env` (Cron hat sonst weder PATH noch Secrets), hängt die Kaufliste-/Scout-Jobs an und installiert sie per `install_crontab` (ein Fehler dort bricht den Start mit Exit 1 ab, damit fehlende Jobs nicht unbemerkt bleiben), startet cron und Bot |
| `skills/*/SKILL.md` | Abläufe für lokale Modelle in LibreChat (Review, Worker, Planer), per GitHub Skill Sync gespiegelt; Regeln bleiben in `AGENTS.md` |
| `.github/scripts/acceptance_guard.py` | CI-Check `acceptance-guard`: Akzeptanztests = Issue, keine neuen skips, CI/Test-Konfiguration geschützt, PR-Text ausgefüllt (`Closes #N`, `Worker:`) |
| `.github/workflows/codeql.yml` | Statische Sicherheitsanalyse mit CodeQL (Python, Actions); ignoriert `tests/**`, um False-Positives bei Test-Assertions zu verhindern |
| `sc_digger/issuecheck.py` | Planer-Preflight: prüft einen Issue-Text und seine Akzeptanztests vor dem Einstellen (`python -m sc_digger.issuecheck <issue.md>`), lässt den Block mit pytest gegen ein Repo laufen; kopiert die Block-Regex aus `acceptance_guard.py`, importiert sie nicht; warnt vor CodeQL-Fehlalarmen durch Substring-Domain-Checks |
| `set-secret.sh` / `update.sh` | Zugangsdaten setzen / Update ausrollen (auf dem Server) |

## Testen

- **Matcher-Testliste:** `tests/fixtures/match_cases.yaml` sammelt Problemfälle des Abgleichs
  Entdeckung ↔ Sammlung (Remix-Varianten, Sonderzeichen, feat-Schreibweisen, Original gegen
  Extended, Edit in der Bibliothek, Werbezusätze). `tests/test_matcher_cases.py` lädt die Fälle
  (`load_match_cases`), wertet sie mit denselben Heuristiken wie der echte Abgleich aus
  (`evaluate_match_case` über `name_key`/`names_match`, Dauer-Toleranz 5 s) und prüft jeden Fall
  parametrisiert gegen sein erwartetes Ergebnis. Neue Fälle anhängen, das fachlich richtige
  Ergebnis eintragen – auch wenn die heutige Heuristik noch abweicht; die Liste ist die Messlatte
  für die Optimierung.

## Bekannte Stolperfallen (alle schon einmal passiert)

- **Cron-Umgebung ist leer:** kein `/usr/local/bin`, keine `.env`. Deshalb `cron.env`.
- **scdl meldet Fehler mit Exit-Code 0.** Erfolg nur daran messen, ob eine Datei entstand.
- **Originale nur mit Login.** Ohne `SOUNDCLOUD_AUTH_TOKEN` gibt es sie nicht; ohne
  `--only-original` lädt scdl still den Stream.
- **Playlists liefern die meisten Tracks nur als Stub** (nur `id`). Nachladen, sonst fehlen sie.
- **Telegram-Token steckt in jeder API-URL** und damit in `requests`-Fehlern.
- **`TELEGRAM_CHAT_ID` ist eine Zahl**, nicht der Bot-Name.
- **Windows-Zeilenenden** brechen `entrypoint.sh`; `.gitattributes` erzwingt LF.
- **Zwei Code-Stände** (Server-Kopie und Repo) liefen schon einmal auseinander. Siehe Regel 1.
- **Labels per MCP:** `issue_write` ersetzt die ganze Label-Liste. So gingen bei #62 Labels
  verloren. Siehe `MCP.md`.
- **Nacharbeit ohne Commit:** Verlangt ein Review nur den PR-Text, gibt es keinen neuen
  Commit (#103). Ob der Text nach dem Review geändert wurde, zeigt `lastEditedAt`:
  `gh api graphql -f query='{repository(owner:"tripitest-art",name:"sc-digger"){pullRequest(number:<PR>){lastEditedAt}}}'`
  Jünger als das letzte Review heißt: Nacharbeit da, der PR ist wieder dran.

## Planer-Ablauf

Für jeden Planer, gleich welches Modell. Die Regeln stehen in `AGENTS.md` (Worker-Aufgaben →
Planer); hier steht, wie man zu einem Issue kommt, das ein anderes Modell ohne Rückfragen
umsetzen kann. Agenten ohne Shell folgen dem Skill `skills/sc-digger-planner/SKILL.md`.

Der Planer schreibt Issues, keinen Code: keine Branches, keine Commits, keine PRs, kein Merge.

0. **Backlog:** Ideen ohne Akzeptanztest stehen als Issue mit Label `idee` (`ROADMAP.md`, „Backlog und Arbeitsweise“). Der Planer macht daraus erst dann ein `worker-task`, wenn die offenen Fragen beantwortet sind.
1. **Auftrag klären.** Aus dem Gespräch mit Stephan oder aus `ROADMAP.md`. Ein Thema pro
   Issue; ist es größer als ein PR, in Teile schneiden („Teil 1 von #58“, wie #77 und #95).
   Offene Fachfragen (Schwellwerte, Verhalten im Fehlerfall) Stephan stellen, nicht raten.
2. **Stand prüfen.** Offene Issues und PRs nach Dubletten und nach denselben Dateien
   durchsuchen (Regel 8). Überschneidung → `blockiert` und `Wartet auf: #X` (siehe `AGENTS.md`).
   Bestehende Akzeptanztests suchen, die die geänderten Funktionen prüfen
   (`git grep <funktion> tests/acceptance`). Worker dürfen sie nicht ändern; die Aufgabe muss
   so geschnitten sein, dass sie grün bleiben (bei #102 hätte `test_issue_76.py` die neue
   Ausgabe von `format_mix_list` abgelehnt).
3. **Code lesen.** Jede Datei, die das Issue nennt, auf dem Stand von `main`. Bestehende
   Helfer und Fakes (`mk`/`FakeSC` in `tests/test_modes.py`, `FakeLinkSC` in
   `tests/test_merge.py`) wiederverwenden, statt dem Worker neue vorzuschreiben.
4. **Issue-Text schreiben** in der Form, die das Formular erzeugt: Überschriften `### Ziel`,
   `### Betroffene Dateien`, `### Schnittstellen`, `### Nicht Teil dieser Aufgabe`,
   `### Akzeptanztests`, `### Fertig, wenn`, `### Berührt sc_digger/main.py`,
   `### Merge-Modus`, `### Kontext`, in dieser Reihenfolge. Dropdowns als Klartext mit dem
   genauen Optionstext. Vorbild: #95.
   - **Betroffene Dateien:** vollständig, mit Funktion und Stelle. Was fehlt, darf der Worker
     nicht anfassen. Immer dabei: `tests/acceptance/test_issue_<N>.py` und eigene Testdatei.
     Bestehende Tests, deren Erwartung sich ändert, mit Datei und Stelle nennen; sonst darf
     der Worker sie nicht anpassen.
   - **Schnittstellen:** exakte Signaturen mit Typen, Rückgabe, Verhalten bei leer/`None`/
     Fehler, Log-Meldungen im Wortlaut, wenn Tests sie prüfen. In einem ```` ```python ````-Block.
   - **Nicht Teil dieser Aufgabe:** das naheliegende „gleich mit aufräumen“ ausschließen.
   - **Merge-Modus** „manuell“ bei Betrieb, Dateien auf dem Server, Zugangsdaten, Downloads.
5. **Akzeptanztests** in **genau einem** ```` ```python ````-Block direkt unter
   `### Akzeptanztests` (so liest ihn `acceptance-guard`). Sie
   - prüfen nur die Schnittstellen aus dem Issue, keine Interna;
   - laufen ohne Netzwerk, ohne Uhrzeit und Zufall, ohne `skip`/`xfail`;
   - importieren nur aus `sc_digger` und vorhandenen Test-Helfern;
   - schreiben nur in `tmp_path`, nie nach `/music` oder in die echte `config.yaml`.
6. **Akzeptanztests prüfen (mit Shell).** Form und Rot-Stand prüft ein Befehl:
   `python -m sc_digger.issuecheck <issue.md>` (`sc_digger/issuecheck.py`). Datei lokal anlegen
   und laufen lassen: auf `main` rot (meist `ImportError`), gegen eine Probe-Umsetzung grün, und
   die restliche Suite **mit** der Probe-Umsetzung grün (so fallen die Fälle aus Schritt 2 und 4
   auf). Die
   Probe-Umsetzung wird weder committet noch gepusht. Ergebnis mit Commit-Hash von `main` in
   den Kontext („ohne Umsetzung rot, mit Umsetzung 12 grün“).
   **Ohne Shell** geht das nicht: Label `entwurf` statt `bereit`, im Kontext „Akzeptanztests
   noch nicht ausgeführt (Planer ohne Shell)“. Stephan oder ein Agent mit Shell prüft sie und
   ersetzt `entwurf` durch `bereit`.
7. **Entwurf zeigen.** Titel, Text und Labels Stephan im Chat vorlegen; erst nach seinem OK
   anlegen (außer er hat ausdrücklich „direkt anlegen“ gesagt).
8. **Anlegen** mit Labels `worker-task`, `phase-N`, `bereit` (ohne Shell `entwurf`), genau
   einem `agent-<familie>` (Abschnitt „Agenten-Labels“), bei Bedarf `blockiert` und
   `berührt-main.py`. Danach Nummer und Link melden. Im Issue-Text `test_issue_<N>` stehen
   lassen; die Nummer kennt der Worker.

Nachträglich ändern darf der Planer ein Issue nur, solange es nicht `in-arbeit` ist. Danach
nur noch kommentieren (Korrektur des Akzeptanztests: `AGENTS.md`, Planer).

## Standard-Aufträge

Für Stephan: Mit diesen Sätzen startet man einen Agenten.

| Rolle | Auftrag |
|---|---|
| Planer | „Erstelle aus unserem Gespräch ein Issue in tripitest-art/sc-digger nach dem Formular `worker-task` (AGENTS.md, Worker-Aufgaben → Planer; ENTWICKLUNG.md, Planer-Ablauf).“ |
| Worker | „Bearbeite Issue #N nach AGENTS.md, Abschnitt Worker-Aufgaben → Worker.“ |
| Worker (autonom) | „/goal Bearbeite die nächste Aufgabe nach AGENTS.md, Worker-Aufgaben → Nächste Aufgabe selbst wählen.“ |
| Reviewer | „Prüfe PR #M nach AGENTS.md, Abschnitt Worker-Aufgaben → Reviewer.“ |
| Reviewer (autonom) | „/goal Prüfe den nächsten PR nach AGENTS.md, Worker-Aufgaben → Nächsten Review selbst wählen.“ |
| Worker (Nacharbeit) | „Arbeite das Review in PR #M ab (AGENTS.md, Worker → Schritt 8).“ |
| Qwen Reviewer (LibreChat) | „Repo tripitest-art/sc-digger. Prüfe PR #M nach dem Skill sc-digger-review.“ |
| Qwen Worker (LibreChat) | „Repo tripitest-art/sc-digger. Bearbeite Issue #N nach dem Skill sc-digger-worker.“ |
| Qwen Worker (Nacharbeit) | „Repo tripitest-art/sc-digger. Arbeite das Review in PR #M ab, Skill sc-digger-worker, Teil B.“ |
| Planer (LibreChat) | „Repo tripitest-art/sc-digger. Plane nach dem Skill sc-digger-planner: <Aufgabe in ein paar Sätzen>.“ Legt das Issue mit `entwurf` an; freigeben erst nach Prüfung der Akzeptanztests. |

Die Qwen-Aufträge nennen Repo und Skill ausdrücklich: Mit der Kurzform suchte Qwen nach einem
anderen Repo und hielt AGENTS.md für den Prüfgegenstand. Die Skills liegen unter `skills/`
(siehe `MCP.md`, Abschnitt Skills). Jeden Auftrag in einem neuen Chat starten.

## Agenten-Labels

Der Planer gibt jedem Worker-Issue genau ein Label, welche Modellfamilie es umsetzt. Worker
nehmen nur Issues mit ihrem Label (`AGENTS.md`, Nächste Aufgabe selbst wählen). Der Reviewer
stammt aus einer anderen Familie.

| Label | Worker | Passende Issues |
|---|---|---|
| `agent-qwen` | Sandbox-Worker (OpenCode/Qwen Code; Modell aktuell DeepSeek über LiteLLM, früher Qwen lokal) | Issues mit genauer Schnittstelle und Akzeptanztests, auch `sc_digger/main.py` (Beleg: #109/PR #135, #116) |
| `agent-antigravity` | Antigravity / Gemini | mittel: mehrere Dateien, Muster, Eskalationsstufe für Qwen |
| `agent-claude` | Claude Code Opus 5.5 + Mensch | schwer: `main.py`, Betrieb, finale Eskalationsstufe |

Im Zweifel die größere Stufe. Ein Issue, das an einem Agenten scheitert, wird explizit an die nächsthöhere Stufe eskaliert (`agent-qwen` → `agent-antigravity` → `agent-claude`); dies ist keine Änderung am Vertrag und erlaubt, solange es nicht mehr `in-arbeit` ist.
