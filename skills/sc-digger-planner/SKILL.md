---
name: sc-digger-planner
description: "Worker-Issue für ein Projekt von tripitest-art planen und anlegen (Planer-Rolle), z. B. sc-digger oder agent-sandbox-infra. Nutzen bei „Plane …“, „Erstelle ein Issue für …“ oder „Mach daraus eine Aufgabe“. Erst Entwurf im Chat, nach Stephans OK Issue mit Label entwurf."
always-apply: true
compatibility: Braucht den MCP-Server github-planner (siehe MCP.md). Für lokale Modelle in LibreChat.
---

# Worker-Issue planen (alle Projekte)

Die Regeln stehen in `AGENTS.md` (Abschnitt „Planer“). Dieser Skill legt nur fest, **wie** du
sie mit den MCP-Werkzeugen abarbeitest. Der Skill gilt für jedes Projekt mit Worker-Vertrag; was
projektspezifisch ist, steht in der `AGENTS.md` des Zielrepos und geht vor. Du schreibst nur in
**ein** Repo, das Zielrepo (`owner: tripitest-art`). Suche nie nach anderen Repositories.

Führe jeden Schritt als Werkzeugaufruf aus. Kündige ihn nicht nur an. Ein Schritt ist erst
erledigt, wenn das Werkzeug ein Ergebnis geliefert hat.

Du hast keine Shell und kannst Tests nicht ausführen. Deshalb legst du Issues **immer** mit
dem Label `entwurf` an, nie mit `bereit`. Stephan oder ein Agent mit Shell prüft die
Akzeptanztests und gibt das Issue danach frei.

## Schritt 0: Zielrepo bestimmen

Stephan nennt das Projekt im Auftrag (`sc-digger`, `agent-sandbox-infra`, …). Nennt er keins,
frag einmal nach. Danach `get_file_contents` (`ref: main`) für `AGENTS.md` im Zielrepo. Fehlt die
Datei oder hat sie keinen Abschnitt „Worker-Aufgaben“, hör auf und sag Stephan, dass dieses Repo
den Vertrag nicht hat. Weise ihn ausserdem darauf hin, dass der Worker nur Repos aus der
Projektliste des Servers (`/etc/agent-projects.json`) bearbeitet; ein neues Repo muss dort erst
eingetragen werden.

## Schritt 1: Regeln und Architektur lesen

`get_file_contents` (`ref: main`), alles im Zielrepo: `AGENTS.md`, `ENTWICKLUNG.md` (falls
vorhanden) und `.github/ISSUE_TEMPLATE/worker-task.yml` (das Formular bestimmt die Abschnitte des
Issues). Verweist die `AGENTS.md` auf eine Datei in einem anderen Repo (das Infra-Repo verweist für
den Planer-Ablauf und die Agenten-Labels auf `ENTWICKLUNG.md` in sc-digger), darfst du genau diese
Datei lesen. Geschrieben wird nur im Zielrepo.

## Schritt 2: Auftrag klären

Stell Stephan Rückfragen, bis diese Punkte klar sind:
- das Ziel in ein, zwei Sätzen
- was ausdrücklich **nicht** dazugehört
- der Merge-Modus: „automatisch nach Review“ oder „manuell durch Stephan“. Manuell ist Pflicht
  bei Betrieb, Dateien auf dem Server, Zugangsdaten und Downloads.

Eine Aufgabe = ein Thema. Ist der Auftrag zu groß, schlag eine Aufteilung in mehrere Issues vor.

## Schritt 3: Code lesen

1. Welche Dateien betroffen sind, steht in der Architektur-Tabelle in `ENTWICKLUNG.md` bzw. im
   Abschnitt „Worum es geht“ der `AGENTS.md`.
2. `get_file_contents` für jede betroffene Datei (`ref: main`). Lies die Funktionen, die sich
   ändern, und ihre Aufrufer.
3. Für die Akzeptanztests: `get_file_contents` für einen passenden bestehenden Test im Zielrepo und
   übernimm dessen Muster, statt eigene Fakes zu erfinden. In sc-digger stehen `FakeSC` in
   `tests/test_modes.py`, `FakeLinkSC` in `tests/test_merge.py`, synthetisches Audio in
   `tests/test_analysis_organize.py`. Im Infra-Repo werden die Takt-Skripte wie in
   `tests/test_worker_tick.py` per Pfad geladen; `gh`, Uhr und Netz sind dort gefaked.
