"""Unit-Tests für die Heute-Liste der Web-Startseite (sc_digger/web/today.py)."""
import logging
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.output import State
from sc_digger.web.today import (
    GATES_HREF,
    UNRATED_HREF,
    WATCH_HREF,
    TodayItem,
    _count_watch,
    collect_today,
)

ROOT = Path(__file__).resolve().parents[1]


def _make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    cfg.raw.setdefault("download", {})["inbox_dir"] = str(inbox)
    return cfg


def test_today_item_frozen():
    item = TodayItem("unrated", 1, "1 Vorschlag unbewertet", UNRATED_HREF)
    with pytest.raises(FrozenInstanceError):
        item.count = 2  # type: ignore[misc]


def test_singular_beschriftungen(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    State(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("INSERT INTO seen (id, url, title) VALUES (1, 'https://sc.com/1', 'Titel 1')")

    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        pass
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute(
            "INSERT INTO gate_items (sc_id, title, artist, gate_url) VALUES (1, 't', 'a', 'https://gate.com')"
        )
        db.execute(
            "INSERT INTO availability_watch VALUES (99, '2026-10-01 10:00:00', "
            "'2026-11-01 10:00:00', 'direct', '2026-11-01 10:00:00')"
        )

    items = collect_today(cfg)
    assert len(items) == 3
    assert items[0] == TodayItem("unrated", 1, "1 Vorschlag unbewertet", UNRATED_HREF)
    assert items[1] == TodayItem("gates", 1, "1 offenes Gate", GATES_HREF)
    assert items[2] == TodayItem("watch", 1, "1 Track neu verfügbar", WATCH_HREF)


def test_plural_beschriftungen(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    State(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        for i in (1, 2):
            db.execute("INSERT INTO seen (id, url, title) VALUES (?, ?, ?)", (i, f"https://sc.com/{i}", f"Titel {i}"))

    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        pass
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        for i in (1, 2):
            db.execute(
                "INSERT INTO gate_items (sc_id, title, artist, gate_url) VALUES (?, 't', 'a', 'https://gate.com')",
                (i,),
            )
            db.execute(
                "INSERT INTO availability_watch VALUES (?, '2026-10-01 10:00:00', "
                "'2026-11-01 10:00:00', 'direct', '2026-11-01 10:00:00')",
                (i + 10,),
            )

    items = collect_today(cfg)
    assert items[0].label == "2 Vorschläge unbewertet"
    assert items[1].label == "2 offene Gates"
    assert items[2].label == "2 Tracks neu verfügbar"


def test_watch_filtert_leere_zeitstempel_und_vorhandene_tracks(tmp_path: Path):
    track_db_path = tmp_path / "tracks.sqlite"
    with TrackDB(track_db_path) as tdb:
        tdb.upsert_track(TrackRecord(path=str(tmp_path / "t1.wav"), mtime=1.0, size=1, sc_id=10, artist="A", title="T"))

    with sqlite3.connect(track_db_path) as db:
        # sc_id 10 ist bereits in tracks
        db.execute(
            "INSERT INTO availability_watch VALUES (10, '2026-10-01', '2026-11-01', 'direct', '2026-11-01 10:00:00')"
        )
        # sc_id 20 hat available_at = '' (leer)
        db.execute("INSERT INTO availability_watch VALUES (20, '2026-10-01', '2026-11-01', 'none', '')")
        # sc_id 30 hat available_at IS NULL
        db.execute("INSERT INTO availability_watch VALUES (30, '2026-10-01', '2026-11-01', 'none', NULL)")
        # sc_id 40 ist neu verfügbar und nicht in tracks
        db.execute(
            "INSERT INTO availability_watch VALUES (40, '2026-10-01', '2026-11-01', 'direct', '2026-11-01 10:00:00')"
        )

    assert _count_watch(track_db_path) == 1


def test_watch_ohne_tabelle_oder_datei(tmp_path: Path):
    assert _count_watch(tmp_path / "nicht_da.sqlite") == 0

    leere_db = tmp_path / "leer.sqlite"
    with sqlite3.connect(leere_db) as db:
        db.execute("CREATE TABLE foo (id INT)")
    assert _count_watch(leere_db) == 0


def test_quelle_scheitert_loggt_warning_und_unterbricht_nicht(tmp_path: Path, caplog):
    cfg = _make_cfg(tmp_path)
    with TrackDB(tmp_path / "tracks.sqlite"):
        pass
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute(
            "INSERT INTO availability_watch VALUES (50, '2026-10-01', '2026-11-01', 'direct', '2026-11-01 10:00:00')"
        )

    with caplog.at_level(logging.WARNING):
        # query_digest wirft für unrated, watch soll trotzdem durchlaufen
        with patch("sc_digger.web.today.query_digest", side_effect=sqlite3.OperationalError("disk I/O error")):
            items = collect_today(cfg)

    # unrated und gates sind ausgefallen, watch ist da
    assert len(items) == 1
    assert items[0].key == "watch"
    assert items[0].count == 1

    # Warning muss genau dem Muster entsprechen: "Heute-Liste: Quelle <key> nicht lesbar (<Ausnahmetyp>)"
    # ohne Pfade, ohne Fehlertext
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert "Heute-Liste: Quelle unrated nicht lesbar (OperationalError)" in warnings
    assert "Heute-Liste: Quelle gates nicht lesbar (OperationalError)" in warnings
    for w in warnings:
        assert "disk I/O error" not in w
        assert str(tmp_path) not in w
