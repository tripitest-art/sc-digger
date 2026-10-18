# Changelog

Was seit dem letzten Deploy neu ist: Stephan liest hier, bevor er auf dem Server
`./update.sh` ausführt. Format: [Keep a Changelog](https://keepachangelog.com/de/),
neueste Einträge oben. Jede Zeile nennt den Pull Request; die Zeile für einen neuen PR steht
unter „Unreleased“ (Regeln: `AGENTS.md`, Abschnitt „Doku und Changelog“).

## Unreleased

### Hinzugefügt
- Track-Explorer zeigt den Breakdown-Anteil aus der Strukturanalyse als Badge (unter 15 % als Peaktime-Tool) und filtert per „Max. Breakdown %“ (`/tracks`, `GET /api/tracks`, `query_tracks(breakdown_ratio_max=…)`) (#335).
- Job-Queue-REST-API im Web-Dashboard (`POST /api/jobs/claim`, `/api/jobs/{id}/heartbeat`, `/api/jobs/{id}/complete`), damit externe GPU-Worker Jobs ohne SMB/NFS-Mount der Track-DB claimen und abschließen können (#330).
- Strukturanalyse und Cue-Tags in `finish_file()` eingebunden: Schreibt `SCDIGGER_FIRST_KICK`, `SCDIGGER_DROP` und `SCDIGGER_BREAKDOWN` in neue Inbox-Tracks und persistiert `breakdown_ratio` in der TrackDB (#327).
- Dependabot für GitHub Actions und pip (`.github/dependabot.yml`): wöchentlich, höchstens 2 offene PRs je Ökosystem (#316).
- `SECURITY.md` nennt den vertraulichen Meldeweg für Sicherheitslücken (GitHub „Private vulnerability reporting“) und warnt vor Zugangsdaten in öffentlichen Issues (#315).
- Doku-Pflicht: `CHANGELOG.md`, der Abschnitt „Doku und Changelog“ in `AGENTS.md` und `tests/test_doku.py` halten Doku und Changelog im selben PR wie die Änderung (#314).
