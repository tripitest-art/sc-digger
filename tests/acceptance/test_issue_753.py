"""Akzeptanztests: Auto-Deploy holt den Ziel-Stand direkt vom Server statt aus dem lokalen Ref."""
import subprocess

import pytest

from sc_digger import autodeploy as ad

HEALTH = ["docker", "compose", "exec", "-T", "sc-digger", "python", "-m", "sc_digger.healthcheck"]
VOLL = "1111111111111111111111111111111111111111"


class FakeRun:
    """Ersetzt subprocess: kennt git ls-remote, git rev-parse, ./update.sh und den Healthcheck."""

    def __init__(self, repo, *, built="aaa1111", ls_remote="1111111111111111111111111111111111111111",
                 ls_rc=0, kurz="1111111", ref_hash="9999999", update=(0,)):
        self.repo, self.built = repo, built
        self.ls_remote, self.ls_rc = ls_remote, ls_rc
        self.kurz, self.ref_hash = kurz, ref_hash
        self.update = list(update)
        self.calls = []
        if built:
            (repo / ".last-build").write_text(built + "\n")

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        rc, out = 0, ""
        if args[:2] == ["git", "status"]:
            out = ""
        elif args[:2] == ["git", "ls-remote"]:
            rc = self.ls_rc
            out = f"{self.ls_remote}\trefs/heads/main\n" if self.ls_remote else ""
        elif args[:2] == ["git", "rev-parse"] and len(args) == 4 and args[3] != "origin/main":
            out = self.kurz + "\n"
        elif args[:2] == ["git", "rev-parse"]:
            out = self.ref_hash + "\n"
        elif args[0] == "./update.sh":
            rc = self.update.pop(0) if self.update else 0
            if rc == 0:
                (self.repo / ".last-build").write_text(self.kurz + "\n")
        elif args == HEALTH:
            rc = 0
        return subprocess.CompletedProcess(args, rc, out, "")

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def deploy(tmp_path, **kw):
    fake = FakeRun(tmp_path, **kw)
    res = ad.deploy_once(tmp_path, fake, lambda t: None, sleep=lambda s: None,
                         health_tries=2, health_sleep=0)
    return res, fake


def test_ziel_stand_kommt_von_ls_remote_nicht_aus_dem_lokalen_ref(tmp_path):
    """Der lokale Ref zeigt auf 9999999, der Server auf 1111111: Es muss 1111111 werden."""
    res, fake = deploy(tmp_path, built="8888888", kurz="1111111", ref_hash="9999999")
    assert res.new == "1111111"
    assert res.status == "ausgerollt"
    assert fake.ran("./update.sh")


def test_unveraendert_wenn_server_stand_schon_gebaut_ist(tmp_path):
    res, fake = deploy(tmp_path, built="1111111", kurz="1111111", ref_hash="9999999")
    assert res.status == "unveraendert"
    assert not fake.ran("./update.sh")


def test_ls_remote_fehler_faellt_auf_lokalen_ref_zurueck(tmp_path):
    res, fake = deploy(tmp_path, built="8888888", ls_rc=128, ref_hash="9999999")
    assert res.new == "9999999"
    assert res.status == "ausgerollt"


def test_leere_ls_remote_antwort_faellt_auf_lokalen_ref_zurueck(tmp_path):
    res, fake = deploy(tmp_path, built="8888888", ls_remote="", ref_hash="9999999")
    assert res.new == "9999999"