4. Bestehende Akzeptanztests, die die geänderten Funktionen prüfen (`search_code` nach dem
   Funktionsnamen in `tests/acceptance`): Der Worker darf sie nicht ändern, die Aufgabe muss sie
   grün lassen.

## Schritt 4: Konflikte prüfen (Regel 8)

1. `list_pull_requests` (`state: open`) und für jeden PR `pull_request_read` mit
   `method: get_files`.
2. `search_issues` mit `repo:tripitest-art/<Zielrepo> is:issue is:open label:in-arbeit`.

Ändert ein offener PR oder ein Issue in Arbeit dieselben Dateien, oder baut die Aufgabe darauf
auf: Label `blockiert` zusätzlich setzen und im Kontext `Wartet auf: #X` eintragen. `#X` gilt nur
im selben Repo. Hängt die Aufgabe an einem anderen Repo, schreib das in den Kontext, sag es
Stephan und setz dafür kein `blockiert`. Berührt die Aufgabe den Hotspot des Projekts
(sc-digger: `sc_digger/main.py`, Label `berührt-main.py`), das Label zusätzlich setzen; Repos ohne
Hotspot-Frage im Formular haben es nicht.

## Schritt 5: Entwurf im Chat

Schreib den vollständigen Issue-Text nach der Vorlage unten **in den Chat** und frag Stephan:
„Soll ich das Issue so anlegen?“ Erst nach seinem OK weiter mit Schritt 6.

Hast du eine Shell, prüf den Entwurf vorher mit `python -m sc_digger.issuecheck <issue.md>`
(nur in sc-digger vorhanden; Form und Rot-Stand der Akzeptanztests) und schreib das Ergebnis in den Abschnitt „Kontext“.

Regeln für die Akzeptanztests:
- pytest, ohne Netzwerk (Fakes), keine `skip`/`xfail`.
- Sie importieren nur die Schnittstellen aus „Schnittstellen“ und prüfen deren Verhalten.
- Vor der Umsetzung müssen sie rot sein (die neue Funktion fehlt oder verhält sich anders).
  Schreib dazu, **warum** sie heute rot sind.
- Jeder Test prüft mit `assert` ein konkretes Ergebnis, nicht nur „kein Fehler“.

## Schritt 6: Issue anlegen

`issue_write` mit `method: create`, `title:` kurz und sachlich, `body:` der Text aus Schritt 5
und `labels: ["worker-task", "entwurf", "phase-N", "agent-<familie>"]` (Tabelle „Agenten-Labels“ in
`ENTWICKLUNG.md` in sc-digger), dazu bei Bedarf `berührt-main.py` und
`blockiert`.

Danach Stephan melden: „Issue #N angelegt (Label `entwurf`). Bitte die Akzeptanztests
prüfen lassen und dann `entwurf` durch `bereit` ersetzen.“

## Vorlage für den Issue-Text

Die Überschriften genau so, denn `acceptance-guard` sucht die Überschrift „Akzeptanztests“. Der
Aufbau folgt dem Formular des Zielrepos; Abschnitte, die es dort nicht gibt (z. B. „Berührt
sc_digger/main.py“ im Infra-Repo), lässt du weg. Der Merge-Modus im Infra-Repo ist immer
„manuell durch Stephan“ (`AGENTS.md`, Regel 4):

````
### Ziel

<ein, zwei Sätze>

### Betroffene Dateien

- `<pfad/zur/datei>.py` (<Funktion>)
- `tests/acceptance/test_issue_<N>.py`: Akzeptanztests unten, zeichengenau
- `tests/test_<thema>.py` (neu): eigene Tests

### Schnittstellen

```python
def <name>(<parameter mit Typen>) -> <Rückgabetyp>:
    """<Verhalten, auch im Fehlerfall>"""
```

### Nicht Teil dieser Aufgabe

- <…>

### Akzeptanztests

```python
<pytest-Code>
```

### Fertig, wenn

- [ ] Akzeptanztests grün
- [ ] Weitere Tests decken das neue Verhalten ab (Netzwerk nur über Fakes), u. a.: <…>
- [ ] `python -m pytest -q` komplett grün

### Berührt sc_digger/main.py

nein

### Merge-Modus

automatisch nach Review

### Kontext

- <Anlass, Beobachtung>
- Regel 8: <welche offenen PRs/Issues geprüft, Ergebnis>
- Akzeptanztests noch nicht ausgeführt (Planer ohne Shell). Erwartung: rot, weil <…>.
````

`<N>` im Dateinamen bleibt so stehen; die Nummer kennt erst GitHub.
