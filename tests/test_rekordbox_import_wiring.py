"""Rekordbox-Rückkanal im täglichen Lauf: Verdrahtung in _discover und Health-Alarm (Issue #397)."""
import logging
import sqlite3
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.db import TrackDB
from sc_digger.models import Config
from tests.test_modes import mk
from tests.test_retry import CFG, FakeSC


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = Config(dict(CFG.raw))
    c.raw["search"] = {**CFG["search"], "tags": ["schranz"], "followed_users": [],
                       "reference_accounts": []}
    c.raw["state"] = {**CFG["state"], "db_path": str(tmp_path / "state.sqlite"),
                      "track_db_path": str(tmp_path / "tracks.sqlite")}
    c.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                         "inbox_dir": str(tmp_path / "inbox")}
    c.raw["organize"] = {**CFG["organize"], "detect_bpm": False, "detect_key": False,
                         "write_tags": False, "enabled": False}
    c.raw["rekordbox"] = {"xml_enabled": False}
    c.raw["health"] = {"alert_after_bad_runs": 2}
    monkeypatch.setattr(m, "finalize_quality", lambda t, path, inbox, cfg: path)

    class SC(FakeSC):
        def search_tag(self, tag, age, limit):
            return [mk(100, playback_count=2000, likes_count=50)]

    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: SC())
    monkeypatch.setattr(m, "retry_downloads", lambda sc, cfg: [])
    return c


def _runs(cfg: Config) -> list[tuple]:
    conn = sqlite3.connect(cfg["state"]["db_path"])
    try:
        return conn.execute(
            "SELECT ok, found, error FROM runs WHERE mode='rekordbox_import' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# ------------------------------------------------------------------ _discover


def test_discover_importiert_nach_dem_xml_export(cfg, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda c: calls.append("export"))
    monkeypatch.setattr(m, "run_rekordbox_import",
                        lambda c, no_telegram=False: calls.append(f"import:{no_telegram}"))

    m._discover(cfg, False, True)

    assert calls == ["export", "import:True"]


def test_discover_dry_run_importiert_nicht(cfg, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda c: calls.append("export"))
    monkeypatch.setattr(m, "run_rekordbox_import",
                        lambda c, no_telegram=False: calls.append("import"))

    m._discover(cfg, True, True)

    assert calls == []


def test_discover_laeuft_trotz_kaputter_xml_durch(cfg, tmp_path, monkeypatch):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text("kein xml", encoding="utf-8")
    cfg.raw["rekordbox"] = {"xml_enabled": False, "import_xml_path": str(xml_file)}

    m._discover(cfg, False, True)  # wirft nicht

    runs = _runs(cfg)
    assert len(runs) == 1 and runs[0][0] == 0


# ------------------------------------------------------------------ run_rekordbox_import


def test_import_fehler_im_trackdb_oeffnen_wirft_nicht(cfg, tmp_path, monkeypatch, caplog):
    cfg.raw["rekordbox"] = {"import_xml_path": str(tmp_path / "x.xml")}

    def boom(*a, **k):
        raise OSError("db kaputt")

    monkeypatch.setattr(m, "TrackDB", boom)
    with caplog.at_level(logging.ERROR, logger=m.log.name):
        m.run_rekordbox_import(cfg, no_telegram=True)  # wirft nicht

    assert "Import oder Health-Eintrag fehlgeschlagen" in caplog.text
    # Eine kaputte TrackDB darf keinen Health-Eintrag erzeugen: state.db bleibt unberührt.
    assert not Path(cfg["state"]["db_path"]).exists()


def test_alarm_nach_zwei_schlechten_laeufen_ueber_telegram(cfg, tmp_path, monkeypatch):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text("kein xml", encoding="utf-8")
    cfg.raw["rekordbox"] = {"import_xml_path": str(xml_file)}
    sent: list[list[str]] = []
    monkeypatch.setattr(m, "send_telegram", lambda c, msgs, **k: sent.append(msgs))

    m.run_rekordbox_import(cfg, no_telegram=False)
    assert sent == []  # erster schlechter Lauf liegt unter der Schwelle

    m.run_rekordbox_import(cfg, no_telegram=False)
    assert len(sent) == 1
    assert "rekordbox_import" in sent[0][0]
    assert "ParseError" in sent[0][0]


def test_alarm_mit_no_telegram_wird_nicht_gesendet(cfg, tmp_path, monkeypatch):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text("kein xml", encoding="utf-8")
    cfg.raw["rekordbox"] = {"import_xml_path": str(xml_file)}
    sent: list = []
    monkeypatch.setattr(m, "send_telegram", lambda c, msgs, **k: sent.append(msgs))

    m.run_rekordbox_import(cfg, no_telegram=True)
    m.run_rekordbox_import(cfg, no_telegram=True)

    assert sent == []
    assert len(_runs(cfg)) == 2


def test_import_schreibt_nur_in_die_trackdb(cfg, tmp_path):
    """Die XML bleibt unverändert, es entsteht keine weitere Datei neben ihr."""
    xml_file = tmp_path / "rueck" / "export.xml"
    xml_file.parent.mkdir()
    xml_file.write_text('<DJ_PLAYLISTS><COLLECTION Entries="0"/></DJ_PLAYLISTS>', encoding="utf-8")
    vorher = xml_file.read_bytes()
    cfg.raw["rekordbox"] = {"import_xml_path": str(xml_file)}

    m.run_rekordbox_import(cfg, no_telegram=True)

    assert xml_file.read_bytes() == vorher
    assert [p.name for p in xml_file.parent.iterdir()] == ["export.xml"]
    assert Path(cfg["state"]["track_db_path"]).exists()
    with TrackDB(cfg["state"]["track_db_path"]):
        pass
    # XML ohne Tracks: matched == 0 -> schlechter Lauf, aber ok
    assert _runs(cfg) == [(1, 0, None)]
