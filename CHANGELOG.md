# Changelog

Was seit dem letzten Deploy neu ist: Stephan liest hier, bevor er auf dem Server
`./update.sh` ausführt. Format: [Keep a Changelog](https://keepachangelog.com/de/),
neueste Einträge oben. Jede Zeile nennt den Pull Request; die Zeile für einen neuen PR steht
unter „Unreleased“ (Regeln: `AGENTS.md`, Abschnitt „Doku und Changelog“).

## Unreleased

### Hinzugefügt
- `/curator_mining` schlägt zusätzlich häufige Künstler der eigenen Sammlung vor (`curator_mining.min_collection_tracks`, Standard 3; `TrackDB.get_top_collection_artists`), gegliedert in Liker-Vorschläge und „📚 Sammlungs-Favoriten“ mit denselben Follow-Buttons; bereits gefolgte oder als Referenz hinterlegte Accounts bleiben außen vor (#333).
- Cue- und Strukturanalyse als Job-Queue-Aufgabe: neuer `JobType.CUES` mit `enqueue_cue_job`, `GET /api/jobs/{id}/audio` zum Herunterladen der Job-Datei für den GPU-Worker und automatische Übernahme des Ergebnisses in `rb_cues`/`breakdown_ratio` samt Cue-Tags (nur Inbox-Dateien, Sammlung bleibt unberührt); `finish_file` reiht bei `cues.auto_queue: true` je neuem Inbox-Track einen Job ein (#360).
- `/curator_mining` im Telegram-Bot liefert Inline-Buttons „➕ <Name>“; ein Klick trägt den Account dauerhaft in `search.followed_users` der lokalen Override-Datei (`SC_DIGGER_CONFIG_LOCAL`) ein, doppelte Klicks und Callbacks aus fremden Chats bleiben wirkungslos (#359).
- Audio-Vorhören im Track-Explorer (`/tracks`, `GET /api/audio/{track_id}`): HTML5-Player je Zeile mit Range-Requests zum Springen im Track, rein lesend und nur für Dateien unterhalb von `download.inbox_dir` und `download.collection_dir`; Pfade außerhalb und Symlinks aus den Ordnern heraus ergeben 404 (#350).
- Statusseite (`/`) zeigt den zuletzt deployten Commit und Zeitpunkt (`data/version.json`, von `update.sh` nach dem Rauchtest geschrieben, gelesen über `sc_digger/web/status.py`); ohne Deploy über `update.sh` bleibt das Feld leer (#358).
- Harmonischer Set-Builder im Web-Dashboard (`/mix`, `POST /api/set-builder`, `GET /api/set-builder/m3u8`): sortiert Tracks aus Sammlung und/oder Inbox per `order_harmonic` zu einer Kette, zeigt BPM-/Key-Sprünge und setzt bei nicht verknüpfbaren Tracks eine sichtbare Schnitt-Segmentgrenze; die Playlist lässt sich als M3U8 für Rekordbox herunterladen, die Sammlung bleibt unverändert (#346).
- GPU-Worker-Client für die Workstation (`sc_digger/worker.py`, `python -m sc_digger.worker`): holt Jobs per REST (`/api/jobs/claim`), sendet während der Ausführung Heartbeats und meldet Ergebnis oder Fehler an `/api/jobs/{id}/complete`; Job-Handler kommen über `handler_map`, `--once` bearbeitet genau einen Job (#332).
- CodeQL-Konfiguration (`.github/workflows/codeql.yml`) schließt `tests/**` von der Sicherheitsanalyse aus; `sc_digger/issuecheck.py` warnt Planer vor Substring-Prüfungen auf Hostnamen in Akzeptanztests (#345).
- Interaktive Kaufliste im Web-Dashboard: `/kaufliste` und `GET /api/kaufliste` zeigen offene Store-Tracks aus `store_items`, suchen nach Artist/Titel und verlinken direkt zum Kauf; bewertet wird per 👍/👎/⏳ über `POST /api/feedback`, der bisher deaktivierte Nav-Tab ist aktiv (#339).
- Status-Seite (`/`, `GET /api/status`) zeigt die Job-Queue der KI-Workstation: Zähler für anstehend, in Arbeit, erledigt und Fehler aus der `jobs`-Tabelle der Track-DB sowie „Workstation aktiv“ bzw. „im Standby“ anhand des letzten Heartbeats (5 Minuten); rein lesend (#337).
- Track-Explorer zeigt den Breakdown-Anteil aus der Strukturanalyse als Badge (unter 15 % als Peaktime-Tool) und filtert per „Max. Breakdown %“ (`/tracks`, `GET /api/tracks`, `query_tracks(breakdown_ratio_max=…)`) (#335).
- Job-Queue-REST-API im Web-Dashboard (`POST /api/jobs/claim`, `/api/jobs/{id}/heartbeat`, `/api/jobs/{id}/complete`), damit externe GPU-Worker Jobs ohne SMB/NFS-Mount der Track-DB claimen und abschließen können (#330).
- Strukturanalyse und Cue-Tags in `finish_file()` eingebunden: Schreibt `SCDIGGER_FIRST_KICK`, `SCDIGGER_DROP` und `SCDIGGER_BREAKDOWN` in neue Inbox-Tracks und persistiert `breakdown_ratio` in der TrackDB (#327).
- Dependabot für GitHub Actions und pip (`.github/dependabot.yml`): wöchentlich, höchstens 2 offene PRs je Ökosystem (#316).
- `SECURITY.md` nennt den vertraulichen Meldeweg für Sicherheitslücken (GitHub „Private vulnerability reporting“) und warnt vor Zugangsdaten in öffentlichen Issues (#315).
- Doku-Pflicht: `CHANGELOG.md`, der Abschnitt „Doku und Changelog“ in `AGENTS.md` und `tests/test_doku.py` halten Doku und Changelog im selben PR wie die Änderung (#314).
