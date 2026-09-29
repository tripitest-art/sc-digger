#!/usr/bin/env python3
"""Taktgeber für den Qwen-Worker in der Sandbox (CT 112), läuft per systemd-Timer.
Eingerichtet von sandbox/install.sh; Anleitung: sandbox/README.md.

Pro Takt höchstens ein Auftrag, in dieser Reihenfolge:
1. Nacharbeit: offener PR eines agent-qwen-Issues, dessen neuestes Review „Änderungen nötig“
   lautet und jünger ist als der letzte Commit.
2. Neues Issue: worker-task + bereit + agent-qwen, nicht blockiert.
Erst wenn es Arbeit gibt, wird Ollama gefragt; so weckt der Takt den Gaming-PC nicht umsonst.
Läuft dort ein anderes Modell (Chat, Bilder), wartet der Takt auf die nächste Runde.
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
WORKER = "Qwen3-Coder 30B"
STATE = "/root/worker-state.json"
MAX_TRIES = 2               # Versuche je Auftrag, danach liegt er bei Stephan
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


def rework_job():
    """(Schlüssel, Auftrag, PR-Nummer, Head-SHA) für die älteste fällige Nacharbeit oder None.

    Das Review steht wörtlich im Auftrag: Qwen hat es sonst nicht gelesen, nur die Tests auf
    main laufen lassen und den PR selbst für fertig erklärt (#103, erster Lauf).
    """
    prs = gh("pr", "list", "--state", "open", "--json", "number,headRefName,headRefOid,reviews,commits")
    for pr in sorted(prs, key=lambda p: p["number"]):
        m = re.match(r"feature/issue-(\d+)", pr["headRefName"])
        if not m or not pr["reviews"] or not pr["commits"]:
            continue
        issue_no = m.group(1)
        issue = gh("issue", "view", issue_no, "--json", "labels,title")
        if LABEL not in {lab["name"] for lab in issue["labels"]}:
            continue
        review = max(pr["reviews"], key=lambda r: r["submittedAt"])
        last_commit = max(c["committedDate"] for c in pr["commits"])
        if review["submittedAt"] > last_commit and "Änderungen nötig" in review["body"][:80]:
            n = pr["number"]
            key = f"pr{n}@{review['submittedAt']}"
            task = f"""Du arbeitest ein Review ab (AGENTS.md, Worker, Schritt 8). Du bist Worker: {WORKER}.
Gehe genau diese Schritte durch und führe jeden als Befehl aus:

1. `gh pr checkout {n}` (du bist danach auf dem PR-Branch, nicht auf main).
2. Lies das Review unten. Setze jeden „Muss“-Punkt um. „Kann“-Punkte nur, wenn sie im Umfang
   von Issue #{issue_no} liegen. Nie Dateien unter tests/acceptance/ ändern.
3. Ist der PR-Text gefordert: schreib ihn in .git/pr-body.md nach .github/pull_request_template.md,
   mit „Closes #{issue_no}“ und „Worker: {WORKER}“, und führe aus:
   `gh pr edit {n} --title "{issue['title']}" --body-file .git/pr-body.md`
4. `python -m pytest -q` muss komplett grün sein.
5. Alle Code-Änderungen in einem Commit, dann `git push`.
6. Prüfe mit `git log -1 --oneline` und `gh pr view {n}`, dass Commit und Text angekommen sind.
Du gibst den PR nie selbst frei und mergst nie; das macht der Reviewer.

----- Review -----
{review['body']}
----- Ende Review -----"""
            return key, task, n, pr["headRefOid"]
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
    job = rework_job() or issue_job()
    if not job:
        log("Keine Arbeit.")
        return 0
    key, task, pr_no, old_sha = job
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
        new_sha = gh("pr", "view", str(pr_no), "--json", "headRefOid")["headRefOid"]
        if new_sha == old_sha:
            log(f"WARNUNG {key}: kein neuer Commit auf PR #{pr_no}, Nacharbeit nicht erledigt.")
        else:
            log(f"{key}: neuer Commit {new_sha[:7]} auf PR #{pr_no}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
