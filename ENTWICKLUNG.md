# Entwicklung: Architektur, Stolperfallen, Standard-Aufträge

Ergänzt `AGENTS.md`. Steht hier, damit `AGENTS.md` unter 12.000 Zeichen bleibt (Antigravity
schneidet Regeldateien dort ab). Agenten lesen diese Datei vor dem Umsetzen. Wer ein neues
Modul anlegt, trägt es in die Tabelle ein.

## Architektur

| Datei | Zuständig für |
|---|---|
| `sc_digger/main.py` | CLI und Modi (`discover`, `playlist`, `similar`, `check`), gemeinsame Pipeline `process()`, Auslieferung `deliver()` |
| `sc_digger/bot.py` | Telegram-Listener; ruft `main.run_link()`; beendet sich nie selbst (sonst stirbt der Cron) |
| `sc_digger/soundcloud.py` | Inoffizielle api-v2 (client_id aus dem Frontend), Playlists inkl. Stub-Nachladen, Station/Related, Referenz-Accounts (soundcloud-v2-Lib) |
| `sc_digger/pipeline.py` | Text-BPM, Genre-Relevanz, Perzentil-Scoring, Download-Klassifizierung |
| `sc_digger/collection.py` | Duplikat-Abgleich mit der Sammlung (Fuzzy-Match, Remixer beachten) |
| `sc_digger/output.py` | State-DB, Original-Download (scdl), `finalize_quality`, Telegram-Digest, Export-Datei, `telegram_call` |
| `sc_digger/retry.py` | Retry-Queue für fehlgeschlagene Original-Downloads |
| `sc_digger/cloud.py` | Cloud-Downloads (Dropbox, Google Drive) |
| `sc_digger/quality.py` | ffprobe, Spektrum-Cutoff (Fake-Erkennung), EBU R128 / LRA (Brickwall) |
| `sc_digger/loudness.py` | Pegel-Normalisierung neuer Inbox-Downloads (-8.5 LUFS, samplegenau, Metadaten-Erhalt) |
| `sc_digger/analysis.py` | BPM/Key per librosa, BPM-Oktav-Korrektur `resolve_bpm` |
| `sc_digger/fingerprint.py` | Audio-Fingerprints (Chromaprint/fpcalc), gleiche Aufnahme erkennen |
| `sc_digger/harmonic.py` | Harmonische Kompatibilität (Camelot-Wheel), Suche passender Tracks nach Key und BPM |
| `sc_digger/organize.py` | Inbox-Sortierung `<BPM>/<Camelot>/`, Tags schreiben |
| `sc_digger/rekordbox.py` | Rekordbox-XML: eine Playlist pro Woche aus der Inbox |
| `sc_digger/db.py` | Zentrale Track-DB (Phase 2), Metadaten-Index, Audio-Fingerprints, Jobs-Queue, versionierte Migrationen |
| `sc_digger/audit.py` | Read-only Library Audit (Phase 2.2), Fake-Erkennung, HTML-Dashboard |
| `sc_digger/health.py` | Laufprotokoll, Alarm bei wiederholt leeren/fehlerhaften Läufen |
| `sc_digger/healthcheck.py` | Container-Healthcheck: Cron, letzter Lauf, DB-Integrität |
| `sc_digger/redact.py` | Zugangsdaten aus Texten und Logs entfernen |
| `config.yaml` | Einzige Konfiguration (Tags, Referenz-Accounts, Schwellwerte). Ist die Produktivkonfiguration. |
| `entrypoint.sh` | Schreibt `cron.env` (Cron hat sonst weder PATH noch Secrets), startet cron und Bot |
| `skills/*/SKILL.md` | Abläufe für lokale Modelle in LibreChat (Review, Worker, Planer), per GitHub Skill Sync gespiegelt; Regeln bleiben in `AGENTS.md` |
| `.github/scripts/acceptance_guard.py` | CI-Check `acceptance-guard`: Akzeptanztests = Issue, keine neuen skips, CI/Test-Konfiguration geschützt |
| `set-secret.sh` / `update.sh` | Zugangsdaten setzen / Update ausrollen (auf dem Server) |

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

## Standard-Aufträge

Für Stephan: Mit diesen Sätzen startet man einen Agenten.

| Rolle | Auftrag |
|---|---|
| Planer | „Erstelle aus unserem Gespräch ein Issue in tripitest-art/sc-digger nach dem Formular `worker-task` (AGENTS.md, Worker-Aufgaben → Planer).“ |
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
