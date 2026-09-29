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
| `gh pr edit` (PR-Text, Titel) | `update_pull_request` |
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
- **Keine Issues mit `berührt-main.py` übernehmen**, bis Worker-PRs über MCP ohne abgeschnittene
  Dateien durchgelaufen sind (Stephan gibt das frei). `sc_digger/main.py` ist der Engpass aus
  Regel 8; eine unvollständig zurückgeschriebene Datei dort trifft alle Modi.
- **Nach jedem `push_files` prüfen:** `pull_request_read` (`get_files`) zeigt, ob in einer Datei
  mehr gelöscht als geändert wurde. Dann ist sie abgeschnitten: sofort mit dem vollständigen
  Inhalt neu schreiben.
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

**Nur über `librechat.yaml`, nicht über die Oberfläche.** Der Dialog „MCP-Server hinzufügen“
in LibreChat kennt keine eigenen Header und sperrt `${…}`-Umgebungsvariablen. Ohne
`X-MCP-Tools` und `X-MCP-Lockdown` hat Qwen alle Werkzeuge (auch Merge) und liest fremde
Kommentare. Einen über die Oberfläche angelegten GitHub-Server dort wieder löschen.

**Zwei Profile** in `librechat.yaml` (so im Einsatz):

```yaml
mcpServers:
  github-reviewer:
    type: streamable-http
    url: https://api.githubcopilot.com/mcp/
    requiresOAuth: false
    headers:
      Authorization: "Bearer ${GITHUB_MCP_PAT}"
      X-MCP-Lockdown: "true"
      X-MCP-Tools: "get_file_contents,list_issues,search_issues,issue_read,list_pull_requests,search_pull_requests,pull_request_read,pull_request_review_write,get_job_logs"
  github-worker:
    type: streamable-http
    url: https://api.githubcopilot.com/mcp/
    requiresOAuth: false
    headers:
      Authorization: "Bearer ${GITHUB_MCP_PAT}"
      X-MCP-Lockdown: "true"
      X-MCP-Tools: "get_file_contents,search_issues,issue_read,issue_write,add_issue_comment,create_branch,push_files,create_or_update_file,create_pull_request,update_pull_request,pull_request_read,add_reply_to_pull_request_comment,get_job_logs"
  github-planner:
    type: streamable-http
    url: https://api.githubcopilot.com/mcp/
    requiresOAuth: false
    headers:
      Authorization: "Bearer ${GITHUB_MCP_PAT}"
      X-MCP-Lockdown: "true"
      X-MCP-Tools: "get_file_contents,list_issues,search_issues,issue_read,issue_write,add_issue_comment,list_pull_requests,search_pull_requests,pull_request_read"
```

Der Planer kann Issues lesen und anlegen, aber keine Branches, Dateien oder PRs schreiben.

- `requiresOAuth: false` ist Pflicht. LibreChat prüft beim Start ohne die Header, ob ein
  Server OAuth braucht. GitHub antwortet dann mit `401`, LibreChat hält den Server für
  OAuth-geschützt („OAuth Required: true“, „Access token missing“) und lädt keine Werkzeuge.
- Kein Profil hat `merge_pull_request`: Lokale Modelle mergen nicht, das bleibt bei Stephan.
  Der Reviewer hat kein `issue_write` und kann weder Labels noch Issue-Texte ändern.

**Token und Neustart**, je nach Installation:

| | Docker | ohne Docker (systemd, z. B. Proxmox-LXC) |
|---|---|---|
| Ordner | LibreChat-Ordner mit `docker-compose.yml` | `/opt/librechat` |
| Token | `GITHUB_MCP_PAT=…` in `.env` | `GITHUB_MCP_PAT=…` in `.env` (per `EnvironmentFile=` geladen) |
| `librechat.yaml` | in `docker-compose.override.yml` einbinden (`./librechat.yaml` → `/app/librechat.yaml`) | wird direkt gelesen |
| Neustart | `docker compose up -d --force-recreate api` | `systemctl restart librechat` |

Den Token nie in der Kommandozeile tippen (Shell-History), sondern z. B. mit `read -rs` einlesen.

**Prüfen im Log** (`journalctl -u librechat` bzw. `docker compose logs api`): Pro Server
`OAuth Required: false` und unter `Tools:` genau die Werkzeuge aus `X-MCP-Tools`. Beim Teilen
von Logs Zeilen mit `Authorization` und geheimen URL-Pfaden vorher herausfiltern.

**Agenten:** getrennte LibreChat-Agenten je Rolle: Reviewer nur mit `github-reviewer`, Worker
nur mit `github-worker`, Planer nur mit `github-planner`. Keine anderen MCP-Server (Proxmox,
Home Assistant) im selben Agenten: Er liest Texte aus GitHub; eine untergeschobene Anweisung
hätte sonst Zugriff auf diese Systeme. Instructions: Inhalt von `QWEN.md`.

