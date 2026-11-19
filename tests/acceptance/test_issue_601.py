"""Akzeptanztests: Heute-Liste auf der Startseite."""
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.output import State
from sc_digger.web.app import create_app
from sc_digger.web.today import GATES_HREF, UNRATED_HREF, WATCH_HREF, TodayItem, collect_today

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-heute"


def _cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    cfg.raw.setdefault("download", {})["inbox_dir"] = str(inbox)
    return cfg


def _seed(tmp_path, *, vorschlaege=True, gates=True, waechter=True):
    inbox = tmp_path / "inbox"
    State(tmp_path / "seen.sqlite").db.close()
    if vorschlaege:
        with sqlite3.connect(tmp_path / "seen.sqlite") as db:
            for sc_id, titel in [(1, "A - Eins"), (2, "B - Zwei"), (3, "C - Drei"), (4, "D - Vier")]:
                db.execute(
                    "INSERT INTO seen (id, url, title) VALUES (?, ?, ?)",
                    (sc_id, f"https://soundcloud.com/x/{sc_id}", titel),
                )
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        # 4 liegt schon in der Inbox (nicht offen), 101 ist ein geladener Wächter-Track
        tdb.upsert_track(TrackRecord(path=str(inbox / "[4] D - Vier.wav"), mtime=1.0, size=1,
                                     sc_id=4, status="inbox", artist="D", title="Vier"))
        tdb.upsert_track(TrackRecord(path=str(inbox / "[101] E - Fünf.wav"), mtime=1.0, size=1,
                                     sc_id=101, status="inbox", artist="E", title="Fünf"))
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute("INSERT INTO sc_feedback (sc_id, url, value) VALUES (2, 'u', 'like')")
        if gates:
            for sc_id in (3, 4):
                db.execute(
                    "INSERT INTO gate_items (sc_id, title, artist, gate_url) VALUES (?, 't', 'a', ?)",
                    (sc_id, "https://hypeddit.com/x"),
                )
        if waechter:
            for sc_id, verfuegbar in [(100, "2026-11-01 10:00:00"), (101, "2026-11-01 10:00:00"),
                                      (102, None)]:
                db.execute(
                    "INSERT INTO availability_watch VALUES (?, '2026-10-01 10:00:00', "
                    "'2026-11-01 10:00:00', ?, ?)",
                    (sc_id, "direct" if verfuegbar else "none", verfuegbar),
                )


def test_ohne_datenbanken_leer_und_ohne_neue_dateien(tmp_path):
    cfg = _cfg(tmp_path)
    assert collect_today(cfg) == []
    assert not (tmp_path / "seen.sqlite").exists()
    assert not (tmp_path / "tracks.sqlite").exists()


def test_zaehlt_unbewertete_gates_und_neu_verfuegbare(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    assert collect_today(cfg) == [
        TodayItem("unrated", 2, "2 Vorschläge unbewertet", UNRATED_HREF),
        TodayItem("gates", 1, "1 offenes Gate", GATES_HREF),
        TodayItem("watch", 1, "1 Track neu verfügbar", WATCH_HREF),
    ]


def test_sprungziele():
    assert UNRATED_HREF == "/digest?status=open&feedback=none"
    assert GATES_HREF == "/digest?download_kind=gate&status=open"
    assert WATCH_HREF == "/kaufliste#watch"


def test_eintraege_mit_null_entfallen(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path, gates=False, waechter=False)
    assert [item.key for item in collect_today(cfg)] == ["unrated"]


def test_startseite_zeigt_die_karte_oben(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    html = TestClient(create_app(cfg, password=PASSWORD)).get("/", auth=("sc", PASSWORD)).text
    karte = html.index('id="heute"')
    assert karte < html.index('class="status-hero"')
    assert "Heute zu tun" in html
    for key, href, label in [
        ("unrated", "/digest?status=open&amp;feedback=none", "2 Vorschläge unbewertet"),
        ("gates", "/digest?download_kind=gate&amp;status=open", "1 offenes Gate"),
        ("watch", "/kaufliste#watch", "1 Track neu verfügbar"),
    ]:
        assert f'<a href="{href}" class="btn-page" data-today="{key}">{label}</a>' in html


def test_startseite_ohne_offene_aufgaben(tmp_path):
    cfg = _cfg(tmp_path)
    html = TestClient(create_app(cfg, password=PASSWORD)).get("/", auth=("sc", PASSWORD)).text
    assert 'id="heute"' in html
    assert "Heute ist nichts offen." in html
    assert "data-today=" not in html


def test_waechter_karte_ist_sprungziel(tmp_path):
    cfg = _cfg(tmp_path)
    html = TestClient(create_app(cfg, password=PASSWORD)).get("/kaufliste", auth=("sc", PASSWORD)).text
    assert 'id="watch"' in html
