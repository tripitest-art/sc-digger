"""Akzeptanztests Issue #5: DJ-Sets in discover aussortieren, sonst nur markieren."""
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from sc_digger import main as m
from sc_digger.models import Config
from sc_digger.output import build_digest
from sc_digger.pipeline import filter_sets, is_dj_set, mark_sets
from sc_digger.soundcloud import SoundCloudClient

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(timezone.utc).isoformat()
MIN = 60_000


def mk(i: int, title: str, minutes: float):
    return SoundCloudClient._to_track(dict(
        id=i, kind="track", title=title, permalink_url=f"https://soundcloud.com/a/t{i}",
        user={"username": f"Artist{i}", "permalink_url": "https://soundcloud.com/a"},
        created_at=NOW, playback_count=1000, likes_count=50, reposts_count=5, comment_count=1,
        downloadable=False, tag_list="", description="", duration=int(minutes * MIN)))


def cfg(**search) -> Config:
    return Config({"search": search})


SESSION = lambda: mk(1, "Hardtechno Schranz Session", 90)
NOSS = lambda: mk(2, "NØSS – Join me (165bpm schranz mix)", 5)


# ---------------- Erkennung: nur die Dauer entscheidet ----------------
def test_long_session_is_a_set():
    assert is_dj_set(SESSION(), 12) is True


def test_mix_in_title_alone_is_not_a_set():
    assert is_dj_set(NOSS(), 12) is False
    assert is_dj_set(mk(3, "Some Track (Extended Mix)", 9), 12) is False
    assert is_dj_set(mk(4, "Live Set @ Bunker", 6), 12) is False


def test_boundary_is_strictly_longer():
    assert is_dj_set(mk(5, "x", 12), 12) is False
    assert is_dj_set(mk(6, "x", 12 + 1 / 60), 12) is True


def test_unknown_duration_is_not_a_set():
    assert is_dj_set(mk(7, "Mystery Session", 0), 12) is False


# ---------------- discover: aussortieren ----------------
def test_filter_sets_drops_sets_keeps_order_and_logs(caplog):
    tracks = [NOSS(), SESSION(), mk(3, "B", 6), mk(4, "Podcast 042", 58)]
    with caplog.at_level(logging.INFO, logger="sc_digger.pipeline"):
        kept = filter_sets(tracks, cfg(max_duration_min=12))
    assert [t.id for t in kept] == [2, 3]
    assert "DJ-Sets aussortiert: 2" in caplog.text


def test_filter_sets_default_is_12_minutes():
    assert [t.id for t in filter_sets([mk(1, "a", 11), mk(2, "b", 13)], cfg())] == [1]


def test_filter_sets_respects_config():
    assert [t.id for t in filter_sets([mk(1, "Podcast", 58)], cfg(max_duration_min=60))] == [1]


def test_config_has_option_with_default_12():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["search"]["max_duration_min"] == 12


# ---------------- andere Modi: nur markieren ----------------
def test_mark_sets_keeps_everything_and_sets_minutes():
    tracks = [NOSS(), mk(4, "Podcast 042", 58)]
    out = mark_sets(tracks, cfg(max_duration_min=12))
    assert [t.id for t in out] == [2, 4]
    assert out[0].set_minutes is None
    assert out[1].set_minutes == 58


def test_digest_shows_set_marker():
    s, n = mark_sets([mk(4, "Podcast 042", 58), NOSS()], cfg(max_duration_min=12))
    text = build_digest([s, n], None, header="T")[0]
    assert "🎛️ Set, 58 min" in text
    assert text.count("🎛️") == 1


def test_process_marks_sets_without_dropping(tmp_path):
    c = Config.load(ROOT / "config.yaml")
    c.raw["download"] = {**c["download"], "collection_dir": str(tmp_path)}
    fresh, dupes = m.process([mk(4, "Podcast 042", 58), NOSS()], c, dry_run=True)
    assert [t.id for t in fresh] == [4, 2]
    assert fresh[0].set_minutes == 58
    assert fresh[1].set_minutes is None


# ---------------- Einbindung in discover ----------------
def test_discover_never_hands_sets_to_process(tmp_path, monkeypatch):
    class FakeSC:
        def search_tag(self, tag, max_age, limit):
            return [NOSS(), SESSION(), mk(3, "B 155 bpm", 6)]

        def user_uploads(self, *a, **k):
            return []

        def reference_activity(self, *a, **k):
            return []

    seen: list[int] = []

    def fake_process(tracks, cfg, **kw):
        seen.extend(t.id for t in tracks)
        return [], []

    c = Config.load(ROOT / "config.yaml")
    c.raw["state"] = {**c["state"], "db_path": str(tmp_path / "state.sqlite")}
    monkeypatch.setattr(m, "SoundCloudClient", FakeSC)
    monkeypatch.setattr(m, "score_tracks", lambda tracks, cfg, **kw: tracks)
    monkeypatch.setattr(m, "process", fake_process)
    monkeypatch.setattr(m, "deliver", lambda *a, **k: None)

    m._discover(c, dry_run=True, no_telegram=True)
    assert 1 not in seen
    assert 2 in seen and 3 in seen
