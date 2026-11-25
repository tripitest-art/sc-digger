"""Akzeptanztests: Heute-Liste zeigt, was seit dem letzten Besuch neu ist (Issue <N>)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.output import State
from sc_digger.web.app import create_app
from sc_digger.web.today import VISIT_COOKIE, VISIT_GAP_S, TodayItem, collect_today, next_visit_state

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-besuch"


def test_erster_besuch_hat_keinen_vergleich():
    assert next_visit_state(None, datetime(2026, 11, 25, 8, 0, 0)) == (None, "|2026-11-25 08:00:00")
    assert next_visit_state("kaputt", datetime(2026, 11, 25, 8, 0, 0)) == (None, "|2026-11-25 08:00:00")


def test_innerhalb_eines_besuchs_bleibt_der_vergleich_stehen():
    assert VISIT_GAP_S == 1800
    assert next_visit_state("|2026-11-25 08:00:00", datetime(2026, 11, 25, 8, 10, 0)) == (
        None, "|2026-11-25 08:10:00")
    assert next_visit_state("2026-11-24 20:00:00|2026-11-25 08:00:00", datetime(2026, 11, 25, 8, 29, 0)) == (
        "2026-11-24 20:00:00", "2026-11-24 20:00:00|2026-11-25 08:29:00")


def test_nach_einer_pause_beginnt_ein_neuer_besuch():
    assert next_visit_state("2026-11-24 20:00:00|2026-11-25 08:00:00", datetime(2026, 11, 25, 9, 0, 0)) == (
        "2026-11-25 08:00:00", "2026-11-25 08:00:00|2026-11-25 09:00:00")


def _cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    cfg.raw.setdefault("download", {})["inbox_dir"] = str(inbox)
    return cfg


def _seed(tmp_path: Path) -> None:
    State(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        for sc_id, gemeldet in [(1, "2026-11-20 07:30:00"), (2, "2026-11-20 07:30:00"),
                                (3, "2026-11-25 07:30:00"), (4, "2026-11-25 07:30:00")]:
            db.execute("INSERT INTO seen (id, url, title, reported_at) VALUES (?, ?, ?, ?)",
                       (sc_id, f"https://soundcloud.com/x/{sc_id}", f"A{sc_id} - T{sc_id}", gemeldet))
    TrackDB(tmp_path / "tracks.sqlite").close()
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        for sc_id in (2, 4):
            db.execute("INSERT INTO gate_items (sc_id, title, artist, gate_url) VALUES (?, 't', 'a', ?)",
                       (sc_id, "https://hypeddit.com/x"))
        for sc_id, verfuegbar in [(100, "2026-11-20 10:00:00"), (101, "2026-11-25 06:00:00")]:
            db.execute("INSERT INTO availability_watch VALUES (?, '2026-10-01 10:00:00', "
                       "'2026-11-01 10:00:00', 'direct', ?)", (sc_id, verfuegbar))


def test_collect_today_zaehlt_neu_seit(tmp_path):
    _seed(tmp_path)
    cfg = _cfg(tmp_path)
    ohne = collect_today(cfg)
    assert [(i.key, i.count, i.new) for i in ohne] == [("unrated", 4, 0), ("gates", 2, 0), ("watch", 2, 0)]

    mit = collect_today(cfg, since="2026-11-24 12:00:00")
    assert [(i.key, i.count, i.new) for i in mit] == [("unrated", 4, 2), ("gates", 2, 1), ("watch", 2, 1)]
    assert mit[0] == TodayItem("unrated", 4, "4 Vorschläge unbewertet", "/digest?status=open&feedback=none", 2)


def test_startseite_zeigt_neu_seit_dem_letzten_besuch(tmp_path):
    _seed(tmp_path)
    client = TestClient(create_app(_cfg(tmp_path), password=PASSWORD))
    client.cookies.set(VISIT_COOKIE, "2026-11-24 12:00:00|2026-11-24 13:00:00")
    resp = client.get("/", auth=("sc", PASSWORD))
    html = resp.text
    assert 'data-today-new="unrated"' in html
    assert "2 neu seit deinem letzten Besuch" in html
    assert "1 neu seit deinem letzten Besuch" in html
    assert VISIT_COOKIE in resp.headers.get("set-cookie", "")


def test_erster_besuch_zeigt_keine_neu_angabe_und_setzt_den_cookie(tmp_path):
    _seed(tmp_path)
    resp = TestClient(create_app(_cfg(tmp_path), password=PASSWORD)).get("/", auth=("sc", PASSWORD))
    assert "data-today-new=" not in resp.text
    cookie = resp.headers.get("set-cookie", "")
    assert cookie.startswith(f"{VISIT_COOKIE}=")
    assert "httponly" in cookie.lower()
