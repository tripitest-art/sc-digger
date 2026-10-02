---
name: sc-digger-worker
description: "Worker-Aufgabe in tripitest-art/sc-digger über die GitHub-MCP-Werkzeuge. Nutzen bei „Bearbeite Issue #N“ oder „Arbeite das Review in PR #M ab“. Feste Abfolge bis zum PR mit grünen Checks."
always-apply: false
compatibility: Braucht den MCP-Server github-worker (siehe MCP.md). Für Qwen in LibreChat.
---

# Worker-Aufgabe in sc-digger

Die Regeln stehen in `AGENTS.md` (Abschnitt „Worker“) und `MCP.md`. Dieser Skill legt nur
fest, **wie** du sie mit den MCP-Werkzeugen abarbeitest. Repo immer `owner: tripitest-art`,
`repo: sc-digger`. Suche nie nach anderen Repositories.

Führe jeden Schritt als Werkzeugaufruf aus. Kündige ihn nicht nur an. Ein Schritt ist erst
erledigt, wenn das Werkzeug ein Ergebnis geliefert hat.

Lautet der Auftrag „Arbeite das Review in PR #M ab“: direkt zu **Teil B**.

## Teil A: Issue #N bearbeiten

### A1: Regeln lesen

`get_file_contents` (`ref: main`) für `AGENTS.md` und `ENTWICKLUNG.md`.

### A2: Issue lesen

`issue_read` mit `method: get`, `issue_number: <N>`.

Aufhören und Stephan fragen, wenn eines davon zutrifft:
- `bereit` fehlt in den Labels.
- `blockiert` steht in den Labels.
- `berührt-main.py` steht in den Labels (über MCP noch gesperrt).

### A3: Übernehmen (Labels)

1. `issue_read` mit `method: get_labels`, um die vollständige Liste zu lesen.
2. `issue_write` mit `method: update`, `issue_number: <N>` und **nur** `labels`: dieselbe
   Liste, `bereit` ersetzt durch `in-arbeit`. **Nie** `title` oder `body` mitschicken.

`issue_write` ersetzt die ganze Liste. Fehlt ein Label, ist es danach weg.

### A4: Branch anlegen

`create_branch` mit `branch: feature/issue-<N>-<kurz>` und `from_branch: main`.

### A5: Akzeptanztest sichern (eigener Commit)

`push_files` auf deinen Branch:
- Nur die Datei `tests/acceptance/test_issue_<N>.py`.
- Inhalt ist der Codeblock unter „Akzeptanztests“ aus dem Issue, Zeichen für Zeichen kopiert.
  Nicht neu tippen, nicht umformatieren.
- Die Datei endet mit einem Zeilenumbruch.
- Commit-Nachricht: `Akzeptanztests aus Issue #<N>`.

Hat das Issue keinen Akzeptanztest-Block, entfällt dieser Schritt.

### A6: Umsetzen

1. Für **jede** Datei unter „Betroffene Dateien“ `get_file_contents` mit `ref:` deinem Branch
   aufrufen.
2. Die Änderung genau nach „Schnittstellen“ umsetzen. Nichts aus „Nicht Teil dieser Aufgabe“
   anfassen.
3. Eigene Tests unter `tests/` für jeden Punkt aus „Fertig, wenn“ schreiben.
   - Jeder Test braucht mindestens ein `assert`, das echtes Verhalten prüft.
   - Netzwerk nur über Fakes, siehe `FakeSC` in `tests/test_modes.py`. Diese Datei vorher lesen.
4. `push_files` mit **allen** geänderten und neuen Dateien in **einem** Commit:
   - Jede Datei mit ihrem **vollständigen** Inhalt, nie „…“ oder „Rest unverändert“.
   - Commit-Nachricht: *warum*, nicht nur *was*.

### A7: PR öffnen

1. `get_file_contents` für `.github/pull_request_template.md` (`ref: main`).
2. `create_pull_request` mit `head:` deinem Branch, `base: main`, `title:` dem Issue-Titel und
   `body:` der ausgefüllten Vorlage:
   - `Closes #<N>` unter „Was und warum“.
   - Häkchen nur für das, was wirklich geprüft ist. `pytest` lief nicht lokal, dort steht
     „über CI“.
   - Unter „Umgesetzt von / Review durch“: `Worker: Qwen 3.5 9B` (dein echter Modellname, kein
     Platzhalter).
   - Andere Issues ohne „Closes/Fixes/Resolves“ davor erwähnen.

### A8: Diff prüfen

`pull_request_read` mit `method: get_files`.
- Hat eine Datei mehr `deletions` als beabsichtigt, ist sie abgeschnitten. Sofort mit
  `push_files` und vollständigem Inhalt neu schreiben.
- Steht dort eine Datei, die nicht zum Issue gehört: zurücksetzen.

### A9: Checks abwarten

`pull_request_read` mit `method: get_check_runs`.
- Laufen Checks noch (`status` nicht `completed`): erneut abfragen.
- `tests` rot: `get_job_logs` (`job_id` = `id` des Checks, `return_content: true`,
  `tail_lines: 80`) lesen, Ursache beheben, **ein** `push_files`, dann wieder A9.
- `acceptance-guard` rot: Das Log sagt, was fehlt, meist `Closes #<N>` oder ein Akzeptanztest,
  der nicht exakt übernommen wurde. Beheben, dann wieder A9.
- `github-advanced-security` rot: ignorieren.
- Nach drei erfolglosen Anläufen am selben Fehler: aufhören und das Problem mit
  `add_issue_comment` im Issue beschreiben (Worker, Schritt 6).

### A10: Fertig melden

Erst wenn `tests` und `acceptance-guard` `success` zeigen: Stephan in einem Satz melden, dass
PR #<M> offen ist und die Checks grün sind.

## Teil B: Review in PR #M abarbeiten

1. `pull_request_read` mit `method: get`. Merke `head.ref`: das ist dein Branch.
2. `pull_request_read` mit `method: get_reviews` und `method: get_review_comments`. Liste alle
   **Muss**-Punkte auf.
3. Für jede betroffene Datei `get_file_contents` mit `ref:` dem Branch. Alle Muss-Punkte
   umsetzen und **alle** Dateien in **einem** `push_files` schreiben, jede vollständig.
4. Betrifft ein Muss-Punkt den PR-Text: `update_pull_request` mit dem vollständigen neuen Text
   nach der Vorlage.
5. „Kann“-Punkte, die du nicht umsetzt: bei Inline-Kommentaren mit
   `add_reply_to_pull_request_comment` kurz begründen.
6. Weiter mit A8, A9 und A10.

Nie einen Akzeptanztest ändern, nie `skip`/`xfail` einbauen, nie etwas unter `.github/`
ändern. Hältst du einen Muss-Punkt für falsch: nicht umsetzen, im PR begründen und Stephan
fragen.
