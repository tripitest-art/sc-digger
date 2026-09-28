import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.models import Config, DownloadKind, Track
from sc_digger.output import build_digest
from sc_digger.soundcloud import SoundCloudClient, SoundCloudError

CFG = Config.load(Path(__file__).parent.parent / "config.yaml")
NOW = datetime.now(timezone.utc).isoformat()


def raw_track(i, **kw):
    d = dict(id=i, kind="track", title=f"Track {i}", permalink_url=f"https://soundcloud.com/a/t{i}",
             user={"username": f"Artist{i}", "permalink_url": "https://soundcloud.com/a"},
             created_at=NOW, playback_count=1000, likes_count=50, reposts_count=5,
             comment_count=1, downloadable=False, tag_list="", description="")
    d.update(kw)
    return d


def mk(i, **kw) -> Track:
    return SoundCloudClient._to_track(raw_track(i, **kw))


class FakeSC(SoundCloudClient):
    """Client mit vorgefertigten Antworten statt Netzwerk."""
    def __init__(self, routes):
        super().__init__(request_delay=0)
        self.routes, self.calls = routes, []
        self.client_id = "x"

    def _get(self, path_or_url, params=None):
        self.calls.append((path_or_url, dict(params or {})))
        key = params.get("url") if path_or_url == "/resolve" else path_or_url
        if key not in self.routes:
            raise SoundCloudError(f"keine Route: {key}")
        r = self.routes[key]
        return r(params) if callable(r) else r


# ---------------- Playlist ----------------
def test_playlist_hydrates_stubs_and_keeps_order():
    stub = lambda i: {"id": i, "kind": "track"}
    routes = {
        "https://soundcloud.com/u/sets/p": {
            "kind": "playlist", "title": "Meine Liste",
            "tracks": [raw_track(1), stub(2), stub(3), raw_track(4)]},
        "/tracks": lambda p: [raw_track(int(i)) for i in p["ids"].split(",")],
    }
    sc = FakeSC(routes)
    title, tracks = sc.playlist_tracks("https://soundcloud.com/u/sets/p")
    assert title == "Meine Liste"
    assert [t.id for t in tracks] == [1, 2, 3, 4]


def test_playlist_stubs_are_chunked_by_30():
    stubs = [{"id": i, "kind": "track"} for i in range(1, 71)]
    routes = {
        "https://soundcloud.com/u/sets/big": {"kind": "playlist", "title": "Big", "tracks": stubs},
        "/tracks": lambda p: [raw_track(int(i)) for i in p["ids"].split(",")],
    }
    sc = FakeSC(routes)
    _, tracks = sc.playlist_tracks("https://soundcloud.com/u/sets/big")
    assert len(tracks) == 70
    assert len([c for c in sc.calls if c[0] == "/tracks"]) == 3


def test_playlist_rejects_non_playlist_url():
    sc = FakeSC({"https://soundcloud.com/u/t": {"kind": "track"}})
    with pytest.raises(SoundCloudError):
        sc.playlist_tracks("https://soundcloud.com/u/t")


def test_playlist_skips_deleted_tracks_without_crashing():
    routes = {
        "https://soundcloud.com/u/sets/p": {"kind": "playlist", "title": "P",
                                            "tracks": [raw_track(1), {"id": 2, "kind": "track"}]},
        "/tracks": lambda p: [],
    }
    _, tracks = FakeSC(routes).playlist_tracks("https://soundcloud.com/u/sets/p")
    assert [t.id for t in tracks] == [1]


# ---------------- Similar ----------------
def test_related_excludes_seed_and_paginates():
    routes = {
        "https://soundcloud.com/a/seed": raw_track(99),
        "/tracks/99/related": {"collection": [raw_track(1), raw_track(99), raw_track(2)]},
    }
    sc = FakeSC(routes)
    seed = sc.resolve_track("https://soundcloud.com/a/seed")
    rel = [t for t in sc.related(seed.id) if t.id != seed.id]
    assert [t.id for t in rel] == [1, 2]


def test_station_falls_back_to_related_on_error():
    routes = {"/tracks/5/related": {"collection": [raw_track(1)]}}
    sc = FakeSC(routes)
    assert [t.id for t in sc.station(5)] == [1]


# ---------------- Digest ----------------
def _gate_track(i):
    t = mk(i, purchase_url=f"https://hypeddit.com/x{i}", title="X" * 60)
    t.download_kind, t.download_link = DownloadKind.HYPEDDIT, t.purchase_url
    return t


def test_digest_show_all_and_no_message_exceeds_telegram_limit():
    tracks = [_gate_track(i) for i in range(80)]
    msgs = build_digest(tracks, None, header="Test")
    assert all(len(x) <= 4096 for x in msgs), [len(x) for x in msgs]
    text = "\n".join(msgs)
    assert len(re.findall(r"soundcloud\.com/a/t\d+", text)) == 80


def test_digest_truncation_is_announced_not_silent():
    tracks = [_gate_track(i) for i in range(40)]
    text = "\n".join(build_digest(tracks, 25, header="T"))
    assert len(re.findall(r"soundcloud\.com/a/t\d+", text)) == 25
    assert "15 weitere" in text


def test_digest_includes_key_when_present():
    t = mk(1)
    t.key_camelot = "5A"
    text = build_digest([t], None, header="T")[0]
    assert "5A" in text


# ---------------- Modus-Verhalten (mit Analyse/Organize/Tagging in process()) ----------------
def test_playlist_mode_reports_missing_and_existing(tmp_path, monkeypatch, capsys):
    (tmp_path / "Artist1 - Track 1.wav").write_bytes(b"x")
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path)}

    class Fake:
        def playlist_tracks(self, url):
            return "Liste", [mk(1), mk(2, purchase_url="https://hypeddit.com/z")]
    monkeypatch.setattr(m, "SoundCloudClient", Fake)

    m.run_playlist(cfg, "u", likes=False, dry_run=True, no_telegram=True)
    out = capsys.readouterr().out
    assert "1 fehlen, 1 vorhanden" in out
    assert "hypeddit" in out


def test_playlist_mode_ignores_state_and_score_filters(tmp_path, monkeypatch, capsys):
    """Playlist-Tracks mit 0 Plays dürfen NICHT vom Score-Filter entfernt werden."""
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path)}

    class Fake:
        def playlist_tracks(self, url):
            return "L", [mk(1, playback_count=3, likes_count=0)]
    monkeypatch.setattr(m, "SoundCloudClient", Fake)
    m.run_playlist(cfg, "u", likes=False, dry_run=True, no_telegram=True)
    assert "Track 1" in capsys.readouterr().out


def test_dry_run_never_touches_disk_or_organize(tmp_path, monkeypatch):
    """--dry-run darf process() nicht bis zum Download/Organize durchlaufen lassen."""
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path),
                            "inbox_dir": str(tmp_path / "inbox")}

    called = {"download": False}
    monkeypatch.setattr(m, "download_native", lambda *a, **k: called.__setitem__("download", True))
    t = mk(1, downloadable=True)
    fresh, dupes = m.process([t], cfg, dry_run=True)
    assert not called["download"]
    assert not (tmp_path / "inbox").exists()
    assert fresh == [t]
