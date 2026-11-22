"""Akzeptanztests für das Kuratoren-Mining im Dashboard (Issue #659)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.curator import candidates_from_counts
from sc_digger.models import Config
from sc_digger.web.app import create_app

PASSWORD = "mining-test-password"
AUTH = ("sc-digger", PASSWORD)


def _cfg(tmp_path: Path) -> Config:
    return Config(raw={
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
            "health_db_path": str(tmp_path / "seen.sqlite"),
        },
        "download": {
            "inbox_dir": str(tmp_path / "inbox"),
            "collection_dir": str(tmp_path / "collection"),
        },
    })


def test_candidates_from_counts_filtert_und_sortiert():
    kandidaten = candidates_from_counts({"a": 1, "b": 3, "c": 2, "d": 3}, min_appearances=2)

    assert kandidaten == [("b", 3), ("d", 3), ("c", 2)]
    assert candidates_from_counts({}) == []
    assert candidates_from_counts({"a": 1}, min_appearances=2) == []


def test_candidates_from_counts_begrenzt_die_liste():
    counts = {f"profil{nummer}": 5 for nummer in range(40)}

    assert len(candidates_from_counts(counts, min_appearances=2, limit=30)) == 30


def test_kandidaten_ohne_lauf_sind_leer(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, password=PASSWORD)

    with TestClient(app) as client:
        antwort = client.get("/api/kuratoren/kandidaten", auth=AUTH)

    assert antwort.status_code == 200
    assert antwort.json() == {"candidates": [], "checked_at": None}
