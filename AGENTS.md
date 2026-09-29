# AGENTS.md – Einweisung für alle Agenten (Claude, Gemini, Codex, …)

## 📄 Version: 2024.1219-01 | Status: Stable | Author: Admin | Team: Stephan + Review Board
## Generated: 2024-12-19T00:05Z

### ⚠️ Goldene Regeln

> Lies die gesamte Datei, bevor du etwas änderst. Abweichungen im PR begründen.

1. **GitHub main ist die einzige Wahrheit.** Nie Code direkt auf dem Server oder in losen Kopien ändern. Der Server holt nur (Deploy-Key ohne Schreibrecht).
2. **Ein Branch pro Aufgabe,** nach `main` nur per Pull Request. Branch-Namen: `feature/<kurz>`, `fix/<kurz>`, `docs/<kurz>`. Vor dem Start `main` aktualisieren.
3. **Tests müssen grün sein.** `python -m pytest -q` vor jedem Push. Neue Logik bekommt Tests; behobene Fehler bekommen einen Test, der den Fehler reproduziert.
4. **Die Sammlung wird nie verändert.** `/music/Schranz` ist schreibgeschützt eingebunden und bleibt es. Rekordbox verknüpft Cues über Dateipfade; verschieben, umbenennen oder Audio bearbeiten zerstört sie. Sortieren und Taggen nur in der Inbox.
5. **Zugangsdaten nie in Repo, Logs, Fehlermeldungen oder Chat.** Telegram-API immer über `output.telegram_call()` (nie `requests` direkt, die URL enthält den Token). Logging über `redact.install_redacting_logging()`. Keine `.env`-Inhalte ausgeben, auch nicht „maskiert" – Namen und Längen reichen.
6. **Fair zu Artists.** Gates (Hypeddit, Droploud, …) werden erkannt und verlinkt, nie umgangen: keine Wegwerf-Adressen, keine Link-Leaks, keine Fake-Accounts. Stream-Rips werden nicht geladen (`scdl --only-original`).
7. **Nicht still scheitern.** Fehler, die den täglichen Lauf betreffen, müssen im Health-Alarm oder im Digest sichtbar werden.
8. **Nicht zwei Agenten in denselben Dateien.** `sc_digger/main.py` ist der Engpass (alle Modi laufen dort zusammen). Wer dort arbeitet, schreibt es ins Issue.

### 🧩 Token-Effizienz-Regeln (9–15)

> Gilt für alle Agenten inklusive Gemini. Implementiere diese Prinzipien zur Minimierung von Token-Kosten und Maximierung der Effizienz in jedem API-Aufruf und jedem Push zu einem Repository:

**9. Atomic PRs:** Immer eine Datei oder ein zusammenhängendes Thema pro Pull Request. Vermeide riesige Commits, die nur Änderungen an einer einzigen Konfigurationsdatei beinhalten oder viele Unbedeutende Dateien modifizieren. Jeder Commit muss einen klaren Zweck erfüllen und leicht reviewbar sein.

**10. JSON-Output:** Bei allen Textausgaben (Logs, Reports) immer strukturiertes JSON verwenden. Dies ermöglicht es Tools, die Ausgabe automatisch parsen zu können und reduziert unnötige Interpretation durch natürliche Sprache. Beispiel: `{"status": "success", "changes": ["AGENTS.md"]}`

**11. Mocking & Fakes aus Hauptcode auslagern:** Trennung von Produktion und Testing. Schreibe Tests, die keine echten API-Aufrufe oder Datenbankzugriffe machen. Verwende stattdessen Fakes für SoundCloud-Data oder Datenbank-Simulationen. Beispiel: `FakeSC` in `tests/test_modes.py` simuliert eine SoundCloud-API.

**12. Delta-Context:** Push nur geänderte Zeilen. Nicht ganze Dateien pushen, wenn nur wenige Änderungen nötig sind. Dies spart Token-Kosten beim diffing und reduziert Rechenzeit. Beispiel: Wenn nur 5 Zeilen geändert, dann nicht den gesamten Dateiinhalt pushen.

