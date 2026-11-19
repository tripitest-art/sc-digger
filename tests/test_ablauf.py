"""Unit-Tests für scripts/ablauf.py (Entwicklungs-Ablauf Übersicht)."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from scripts.ablauf import (
    Befund,
    haengende_faelle,
    main,
    status_of,
    uebersicht_markdown,
    wartet_auf,
)

JETZT = "2026-11-19T12:00:00Z"


def test_wartet_auf_fehlende_zeile_liefert_leere_liste():
    """Fehlt die Zeile 'Wartet auf:', liefert wartet_auf eine leere Liste."""
    assert wartet_auf(None) == []
    assert wartet_auf("") == []
    assert wartet_auf("Ein beliebiger Text ohne Abhängigkeiten.") == []
    assert wartet_auf("Wartet auf: keine") == []


def test_wartet_auf_extrahiert_nummern():
    """Zahlen werden aus der Zeile 'Wartet auf:' extrahiert, unabhängig von Groß-/Kleinschreibung."""
    body = "Kontext:\n- Wartet auf: #12, #34\nWeitere Details..."
    assert wartet_auf(body) == [12, 34]

    assert wartet_auf("wartet auf: #42") == [42]
    assert wartet_auf("WARTET AUF: #1, #2, #3") == [1, 2, 3]


def test_status_of_reihenfolge_bei_mehreren_labels():
    """'in-arbeit' vor 'blockiert' vor 'bereit' vor 'idee', sonst 'ohne-status'."""
    def mk_issue(labels):
        return {"labels": [{"name": l} for l in labels]}

    # in-arbeit schlägt alles
    assert status_of(mk_issue(["idee", "bereit", "blockiert", "in-arbeit"])) == "in-arbeit"
    assert status_of(mk_issue(["blockiert", "in-arbeit"])) == "in-arbeit"
    assert status_of(mk_issue(["bereit", "in-arbeit"])) == "in-arbeit"

    # blockiert schlägt bereit und idee
    assert status_of(mk_issue(["idee", "bereit", "blockiert"])) == "blockiert"
    assert status_of(mk_issue(["bereit", "blockiert"])) == "blockiert"

    # bereit schlägt idee
    assert status_of(mk_issue(["idee", "bereit"])) == "bereit"

    # nur idee
    assert status_of(mk_issue(["idee", "worker-task"])) == "idee"

    # ohne anerkannten Status
    assert status_of(mk_issue(["worker-task", "bug"])) == "ohne-status"
    assert status_of({"labels": []}) == "ohne-status"


def test_haengende_faelle_sortierung_gewicht_und_nummer():
    """Erst Gewicht 2 (nach Nummer), dann Gewicht 1 (nach Nummer)."""
    issues = [
        {"number": 20, "title": "Iss 20", "labels": [{"name": "in-arbeit"}], "body": ""},
        {
            "number": 5,
            "title": "Iss 5",
            "labels": [{"name": "blockiert"}],
            "body": "Wartet auf: #1",
        },
        {"number": 15, "title": "Iss 15", "labels": [{"name": "in-arbeit"}, {"name": "berührt-main.py"}]},
        {"number": 16, "title": "Iss 16", "labels": [{"name": "in-arbeit"}, {"name": "berührt-main.py"}]},
    ]
    prs = [
        {
            "number": 3,
            "title": "PR 3",
            "isDraft": False,
            "reviewDecision": "",
            "createdAt": "2026-11-15T00:00:00Z",
            "labels": [],
        },
        # PRs für 15 und 16, damit sie nicht zusätzlich unter in-arbeit-ohne-pr fallen
        {"number": 30, "title": "PR 30", "headRefName": "feature/issue-15-kurz", "isDraft": True},
        {"number": 31, "title": "PR 31", "headRefName": "feature/issue-16-kurz", "isDraft": True},
    ]

    faelle = haengende_faelle(issues, prs, now=JETZT, geschlossen={1})
    # Gewicht 2:
    # - PR #3 (pr-ohne-review) -> Nummer 3
    # - Iss #5 (blockiert-frei) -> Nummer 5
    # - Iss #20 (in-arbeit-ohne-pr) -> Nummer 20
    # Gewicht 1:
    # - Regel 8 (Iss #15 und #16) -> Nummer 15
    assert len(faelle) == 4
    assert [f.art for f in faelle] == ["pr-ohne-review", "blockiert-frei", "in-arbeit-ohne-pr", "regel-8"]
    assert [f.gewicht for f in faelle] == [2, 2, 2, 1]
    assert "#3" in faelle[0].satz
    assert "#5" in faelle[1].satz
    assert "#20" in faelle[2].satz
    assert "#15" in faelle[3].satz and "#16" in faelle[3].satz


def test_uebersicht_markdown_ohne_faelle():
    """Wenn keine hängenden Fälle vorliegen, steht '- keine' im Abschnitt."""
    issues = [{"number": 1, "title": "Titel 1", "labels": [{"name": "idee"}]}]
    md = uebersicht_markdown(issues, [], now=JETZT)
    assert "## Hängende Fälle\n- keine" in md


def test_main_mit_fixtures_und_format_json(tmp_path, capsys):
    """--format json liefert die Befunde als JSON-Liste; Fixtures laufen ohne gh und Netz."""
    issues_file = tmp_path / "issues.json"
    prs_file = tmp_path / "prs.json"

    issues_data = [
        {"number": 10, "title": "Offenes Issue", "labels": [{"name": "in-arbeit"}], "body": ""},
    ]
    prs_data = [
        {
            "number": 5,
            "title": "Langer PR",
            "isDraft": False,
            "reviewDecision": "REVIEW_REQUIRED",
            "createdAt": "2026-11-15T00:00:00Z",
            "labels": [],
        }
    ]

    issues_file.write_text(json.dumps(issues_data), encoding="utf-8")
    prs_file.write_text(json.dumps(prs_data), encoding="utf-8")

    code = main([
        "--issues", str(issues_file),
        "--prs", str(prs_file),
        "--format", "json",
        "--now", JETZT,
    ])
    assert code == 0

    out, _ = capsys.readouterr()
    befunde = json.loads(out)
    assert isinstance(befunde, list)
    assert len(befunde) == 2
    assert any(b["art"] == "pr-ohne-review" and b["gewicht"] == 2 for b in befunde)
    assert any(b["art"] == "in-arbeit-ohne-pr" and b["gewicht"] == 2 for b in befunde)


def test_main_mit_fixtures_und_format_md(tmp_path, capsys):
    """Standardausgabe von main mit Fixtures ist Markdown."""
    issues_file = tmp_path / "issues.json"
    prs_file = tmp_path / "prs.json"
    issues_file.write_text(json.dumps([{"number": 1, "title": "Titel", "labels": [{"name": "bereit"}], "body": ""}]), encoding="utf-8")
    prs_file.write_text(json.dumps([]), encoding="utf-8")

    code = main([
        "--issues", str(issues_file),
        "--prs", str(prs_file),
        "--now", JETZT,
    ])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "# Entwicklungs-Ablauf" in out
    assert "## Bereit" in out
    assert "- #1 Titel" in out


def test_main_ohne_dateien_und_ohne_gh_endet_mit_exit_2(capsys):
    """Fehlt gh oder schlägt der Aufruf fehl, endet main mit Exit 2 und Meldung auf stderr."""
    with patch("scripts.ablauf.subprocess.run", side_effect=FileNotFoundError("gh nicht gefunden")):
        code = main([])
        assert code == 2
        _, err = capsys.readouterr()
        assert "Fehler:" in err
        assert "gh" in err


def test_main_gh_aufruf_schlaegt_fehl_endet_mit_exit_2(capsys):
    """Schlägt der Aufruf von gh mit Fehlercode fehl, endet main mit Exit 2."""
    with patch(
        "scripts.ablauf.subprocess.run",
        side_effect=subprocess.CalledProcessError(returncode=1, cmd=["gh"], stderr="API error"),
    ):
        code = main([])
        assert code == 2
        _, err = capsys.readouterr()
        assert "Fehler:" in err
        assert "API error" in err
