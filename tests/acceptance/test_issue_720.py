"""Akzeptanztests: Heute-Liste zählt die mit ⏳ gemerkten Vorschläge (Rest von #594, Teil 2) (Issue <N>)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import sc_digger.bot as bot
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.output import State
from sc_digger.web.app import create_app
from sc_digger.web.today import LATER_HREF, TodayItem, collect_today

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-gemerkt"


def _cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    cfg.raw.setdefault("download", {})["inbox_dir"] = str(inbox)
    cfg.raw.setdefault("web", {})["public_url"] = ""
    return cfg


def _seed(tmp_path: Path) -> None:
    State(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        for sc_id, gemeldet in [(1, "2026-11-20 07:30:00"), (2, "2026-11-20 07:30:00"),
                                (3, "2026-11-25 07:30:00"), (4, "2026-11-25 07:30:00")]:
            db.execute("INSERT INTO seen (id, url, title, reported_at) VALUES (?, ?, ?, ?)",
                       (sc_id, f"https://soundcloud.com/x/{sc_id}", f"A{sc_id} - T{sc_id}", gemeldet))
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        # 2 liegt schon in der Inbox und ist damit nicht mehr offen
        tdb.upsert_track(TrackRecord(path=str(tmp_path / "inbox" / "[2] A2 - T2.wav"), mtime=1.0, size=1,
                                     sc_id=2, status="inbox", artist="A2", title="T2"))
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        for sc_id, wert in [(1, "later"), (2, "later"), (3, "later"), (4, "like")]:
            db.execute("INSERT INTO sc_feedback (sc_id, url, value) VALUES (?, 'u', ?)", (sc_id, wert))


def test_gemerkte_offene_vorschlaege_als_letzter_eintrag(tmp_path):
    _seed(tmp_path)
    items = collect_today(_cfg(tmp_path))
    assert items[-1] == TodayItem("later", 2, "2 für später gemerkt", LATER_HREF, 0)
    assert LATER_HREF == "/digest?status=open&feedback=later"


def test_neu_seit_dem_letzten_besuch(tmp_path):
    _seed(tmp_path)
    later = [i for i in collect_today(_cfg(tmp_path), since="2026-11-24 12:00:00") if i.key == "later"]
    assert [(i.count, i.new) for i in later] == [(2, 1)]


def test_ohne_gemerkte_kein_eintrag(tmp_path):
    _seed(tmp_path)
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute("DELETE FROM sc_feedback WHERE value = 'later'")
    assert "later" not in [i.key for i in collect_today(_cfg(tmp_path))]


def test_startseite_zeigt_den_eintrag(tmp_path):
    _seed(tmp_path)
    html = TestClient(create_app(_cfg(tmp_path), password=PASSWORD)).get("/", auth=("sc", PASSWORD)).text
    assert ('<a href="/digest?status=open&amp;feedback=later" class="btn-page" data-today="later">'
            '2 für später gemerkt</a>') in html


def test_heute_nachricht_in_der_einzahl(tmp_path, monkeypatch):
    items = [TodayItem("later", 1, "1 für später gemerkt", LATER_HREF)]
    monkeypatch.setattr(bot, "collect_today", lambda cfg, **kw: items)
    assert bot.HEUTE_SINGULAR["later"] == "für später gemerkt"
    assert "• 1 für später gemerkt → /digest?status=open&feedback=later" in bot.heute_reply(_cfg(tmp_path), "/heute")
