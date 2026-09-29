---
name: sc-digger-planner
description: "Planer-Aufgabe in tripitest-art/sc-digger über die GitHub-MCP-Werkzeuge. Nutzen bei „Plane …“ oder „Erstelle ein Issue für …“. Feste Abfolge vom Auftrag bis zum angelegten worker-task-Issue."
always-apply: true
compatibility: Braucht den MCP-Server github-planner (siehe MCP.md). Für Qwen in LibreChat.
---

# Planer-Aufgabe in sc-digger

Die Regeln stehen in `AGENTS.md` (Abschnitt „Planer“) und `ENTWICKLUNG.md` (Abschnitt
„Planer-Ablauf“). Dieser Skill legt nur fest, **wie** du sie mit den MCP-Werkzeugen
abarbeitest. Repo immer `owner: tripitest-art`, `repo: sc-digger`. Suche nie nach anderen
Repositories.

Du schreibst ein Issue, keinen Code: kein `create_branch`, kein `push_files`, kein PR.

Führe jeden Schritt als Werkzeugaufruf aus. Kündige ihn nicht nur an. Ein Schritt ist erst
erledigt, wenn das Werkzeug ein Ergebnis geliefert hat.

## Schritt 1: Regeln lesen

`get_file_contents` (`ref: main`) für `AGENTS.md` und `ENTWICKLUNG.md`. Lies „Goldene Regeln“,
„Planer“, „Architektur“ und „Planer-Ablauf“.

## Schritt 2: Auftrag klären

Fasse den Auftrag von Stephan in zwei Sätzen zusammen (Ziel und Grenze). Ist etwas fachlich
offen (Schwellwert, Verhalten im Fehlerfall, welcher Modus), frag Stephan und warte. Nicht
raten. Ist der Auftrag größer als ein PR, schlag Teile vor und plane nur Teil 1.

## Schritt 3: Stand prüfen

1. `search_issues` mit
   `repo:tripitest-art/sc-digger is:issue is:open label:worker-task`: Gibt es das schon?
   Dann aufhören und Stephan die Nummer nennen.
2. `search_pull_requests` mit `repo:tripitest-art/sc-digger is:pr is:open`. Für jeden offenen
   PR, der dieselben Dateien berühren könnte: `pull_request_read` mit `method: get_files`.
3. Berührt ein offenes Issue mit `in-arbeit` oder ein offener PR eine deiner Dateien (oder baut
   deine Aufgabe darauf auf): Dein Issue bekommt `blockiert` statt `bereit` und im Kontext die
   Zeile `Wartet auf: #X, #Y`.

## Schritt 4: Code lesen

`get_file_contents` (`ref: main`) für jede Datei, die du ändern lassen willst, und für
`tests/test_modes.py` (Fakes `mk`, `FakeSC`). Findest du eine Funktion nicht: `search_code`
mit `repo:tripitest-art/sc-digger <Name>`. Übernimm Namen, Typen und Log-Stil aus dem
vorhandenen Code. Nie eine Signatur erfinden, die es schon anders gibt.

Als Vorbild für Aufbau und Genauigkeit: `issue_read` mit `method: get`, `issue_number: 95`.

## Schritt 5: Issue-Text schreiben

Genau diese Überschriften, in dieser Reihenfolge (so erzeugt sie das Formular, und
`acceptance-guard` liest danach):

````
### Ziel

<ein, zwei Sätze>

### Betroffene Dateien

- `sc_digger/<datei>.py`: <Funktion, was sich ändert>
- `tests/acceptance/test_issue_<N>.py`: Akzeptanztests unten, zeichengenau
- `tests/test_<thema>.py` (neu): eigene Tests

### Schnittstellen

```python
def name(arg: Typ) -> Rückgabe:
    """Verhalten, auch bei leer/None/Fehler. Wirft nicht / wirft X."""
```

### Nicht Teil dieser Aufgabe

- …

### Akzeptanztests

```python
<pytest-Code>
```

### Fertig, wenn

- [ ] Akzeptanztests grün
- [ ] Weitere Tests decken das neue Verhalten ab (Netzwerk nur über Fakes), u. a.: …
- [ ] `python -m pytest -q` komplett grün

### Berührt sc_digger/main.py

nein

### Merge-Modus

automatisch nach Review

### Kontext

- …
- Akzeptanztests ungeprüft (Planer ohne Shell).
````

- `test_issue_<N>` so stehen lassen, die Nummer vergibt GitHub.
- „Berührt sc_digger/main.py“: `nein` oder `ja`.
- „Merge-Modus“: `automatisch nach Review` oder
  `manuell durch Stephan (Betrieb, Dateien, Zugangsdaten, Downloads)`.

**Akzeptanztests** (genau ein ```` ```python ````-Block):
- Nur Funktionen aus „Schnittstellen“ aufrufen, keine Interna.
- Kein Netzwerk, keine Uhrzeit, kein Zufall, kein `skip`/`xfail`.
- Imports nur aus `sc_digger` und vorhandenen Test-Helfern (z. B. `from tests.test_modes import mk`).
- Dateien nur in `tmp_path`; nie `/music` oder die echte `config.yaml` beschreiben.
- Jeder Test hat ein `assert`, das echtes Verhalten prüft.
- Ohne Umsetzung müssen sie rot sein. Prüfe das im Kopf: Importiert der Test etwas, das es
  auf `main` noch nicht gibt, oder prüft er ein Verhalten, das es noch nicht gibt?

## Schritt 6: Entwurf vorlegen

Zeig Stephan im Chat Titel, vollständigen Text und Labels. Warte auf sein OK. Änderungswünsche
einarbeiten und erneut vorlegen. Erst nach „OK“ weiter (oder wenn er vorher ausdrücklich
„direkt anlegen“ gesagt hat).

## Schritt 7: Issue anlegen

`issue_write` mit `method: create`, `title`, `body` (der Text aus Schritt 5) und `labels`:
- `worker-task`
- `phase-N` (aus `ROADMAP.md` oder von Stephan; nicht raten)
- `blockiert`, wenn Schritt 3 das ergab. **Nie `bereit`:** Du kannst die Akzeptanztests nicht
  ausführen. Stephan oder ein Planer mit Shell prüft sie und setzt dann `bereit`.
- `berührt-main.py`, wenn „Berührt sc_digger/main.py“ `ja` ist.

## Schritt 8: Fertig melden

`issue_read` mit `method: get` auf die neue Nummer: Stehen Text und Labels richtig da? Dann
Stephan in einem Satz: „Issue #N angelegt, Akzeptanztests ungeprüft, `bereit` fehlt noch.“

## Nachträglich ändern

Nur solange das Issue **nicht** `in-arbeit` trägt: `issue_write` mit `method: update` und
`body` (vollständiger Text). Labels nur mitschicken, wenn sie sich ändern sollen, dann als
vollständige Liste. Trägt es `in-arbeit`: nichts ändern, sondern `add_issue_comment` und
Stephan Bescheid geben.