**13. Akzeptanztests als Issue:** Alle neuen Features oder Fixes benötigen einen zugehörigen Akzeptanztest im Issue. Dieser Test läuft ohne Netzwerk (mit Fakes) und ist vor der Umsetzung rot.

**14. Issue-Kontrakt:** Der Akzeptanztest definieren den Kontrakt des Issues. Neue `skip`/`xfail` oder Änderungen an `.github/`, `conftest.py` sind verboten.

**15. Review-Prozess:** Review durch einen Agenten, der den Code nicht geschrieben hat, oder durch Stephan. Merge nur bei grünen Checks (`tests`, `acceptance-guard`) und abgeschlossenem Review. Deploy via `update.sh` auf dem Server nach Merge in `main`. GitHub-Regeln: Squash-Merge, `main-schutz`, Copilot-Review (optional).

### 🔍 Worum es geht

`sc-digger` ist ein Crate-Digging-Tool für Schranz/Hard Techno. Es sucht täglich neue SoundCloud-Tracks, bewertet sie genre-relativ, gleicht sie mit der Sammlung ab, lädt freigegebene Originale, prüft deren Qualität, analysiert BPM/Key, taggt und sortiert sie in eine Inbox und schickt einen Telegram-Digest. Ein Telegram-Bot prüft Playlists und Track-Stations auf Zuruf.

Details und Ausbaustufen: `ROADMAP.md`.

### 🤖 Worker-Aufgaben (Issues mit Label `worker-task`)

Drei Rollen: **Planer** schreibt das Issue, **Worker** setzt es um, **Reviewer** prüft und merged. Worker und Reviewer sind nie derselbe Agent. Das Issue ist der Vertrag: Was dort nicht steht, wird nicht gebaut.

Alle Agenten arbeiten mit Stephans GitHub-Konto. Die Regeln unten setzen deshalb nicht die Rechteverwaltung durch, sondern der Check `acceptance-guard` (läuft immer mit dem Stand aus `main`) und das Review.

