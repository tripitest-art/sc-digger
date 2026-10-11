"""Eigene Tests für #195: schon in der Inbox liegende Tracks werden nicht erneut geladen."""
from pathlib import Path

from sc_digger import main as m
from sc_digger.models import Config
from tests.test_modes import CFG, mk


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def _cfg(tmp_path) -> Config:
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    return cfg


def _cloud_track(i):
    return mk(i, downloadable=True, has_downloads_left=False,
              description="Download: https://drive.google.com/file/d/abc/view")


def test_process_laedt_cloud_track_nicht_erneut(tmp_path, monkeypatch):
    """Treffer bei Cloud-Download: schon vorhandene ID wird nicht erneut geladen."""
    cfg = _cfg(tmp_path)
    _touch(tmp_path / "inbox" / "150-155" / "6A" / "[7] Artist - Track 7.mp3")
    cfg.raw["download"]["auto_download_native_only"] = False
    calls = []
    monkeypatch.setattr(m, "download_cloud", lambda t, inbox, max_mb=500: calls.append(t.id) or None)
    fresh, _ = m.process([_cloud_track(7)], cfg, dry_run=False)
    assert calls == []
    assert [t.id for t in fresh] == [7]


def test_process_laedt_unbekannten_cloud_track_weiter(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg.raw["download"]["auto_download_native_only"] = False
    calls = []
    monkeypatch.setattr(m, "download_cloud", lambda t, inbox, max_mb=500: calls.append(t.id) or None)
    m.process([_cloud_track(7)], cfg, dry_run=False)
    assert calls == [7]
