# AGENTS.md – Einweisung für alle Agenten (Claude, Gemini, Codex, …)

Diese Datei ist die gemeinsame Arbeitsgrundlage. Lies sie vollständig, bevor du etwas
änderst. Wenn du eine Regel hier für falsch hältst: im Pull Request begründen, nicht
stillschweigend abweichen.

## Worum es geht

`sc-digger` ist ein Crate-Digging-Tool für Schranz/Hard Techno. Es sucht täglich neue
SoundCloud-Tracks, bewertet sie genre-relativ, gleicht sie mit der Sammlung ab, lädt
freigegebene Originale, prüft deren Qualität, analysiert BPM/Key, taggt und sortiert sie
in eine Inbox und schickt einen Telegram-Digest. Ein Telegram-Bot prüft Playlists und
Track-Stations auf Zuruf. Details und Ausbaustufen: `ROADMAP.md`.

## Goldene Regeln

1. **GitHub `main` ist die einzige Wahrheit.** Nie Code direkt auf dem Server oder in
   losen Kopien ändern. Der Server holt nur (Deploy-Key ohne Schreibrecht).
2. **Ein Branch pro Aufgabe, nach `main` nur per Pull Request.** Branch-Namen:
   `feature/<kurz>`, `fix/<kurz>`, `docs/<kurz>`. Vor dem Start `main` aktualisieren.
3. **Tests müssen grün sein.** `python -m pytest -q` vor jedem Push. Neue Logik bekommt
   Tests; behobene Fehler bekommen einen Test, der den Fehler reproduziert.
4. **Die Sammlung wird nie verändert.** `/music/Schranz` ist schreibgeschützt eingebunden
   und bleibt es. Rekordbox verknüpft Cues über Dateipfade; verschieben, umbenennen oder
   Audio bearbeiten zerstört sie. Sortieren und Taggen nur in der Inbox.
5. **Zugangsdaten nie in Repo, Logs, Fehlermeldungen oder Chat.** Telegram-API immer über
   `output.telegram_call()` (nie `requests` direkt, die URL enthält den Token). Logging
   über `redact.install_redacting_logging()`. Keine `.env`-Inhalte ausgeben, auch nicht
   „maskiert" – Namen und Längen reichen.
6. **Fair zu Artists.** Gates (Hypeddit, Droploud, …) werden erkannt und verlinkt, nie
   umgangen: keine Wegwerf-Adressen, keine Link-Leaks, keine Fake-Accounts.
   Stream-Rips werden nicht geladen (`scdl --only-original`).
7. **Nicht still scheitern.** Fehler, die den täglichen Lauf betreffen, müssen im Health-
   Alarm oder im Digest sichtbar werden.
8. **Nicht zwei Agenten in denselben Dateien.** `sc_digger/main.py` ist der Engpass (alle
   Modi laufen dort zusammen). Wer dort arbeitet, schreibt es ins Issue.

### Token-Effizienz-Regeln (Pflicht)

9. **Atomic PRs:** Ein Pull Request = Eine Logikänderung. Kein "Optimiere die Download-Funktion". Stattdessen: "Füge Validierung für `cloud_max_mb` in `sc_digger/output.py` hinzu."
10. **JSON-Output:** Generiere keine Erklärungen, nur Code-Blöcke und Fehlermeldungen. System-Prompt: "Kein Chat-Output. Nur Code und Fehler."
11. **Kontext-Minimierung:** Lese nur die Dateien, die im Issue genannt sind. Ignoriere den Rest des Repos.
12. **Dry-Run-First:** Bevor ein Agent eine API aufruft, muss er `--dry-run` nutzen. Das spart den Token-Verbrauch für den eigentlichen Download-Vorgang.
13. **Mocking & Fakes:** Niemals echte API-Aufrufe oder große Datei-Uploads für den Agenten-Input. Nutze `FakeSC` und synthetisches Audio.
14. **Delta-Context:** Wenn ein Agent Code analysiert, verwende nur die Delta-Änderungen. Vermeide das "Redesign" des gesamten Projekts, wenn nur ein Bug fixen soll.
15. **Error-Handling:** Wenn ein Fehler auftritt, analysiere nur den Fehler-Stacktrace, nicht den ganzen Code.

