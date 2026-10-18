# Changelog

Was seit dem letzten Deploy neu ist: Stephan liest hier, bevor er auf dem Server
`./update.sh` ausführt. Format: [Keep a Changelog](https://keepachangelog.com/de/),
neueste Einträge oben. Jede Zeile nennt den Pull Request; die Zeile für einen neuen PR steht
unter „Unreleased“ (Regeln: `AGENTS.md`, Abschnitt „Doku und Changelog“).

## Unreleased

### Hinzugefügt
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
