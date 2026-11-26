"""Issue #763: Schreibversuche der Rekordbox-XML als Lauf rekordbox_xml in runs."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import sc_digger.main as m
from sc_digger.models import Config

CFG = Config.load(Path(__file__).parent.parent / "config.yaml")


def _runs(db: Path) -> list[tuple]:
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT ok, found, error FROM runs WHERE mode='rekordbox_xml' ORDER BY id").fetchall()
    finally:
        con.close()


def _discover_cfg(tmp_path: Path) -> Config:
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {"db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["health"] = {"alert_after_bad_runs": 2}
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    return cfg


class _FakeSC:
    def search_tag(self, tag, age, limit):
        from tests.test_modes import mk
        return [mk(i, playback_count=2000, likes_count=40 + i) for i in range(5)]

    def user_uploads(self, url, age):
        return []


@pytest.mark.parametrize("dry_run, erwartet", [(False, 1), (True, 0)])
def test_discover_ruft_logged_nur_im_echten_lauf(tmp_path, monkeypatch, dry_run, erwartet):
    cfg = _discover_cfg(tmp_path)
    monkeypatch.setattr(m, "SoundCloudClient", _FakeSC)
    aufrufe = []
    monkeypatch.setattr(m, "write_rekordbox_xml_logged",
                        lambda cfg, *, no_telegram: aufrufe.append(no_telegram))
    monkeypatch.setattr(m, "run_rekordbox_import", lambda *a, **k: None)
    m._discover(cfg, dry_run, True)
    assert aufrufe == [True] * erwartet


def test_intake_cli_protokolliert_den_fehlschlag(tmp_path, monkeypatch):
    cfg = Config({
        "state": {"db_path": str(tmp_path / "seen.sqlite")},
        "rekordbox": {"xml_enabled": True, "xml_path": str(tmp_path / "rb.xml")},
    })
    monkeypatch.setattr(m, "run_intake", lambda cfg, dry_run=False: [])
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    assert m.run_intake_cli(cfg, dry_run=False, no_telegram=True) == 0
    (ok, found, error), = _runs(tmp_path / "seen.sqlite")
    assert (ok, found) == (0, 0) and "Rekordbox-XML" in error


def test_intake_cli_probelauf_protokolliert_nichts(tmp_path, monkeypatch):
    cfg = Config({
        "state": {"db_path": str(tmp_path / "seen.sqlite")},
        "rekordbox": {"xml_enabled": True, "xml_path": str(tmp_path / "rb.xml")},
    })
    monkeypatch.setattr(m, "run_intake", lambda cfg, dry_run=False: [])
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    m.run_intake_cli(cfg, dry_run=True, no_telegram=True)
    assert _runs(tmp_path / "seen.sqlite") == []


def test_echter_fehlschlag_ziel_in_der_sammlung_ergibt_ok_0(tmp_path):
    coll = tmp_path / "coll"
    coll.mkdir()
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    cfg = Config({
        "state": {"db_path": str(tmp_path / "seen.sqlite")},
        "download": {"collection_dir": str(coll), "inbox_dir": str(inbox)},
        "rekordbox": {"xml_enabled": True, "xml_path": str(coll / "rb.xml")},
    })
    assert m.write_rekordbox_xml_logged(cfg, no_telegram=True) is None
    assert not (coll / "rb.xml").exists()  # Goldene Regel 4 bleibt gewahrt
    (ok, found, error), = _runs(tmp_path / "seen.sqlite")
    assert (ok, found) == (0, 0)
    assert error == "Rekordbox-XML konnte nicht geschrieben werden (Details im Log)"


def test_echter_erfolg_ergibt_ok_1(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    ziel = tmp_path / "out" / "rb.xml"
    cfg = Config({
        "state": {"db_path": str(tmp_path / "seen.sqlite")},
        "download": {"collection_dir": str(tmp_path / "coll"), "inbox_dir": str(inbox)},
        "rekordbox": {"xml_enabled": True, "xml_path": str(ziel)},
    })
    assert m.write_rekordbox_xml_logged(cfg, no_telegram=True) == ziel
    assert ziel.exists()
    assert _runs(tmp_path / "seen.sqlite") == [(1, 1, None)]


def test_protokollfehler_wirft_nicht(tmp_path, monkeypatch):
    cfg = Config({
        "state": {"db_path": str(tmp_path / "seen.sqlite")},
        "rekordbox": {"xml_enabled": True},
    })
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)

    def kaputt(*a, **k):
        raise RuntimeError("Telegram weg")
    monkeypatch.setattr(m, "_notify_health", kaputt)
    assert m.write_rekordbox_xml_logged(cfg, no_telegram=False) is None
