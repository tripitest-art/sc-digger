"""Eigene Tests für den Auto-Deploy (sc_digger.autodeploy)."""
import ast
import json
import subprocess
from pathlib import Path

import pytest

from sc_digger import autodeploy as ad
from sc_digger.output import TelegramError

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ["docker", "compose", "exec", "-T", "sc-digger", "python", "-m", "sc_digger.healthcheck"]
ENV = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42"}


class FakeRun:
    """Ersetzt subprocess: nur git status/rev-parse, ./update.sh und HEALTH sind bekannt."""

    def __init__(self, repo, built="aaa1111", target="bbb2222", update=(0,), health=(0,), dirty=""):
        self.repo, self.target, self.head = repo, target, built
        self.update, self.health, self.dirty = list(update), list(health), dirty
        self.calls = []
        if built:
            (repo / ".last-build").write_text(built + "\n")

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        rc, out = 0, ""
        if args[:2] == ["git", "status"]:
            out = self.dirty
        elif args[:2] == ["git", "rev-parse"]:
            out = self.target + "\n"
        elif args[:3] == ["git", "reset", "--hard"]:
            self.head = args[3]
        elif args[0] == "./update.sh":
            rc = self.update.pop(0) if self.update else 0
            if "--force" not in args:
                self.head = self.target
            if rc == 0:
                (self.repo / ".last-build").write_text(self.head + "\n")
        elif args == HEALTH:
            rc = self.health.pop(0) if self.health else 0
        return subprocess.CompletedProcess(args, rc, out, "")

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def deploy(tmp_path, **kw):
    msgs = []
    fake = FakeRun(tmp_path, **kw)
    res = ad.deploy_once(tmp_path, fake, msgs.append, sleep=lambda s: None,
                         health_tries=3, health_sleep=0)
    return res, fake, msgs


# ---------------------------------------------------------------- Ablauf

def test_update_wird_nicht_mit_safe_directory_fehler_behandelt(tmp_path):
    """Ein nicht-null Rückgabecode von ./update.sh ist ein Fehlschlag, kein Erfolg."""
    res, fake, msgs = deploy(tmp_path, update=(2, 0))
    assert res.status == "zurueckgerollt"
    assert fake.ran("git", "reset") == [["git", "reset", "--hard", "aaa1111"]]


def test_unbekannt_in_last_build_gilt_als_kein_alter_stand(tmp_path):
    res, fake, msgs = deploy(tmp_path, built="unbekannt")
    assert res.status == "ausgerollt" and res.old is None
    assert "unbekannt" in msgs[0]


def test_fetch_fehler_bricht_ohne_meldung_ab(tmp_path):
    class KaputtesFetch(FakeRun):
        def __call__(self, args):
            args = list(args)
            self.calls.append(args)
            if args[:2] == ["git", "fetch"]:
                return subprocess.CompletedProcess(args, 1, "", "kein Netz")
            return super().__call__(args)

    msgs = []
    fake = KaputtesFetch(tmp_path)
    res = ad.deploy_once(tmp_path, fake, msgs.append, sleep=lambda s: None, health_tries=3, health_sleep=0)
    assert res.status == "fehler" and msgs == []


def test_detail_enthaelt_keine_zugangsdaten(tmp_path):
    res, fake, msgs = deploy(tmp_path, update=(1, 1))
    assert "TELEGRAM_BOT_TOKEN" not in res.detail and "123:abc" not in res.detail


def test_zustand_wird_atomar_geschrieben_keine_temp_datei(tmp_path):
    deploy(tmp_path)
    assert (tmp_path / ".deploy-state.json").exists()
    assert not (tmp_path / ".deploy-state.json.tmp").exists()
    assert json.loads((tmp_path / ".deploy-state.json").read_text())["last_good"] == "bbb2222"


