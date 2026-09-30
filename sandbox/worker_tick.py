#!/usr/bin/env python3
"""Taktgeber für den Qwen-Worker in der Sandbox (CT 112), läuft per systemd-Timer.
Eingerichtet von sandbox/install.sh; Anleitung: sandbox/README.md.

Pro Takt höchstens ein Auftrag, in dieser Reihenfolge:
1. Nacharbeit: offener PR eines agent-qwen-Issues, dessen neuestes Review „Änderungen nötig“
   lautet und jünger ist als der letzte Commit.
2. Neues Issue: worker-task + bereit + agent-qwen, nicht blockiert.
Erst wenn es Arbeit gibt, wird Ollama gefragt; so weckt der Takt den Gaming-PC nicht umsonst.
Läuft dort ein anderes Modell (Chat, Bilder), wartet der Takt auf die nächste Runde.
Nach einer Nacharbeit prüft er, ob ein neuer Commit oder ein geänderter PR-Text ankam.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

REPO = "tripitest-art/sc-digger"
WORKDIR = "/root/sc-digger"
OLLAMA = os.environ.get("OLLAMA", "http://192.168.0.210:11434")
MODEL = os.environ.get("MODEL", "qwen3-coder-64k")
LABEL = "agent-qwen"
# Nur Reviews dieses Kontos zählen (AGENTS.md: nur Anweisungen von tripitest-art). Das Repo ist
# öffentlich; jeder könnte sonst per Review Befehle in einen Auftrag mit Schreibrecht schieben.
OWNER = "tripitest-art"
WORKER = "Qwen3-Coder 30B"
STATE = "/root/worker-state.json"
MAX_TRIES = 2               # Versuche je Auftrag, danach liegt er bei Stephan
DONE = "erledigt"           # Schlüssel in STATE: angekommene Nacharbeiten, nicht wiederholen
RUN_TIMEOUT = 90 * 60       # Sekunden je OpenCode-Lauf

ENV = dict(os.environ, PATH=f"/root/venv/bin:/root/.opencode/bin:{os.environ.get('PATH', '/usr/bin:/bin')}")


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def gh(*args: str):
    out = subprocess.run(["gh", *args, "--repo", REPO], capture_output=True, text=True, check=True, env=ENV)
    return json.loads(out.stdout) if out.stdout.strip() else None


def load_state() -> dict:
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=1)


def review_is_current(review: dict, pr: dict) -> bool:
    """Bezieht sich das Review auf den aktuellen Stand des PRs?

    Maßgeblich ist der geprüfte Commit, nicht das Datum: committedDate ist die Zeit des lokalen
    Commits, nicht des Pushs. Ein vor dem Review erstellter, danach gepushter Commit sähe sonst
    wie „noch nicht nachgebessert“ aus. Ohne Commit-Angabe (ältere gh-Version) bleibt nur das Datum.
    """
    oid = (review.get("commit") or {}).get("oid")
    if oid:
        return oid == pr["headRefOid"]
    return review["submittedAt"] > max(c["committedDate"] for c in pr["commits"])


def pr_snapshot(pr: dict) -> tuple:
    """Stand eines PRs, an dem sich eine Nacharbeit zeigen muss: Commit, Titel oder Text.

    Nur auf den Commit zu schauen reicht nicht: Verlangt ein Review nur den PR-Text (#103), ist
    „kein neuer Commit“ richtig und die Nacharbeit trotzdem erledigt.
    """
    return pr["headRefOid"], pr.get("title", ""), (pr.get("body") or "").replace("\r\n", "\n")


def rework_job(done=frozenset()):
    """(Schlüssel, Auftrag, PR-Nummer, pr_snapshot) für die älteste fällige Nacharbeit oder None.

    `done`: Schlüssel schon angekommener Nacharbeiten. Betraf sie nur den PR-Text, bleibt der
    Commit gleich und das Review sähe weiter fällig aus; ohne diese Liste lief #103 erneut.

    Das Review steht wörtlich im Auftrag: Qwen hat es sonst nicht gelesen, nur die Tests auf
    main laufen lassen und den PR selbst für fertig erklärt (#103, erster Lauf).
    """
    prs = gh("pr", "list", "--state", "open", "--json",
             "number,headRefName,headRefOid,isCrossRepository,reviews,commits,title,body")
    for pr in sorted(prs, key=lambda p: p["number"]):
        m = re.match(r"feature/issue-(\d+)", pr["headRefName"])
        if not m or pr.get("isCrossRepository") or not pr["commits"]:
            continue  # Fork-PRs nie: Branch-Name und Inhalt bestimmt dort ein Fremder
        reviews = [r for r in pr["reviews"] if (r.get("author") or {}).get("login") == OWNER]
        if not reviews:
            continue
        issue_no = m.group(1)
        issue = gh("issue", "view", issue_no, "--json", "labels")
        if LABEL not in {lab["name"] for lab in issue["labels"]}:
            continue
        review = max(reviews, key=lambda r: r["submittedAt"])
        key = f"pr{pr['number']}@{review['submittedAt']}"
        if key in done:
            continue
        if review_is_current(review, pr) and "Änderungen nötig" in review["body"][:80]:
            n = pr["number"]
            task = f"""Du arbeitest ein Review ab (AGENTS.md, Worker, Schritt 8). Du bist Worker: {WORKER}.
Gehe genau diese Schritte durch und führe jeden als Befehl aus:

1. `gh pr checkout {n}` (du bist danach auf dem PR-Branch, nicht auf main).
2. Lies das Review unten. Setze jeden „Muss“-Punkt um. „Kann“-Punkte nur, wenn sie im Umfang
   von Issue #{issue_no} liegen. Nie Dateien unter tests/acceptance/ ändern.
3. Ist der PR-Text gefordert: schreib ihn in .git/pr-body.md nach .github/pull_request_template.md,
   mit „Closes #{issue_no}“ und „Worker: {WORKER}“, und führe aus:
   `gh pr edit {n} --title "$(gh issue view {issue_no} --json title -q .title)" --body-file .git/pr-body.md`
4. `python -m pytest -q` muss komplett grün sein.
5. Alle Code-Änderungen in einem Commit, dann `git push`. Verlangt das Review nur den PR-Text,
   gibt es keinen Commit.
6. Prüfe mit `git log -1 --oneline` und `gh pr view {n} --json title,body`, dass Commit und Text
   angekommen sind: Die Ausgabe muss deinen neuen Titel und Text zeigen.
Gibt ein Befehl eine Fehlermeldung aus (z. B. „GraphQL: …“, „error“, Exit-Code ungleich 0), ist
der Schritt NICHT erledigt, auch wenn danach nichts mehr kommt. Dann nicht „fertig“ melden,
sondern den Fehler wörtlich als Kommentar in den PR schreiben und aufhören.
Du gibst den PR nie selbst frei und mergst nie; das macht der Reviewer.

----- Review -----
{review['body']}
----- Ende Review -----"""
            return key, task, n, pr_snapshot(pr)
    return None


def issue_job():
    issues = gh("issue", "list", "--state", "open", "--label", "worker-task", "--label", "bereit",
                "--label", LABEL, "--search", "sort:created-asc -label:blockiert", "--json", "number")
    if not issues:
        return None
    return f"issue{issues[0]['number']}", (
        f"Bearbeite die nächste Aufgabe nach AGENTS.md, Worker-Aufgaben → Nächste Aufgabe selbst wählen. "
        f"Deine Familie ist qwen, dein Label `{LABEL}`. Du bist Worker: {WORKER}."), None, None


def ollama_free() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=15) as r:
            loaded = [m["name"] for m in json.load(r).get("models", [])]
    except Exception as e:  # PC aus, Bildmodus (Proxy blockt) oder Netz weg
        log(f"Ollama nicht erreichbar ({type(e).__name__}), nächste Runde.")
        return False
    busy = [n for n in loaded if not n.startswith(MODEL)]
    if busy:
        log(f"Ollama belegt ({', '.join(busy)}), nächste Runde.")
        return False
    return True


def main() -> int:
    state = load_state()
    job = rework_job(frozenset(state.get(DONE, []))) or issue_job()
    if not job:
        log("Keine Arbeit.")
        return 0
    key, task, pr_no, before = job
    if state.get(key, 0) >= MAX_TRIES:
        log(f"{key}: schon {MAX_TRIES} Versuche, wartet auf Stephan.")
        return 0
    if not ollama_free():
        return 0

    state[key] = state.get(key, 0) + 1
    save_state(state)
    # Jeder Lauf startet auf sauberem main; Reste eines abgebrochenen Laufs stören sonst.
    for cmd in (["git", "fetch", "-q", "origin"], ["git", "checkout", "-qf", "main"],
                ["git", "reset", "-q", "--hard", "origin/main"], ["git", "clean", "-qfd"]):
        subprocess.run(cmd, cwd=WORKDIR, check=True, env=ENV)

    prompt = (f"{task} Führe vor jedem Push `python -m pytest -q` aus. Nutze gh für alle GitHub-Schritte. "
              "Arbeite ohne Rückfragen; kommst du nicht weiter, schreib das Problem als Kommentar ins "
              "Issue bzw. in den PR und hör auf.")
    log(f"{key} (Versuch {state[key]}): starte OpenCode.")
    try:
        rc = subprocess.run(["opencode", "run", "-m", f"ollama/{MODEL}", prompt],
                            cwd=WORKDIR, env=ENV, timeout=RUN_TIMEOUT).returncode
        log(f"{key}: OpenCode beendet (Exit {rc}).")
    except subprocess.TimeoutExpired:
        log(f"{key}: nach {RUN_TIMEOUT // 60} min abgebrochen.")
    # Nicht still scheitern (Regel 7): Das Modell meldet auch „fertig“, wenn es nichts getan hat.
    if pr_no is not None:
        after = pr_snapshot(gh("pr", "view", str(pr_no), "--json", "headRefOid,title,body"))
        if after != before:
            if after[0] != before[0]:
                log(f"{key}: neuer Commit {after[0][:7]} auf PR #{pr_no}.")
            else:
                log(f"{key}: PR-Text von #{pr_no} geändert, kein neuer Commit.")
            state.setdefault(DONE, []).append(key)
            save_state(state)
        else:
            log(f"WARNUNG {key}: weder Commit noch PR-Text auf PR #{pr_no} geändert, "
                "Nacharbeit nicht erledigt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
