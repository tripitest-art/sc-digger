"""Akzeptanztests: Worktrees gemergter Branches aufraeumen."""

import importlib.util
import pathlib
import subprocess

TOOL = pathlib.Path("scripts/cleanup_worktrees.py")


def _tool():
    assert TOOL.exists(), "scripts/cleanup_worktrees.py fehlt"
    spec = importlib.util.spec_from_file_location("cleanup_worktrees", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_plan_waehlt_nur_gemergte_und_geschuetzte_bleiben(tmp_path):
    a, b, c = (tmp_path / n for n in ("a", "b", "c"))
    for p in (a, b, c):
        p.mkdir()
    worktrees = [(str(a), "feature/issue-1-x"), (str(b), "feature/issue-2-y"), (str(c), "main")]
    plan = _tool().plan(worktrees, {"feature/issue-1-x", "main"})
    assert plan == [str(a)], "nur der gemergte, nicht geschuetzte Worktree darf weg"


def test_plan_ignoriert_verschwundene_pfade(tmp_path):
    weg = tmp_path / "gibt-es-nicht"
    worktrees = [(str(weg), "feature/issue-3-z")]
    assert _tool().plan(worktrees, {"feature/issue-3-z"}) == []


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_entfernt_worktree_und_laesst_repo_stehen(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", "-b", "feature/issue-9-q", str(wt), cwd=repo)

    entfernt = _tool().remove_worktrees(repo, [str(wt)])

    assert entfernt == [str(wt)]
    assert not wt.exists()
    assert (repo / "a.txt").exists(), "Hauptordner darf nicht angetastet werden"
    assert "feature/issue-9-q" in _git("branch", cwd=repo)


def test_dry_run_entfernt_nichts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", "-b", "feature/issue-8-r", str(wt), cwd=repo)

    assert _tool().remove_worktrees(repo, [str(wt)], dry_run=True) == []
    assert wt.exists()