def test_dirty_merker_ueberlebt_anderen_ziel_hash(tmp_path):
    """Erst bei einem neuen Ziel-Hash wird erneut gewarnt."""
    res, fake, msgs = deploy(tmp_path, dirty=" M update.sh\n")
    assert res.status == "uebersprungen" and len(msgs) == 1
    res2, fake2, msgs2 = deploy(tmp_path, dirty=" M update.sh\n", target="ccc3333")
    assert res2.status == "uebersprungen" and len(msgs2) == 1


# ---------------------------------------------------------------- exit_code / main

@pytest.mark.parametrize("status,code", [
    ("unveraendert", 0), ("ausgerollt", 0), ("uebersprungen", 0),
    ("zurueckgerollt", 1), ("rollback_fehlgeschlagen", 1), ("fehler", 1),
])
def test_exit_code_tabelle(status, code):
    assert ad.exit_code(ad.DeployResult(status, None, None, "")) == code


def test_main_notify_container_modus(monkeypatch):
    monkeypatch.setattr(ad, "send_telegram", lambda text, env=None: text == "ok")
    assert ad.main(["notify", "ok"]) == 0
    assert ad.main(["notify", "nope"]) == 1


def test_main_host_lauf_status_und_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(ad, "make_runner", lambda repo, timeout=1800: lambda args: None)
    monkeypatch.setattr(ad, "deploy_once",
                        lambda repo, run, notify: ad.DeployResult("zurueckgerollt", "a", "b", "Rollback ok"))
    assert ad.main([]) == 1
    assert "zurueckgerollt: Rollback ok" in capsys.readouterr().out


def test_main_host_meldet_gescheiterte_notify_auf_stderr(monkeypatch, capsys):
    def fake_run(args):
        return subprocess.CompletedProcess(list(args), 1, "", "kein Container")

    monkeypatch.setattr(ad, "make_runner", lambda repo, timeout=1800: fake_run)

    def fake_deploy(repo, run, notify):
        notify("hallo")
        return ad.DeployResult("ausgerollt", None, "x", "ok")

    monkeypatch.setattr(ad, "deploy_once", fake_deploy)
    assert ad.main([]) == 0
    assert "hallo" in capsys.readouterr().err


# ---------------------------------------------------------------- make_runner

def test_make_runner_timeout_liefert_stderr(tmp_path):
    res = ad.make_runner(tmp_path, timeout=0.2)(["sleep", "5"])
    assert res.returncode == 124 and "Zeitüberschreitung" in res.stderr


# ---------------------------------------------------------------- send_telegram

def test_send_telegram_ohne_chat_id_kein_senden(monkeypatch):
    monkeypatch.setattr("sc_digger.output.telegram_call", lambda *a, **k: pytest.fail("darf nicht senden"))
    assert ad.send_telegram("hallo", env={"TELEGRAM_BOT_TOKEN": "123:abc"}) is False


def test_send_telegram_faengt_telegram_error(monkeypatch):
    def fehler(*a, **k):
        raise TelegramError("sendMessage: Telegram 400: kaputt")
    monkeypatch.setattr("sc_digger.output.telegram_call", fehler)
    assert ad.send_telegram("hallo", env=ENV) is False


# ---------------------------------------------------------------- Dateien / Importe

def test_service_und_timer_datei_inhalt():
    service = (ROOT / "deploy" / "sc-digger-deploy.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "sc-digger-deploy.timer").read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "WorkingDirectory=/root/sc-digger" in service
    assert "flock -n /run/sc-digger-deploy.lock" in service
    assert "python3 -m sc_digger.autodeploy" in service
    assert "OnBootSec=5min" in timer and "OnUnitActiveSec=15min" in timer
    assert "WantedBy=timers.target" in timer


def test_modul_nutzt_requests_nirgends():
    """Regel 5: Telegram nur über telegram_call, nie requests (Token steckt in der URL)."""
    source = (ROOT / "sc_digger" / "autodeploy.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    importierte = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importierte |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            importierte.add(node.module.split(".")[0])
    assert "requests" not in importierte
    assert "import requests" not in source