**Planer ohne Shell:** Er kann seine Akzeptanztests nicht ausführen. Deshalb legt er Issues mit
dem Label `entwurf` statt `bereit` an. Worker nehmen nur `bereit`-Issues, ein Entwurf bleibt
also liegen, bis Stephan oder ein Agent mit Shell die Tests gegen `main` laufen lässt (rot wie
erwartet, ohne Syntaxfehler) und `entwurf` durch `bereit` ersetzt.

**Skills:** Die Abläufe liegen als LibreChat-Skills im Repo (`skills/sc-digger-review/`,
`skills/sc-digger-worker/`, `skills/sc-digger-planner/`). Sie legen die Abfolge der Werkzeugaufrufe fest; die Regeln
bleiben in `AGENTS.md`. LibreChat spiegelt sie per GitHub Skill Sync aus `main`, damit es
keine Kopie gibt, die von Hand gepflegt werden muss (Regel 1). In `librechat.yaml`:

```yaml
skillSync:
  github:
    enabled: true
    intervalMinutes: 60
    runOnStartup: true
    sources:
      - id: sc-digger
        owner: tripitest-art
        repo: sc-digger
        ref: main
        paths:
          - skills
        skillDiscoveryDepth: 2
        token: '${GITHUB_MCP_PAT}'
```

- Der Token wird nur vom LibreChat-Server zum Lesen benutzt, nie vom Modell. Wer es strenger
  will, legt einen eigenen Token nur mit „Contents: Read“ an (`GITHUB_SKILLS_TOKEN`).
- In der Skills-Seitenleiste bei jedem Skill „Available to agent“ einschalten.
- Im Agenten-Editor „Enable skills“ an, „Use all skills“ aus und genau den Skill der Rolle
  auswählen: `sc-digger-review`, `sc-digger-worker` oder `sc-digger-planner`. Alle sind
  `always-apply` und stehen damit in jedem Zug vollständig im Kontext. Beim Kopieren eines
  Agenten die Skill-Auswahl prüfen.
- Ob der Sync lief, zeigt das Log nur bei Fehlern (`[GitHubSkillSync] … failed`); ohne Fehler
  die Seitenleiste „Skills“ prüfen.
- Änderungen an einem Skill laufen wie Code über PR und Review. Nach dem Merge übernimmt
  LibreChat sie beim nächsten Sync (spätestens nach 60 Minuten oder beim Neustart).

**Modell:**

- Kontextfenster mindestens 32k Token. Maßgeblich ist der kleinste Wert aus Backend
  (Ollama `num_ctx`, llama.cpp `-c`, vLLM `--max-model-len`) und „Max Context Tokens“ in
  LibreChat. Darüber wird still abgeschnitten.
- **Maximale Antwortlänge mindestens 16k Token** (LibreChat „Max Output Tokens“ und Backend,
  z. B. Ollama `num_predict`). `push_files` schreibt ganze Dateien; die größten Module haben
  rund 9k Token. Ist die Grenze kleiner, endet die Datei mitten im Code und der Rest ist weg.
- **Agenten ohne Denkphase.** Qwen 3.5 schreibt Werkzeugaufrufe sonst oft nur in seine
  Gedanken; der Zug endet dann ohne Aktion. Für die Agenten einen Ollama-Endpunkt mit
  `addParams: { reasoning_effort: "none" }` wählen (Ollamas OpenAI-Schnittstelle schaltet damit
  das Denken ab).
- **Schrittgrenze anheben:** in `librechat.yaml` unter `endpoints:` → `agents:`
  `recursionLimit: 50` und `maxRecursionLimit: 100`. Standard sind 25 Schritte, ein Worker-Lauf
  braucht mehr. (Der Abschnitt `interface: agents:` regelt nur die Berechtigung.)
- **Jeder Auftrag in einem neuen Chat.** Der Verlauf früherer Aufträge füllt sonst den Kontext.
- **Sampling der Agenten:** `temperature` 0.6, `top_p` 0.95, `presence_penalty` 0 (im
  Agenten-Editor unter den Modell-Parametern). Eine `presence_penalty` wie im Chat-Profil (1.5)
  bestraft Wiederholungen; Code wiederholt aber ständig Namen und Einrückungen, und Qwen weicht
  dann auf falsche Varianten aus.

**Probe:** Agent fragen „Welche GitHub-Werkzeuge hast du? Nur die Namen.“ Es müssen genau die
aus `X-MCP-Tools` sein. Dann: „Lies `AGENTS.md` aus `tripitest-art/sc-digger` und nenne die
Goldenen Regeln.“

**Einstieg:** zuerst Reviews. Worker-Aufgaben anfangs nur kleine Issues ohne `berührt-main.py`;
nach jedem Worker-PR den Diff auf abgeschnittene Dateien und die Labels des Issues prüfen.
