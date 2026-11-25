"""Heute-Liste: Eintrag „für später gemerkt“ (Issue #720)."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import sc_digger.web.today as today
from sc_digger.models import Config
from sc_digger.output import State

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    return cfg


def test_ohne_datenbanken_kein_later_eintrag(tmp_path):
    assert "later" not in [i.key for i in today.collect_today(_cfg(tmp_path))]


def test_einzahl_label_und_ohne_since_new_null(tmp_path):
    State(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("INSERT INTO seen (id, url, title, reported_at) VALUES (1, 'u', 'A - T', '2026-11-20 07:30:00')")
    from sc_digger.db import TrackDB
    TrackDB(tmp_path / "tracks.sqlite").close()
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute("INSERT INTO sc_feedback (sc_id, url, value) VALUES (1, 'u', 'later')")
    later = [i for i in today.collect_today(_cfg(tmp_path)) if i.key == "later"]
    assert later == [today.TodayItem("later", 1, "1 für später gemerkt", today.LATER_HREF, 0)]


def test_fehler_der_quelle_laesst_nur_later_entfallen(tmp_path, monkeypatch, caplog):
    real = today.query_digest

    def kaputt(*a, **kw):
        if kw.get("feedback") == "later":
            raise RuntimeError("boom")
        return real(*a, **kw)

    monkeypatch.setattr(today, "query_digest", kaputt)
    with caplog.at_level(logging.WARNING):
        today.collect_today(_cfg(tmp_path))
    assert "Heute-Liste: Quelle later nicht lesbar (RuntimeError)" in caplog.text
