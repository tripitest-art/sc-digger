"""Akzeptanztests: Eingangsordner für manuell geladene Tracks (Prüfung, Analyse, Tags, Sortierung)."""
import os
import time
from pathlib import Path

import pytest
import yaml

import sc_digger.output as out
from sc_digger import main as m
from sc_digger.intake import find_ready_files, track_from_file
from sc_digger.models import Config
from sc_digger.rekordbox import scan_inbox
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")
OLD = time.time() - 3600
OK = {"ok": True, "clipped": False, "ext": "wav", "bitrate_kbps": 1411, "reason": "echte Qualität"}


def put(path: Path, age: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.utime(path, (age, age))
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    cfg = Config(dict(CFG.raw))
    cfg.raw["search"] = {**CFG["search"], "tags": ["schranz"], "followed_users": [],
                         "reference_accounts": []}
    cfg.raw["state"] = {**CFG["state"], "db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(inbox), "intake_dir": str(inbox / "_eingang"),
                           "intake_min_age_s": 120}
    cfg.raw["fingerprint"] = {"check_downloads": False}
    cfg.raw["rekordbox"] = {"xml_enabled": False}
    monkeypatch.setattr(out, "check_file", lambda p, cfg: dict(OK))
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 152.0, "key_camelot": "6A",
                                                        "key_name": "Gm"})
    tags = []
    monkeypatch.setattr(m, "write_tags", lambda p, **kw: tags.append(kw) or True)
    return cfg, inbox, inbox / "_eingang", tags


def test_config_defaults():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["download"]["intake_dir"] == "/music/inbox/_eingang"
    assert raw["download"]["intake_min_age_s"] == 120


def test_find_ready_files_skips_young_hidden_and_underscore_dirs(tmp_path):
    d = tmp_path / "_eingang"
    put(d / "a.wav")
    put(d / "b.MP3")
    put(d / "notes.txt")
    put(d / "sub" / "c.flac")
    put(d / "young.wav", age=time.time())
    put(d / ".hidden.wav")
    put(d / "_unbekannt" / "x.wav")
    got = [p.relative_to(d).as_posix() for p in find_ready_files(d, 120)]
    assert got == ["a.wav", "b.MP3", "notes.txt", "sub/c.flac"]
    assert find_ready_files(tmp_path / "fehlt", 120) == []


def test_track_from_file_uses_filename_without_tags(tmp_path):
    t = track_from_file(put(tmp_path / "Artist X - Title Y (Schranz Edit).wav"))
    assert (t.artist, t.title) == ("Artist X", "Title Y (Schranz Edit)")
    t = track_from_file(put(tmp_path / "nur_ein_name.mp3"))
    assert (t.artist, t.title) == ("", "nur_ein_name")


def test_good_file_is_analysed_tagged_and_sorted(env):
    cfg, inbox, intake, tags = env
    f = put(intake / "Artist - Tune.wav")
    lines = m.run_intake(cfg)
    assert not f.exists()
    assert list(inbox.glob("150-155/6A/Artist - Tune.wav"))
    assert tags and tags[0]["artist"] == "Artist" and tags[0]["title"] == "Tune"
    assert tags[0]["bpm"] == 152.0
    text = "\n".join(lines)
    assert "Artist – Tune" in text and "152" in text and "6A" in text


def test_fake_goes_to_rejected(env, monkeypatch):
    cfg, inbox, intake, _ = env
    monkeypatch.setattr(out, "check_file", lambda p, cfg: {**OK, "ok": False,
                                                            "reason": "Spektrum endet bei 16 kHz"})
    put(intake / "Fake - Upscale.wav")
    lines = m.run_intake(cfg)
    assert (inbox / "_rejected" / "Fake - Upscale.wav").exists()
    assert any("❌" in line and "Fake - Upscale.wav" in line for line in lines)


def test_non_audio_is_parked_once(env):
    cfg, inbox, intake, _ = env
    put(intake / "pack.zip")
    lines = m.run_intake(cfg)
    assert (intake / "_unbekannt" / "pack.zip").exists()
    assert any("pack.zip" in line for line in lines)
    assert m.run_intake(cfg) == []


def test_failed_check_is_parked_not_retried_forever(env, monkeypatch):
    cfg, inbox, intake, _ = env

    def boom(p, cfg):
        raise RuntimeError("ffprobe kaputt")
    monkeypatch.setattr(out, "check_file", boom)
    put(intake / "Artist - Kaputt.wav")
    lines = m.run_intake(cfg)
    assert (intake / "_fehler" / "Artist - Kaputt.wav").exists()
    assert any("Artist - Kaputt.wav" in line for line in lines)
    assert m.run_intake(cfg) == []


def test_young_files_and_dry_run_are_left_alone(env):
    cfg, inbox, intake, _ = env
    young = put(intake / "Artist - Kopiert gerade.wav", age=time.time())
    assert m.run_intake(cfg) == []
    assert young.exists()
    old = put(intake / "Artist - Alt.wav")
    m.run_intake(cfg, dry_run=True)
    assert old.exists()
    assert not list(inbox.glob("*/*/Artist - Alt.wav"))


def test_rekordbox_scan_ignores_intake(env):
    cfg, inbox, intake, _ = env
    put(intake / "Artist - Wartet.wav")
    put(inbox / "150-155" / "6A" / "Artist - Fertig.wav")
    names = [Path(t.path).name for t in scan_inbox(inbox)]
    assert names == ["Artist - Fertig.wav"]


def test_discover_runs_intake_and_reports_it(env, monkeypatch, capsys):
    cfg, *_ = env
    calls = []

    class SC:
        def resolve_track(self, url):
            raise AssertionError("keine Retry-Einträge erwartet")

        def search_tag(self, tag, age, limit):
            return [mk(i, playback_count=2000, likes_count=40 + i) for i in range(40)]

    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: SC())
    monkeypatch.setattr(m, "run_intake", lambda cfg, dry_run=False: calls.append(dry_run)
                        or ["📥 Eingang: 1 Datei verarbeitet"])
    m._discover(cfg, False, True)
    assert calls == [False]
    assert "📥 Eingang: 1 Datei verarbeitet" in capsys.readouterr().out
    m._discover(cfg, True, True)
    assert calls == [False]