Auf `main` gilt die GitHub-Regel `main-schutz`: nur per Pull Request, nur Squash-Merge, Pflicht-Checks `tests`, `acceptance-guard` und CodeQL (blockiert ab „High"). Copilot-Review und `github-advanced-security` brauchen einen bezahlten Copilot-Plan.

#### Planer

- Issue über das Formular „Aufgabe für einen Agenten": exakte Signaturen, was ausdrücklich nicht dazugehört, Merge-Modus.
- Akzeptanztests als pytest-Code ins Issue. Sie laufen ohne Netzwerk (Fakes, synthetisches Audio per ffmpeg wie in `tests/test_analysis_organize.py`) und sind vor der Umsetzung rot.
- Labels: `worker-task`, `bereit`, `phase-N`, bei Bedarf `berührt-main.py`.

#### Worker

1. `gh issue view <N>` und diese Datei lesen. Übernehmen:
   `gh issue edit <N> --add-label in-arbeit --remove-label bereit`
2. `git checkout main && git pull && git checkout -b feature/issue-<N>-<kurz>`
3. Den Akzeptanztest-Block **zeichengenau** nach `tests/acceptance/test_issue_<N>.py` kopieren und als eigenen Commit sichern, **bevor** du etwas umsetzt.
4. Umsetzen, nur in den Dateien aus dem Issue. Eigene Tests ergänzen.
   `python -m pytest -q`, bis alles grün ist.
5. **Verboten:** Akzeptanztests ändern; neue `skip`/`xfail`/`importorskip`; Änderungen an `.github/`, `conftest.py` oder pytest-Konfiguration; das Label `freigabe-geschützt` setzen.
   Hältst du einen Akzeptanztest für falsch: aufhören und im Issue begründen. Nie den Test passing machen.
6. Nach drei erfolglosen Anläufen am selben Fehler: aufhören, Branch pushen, Draft-PR (`--draft`) mit genauer Beschreibung des Problems. Kein Umbau quer durchs Projekt.
7. PR mit ausgefüllter Vorlage öffnen. `--fill` reicht nicht, weil `Closes #<N>` fehlen würde:
   `gh pr create --title "<Issue-Titel>" --body-file <ausgefüllte Vorlage>`
   Der Body muss `Closes #<N>` enthalten, sonst prüft `acceptance-guard` nichts gegen das Issue.
   Unter Windows/PowerShell Texte nie inline übergeben (Backtick ist dort Escape-Zeichen), immer `--body-file`.
8. Review-Kommentare (Reviewer, ggf. Copilot): „Muss"-Punkte und echte Fehler im Issue-Umfang beheben; alles andere im Thread kurz begründen, nicht umsetzen. Das Issue gilt, nicht der Vorschlag. Nie deshalb Tests abschwächen oder weitere Dateien anfassen. Alle Korrekturen in **einem** Push.

#### Nächste Aufgabe selbst wählen

1. `gh issue list --state open --label worker-task --label bereit --search "sort:created-asc -label:blockiert"`
2. Das erste nehmen. Ausnahme Regel 8: Hat es `berührt-main.py` und ist ein anderes offenes Issue mit `in-arbeit` und `berührt-main.py` vorhanden, das nächste nehmen.
3. Bleibt keins übrig: sagen und aufhören, nichts anderes anfangen.
4. Genau **ein** Issue bearbeiten (Worker 1–7). Fertig, wenn der PR offen ist und `tests` und `acceptance-guard` grün sind (`gh pr checks`). Abbrechen statt weitermachen bei Worker 5/6 oder wenn eine Datei außerhalb des Issues nötig wäre.

#### Reviewer

- Prüft den Pull Request auf Code-Qualität, Testabdeckung und Einhaltung der Regeln.
- Merge nur bei grünen Checks (`tests`, `acceptance-guard`) und abgeschlossenem Review.

### 📁 Architektur-Übersicht

| Datei/Pfad | Verantwortlichkeit | Beschreibung |
|------------|------------------|--------------|
| `sc_digger/main.py` | Worker (Engpass) | Zentrale Einstiegspunkt für alle Modi (Download, Analyse, Tagging), läuft auf dem Server |
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

### 🖥 Betrieb

Server, Mounts, Zeitplan, Zugangsdaten, Logs: `BETRIEB.md`. Host/IP/SSH nur in `BETRIEB.local.md` (ignoriert) oder beim Menschen erfragen, nie raten.

### 💻 Entwickeln

```bash
pip install -r requirements.txt pytest     # braucht ffmpeg im PATH
python -m pytest -q                         # alle Tests, ~10 s
python -m sc_digger.main --dry-run --no-telegram -v   # lokal, braucht Netz zu SoundCloud
```

Konventionen: Python 3.12, Typ-Hinweise, Kommentare und Log-Meldungen auf Deutsch, Kommentare erklären das *Warum*. Netzwerk in Tests immer durch Fakes ersetzen (Beispiele: `FakeSC` in `tests/test_modes.py`, `FakeLinkSC` in `tests/test_merge.py`).

### ⚠️ Bekannte Stolperfallen (alle schon einmal passiert)

- **Cron-Umgebung ist leer:** kein `/usr/local/bin`, keine `.env`. Deshalb `cron.env`.
- **scdl meldet Fehler mit Exit-Code 0.** Erfolg nur daran messen, ob eine Datei entstand.
- **Originale nur mit Login.** Ohne `SOUNDCLOUD_AUTH_TOKEN` gibt es sie nicht; ohne `--only-original` lädt scdl still den Stream.
- **Playlists liefern die meisten Tracks nur als Stub** (nur `id`). Nachladen, sonst fehlen sie.
- **Telegram-Token steckt in jeder API-URL** und damit in `requests`-Fehlern.
- **`TELEGRAM_CHAT_ID` ist eine Zahl**, nicht der Bot-Name.
- **Windows-Zeilenenden** brechen `entrypoint.sh`; `.gitattributes` erzwingt LF.
- **Zwei Code-Stände** (Server-Kopie und Repo) liefen schon einmal auseinander. Siehe Regel 1.
