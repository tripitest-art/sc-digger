"""Kuratoren-Mining: Kernlogik, Ablage der Auswertung und Wartungsaktion (Issue #659)."""
from __future__ import annotations

import json
from pathlib import Path

from sc_digger.curator import (
    MAX_CANDIDATES,
    candidates_from_counts,
    read_candidates,
    write_candidates,
)
from sc_digger.web.tasks import action_labels, default_runners


def test_leeres_mapping():
    assert candidates_from_counts({}) == []


def test_gleichstand_alphabetisch():
    assert candidates_from_counts({"zeta": 3, "alpha": 3, "mitte": 4}) == [
        ("mitte", 4), ("alpha", 3), ("zeta", 3)]


def test_limit_und_standard():
    counts = {f"p{i:02d}": 5 for i in range(40)}
    assert len(candidates_from_counts(counts)) == MAX_CANDIDATES == 30
    assert candidates_from_counts(counts, limit=2) == [("p00", 5), ("p01", 5)]


def test_min_appearances_grenze():
    assert candidates_from_counts({"a": 2, "b": 1}, min_appearances=2) == [("a", 2)]


def test_read_candidates_fehlende_und_kaputte_datei(tmp_path: Path):
    leer = {"candidates": [], "checked_at": None}
    assert read_candidates(tmp_path / "gibt-es-nicht.json") == leer
    kaputt = tmp_path / "kaputt.json"
    kaputt.write_text("{nicht json", encoding="utf-8")
    assert read_candidates(kaputt) == leer
    falsch = tmp_path / "falsch.json"
    falsch.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert read_candidates(falsch) == leer


def test_write_und_read_roundtrip(tmp_path: Path):
    path = tmp_path / "sub" / "curator-candidates.json"
    write_candidates(path, [("a", 3), ("b", 2)])

    result = read_candidates(path)

    assert result["candidates"] == [
        {"profile": "a", "appearances": 3}, {"profile": "b", "appearances": 2}]
    assert isinstance(result["checked_at"], str)


def test_wartungsaktion_registriert(tmp_path: Path):
    from sc_digger.models import Config

    cfg = Config(raw={"state": {"db_path": str(tmp_path / "seen.sqlite"),
                                "track_db_path": str(tmp_path / "tracks.sqlite")}})

    assert "curator-mining" in default_runners(cfg)
    assert "curator-mining" in action_labels()
