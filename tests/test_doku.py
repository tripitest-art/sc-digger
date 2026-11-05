"""Doku und Changelog dürfen dem Code nicht davonlaufen."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    p = ROOT / name
    assert p.is_file(), f"{name} fehlt"
    return p.read_text(encoding="utf-8")


def test_changelog_hat_unreleased_abschnitt():
    t = _read("CHANGELOG.md")
    assert t.startswith("# Changelog")
    assert "## Unreleased" in t


def test_changelog_eintraege_nennen_pr_nummer():
    """Ohne PR-Nummer lässt sich vor dem Deploy nicht nachlesen, was neu ist."""
    t = _read("CHANGELOG.md").split("## Unreleased", 1)[1]
    assert re.search(r"\(#\d+\)", t), "Einträge unter „Unreleased“ nennen keine PR-Nummer"


def test_readme_verweist_auf_den_changelog():
    assert "CHANGELOG.md" in _read("README.md")


def test_agents_md_nennt_die_dokupflicht():
    assert "## Doku und Changelog" in _read("AGENTS.md")


def test_agents_md_bleibt_unter_der_antigravity_grenze():
    """Antigravity schneidet Regeldateien bei 12.000 Zeichen ab (BETRIEB.md).

    Was dahinter steht, liest ein Antigravity-Agent nie: Regeln verschwinden lautlos, und
    niemand merkt es, weil die Datei im Repo vollständig aussieht. Deshalb hier eine harte
    Grenze statt einer Bitte in der Datei.
    """
    t = _read("AGENTS.md")
    assert len(t) <= 12000, (
        f"AGENTS.md hat {len(t)} Zeichen; ab 12.000 schneidet Antigravity ab. "
        "Details gehören nach ENTWICKLUNG.md, BETRIEB.md oder MCP.md."
    )