## Ablauf pro Aufgabe

1. Issue lesen (oder anlegen lassen). Umfang klein halten, ein Thema pro Pull Request.
2. `git checkout main && git pull && git checkout -b feature/<kurz>`
   Mehrere Agenten auf einem Rechner: jeder in eigenem Worktree
   (`git worktree add ../sc-digger-<kurz> -b feature/<kurz>`).
3. Umsetzen, Tests schreiben, `python -m pytest -q`.
4. Committen (Nachricht: *warum*, nicht nur *was*), Branch pushen.
5. Pull Request öffnen und die Vorlage ausfüllen. Kann das Werkzeug keinen PR öffnen:
   Branch pushen und dem Menschen den Link `https://github.com/tripitest-art/sc-digger/pull/new/<branch>` geben.
6. Review durch einen Agenten, der den Code nicht geschrieben hat, oder durch Stephan.
   Merge nur bei grünen Checks `tests` und `acceptance-guard` und abgeschlossenem Review.
7. Deploy: `cd /root/sc-digger && ./update.sh` auf dem Server (nur nach Merge in `main`).

## Worker-Aufgaben (Issues mit Label `worker-task`)

Drei Rollen: **Planer** schreibt das Issue, **Worker** setzt es um, **Reviewer** prüft und
merged. Worker und Reviewer sind nie derselbe Agent. Das Issue ist der Vertrag: Was dort
nicht steht, wird nicht gebaut.

Alle Agenten arbeiten mit Stephans GitHub-Konto. Die Regeln unten setzt deshalb nicht die
Rechteverwaltung durch, sondern der Check `acceptance-guard` (läuft immer mit dem Stand aus
`main`) und das Review.

