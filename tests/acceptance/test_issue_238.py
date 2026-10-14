"""Akzeptanztests: automatischer Deploy mit Rollback (sc_digger.autodeploy)."""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sc_digger import autodeploy as ad
from sc_digger.output import TelegramError

ROOT = Path(__file__).resolve().parents[2]
HEALTH = ["docker", "compose", "exec", "-T", "sc-digger", "python", "-m", "sc_digger.healthcheck"]


class FakeRun:
    """Ersetzt subprocess: kennt git, ./update.sh und den Healthcheck, merkt sich alle Aufrufe."""

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
                self.head = self.target          # git pull --ff-only ist schon passiert
            if rc == 0:
                (self.repo / ".last-build").write_text(self.head + "\n")
        elif args == HEALTH:
            rc = self.health.pop(0) if self.health else 0
        return subprocess.CompletedProcess(args, rc, out, "")

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def deploy(tmp_path, notify=None, **kw):
    msgs = []
    fake = FakeRun(tmp_path, **kw)
    res = ad.deploy_once(tmp_path, fake, notify or msgs.append, sleep=lambda s: None, health_tries=3, health_sleep=0)
    return res, fake, msgs


def state(tmp_path):
    return json.loads((tmp_path / ".deploy-state.json").read_text())


def test_unveraendert_tut_nichts(tmp_path):
    res, fake, msgs = deploy(tmp_path, built="bbb2222", target="bbb2222")
    assert res.status == "unveraendert"
    assert fake.ran("./update.sh") == [] and msgs == []
    assert fake.ran("git", "fetch") == [["git", "fetch", "--quiet", "origin", "main"]]


def test_erfolgreicher_deploy_meldet_einmal(tmp_path):
    res, fake, msgs = deploy(tmp_path)
    assert (res.status, res.old, res.new) == ("ausgerollt", "aaa1111", "bbb2222")
    assert fake.ran("./update.sh") == [["./update.sh"]]
    assert len(msgs) == 1 and "✅" in msgs[0] and "aaa1111" in msgs[0] and "bbb2222" in msgs[0]
    assert state(tmp_path)["last_good"] == "bbb2222" and state(tmp_path)["bad"] is None


def test_erster_deploy_ohne_last_build(tmp_path):
    res, fake, msgs = deploy(tmp_path, built="")
    assert res.status == "ausgerollt" and res.old is None and res.new == "bbb2222"
    assert len(msgs) == 1 and "unbekannt" in msgs[0]


def test_fehlgeschlagener_build_rollt_zurueck(tmp_path):
    res, fake, msgs = deploy(tmp_path, update=(1, 0))
    assert res.status == "zurueckgerollt"
    assert fake.ran("git", "reset") == [["git", "reset", "--hard", "aaa1111"]]
    assert fake.ran("./update.sh") == [["./update.sh"], ["./update.sh", "--force"]]
    assert calls_in_order(fake, ["git", "reset", "--hard", "aaa1111"], ["./update.sh", "--force"])
    assert len(msgs) == 1 and "🚨" in msgs[0] and "bbb2222" in msgs[0] and "aaa1111" in msgs[0]
    assert state(tmp_path)["bad"] == "bbb2222"


def calls_in_order(fake, first, second):
    return fake.calls.index(first) < fake.calls.index(second)


def test_ungesunder_container_rollt_nach_drei_versuchen_zurueck(tmp_path):
    res, fake, msgs = deploy(tmp_path, health=(1, 1, 1, 0))
    assert res.status == "zurueckgerollt"
    assert len(fake.ran(*HEALTH)) == 4          # 3 Versuche nach dem Update, 1 nach dem Rollback
    assert fake.ran("git", "reset") == [["git", "reset", "--hard", "aaa1111"]]
    assert state(tmp_path)["bad"] == "bbb2222"


def test_gesund_beim_zweiten_versuch_genuegt(tmp_path):
    res, fake, msgs = deploy(tmp_path, health=(1, 0))
    assert res.status == "ausgerollt" and fake.ran("git", "reset") == []


def test_rollback_scheitert_meldet_manuellen_eingriff(tmp_path):
    res, fake, msgs = deploy(tmp_path, update=(1, 1))
    assert res.status == "rollback_fehlgeschlagen"
    assert len(msgs) == 1 and "🚨" in msgs[0] and "manuell" in msgs[0]
    assert state(tmp_path)["bad"] == "bbb2222"


def test_rollback_gesund_aber_alter_stand_ungesund(tmp_path):
    res, fake, msgs = deploy(tmp_path, update=(0, 0), health=(1, 1, 1, 1, 1, 1))
    assert res.status == "rollback_fehlgeschlagen" and "manuell" in msgs[0]


def test_ohne_last_build_kein_rollback(tmp_path):
    res, fake, msgs = deploy(tmp_path, built="", update=(1,))
    assert res.status == "fehler"
    assert fake.ran("git", "reset") == [] and len(msgs) == 1 and "🚨" in msgs[0]


