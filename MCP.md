# Arbeiten über MCP (ohne Shell)

Für Agenten, die GitHub nur über den MCP-Server von GitHub erreichen, z. B. Qwen in
LibreChat. Es gelten alle Regeln aus `AGENTS.md`, besonders der Abschnitt „Agenten ohne
Shell“. Hier steht, womit die `gh`-Befehle ersetzt werden und wie Stephan den Zugang
einrichtet.

## Befehle → MCP-Werkzeuge

Repo immer `owner: tripitest-art`, `repo: sc-digger`.

| In `AGENTS.md` | MCP-Werkzeug |
|---|---|
| Datei lesen | `get_file_contents` (`ref`: `main` oder dein Branch) |
| `gh issue view <N>` | `issue_read` (`method: get`, für Kommentare `get_comments`) |
| `gh issue list …` | `search_issues`, z. B. `repo:tripitest-art/sc-digger is:issue is:open label:worker-task label:bereit -label:blockiert`, sortiert nach `created` aufsteigend |
| `gh issue edit --add-label/--remove-label` | `issue_write` (`method: update`, `labels`: **vollständige** Liste) |
| `gh issue comment` | `add_issue_comment` |
| `git checkout -b <branch>` | `create_branch` (`from_branch: main`) |
| `git commit` + `git push` | `push_files` (mehrere Dateien, ein Commit) |
| `gh pr create` | `create_pull_request` (`base: main`, Body nach `.github/pull_request_template.md`, mit `Closes #<N>`) |
| `gh pr view --comments` | `pull_request_read` (`get`, `get_comments`, `get_review_comments`) |
| `gh pr diff` | `pull_request_read` (`get_diff`, bei großen PRs `get_files`) |
| `gh pr checks` | `pull_request_read` (`get_check_runs`) |
| `gh pr view --json reviews,commits` | `pull_request_read` (`get_reviews`, `get_commits`) |
| `gh pr review --comment` | `pull_request_review_write` (`method: create`, `event: COMMENT`, `body`) |
| `gh pr merge --squash` | `merge_pull_request` (`merge_method: squash`) |
| `gh pr list …` | `list_pull_requests` / `search_pull_requests` |
| CI-Log bei rotem Check | `get_job_logs` |

## Regeln, die nur über MCP gelten

- **`push_files` schreibt ganze Dateien.** Immer den vollständigen Inhalt schicken, vorher
  mit `get_file_contents` vom eigenen Branch lesen. Nie „…“ oder „Rest unverändert“: Was
  fehlt, ist danach gelöscht.
- **Akzeptanztest zeichengenau:** Den Block aus dem Issue-Text kopieren, nicht neu tippen oder
  umformatieren. Erster Commit auf dem Branch enthält nur `tests/acceptance/test_issue_<N>.py`.
- **Labels:** `issue_write` ersetzt die ganze Label-Liste (so gingen bei #62 Labels verloren).
  Erst mit `issue_read` alle Labels lesen, nur `bereit` durch `in-arbeit` ersetzen, die
  vollständige Liste zurückschreiben. Titel und Text nie mitschicken.
- **Keine Issues mit `berührt-main.py` übernehmen.** `sc_digger/main.py` ist zu groß, um sie
  ohne Shell sicher vollständig neu zu schreiben.
- **Tests:** Ohne Shell zählt der CI-Check `tests`. Fertig erst, wenn `tests` und
  `acceptance-guard` grün sind.
- **Kontext knapp?** Lieber aufhören und im Issue kommentieren als raten (Worker, Schritt 6).

## Einrichtung (Stephan)

Server: der gehostete MCP-Server von GitHub, `https://api.githubcopilot.com/mcp/`.

**Token:** fein granularer Personal Access Token, nur für `tripitest-art/sc-digger`:

| Recht | Stufe |
|---|---|
| Contents | Read and write |
| Issues | Read and write |
| Pull requests | Read and write |
| Actions | Read |
| Metadata | Read |
| Workflows, Administration | **nicht vergeben** |

Ohne „Workflows“ lehnt GitHub jede Änderung unter `.github/workflows/` ab. Den Token nur als
Umgebungsvariable in LibreChat ablegen, nie in eine Datei im Repo.

**Header:**

- `X-MCP-Lockdown: true` – blendet Inhalte von Nutzern ohne Schreibrecht aus. Das Repo ist
  öffentlich; so kann niemand Qwen per Kommentar Anweisungen unterschieben. Immer setzen.
- `X-MCP-Tools` – nur diese Werkzeuge gibt der Server heraus. Damit die Rolle begrenzen.
- `X-MCP-Readonly: true` – nur lesen (für reine Auswertungen).

**Zwei Profile** in `librechat.yaml` (Skizze, gegen die eigene LibreChat-Version prüfen):

```yaml
mcpServers:
  github-reviewer:
    type: streamable-http
    url: https://api.githubcopilot.com/mcp/
    headers:
      Authorization: "Bearer ${GITHUB_MCP_PAT}"
      X-MCP-Lockdown: "true"
      X-MCP-Tools: "get_file_contents,list_issues,search_issues,issue_read,list_pull_requests,search_pull_requests,pull_request_read,pull_request_review_write,get_job_logs"
  github-worker:
    type: streamable-http
    url: https://api.githubcopilot.com/mcp/
    headers:
      Authorization: "Bearer ${GITHUB_MCP_PAT}"
      X-MCP-Lockdown: "true"
      X-MCP-Tools: "get_file_contents,search_issues,issue_read,issue_write,add_issue_comment,create_branch,push_files,create_pull_request,pull_request_read,add_reply_to_pull_request_comment,get_job_logs"
```

Beide Profile haben bewusst kein `merge_pull_request`: Qwen merged nicht, das bleibt bei
Stephan. Der Reviewer hat kein `issue_write` und kann damit weder Labels noch Issue-Texte
ändern.

**Modell:** Kontextfenster mindestens 32k Token (bei Ollama `num_ctx`). Der Standardwert ist
kleiner und schneidet `AGENTS.md`, Issues und Diffs still ab.

**Agent-Anweisungen:** Inhalt von `QWEN.md` als Anweisungen des LibreChat-Agents eintragen.

**Probe:** „Lies `AGENTS.md` aus `tripitest-art/sc-digger` und nenne die Goldenen Regeln.“
Stimmt die Antwort, funktionieren Token, Header und Kontextlänge.

**Einstieg:** zuerst nur das Profil `github-reviewer`. Worker-Aufgaben erst, wenn die Reviews
brauchbar sind, und anfangs nur kleine Issues.