Auf `main` gilt die GitHub-Regel `main-schutz`: nur per Pull Request, nur Squash-Merge,
Pflicht-Checks `tests`, `acceptance-guard` und CodeQL (blockiert ab „High"). Copilot-Review
und `github-advanced-security` brauchen einen bezahlten Copilot-Plan: nie darauf warten, ein
roter `github-advanced-security` ist kein Mangel.

### Planer

- Issue über das Formular „Aufgabe für einen Agenten": exakte Signaturen, was ausdrücklich
  nicht dazugehört, Merge-Modus.
- Akzeptanztests als pytest-Code ins Issue. Sie laufen ohne Netzwerk (Fakes, synthetisches
  Audio per ffmpeg wie in `tests/test_analysis_organize.py`) und sind vor der Umsetzung rot.
- Labels: `worker-task`, `bereit`, `phase-N`, bei Bedarf `berührt-main.py`.
- **Token-Effizienz:** Issue-Titel muss die exakte Änderung beschreiben: "Füge X in Y hinzu", nicht "Optimiere Y".

### Worker

1. `gh issue view <N>` und diese Datei lesen. Übernehmen:
   `gh issue edit <N> --add-label in-arbeit --remove-label bereit`
2. `git checkout main && git pull && git checkout -b feature/issue-<N>-<kurz>`
3. Den Akzeptanztest-Block **zeichengenau** nach `tests/acceptance/test_issue_<N>.py`
   kopieren und als eigenen Commit sichern, **bevor** du etwas umsetzt.
4. Umsetzen, nur in den Dateien aus dem Issue. Eigene Tests ergänzen.
   `python -m pytest -q`, bis alles grün ist.
5. **Verboten:** Akzeptanztests ändern; neue `skip`/`xfail`/`importorskip`; Änderungen an
   `.github/`, `conftest.py` oder pytest-Konfiguration; das Label `freigabe-geschützt` setzen.
   Hältst du einen Akzeptanztest für falsch: aufhören und im Issue begründen. Nie den Test
   passend machen.
6. Nach drei erfolglosen Anläufen am selben Fehler: aufhören, Branch pushen, Draft-PR
   (`--draft`) mit genauer Beschreibung des Problems. Kein Umbau quer durchs Projekt.
7. PR mit ausgefüllter Vorlage öffnen. `--fill` reicht nicht, weil `Closes #<N>` fehlen würde:
   `gh pr create --title "<Issue-Titel>" --body-file <ausgefüllte Vorlage>`
   Der Body muss `Closes #<N>` enthalten, sonst prüft `acceptance-guard` nichts gegen das Issue.
   Unter Windows/PowerShell Texte nie inline übergeben (Backtick ist dort Escape-Zeichen,
   aus `` `t `` wird ein Tabulator), immer `--body-file`.
8. Review-Kommentare (Reviewer, ggf. Copilot): „Muss"-Punkte und echte Fehler im Issue-Umfang
   beheben; alles andere im Thread kurz begründen, nicht umsetzen. Das Issue gilt, nicht der
   Vorschlag. Nie deshalb Tests abschwächen oder weitere Dateien anfassen. Alle Korrekturen
   in **einem** Push.

### Nächste Aufgabe selbst wählen

1. `gh issue list --state open --label worker-task --label bereit --search "sort:created-asc -label:blockiert"`
2. Das erste nehmen. Ausnahme Regel 8: Hat es `berührt-main.py` und ist ein anderes offenes
   Issue mit `in-arbeit` und `berührt-main.py` vorhanden, das nächste nehmen.
3. Bleibt keins übrig: sagen und aufhören, nichts anderes anfangen.
4. Genau **ein** Issue bearbeiten (Worker 1–7). Fertig, wenn der PR offen ist und `tests` und
   `acceptance-guard` grün sind (`gh pr checks`). Abbrechen statt weitermachen bei Worker 5/6
   oder wenn eine Datei außerhalb des Issues nötig wäre.

### Reviewer

1. `gh pr view <PR> --comments`, `gh pr diff <PR>`, `gh pr checks <PR>`. Sind `tests`,
   `acceptance-guard` oder CodeQL nicht grün: nicht mergen, Befund als Review schreiben.
   Die Regel `main-schutz` erzwingt das ohnehin; der Merge würde abgelehnt.
2. Gegen das Issue prüfen: nur genannte Dateien geändert, Signaturen exakt, „Fertig, wenn"
   vollständig, Goldene Regeln eingehalten (besonders 4–7). Eigene Tests des Workers auf
   Aussagekraft prüfen (`assert True`, zu schwache Vergleiche, gemockter Prüfling).
   Copilot-Kommentare: umgesetzt oder begründet abgelehnt; Umsetzungen außerhalb des
   Issue-Umfangs sind ein Mangel.
3. Ergebnis immer als `gh pr review <PR> --comment --body-file <datei>`. `--request-changes`
   und `--approve` lehnt GitHub ab (alle PRs laufen über Stephans Konto = eigener PR).
   Erste Zeile ist das Urteil: **„Änderungen nötig"** (dann „Muss"/„Kann"-Punkte, der Worker
   arbeitet auf demselben Branch in einem Push nach) oder **„Freigegeben"**.
4. Freigegeben, Merge-Modus „automatisch": `gh pr merge <PR> --squash`.
   Merge-Modus „manuell": Stephan Bescheid geben, er merged.
5. Trägt ein PR das Label `freigabe-geschützt`, stand die Änderung an geschützten Dateien
   zur Entscheidung. Im Review ausdrücklich bestätigen, dass sie begründet ist.

### Standard-Aufträge

| Rolle | Auftrag |
|---|---|
| Planer | „Erstelle aus unserem Gespräch ein Issue in tripitest-art/sc-digger nach dem Formular `worker-task` (AGENTS.md, Worker-Aufgaben → Planer). **Token-Effizienz:** Issue-Titel muss die exakte Änderung beschreiben." |
| Worker | „Bearbeite Issue #N nach AGENTS.md, Abschnitt Worker-Aufgaben → Worker. **Token-Effizienz:** Atomic PR, JSON-Output, Dry-Run-First." |
| Worker (autonom) | „/goal Bearbeite die nächste Aufgabe nach AGENTS.md, Worker-Aufgaben → Nächste Aufgabe selbst wählen. **Token-Effizienz:** Nur Delta-Context, keine Redesigns." |
| Reviewer | „Prüfe PR #M nach AGENTS.md, Abschnitt Worker-Aufgaben → Reviewer. **Token-Effizienz:** Nur Delta-Context, keine Erklärungen." |
| Worker (Nacharbeit) | „Arbeite das Review in PR #M ab (AGENTS.md, Worker → Schritt 8). **Token-Effizienz:** Nur die geforderten Änderungen, keine zusätzlichen." |

## Architektur

| Datei | Zuständig für |
|---|---|
| `sc_digger/main.py` | CLI und Modi (`discover`, `playlist`, `similar`, `check`), gemeinsame Pipeline `process()`, Auslieferung `deliver()` |
| `sc_digger/bot.py` | Telegram-Listener; ruft `main.run_link()`; beendet sich nie selbst (sonst stirbt der Cron) |
| `sc_digger/soundcloud.py` | Inoffizielle api-v2 (client_id aus dem Frontend), Playlists inkl. Stub-Nachladen, Station/Related, Referenz-Accounts (soundcloud-v2-Lib) |
| `sc_digger/pipeline.py` | Text-BPM, Genre-Relevanz, Perzentil-Scoring, Download-Klassifizierung |
| `sc_digger/collection.py` | Duplikat-Abgleich mit der Sammlung (Fuzzy-Match, Remixer beachten) |
| `sc_digger/output.py` | State-DB, Original-Download (scdl), `finalize_quality`, Telegram-Digest, Export-Datei, `telegram_call` |
| `sc_digger/quality.py` | ffprobe, Spektrum-Cutoff (Fake-Erkennung), EBU R128 / LRA (Brickwall) |
| `sc_digger/analysis.py` | BPM/Key per librosa, BPM-Oktav-Korrektur `resolve_bpm` |
| `sc_digger/organize.py` | Inbox-Sortierung `<BPM>/<Camelot>/`, Tags schreiben |
| `sc_digger/db.py` | Zentrale Track-DB (Phase 2), Metadaten-Index, Audio-Fingerprints, Jobs-Queue, versionierte Migrationen |
| `sc_digger/audit.py` | Read-only Library Audit (Phase 2.2), Fake-Erkennung, HTML-Dashboard |
| `sc_digger/health.py` | Laufprotokoll, Alarm bei wiederholt leeren/fehlerhaften Läufen |
| `sc_digger/redact.py` | Zugangsdaten aus Texten und Logs entfernen |
| `config.yaml` | Einzige Konfiguration (Tags, Referenz-Accounts, Schwellwerte). Ist die Produktivkonfiguration. |
| `entrypoint.sh` | Schreibt `cron.env` (Cron hat sonst weder PATH noch Secrets), startet cron und Bot |
| `.github/scripts/acceptance_guard.py` | CI-Check `acceptance-guard`: Akzeptanztests = Issue, keine neuen skips, CI/Test-Konfiguration geschützt |
| `set-secret.sh` / `update.sh` | Zugangsdaten setzen / Update ausrollen (auf dem Server) |

## Betrieb

Server, Mounts, Zeitplan, Zugangsdaten, Logs: `BETRIEB.md`. Host/IP/SSH nur in
`BETRIEB.local.md` (ignoriert) oder beim Menschen erfragen, nie raten.

## Entwickeln

```bash
pip install -r requirements.txt pytest     # braucht ffmpeg im PATH
python -m pytest -q                         # alle Tests, ~10 s
python -m sc_digger.main --dry-run --no-telegram -v   # lokal, braucht Netz zu SoundCloud