def test_schlechter_commit_wird_uebersprungen_bis_main_weiterlaeuft(tmp_path):
    (tmp_path / ".deploy-state.json").write_text(json.dumps({"last_good": "aaa1111", "bad": "bbb2222"}))
    res, fake, msgs = deploy(tmp_path)
    assert res.status == "uebersprungen" and fake.ran("./update.sh") == [] and msgs == []
    res2, fake2, msgs2 = deploy(tmp_path, built="aaa1111", target="ccc3333")
    assert res2.status == "ausgerollt" and state(tmp_path)["bad"] is None


def test_lokale_aenderungen_werden_nie_ueberschrieben_und_nur_einmal_gemeldet(tmp_path):
    res, fake, msgs = deploy(tmp_path, dirty=" M update.sh\n")
    assert res.status == "uebersprungen"
    assert fake.ran("./update.sh") == [] and fake.ran("git", "reset") == []
    assert len(msgs) == 1 and "Änderungen" in msgs[0]
    res2, fake2, msgs2 = deploy(tmp_path, dirty=" M update.sh\n")
    assert res2.status == "uebersprungen" and msgs2 == []


def test_fehler_beim_melden_bricht_den_deploy_nicht_ab(tmp_path):
    def kaputt(text):
        raise RuntimeError("Telegram weg")
    res, fake, msgs = deploy(tmp_path, notify=kaputt)
    assert res.status == "ausgerollt"


@pytest.mark.parametrize("status,code", [("unveraendert", 0), ("ausgerollt", 0), ("uebersprungen", 0),
                                         ("zurueckgerollt", 1), ("rollback_fehlgeschlagen", 1), ("fehler", 1)])
def test_exit_code(status, code):
    assert ad.exit_code(ad.DeployResult(status=status, old=None, new=None, detail="")) == code


def test_make_runner_fuehrt_im_repo_aus(tmp_path):
    res = ad.make_runner(tmp_path)(["pwd"])
    assert res.returncode == 0 and Path(res.stdout.strip()).resolve() == tmp_path.resolve()


def test_make_runner_setzt_safe_directory_fuer_root(tmp_path):
    """Das Repo gehört dem Benutzer claude, der Timer läuft als root (wie bei update.sh)."""
    env = ad.make_runner(tmp_path)(["env"]).stdout.splitlines()
    assert "GIT_CONFIG_COUNT=1" in env and "GIT_CONFIG_KEY_0=safe.directory" in env
    assert f"GIT_CONFIG_VALUE_0={tmp_path}" in env


def test_make_runner_zeitueberschreitung_ist_ein_fehlschlag_keine_ausnahme(tmp_path):
    res = ad.make_runner(tmp_path, timeout=0.2)(["sleep", "5"])
    assert res.returncode == 124


ENV = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42"}


def test_send_telegram_ohne_zugangsdaten(monkeypatch):
    monkeypatch.setattr("sc_digger.output.telegram_call", lambda *a, **k: pytest.fail("darf nicht senden"))
    assert ad.send_telegram("hallo", env={}) is False


def test_send_telegram_sendet_ueber_telegram_call(monkeypatch):
    seen = []
    monkeypatch.setattr("sc_digger.output.telegram_call", lambda token, method, **kw: seen.append((token, method, kw)) or {"ok": True})
    assert ad.send_telegram("hallo", env=ENV) is True
    assert seen == [("123:abc", "sendMessage", {"json": {"chat_id": "42", "text": "hallo"}})]


def test_send_telegram_wirft_nie(monkeypatch):
    def fehler(*a, **k):
        raise TelegramError("sendMessage: Telegram 400: kaputt")
    monkeypatch.setattr("sc_digger.output.telegram_call", fehler)
    assert ad.send_telegram("hallo", env=ENV) is False


def test_main_notify_gibt_exit_code_der_zustellung(monkeypatch):
    monkeypatch.setattr(ad, "send_telegram", lambda text, env=None: text == "ok")
    assert ad.main(["notify", "ok"]) == 0
    assert ad.main(["notify", "nope"]) == 1


def test_modul_importiert_oben_nur_standardbibliothek():
    """Läuft auf dem Host (python3 -m sc_digger.autodeploy), dort fehlen requests & Co."""
    tree = ast.parse((ROOT / "sc_digger" / "autodeploy.py").read_text(encoding="utf-8"))
    tops = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            tops += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "keine relativen Importe auf Modulebene"
            tops.append((node.module or "").split(".")[0])
    assert tops and all(t in sys.stdlib_module_names for t in tops), tops


def test_systemd_units_liegen_im_repo():
    service = (ROOT / "deploy" / "sc-digger-deploy.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "sc-digger-deploy.timer").read_text(encoding="utf-8")
    assert "Type=oneshot" in service and "sc_digger.autodeploy" in service and "flock" in service
    assert "OnUnitActiveSec=15min" in timer and "WantedBy=timers.target" in timer
