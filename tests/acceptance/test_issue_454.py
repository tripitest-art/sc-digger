"""Akzeptanztests B1: Verfügbarkeits-Wächter für 👍-Tracks."""
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from sc_digger import output
from sc_digger import watch
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config, DownloadKind, Track

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 1, 4, 19, 0, 0)
T1 = datetime(2026, 1, 11, 19, 0, 0)
T2 = datetime(2026, 1, 18, 19, 0, 0)


def _sc(sc_id: int, *, description: str = "", downloadable: bool = False,
        purchase_url: str | None = None, title: str | None = None) -> Track:
    return Track(id=sc_id, title=title or f"Track {sc_id}",
                 url=f"https://soundcloud.com/artist/track-{sc_id}", artist="Artist",
                 artist_url="https://soundcloud.com/artist", created_at="2026-01-01T00:00:00Z",
                 duration_ms=300000, genre="Schranz", tags=[], description=description,
                 bpm=None, plays=100, likes=10, reposts=1, comments=0,
                 downloadable=downloadable, has_downloads_left=downloadable,
                 purchase_url=purchase_url, purchase_title=None)


class FakeFetch:
    def __init__(self, tracks: dict[int, Track] | None = None):
        self.tracks = tracks or {}
        self.calls: list[list[int]] = []

    def __call__(self, ids: list[int]) -> list[Track]:
        self.calls.append(list(ids))
        return [self.tracks[i] for i in ids if i in self.tracks]


def _db(tmp_path: Path) -> TrackDB:
    db = TrackDB(tmp_path / "tracks.sqlite")
    db.set_sc_feedback(101, "like", "https://soundcloud.com/artist/track-101")
    db.set_sc_feedback(102, "like", "https://soundcloud.com/artist/track-102")
    db.set_sc_feedback(103, "dislike", "https://soundcloud.com/artist/track-103")
    db.set_sc_feedback(104, "like", "https://soundcloud.com/artist/track-104")
    db.upsert_track(TrackRecord(path="/inbox/104.flac", mtime=1.0, size=1, sc_id=104,
                                status="inbox"))
    return db


def test_erste_pruefung_meldet_nichts_und_waehlt_nur_offene_likes(tmp_path: Path):
    fetch = FakeFetch({101: _sc(101), 102: _sc(102)})
    with _db(tmp_path) as db:
        changes = watch.check_availability(db, fetch, now=T0)
    assert changes == []
    assert fetch.calls == [[101, 102]]


def test_wechsel_von_stream_auf_gate_wird_einmal_gemeldet(tmp_path: Path):
    gate = "https://hypeddit.com/artist/track-101"
    with _db(tmp_path) as db:
        watch.check_availability(db, FakeFetch({101: _sc(101), 102: _sc(102)}), now=T0)
        fetch = FakeFetch({101: _sc(101, description=f"Free DL: {gate}"), 102: _sc(102)})
        changes = watch.check_availability(db, fetch, now=T1)
        assert len(changes) == 1
        c = changes[0]
        assert c.sc_id == 101
        assert c.kind == DownloadKind.HYPEDDIT
        assert c.link == gate
        assert c.url == "https://soundcloud.com/artist/track-101"
        assert c.title == "Track 101"
        later = FakeFetch({101: _sc(101, description=f"Free DL: {gate}"), 102: _sc(102)})
        assert watch.check_availability(db, later, now=T2) == []
    assert later.calls == [[102]]


def test_freier_download_wird_als_native_gemeldet(tmp_path: Path):
    with _db(tmp_path) as db:
        watch.check_availability(db, FakeFetch({101: _sc(101), 102: _sc(102)}), now=T0)
        changes = watch.check_availability(
            db, FakeFetch({101: _sc(101, downloadable=True), 102: _sc(102)}), now=T1)
    assert [(c.sc_id, c.kind, c.link) for c in changes] == [
        (101, DownloadKind.NATIVE, "https://soundcloud.com/artist/track-101")]


def test_schon_beim_ersten_mal_verfuegbar_wird_nicht_gemeldet_und_nicht_mehr_geprueft(tmp_path: Path):
    store = _sc(101, purchase_url="https://artist.bandcamp.com/track/track-101")
    with _db(tmp_path) as db:
        assert watch.check_availability(db, FakeFetch({101: store, 102: _sc(102)}), now=T0) == []
        fetch = FakeFetch({101: store, 102: _sc(102)})
        assert watch.check_availability(db, fetch, now=T1) == []
    assert fetch.calls == [[102]]


