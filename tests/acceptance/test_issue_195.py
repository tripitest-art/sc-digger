"""Akzeptanztests: schon in der Inbox vorhandene SoundCloud-Tracks nicht erneut laden."""
from pathlib import Path

from sc_digger import main as m
from sc_digger.models import Config
from sc_digger.output import find_in_inbox
from tests.test_modes import CFG, mk


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_findet_datei_mit_id_praefix_in_unterordner(tmp_path):
    f = _touch(tmp_path / "150-155" / "6A" / "[2273961158] Gaiveu - Mix.wav")
    assert find_in_inbox(tmp_path, 2273961158) == f


def test_findet_auch_kopie_mit_endung_und_lose_im_root(tmp_path):
    f = _touch(tmp_path / "[42] A - B_1.mp3")
    assert find_in_inbox(tmp_path, 42) == f


def test_kein_treffer_bei_anderer_id_oder_teil_id(tmp_path):
    _touch(tmp_path / "x" / "[2273961158] A - B.wav")
    assert find_in_inbox(tmp_path, 273961158) is None
    assert find_in_inbox(tmp_path, 22739611580) is None
    assert find_in_inbox(tmp_path, 1) is None


def test_ignoriert_rejected_temp_und_nicht_audio(tmp_path):
    _touch(tmp_path / "_rejected" / "duplicate" / "[5] A - B.wav")
    _touch(tmp_path / ".cloud-tmp" / "[5] A - B.wav")
    _touch(tmp_path / "_dl_x" / "[5] A - B.wav")
    _touch(tmp_path / "x" / "[5] A - B.txt")
    assert find_in_inbox(tmp_path, 5) is None


def test_inbox_fehlt_oder_leer(tmp_path):
    assert find_in_inbox(tmp_path / "gibt-es-nicht", 1) is None
    assert find_in_inbox(tmp_path, 1) is None


def _cfg(tmp_path) -> Config:
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    return cfg


def test_process_laedt_vorhandenen_track_nicht_erneut(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _touch(tmp_path / "inbox" / "150-155" / "6A" / "[7] Artist - Track 7.wav")
    calls = []
    monkeypatch.setattr(m, "download_native", lambda *a, **k: calls.append(a) or None)
    fresh, _ = m.process([mk(7, downloadable=True)], cfg, dry_run=False)
    assert calls == []
    assert [t.id for t in fresh] == [7]


def test_process_laedt_unbekannten_track_weiter(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _touch(tmp_path / "inbox" / "150-155" / "6A" / "[8] Artist - Track 8.wav")
    calls = []
    monkeypatch.setattr(m, "download_native", lambda t, inbox, token=None: calls.append(t.id) or None)
    m.process([mk(7, downloadable=True)], cfg, dry_run=False)
    assert calls == [7]
