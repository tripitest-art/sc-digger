"""Akzeptanztests: Übersicht des Entwicklungs-Ablaufs aus Labels (Issue #<N>)."""
from scripts.ablauf import haengende_faelle, uebersicht_markdown

JETZT = "2026-11-19T12:00:00Z"


def _issue(nr, labels, *, body="", titel=None):
    return {"number": nr, "title": titel or f"Issue {nr}", "body": body,
            "labels": [{"name": name} for name in labels],
            "updatedAt": JETZT}


def _pr(nr, *, branch=None, review="", draft=False, erstellt=JETZT):
    return {"number": nr, "title": f"PR {nr}",
            "headRefName": branch or f"feature/issue-{nr}-x",
            "reviewDecision": review, "isDraft": draft,
            "createdAt": erstellt, "labels": []}


def test_pr_ohne_review_seit_mehr_als_zwei_tagen():
    faelle = haengende_faelle([], [_pr(10, erstellt="2026-11-16T12:00:00Z")], now=JETZT)
    assert any(f.art == "pr-ohne-review" and "#10" in f.satz for f in faelle)


def test_frischer_pr_ohne_review_ist_kein_befund():
    faelle = haengende_faelle([], [_pr(11, erstellt="2026-11-19T06:00:00Z")], now=JETZT)
    assert not [f for f in faelle if f.art == "pr-ohne-review"]


def test_blockiert_ohne_offene_abhaengigkeit():
    issues = [_issue(7, ["worker-task", "blockiert"], body="Wartet auf: #3, #4")]
    faelle = haengende_faelle(issues, [], now=JETZT, geschlossen={3, 4})
    assert any(f.art == "blockiert-frei" and "#7" in f.satz for f in faelle)


def test_blockiert_mit_offener_abhaengigkeit_ist_kein_befund():
    issues = [_issue(8, ["worker-task", "blockiert"], body="Wartet auf: #3, #4")]
    faelle = haengende_faelle(issues, [], now=JETZT, geschlossen={3})
    assert not [f for f in faelle if f.art == "blockiert-frei"]


def test_in_arbeit_ohne_pr_haengt():
    issues = [_issue(9, ["worker-task", "in-arbeit"])]
    faelle = haengende_faelle(issues, [], now=JETZT)
    assert any(f.art == "in-arbeit-ohne-pr" and "#9" in f.satz for f in faelle)


def test_in_arbeit_mit_pr_ist_kein_befund():
    issues = [_issue(12, ["worker-task", "in-arbeit"])]
    prs = [_pr(40, branch="feature/issue-12-kurz")]
    faelle = haengende_faelle(issues, prs, now=JETZT)
    assert not [f for f in faelle if f.art == "in-arbeit-ohne-pr"]


def test_zwei_laeufe_mit_main_py_gleichzeitig():
    issues = [_issue(5, ["in-arbeit", "berührt-main.py"]),
              _issue(6, ["in-arbeit", "berührt-main.py"])]
    faelle = haengende_faelle(issues, [], now=JETZT)
    regel8 = [f for f in faelle if f.art == "regel-8"]
    assert regel8 and "#5" in regel8[0].satz and "#6" in regel8[0].satz


def test_markdown_hat_jede_spalte_und_die_haengenden_faelle():
    issues = [_issue(1, ["idee"]), _issue(2, ["bereit", "worker-task"]),
              _issue(13, ["worker-task", "in-arbeit"])]
    md = uebersicht_markdown(issues, [], now=JETZT)
    for wort in ("Idee", "Bereit", "In Arbeit", "Hängende Fälle"):
        assert wort in md
    assert "#13" in md
    assert "ohne-status" not in md