def test_nach_max_age_days_wird_nicht_mehr_geprueft(tmp_path: Path):
    with _db(tmp_path) as db:
        watch.check_availability(db, FakeFetch({101: _sc(101), 102: _sc(102)}), now=T0)
        fetch = FakeFetch({101: _sc(101), 102: _sc(102)})
        changes = watch.check_availability(db, fetch, now=datetime(2026, 7, 5, 19, 0, 0),
                                           max_age_days=180)
    assert changes == []
    assert fetch.calls == []


def test_geloeschter_track_bleibt_im_wachter(tmp_path: Path):
    with _db(tmp_path) as db:
        watch.check_availability(db, FakeFetch({102: _sc(102)}), now=T0)
        fetch = FakeFetch({101: _sc(101, downloadable=True), 102: _sc(102)})
        changes = watch.check_availability(db, fetch, now=T1)
    assert fetch.calls == [[101, 102]]
    assert [c.sc_id for c in changes] == [101]


def test_fetch_fehler_wird_weitergereicht_und_nichts_geschrieben(tmp_path: Path):
    def boom(ids):
        raise RuntimeError("SoundCloud weg")

    with _db(tmp_path) as db:
        with pytest.raises(RuntimeError):
            watch.check_availability(db, boom, now=T0)
        fetch = FakeFetch({101: _sc(101, downloadable=True), 102: _sc(102)})
        assert watch.check_availability(db, fetch, now=T1) == []


def test_nachricht_format():
    assert watch.format_available_message([]) == []
    change = watch.AvailabilityChange(
        sc_id=1, title="Rave & Roll", artist="DJ <X>",
        url="https://soundcloud.com/x/rave", kind=DownloadKind.CLOUD,
        link="https://drive.google.com/file/d/abc?x=1&y=2")
    msgs = watch.format_available_message([change])
    assert len(msgs) == 1
    assert msgs[0].startswith("🔓 <b>Jetzt verfügbar</b>")
    assert "DJ &lt;X&gt; – Rave &amp; Roll" in msgs[0]
    assert "cloud: https://drive.google.com/file/d/abc?x=1&amp;y=2" in msgs[0]


def test_nachricht_wird_geteilt():
    changes = [watch.AvailabilityChange(sc_id=i, title="T" * 150, artist="A", url="u",
                                        kind=DownloadKind.STORE, link="https://bandcamp.com/x")
               for i in range(60)]
    msgs = watch.format_available_message(changes)
    assert len(msgs) > 1
    assert all(len(m) <= 4000 for m in msgs)
    assert sum(m.count("• ") for m in msgs) == 60


def _cfg(tmp_path: Path, enabled: bool = True) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["watch"] = {"enabled": enabled, "max_age_days": 180}
    return cfg


def _runs(tmp_path: Path) -> list[tuple]:
    con = sqlite3.connect(tmp_path / "seen.sqlite")
    try:
        return con.execute("SELECT mode, ok, found FROM runs ORDER BY id").fetchall()
    finally:
        con.close()


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(output, "telegram_call",
                        lambda token, method, **kw: calls.append(kw.get("json") or {}) or {})
    return calls


def test_run_watch_meldet_und_protokolliert(tmp_path: Path, sent):
    _db(tmp_path).close()
    cfg = _cfg(tmp_path)
    assert watch.run_watch(cfg, fetch=FakeFetch({101: _sc(101), 102: _sc(102)}), now=T0) == 0
    assert sent == []
    fetch = FakeFetch({101: _sc(101, downloadable=True), 102: _sc(102)})
    assert watch.run_watch(cfg, fetch=fetch, now=T1) == 0
    assert len(sent) == 1
    assert "Jetzt verfügbar" in sent[0]["text"]
    assert _runs(tmp_path) == [(watch.WATCH_MODE, 1, 2), (watch.WATCH_MODE, 1, 2)]


def test_run_watch_fehler_alarmiert_und_wirft_nicht(tmp_path: Path, sent):
    _db(tmp_path).close()

    def boom(ids):
        raise RuntimeError("client_id nicht ermittelbar")

    assert watch.run_watch(_cfg(tmp_path), fetch=boom, now=T0) == 1
    assert len(sent) == 1
    assert "Verfügbarkeits-Wächter fehlgeschlagen" in sent[0]["text"]
    assert _runs(tmp_path) == [(watch.WATCH_MODE, 0, 0)]


def test_run_watch_ausgeschaltet(tmp_path: Path, sent):
    _db(tmp_path).close()
    fetch = FakeFetch({101: _sc(101)})
    assert watch.run_watch(_cfg(tmp_path, enabled=False), fetch=fetch, now=T0) == 0
    assert fetch.calls == []
    assert sent == []
