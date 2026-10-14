"""Auto-Deploy: neuen Stand von ``main`` ausrollen, Healthcheck prüfen, sonst zurückrollen.

Läuft als systemd-Timer auf dem Host (``deploy/sc-digger-deploy.timer``), NICHT im
Container: Von dort kommt ``./update.sh`` gar nicht an Docker heran. Der Host hat keine
Python-Abhängigkeiten installiert, deshalb importiert dieses Modul auf Modulebene nur die
Standardbibliothek; alles Container-nahe (Telegram) läuft über ``docker compose exec``.

Warum das überhaupt vertretbar ist: ``./update.sh`` war der letzte Schritt des Workflows
(Planer -> Worker -> CI -> Review -> Merge -> Deploy), der von Hand lief; ein gemergter PR
lag so Stunden bis Tage ungenutzt auf GitHub. Mit automatischem Rollback bleibt ein kaputter
Stand höchstens bis zum Healthcheck draußen.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

# Direkter Aufruf statt des Docker-Healthchecks: Der Container-Healthcheck läuft nur alle
# 15 Minuten (siehe Dockerfile), darauf zu warten wäre für einen Deploy zu langsam.
HEALTH: list[str] = [
    "docker", "compose", "exec", "-T", "sc-digger",
    "python", "-m", "sc_digger.healthcheck",
]

STATE_FILE = ".deploy-state.json"
LAST_BUILD = ".last-build"

# Status, bei denen der Aufruf als erfolgreich gilt (kein Handlungsbedarf).
_OK_STATUS = ("unveraendert", "ausgerollt", "uebersprungen")


@dataclass
class DeployResult:
    """Ergebnis eines Durchlaufs. ``old``/``new`` sind Kurz-Hashes oder ``None``."""
    status: str        # unveraendert | ausgerollt | zurueckgerollt | rollback_fehlgeschlagen | uebersprungen | fehler
    old: str | None    # zuletzt gebauter Stand (Kurz-Hash) oder None
    new: str | None    # Ziel-Stand (Kurz-Hash) oder None
    detail: str


def exit_code(res: DeployResult) -> int:
    """0 bei unveraendert, ausgerollt, uebersprungen; sonst 1."""
    return 0 if res.status in _OK_STATUS else 1


def make_runner(
    repo: Path, timeout: float = 1800,
) -> Callable[[Sequence[str]], subprocess.CompletedProcess]:
    """Führt Kommandos in ``repo`` aus (capture_output, text).

    Setzt per Umgebung ``GIT_CONFIG_COUNT=1``, ``GIT_CONFIG_KEY_0=safe.directory``,
    ``GIT_CONFIG_VALUE_0=<repo>``: Der Timer läuft als root, das Repo gehört dem Benutzer
    claude (wie bei ``update.sh``), sonst verweigert git die Arbeit ("dubious ownership").
    Bei ``subprocess.TimeoutExpired`` kein Absturz, sondern CompletedProcess mit
    returncode 124 und stderr "Zeitüberschreitung".
    """
    repo = Path(repo)

    def run(args: Sequence[str]) -> subprocess.CompletedProcess:
        argv = list(args)
        env = dict(os.environ)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = str(repo)
        try:
            return subprocess.run(
                argv, cwd=repo, capture_output=True, text=True,
                timeout=timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 124, "", "Zeitüberschreitung")

    return run


def _load_state(repo: Path) -> dict:
    """Liest ``.deploy-state.json``; fehlend oder kaputt zählt als leer."""
    try:
        data = json.loads((Path(repo) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(repo: Path, state: dict) -> None:
    """Schreibt den Zustand atomar (temp + replace), damit ein Abbruch keine halbe Datei
    hinterlässt – der Merker ist die einzige Erinnerung über Timer-Läufe hinweg."""
    path = Path(repo) / STATE_FILE
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _read_last_build(repo: Path) -> str | None:
    """Zuletzt erfolgreich gebauter Stand aus ``.last-build`` (von update.sh geschrieben).
    Fehlt die Datei oder steht "unbekannt" darin, gibt es keinen bekannten alten Stand."""
    try:
        text = (Path(repo) / LAST_BUILD).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text or text == "unbekannt":
        return None
    return text


def _notify(notify: Callable[[str], None], text: str) -> None:
    """Eine Ausnahme beim Melden darf den Ablauf nie abbrechen."""
    try:
        notify(text)
    except Exception:
        pass


def _health_ok(run, health_tries: int, health_sleep: float, sleep) -> bool:
    """Bis zu ``health_tries`` Mal HEALTH prüfen, dazwischen ``sleep(health_sleep)``."""
    for attempt in range(health_tries):
        if attempt:
            sleep(health_sleep)
        if run(list(HEALTH)).returncode == 0:
            return True
    return False


def deploy_once(
    repo: Path, run, notify: Callable[[str], None], *, sleep=time.sleep,
    health_tries: int = 10, health_sleep: float = 15,
) -> DeployResult:
    """Ein Durchlauf. ``run(args) -> CompletedProcess`` und ``notify(text)`` sind
    injizierbar, damit Tests ohne git, Docker und Netz laufen.
    """
    repo = Path(repo)
    state = _load_state(repo)

    # 1. Lokale Änderungen: nie überschreiben, nur einmal je Ziel-Hash melden.
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"]).stdout
    if dirty.strip():
        fetch = run(["git", "fetch", "--quiet", "origin", "main"])
        target = None
        if fetch.returncode == 0:
            target = run(["git", "rev-parse", "--short", "origin/main"]).stdout.strip()
        target = target or "?"
        if state.get("dirty_gemeldet") != target:
            _notify(notify, "⚠️ Auto-Deploy: Lokale Änderungen im Repo auf dem Server, "
                            "kein Deploy. Bitte prüfen.")
            state["dirty_gemeldet"] = target
            _save_state(repo, state)
        return DeployResult(
            "uebersprungen", None, target,
            "lokale Änderungen im Repo (nie per reset --hard überschrieben)",
        )

    # 2. Neuen Stand holen. Fehler hier ist kein Deploy-Problem, sondern Infrastruktur.
    fetch = run(["git", "fetch", "--quiet", "origin", "main"])
    if fetch.returncode != 0:
        return DeployResult("fehler", None, None, "git fetch fehlgeschlagen")

    # 3. Ziel-Stand bestimmen.
    new = run(["git", "rev-parse", "--short", "origin/main"]).stdout.strip()
    if state.get("bad") and state["bad"] == new:
        return DeployResult("uebersprungen", None, new,
                            "dieser Stand ist als fehlerhaft gemerkt, kein erneuter Versuch")
    old = _read_last_build(repo)
    if old is not None and old == new:
        return DeployResult("unveraendert", old, new, "bereits ausgerollt")

    # 4. Ausrollen und prüfen.
    update = run(["./update.sh"])
    if update.returncode == 0 and _health_ok(run, health_tries, health_sleep, sleep):
        state["last_good"] = new
        state["bad"] = None
        _save_state(repo, state)
        _notify(notify, f"✅ Deploy ausgerollt: {old or 'unbekannt'} → {new}")
        return DeployResult("ausgerollt", old, new, "Build und Healthcheck ok")

    # 5. Fehlschlag: bad merken, damit derselbe Commit nicht endlos neu versucht wird.
    state["last_good"] = old
    state["bad"] = new
    _save_state(repo, state)

    if old is None:
        _notify(notify, f"🚨 Deploy von {new} fehlgeschlagen, kein früherer Stand bekannt. "
                        "Bitte manuell prüfen.")
        return DeployResult("fehler", old, new, "Build/Healthcheck fehlgeschlagen, kein Rollback möglich")

    run(["git", "reset", "--hard", old])
    force = run(["./update.sh", "--force"])
    if force.returncode == 0 and _health_ok(run, health_tries, health_sleep, sleep):
        _notify(notify, f"🚨 Deploy von {new} fehlgeschlagen, zurück auf {old}, läuft wieder.")
        return DeployResult("zurueckgerollt", old, new, f"Rollback auf {old} erfolgreich")

    _notify(notify, f"🚨 Deploy von {new} fehlgeschlagen, Rollback auf {old} auch. "
                    "Bitte manuell eingreifen.")
    return DeployResult("rollback_fehlgeschlagen", old, new, f"Rollback auf {old} ebenfalls fehlgeschlagen")


def send_telegram(text: str, env: Mapping[str, str] | None = None) -> bool:
    """Sendet ``text`` an Stephan. Ohne Zugangsdaten oder bei Fehler ``False``, nie eine
    Ausnahme und nie ein Token in einer Meldung.

    Der Import steht in der Funktion, weil der Host die Abhängigkeiten nicht hat; der Aufruf
    geht über ``sc_digger.output.telegram_call`` (Regel 5: nie ``requests`` direkt, die URL
    enthält den Bot-Token).
    """
    values = os.environ if env is None else env
    token = values.get("TELEGRAM_BOT_TOKEN")
    chat_id = values.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        from sc_digger.output import telegram_call
    except Exception:
        return False
    try:
        telegram_call(token, "sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    """``notify <text>`` läuft IM Container und ruft direkt ``send_telegram`` (0 bei Erfolg,
    sonst 1). Ohne Argument läuft es auf dem Host: ``notify`` wird per ``docker compose
    exec`` in den Container gereicht (der Host hat kein requests). Gibt ``<status>: <detail>``
    aus und liefert ``exit_code()``.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] == "notify":
        text = " ".join(args[1:])
        return 0 if send_telegram(text) else 1

    repo = Path(__file__).resolve().parents[1]
    run = make_runner(repo)

    def notify(text: str) -> None:
        res = run(["docker", "compose", "exec", "-T", "sc-digger",
                   "python", "-m", "sc_digger.autodeploy", "notify", text])
        if res.returncode != 0:
            print(text, file=sys.stderr)

    res = deploy_once(repo, run, notify)
    print(f"{res.status}: {res.detail}")
    return exit_code(res)


if __name__ == "__main__":
    sys.exit(main())
