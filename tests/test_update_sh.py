"""update.sh mit Attrappen für git und docker: ein gescheiterter Build darf nicht still bleiben.

Nachstellung vom 28.09.: `git pull` holte neuen Code, der Build scheiterte, der nächste Aufruf
meldete „Schon aktuell“ und der Container lief weiter mit dem alten Image.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None,
                                reason="Benötigt bash (update.sh läuft auf dem Linux-Server)")

FAKE_GIT = """#!/usr/bin/env bash
# Attrappe: HEAD steht in state/head, `pull` übernimmt state/remote.
while [ "$1" = "-c" ]; do shift 2; done
case "$1" in
  pull) cp state/remote state/head ;;
  rev-parse) cat state/head ;;
  log) echo "log $2" ;;
esac
"""

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> state/docker_calls
if [ "$1 $2" = "compose up" ] && [ -f state/build_fails ]; then exit 1; fi
if [ "$1 $2" = "compose exec" ] && [ -f state/smoke_fails ]; then exit 1; fi
exit 0
"""


@pytest.fixture
def env(tmp_path):
    (tmp_path / "state").mkdir()
    shutil.copy(ROOT / "update.sh", tmp_path / "update.sh")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("git", FAKE_GIT), ("docker", FAKE_DOCKER)):
        f = bindir / name
        f.write_text(body, newline="\n")
        f.chmod(0o755)
    (tmp_path / "state" / "head").write_text("aaa1111\n")
    (tmp_path / "state" / "remote").write_text("aaa1111\n")
    e = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "UPDATE_RETRY_SLEEP": "0"}
    return tmp_path, e


def run(tmp, e, *args):
    return subprocess.run(["bash", "update.sh", *args], cwd=tmp, env=e, capture_output=True, text=True)


def builds(tmp) -> int:
    f = tmp / "state" / "docker_calls"
    return f.read_text().count("compose up -d --build") if f.exists() else 0


def test_first_run_builds_and_remembers(env):
    tmp, e = env
    r = run(tmp, e)
    assert r.returncode == 0, r.stderr
    assert builds(tmp) == 1
    assert (tmp / ".last-build").read_text().strip() == "aaa1111"
    assert run(tmp, e).stdout.strip() == "Schon aktuell (aaa1111)."
    assert builds(tmp) == 1


def test_failed_build_is_retried_on_next_run(env):
    tmp, e = env
    run(tmp, e)                                              # aaa1111 gebaut
    (tmp / "state" / "remote").write_text("bbb2222\n")       # neuer Code auf GitHub
    (tmp / "state" / "build_fails").touch()
    r = run(tmp, e)
    assert r.returncode != 0 and "FEHLER" in r.stderr
    assert (tmp / ".last-build").read_text().strip() == "aaa1111"
    (tmp / "state" / "build_fails").unlink()
    r = run(tmp, e)                                           # früher: „Schon aktuell“
    assert r.returncode == 0, r.stderr
    assert "Schon aktuell" not in r.stdout
    assert (tmp / ".last-build").read_text().strip() == "bbb2222"


def test_container_that_does_not_answer_is_not_marked_as_deployed(env):
    tmp, e = env
    (tmp / "state" / "smoke_fails").touch()
    r = run(tmp, e)
    assert r.returncode != 0 and "antwortet nicht" in r.stderr
    assert not (tmp / ".last-build").exists()


def test_force_rebuilds_even_when_current(env):
    tmp, e = env
    run(tmp, e)
    assert run(tmp, e, "--force").returncode == 0
    assert builds(tmp) == 2


def test_state_file_is_ignored_by_git():
    assert ".last-build" in (ROOT / ".gitignore").read_text().splitlines()
