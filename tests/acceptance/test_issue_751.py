"""Akzeptanztests: merge-nach-freigabe mergt nicht, wenn ein verknüpftes Issue „manuell“ verlangt."""

import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "merge_nach_freigabe.py"
_spec = importlib.util.spec_from_file_location("merge_nach_freigabe_merge_modus", _PATH)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)

BOT = "sandbox-agent-reviewer-app[bot]"
HEAD = "c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0"
PR = {"number": 30, "draft": False, "mergeable": True, "head": {"sha": HEAD, "ref": "feature/issue-7-x"}}
REVIEW = {"state": "APPROVED", "user": {"login": BOT}, "commit_id": HEAD,
          "submitted_at": "2026-11-25T20:00:00Z"}
CHECKS = {"state": "success", "runs": [
    {"name": "acceptance-guard", "status": "completed", "conclusion": "success"},
    {"name": "tests", "status": "completed", "conclusion": "success"},
]}


def _issue(modus: str | None) -> str:
    teile = ["### Ziel\n\nEtwas bauen.", "### Berührt sc_digger/main.py\n\nnein"]
    if modus is not None:
        teile.append(f"### Merge-Modus\n\n{modus}")
    teile.append("### Kontext\n\nAus dem Gespräch.")
    return "\n\n".join(teile) + "\n"


MANUELL = "manuell durch Stephan (Betrieb, Dateien, Zugangsdaten, Downloads)"
AUTOMATISCH = "automatisch nach Review"


# --- merge_modus_aus_issue_text --------------------------------------------------------


def test_merge_modus_wird_aus_dem_formular_gelesen():
    assert m.merge_modus_aus_issue_text(_issue(MANUELL)) == "manuell"
    assert m.merge_modus_aus_issue_text(_issue(AUTOMATISCH)) == "automatisch"


def test_merge_modus_fehlt_oder_ist_unbekannt():
    assert m.merge_modus_aus_issue_text(_issue(None)) is None
    assert m.merge_modus_aus_issue_text(_issue("_No response_")) is None
    assert m.merge_modus_aus_issue_text("") is None
    assert m.merge_modus_aus_issue_text(None) is None


def test_merge_modus_nur_aus_dem_eigenen_abschnitt():
    # „manuell“ im Kontext ist kein Merge-Modus.
    body = _issue(AUTOMATISCH).replace("Aus dem Gespräch.", "Früher wurde manuell gemergt.")
    assert m.merge_modus_aus_issue_text(body) == "automatisch"


# --- decide ------------------------------------------------------------------------------


def _decide(**extra):
    return m.decide(PR, [REVIEW], CHECKS, BOT, {"tripitest-art"}, BOT, **extra)


def test_manuelles_issue_blockiert_den_merge():
    decision = _decide(manuelle_issues=[749])
    assert decision.merge is False
    assert any("#749" in r and "manuell" in r for r in decision.reasons)


def test_ohne_manuelle_issues_bleibt_alles_wie_bisher():
    assert _decide().merge is True
    assert _decide(manuelle_issues=[]).merge is True


# --- manuelle_issues ---------------------------------------------------------------------


class _Gh:
    def __init__(self, pr_body, issues):
        self._pr_body = pr_body
        self._issues = issues
        self.abgefragt = []

    def pull_body(self, number):
        return self._pr_body

    def issue_body(self, number):
        self.abgefragt.append(number)
        return self._issues[number]


def test_manuelle_issues_liest_jedes_verknuepfte_issue():
    gh = _Gh("Closes #7, #8\nFixes #9", {7: _issue(AUTOMATISCH), 8: _issue(MANUELL), 9: _issue(None)})
    assert m.manuelle_issues(gh, 30) == [8]
    assert gh.abgefragt == [7, 8, 9]


def test_manuelle_issues_ohne_verweis_ist_leer():
    gh = _Gh("Nur Doku, siehe #8", {8: _issue(MANUELL)})
    assert m.manuelle_issues(gh, 30) == []
    assert gh.abgefragt == []


# --- main --------------------------------------------------------------------------------


class _FakeGitHub:
    instances = []
    issue_text = _issue(MANUELL)

    def __init__(self, repo, token, api="", graphql_url=""):
        self.calls = []
        type(self).instances.append(self)

    def reviews(self, number):
        return [REVIEW]

    def pull(self, number):
        return PR

    def checks(self, sha):
        return CHECKS

    def label_actor(self, number, label):
        return BOT

    def merge(self, number, sha):
        self.calls.append(("merge", number))
        return {"merged": True}

    def delete_branch(self, ref):
        self.calls.append(("delete", ref))

    def pull_body(self, number):
        return "Closes #7\n"

    def issue_body(self, number):
        return type(self).issue_text

    def issue_offen(self, number):
        return True

    def close_issue(self, number):
        self.calls.append(("close", number))


def _main(monkeypatch, tmp_path, issue_text):
    pfad = tmp_path / "event.json"
    pfad.write_text(json.dumps({"label": {"name": "review-freigabe"}, "pull_request": PR}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(pfad))
    monkeypatch.setenv("GITHUB_REPOSITORY", "tripitest-art/sc-digger")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GUARD_TRUSTED_ACTORS", "tripitest-art")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(_FakeGitHub, "issue_text", issue_text)
    monkeypatch.setattr(m, "GitHub", _FakeGitHub)
    _FakeGitHub.instances.clear()
    return m.main()


def test_main_mergt_ein_manuelles_issue_nicht(monkeypatch, tmp_path, capsys):
    assert _main(monkeypatch, tmp_path, _issue(MANUELL)) == 1
    assert _FakeGitHub.instances[-1].calls == []
    assert "manuell" in capsys.readouterr().out


def test_main_mergt_ein_automatisches_issue_wie_bisher(monkeypatch, tmp_path):
    assert _main(monkeypatch, tmp_path, _issue(AUTOMATISCH)) == 0
    assert _FakeGitHub.instances[-1].calls == [("merge", 30), ("delete", "feature/issue-7-x"), ("close", 7)]
