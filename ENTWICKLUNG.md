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
| `sc_digger/output.py` | State-DB, Original-Download (scdl), `finalize_quality`, Telegram-Digest, Export-Datei, `telegram_call`, `send_telegram_photo` |
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
| `sc_digger/web/status.py` | Statusseite (Phase 5): `collect_status` liest Läufe, Health-Alarm, Inbox-Größe und Wochenstatistik nur lesend zusammen |
| `sc_digger/web/app.py` | FastAPI-App der Statusseite (HTTP-Basic, `docs_url=None`), HTML-Vorlage unter `sc_digger/web/templates/`; mit `base_path`/`local_path` zusätzlich die Editor-Routen `/config`, `/config/preview`, `/config/save`, `/config/reset` inkl. Origin-/Referer-Schutz |
| `sc_digger/web/configedit.py` | Reine Logik des Konfigeditors (ohne Webframework): Formularwert parsen/formatieren, minimaler Override, Diff, Schlüssel entfernen, atomares Schreiben der lokalen Datei mit Backup |
| `sc_digger/web/__main__.py` | Startbefehl `python -m sc_digger.web`; startet ohne `SC_DIGGER_WEB_PASSWORD` nicht, schaltet den Editor nur mit `SC_DIGGER_CONFIG_LOCAL` ein |
| `sc_digger/stats.py` | Statistikberechnung für Telegram-Digest und /stats-Bot-Befehl, `render_stats_chart`, `send_weekly_digest`, `_trend_section` (Trend-Abschnitt) |
| `sc_digger/trends.py` | Trend-Radar (Phase 4): Wachstumsanalyse historischer Engagement-Snapshots (`track_snapshots`) je Track und Artist |
| `sc_digger/preview.py` | Live-Preview (Phase 3): energiereichstes Segment einer Audiodatei per ffmpeg finden und als OGG-Opus-Snippet extrahieren (`find_loudest_segment`, `extract_preview`) |
| `sc_digger/schema.py` | Config-Schema: `FIELDS`/`SECTION_TITLES`, `validate_config` (Typen, Wertebereiche, Warnungen), `deep_merge` für die lokale Override-Datei, Prüfbefehl `python -m sc_digger.schema` |
| `config.yaml` | Einzige Konfiguration (Tags, Referenz-Accounts, Schwellwerte). Ist die Produktivkonfiguration. |
| `entrypoint.sh` | Schreibt `cron.env` (Cron hat sonst weder PATH noch Secrets), startet cron und Bot |
| `skills/*/SKILL.md` | Abläufe für lokale Modelle in LibreChat (Review, Worker, Planer), per GitHub Skill Sync gespiegelt; Regeln bleiben in `AGENTS.md` |
| `.github/scripts/acceptance_guard.py` | CI-Check `acceptance-guard`: Akzeptanztests = Issue, keine neuen skips, CI/Test-Konfiguration geschützt, PR-Text ausgefüllt (`Closes #N`, `Worker:`) |
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
- **Nacharbeit ohne Commit:** Verlangt ein Review nur den PR-Text, gibt es keinen neuen
  Commit (#103). Ob der Text nach dem Review geändert wurde, zeigt `lastEditedAt`:
  `gh api graphql -f query='{repository(owner:"tripitest-art",name:"sc-digger"){pullRequest(number:<PR>){lastEditedAt}}}'`
  Jünger als das letzte Review heißt: Nacharbeit da, der PR ist wieder dran.

## Planer-Ablauf

Für jeden Planer, gleich welches Modell. Die Regeln stehen in `AGENTS.md` (Worker-Aufgaben →
Planer); hier steht, wie man zu einem Issue kommt, das ein anderes Modell ohne Rückfragen
umsetzen kann. Agenten ohne Shell folgen dem Skill `skills/sc-digger-planner/SKILL.md`.

Der Planer schreibt Issues, keinen Code: keine Branches, keine Commits, keine PRs, kein Merge.

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
6. **Akzeptanztests prüfen (mit Shell).** Datei lokal anlegen und laufen lassen: auf `main`
   rot (meist `ImportError`), gegen eine Probe-Umsetzung grün, und die restliche Suite
   **mit** der Probe-Umsetzung grün (so fallen die Fälle aus Schritt 2 und 4 auf). Die
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
