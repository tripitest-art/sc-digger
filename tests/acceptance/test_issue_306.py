"""Akzeptanztests: Skript zum Verknüpfen der Sammlung mit SoundCloud (ohne Netzwerk)."""
import csv

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.soundcloud import SoundCloudClient, SoundCloudError
from scripts.link_sources import LinkStats, run_link
from tests.test_modes import FakeSC, mk, raw_track


def cand(i, title, artist="uploader"):
    return mk(
        i, title=title, permalink_url=f"https://soundcloud.com/{artist}/t{i}",
        user={"username": artist, "permalink_url": f"https://soundcloud.com/{artist}"},
    )


class FakeLinkSC:
    """Nur die Methoden, die run_link aufrufen darf."""

    def __init__(self, likes=None, playlists=None, search=None, broken=()):
        self.likes, self.playlists, self.search = likes or {}, playlists or {}, search or {}
        self.broken, self.queries = set(broken), []

    def likes_of_user(self, url, limit=200):
        if url in self.broken:
            raise SoundCloudError("kaputt")
        return "Likes", self.likes[url]

    def playlist_tracks(self, url, limit=None):
        if url in self.broken:
            raise SoundCloudError("kaputt")
        return "Liste", self.playlists[url]

    def search_tracks(self, query, limit=10):
        self.queries.append((query, limit))
        return self.search.get(query, [])


def seed(db):
    a = db.upsert_track(TrackRecord(path="/sammlung/Svetec - Acid Kick.wav", mtime=1.0, size=1, status="archive"))
    b = db.upsert_track(TrackRecord(path="/sammlung/Viper - Raw Groove.wav", mtime=1.0, size=1, status="archive"))
    c = db.upsert_track(TrackRecord(path="/eingang/Neu - Track.wav", mtime=1.0, size=1, status="inbox"))
    return a, b, c


def test_search_tracks_uses_search_endpoint_without_date_filter():
    seen = {}

    def route(params):
        seen.update(params)
        return {"collection": [raw_track(1), {"kind": "playlist", "id": 9}, raw_track(2)]}

    sc = FakeSC({"/search/tracks": route})
    out = sc.search_tracks("svetec acid kick", limit=5)
    assert [t.id for t in out] == [1, 2]
    assert seen["q"] == "svetec acid kick"
    assert "filter.created_at" not in seen


def test_dry_run_writes_report_but_not_the_db(tmp_path):
    report = tmp_path / "vorschlag.csv"
    with TrackDB(tmp_path / "t.sqlite") as db:
        a, b, c = seed(db)
        sc = FakeLinkSC(likes={"https://soundcloud.com/me": [cand(1, "Svetec - Acid Kick")]})
        stats = run_link(sc, db, likes=["https://soundcloud.com/me"], report=report)
        assert isinstance(stats, LinkStats)
        assert (stats.records, stats.candidates, stats.proposals, stats.applied) == (2, 1, 1, 0)
        assert not db.get_track_by_id(a.id).source_url
    rows = list(csv.DictReader(report.open(encoding="utf-8"), delimiter=";"))
    assert [(r["record_id"], r["url"], r["ambiguous"], r["quelle"]) for r in rows] == [
        (str(a.id), "https://soundcloud.com/uploader/t1", "False", "likes/playlist")
    ]


def test_apply_writes_sure_matches_and_skips_inbox(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a, b, c = seed(db)
        sc = FakeLinkSC(playlists={"https://soundcloud.com/me/sets/p": [
            cand(1, "Svetec - Acid Kick"), cand(3, "Neu - Track")]})
        stats = run_link(sc, db, playlists=["https://soundcloud.com/me/sets/p"], apply=True)
        assert stats.applied == 1
        assert db.get_track_by_id(a.id).source_url == "https://soundcloud.com/uploader/t1"
        assert not db.get_track_by_id(c.id).source_url


def test_search_only_for_records_without_proposal_and_always_unsure(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a, b, c = seed(db)
        sc = FakeLinkSC(
            likes={"https://soundcloud.com/me": [cand(1, "Svetec - Acid Kick")]},
            search={"Viper - Raw Groove": [cand(2, "Viper - Raw Groove")]},
        )
        stats = run_link(sc, db, likes=["https://soundcloud.com/me"], search=True, search_limit=3, apply=True)
        assert sc.queries == [("Viper - Raw Groove", 3)]
        assert stats.proposals == 2
        assert stats.ambiguous == 1
        assert stats.applied == 1  # Suchtreffer sind unsicher und ohne include_ambiguous nicht geschrieben
        assert db.get_track_by_id(a.id).source_url
        assert not db.get_track_by_id(b.id).source_url
        stats = run_link(sc, db, search=True, apply=True, include_ambiguous=True)
        assert stats.applied == 1
        assert db.get_track_by_id(b.id).source_url == "https://soundcloud.com/uploader/t2"


def test_broken_source_is_skipped_not_fatal(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a, b, c = seed(db)
        sc = FakeLinkSC(
            likes={"https://soundcloud.com/ok": [cand(1, "Svetec - Acid Kick")]},
            broken=["https://soundcloud.com/kaputt"],
        )
        stats = run_link(sc, db, likes=["https://soundcloud.com/kaputt", "https://soundcloud.com/ok"], apply=True)
        assert stats.applied == 1
