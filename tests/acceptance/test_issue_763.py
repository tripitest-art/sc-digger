"""Akzeptanztests: Rekordbox-XML-Schreibfehler werden als Lauf protokolliert und alarmiert."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import sc_digger.main as m
from sc_digger.models import Config
from sc_digger.web.status import ALERT_LABELS


def _cfg(tmp_path: Path, *, aktiv: bool = True, schwelle: int = 2) -> Config:
    return Config({
        "state": {"db_path": str(tmp_path / "seen.sqlite"),
                  "track_db_path": str(tmp_path / "tracks.sqlite")},
        "health": {"alert_after_bad_runs": schwelle},
        "rekordbox": {"xml_enabled": aktiv, "xml_path": str(tmp_path / "out" / "rb.xml")},
    })


def _runs(tmp_path: Path) -> list[tuple]:
    db = tmp_path / "seen.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT ok, found, error FROM runs WHERE mode='rekordbox_xml' ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def test_erfolg_schreibt_ok_lauf_und_gibt_pfad_zurueck(tmp_path, monkeypatch):
    ziel = tmp_path / "out" / "rb.xml"
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: ziel)
    assert m.write_rekordbox_xml_logged(_cfg(tmp_path), no_telegram=True) == ziel
    assert _runs(tmp_path) == [(1, 1, None)]


def test_fehlschlag_schreibt_fehllauf_und_gibt_none_zurueck(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    assert m.write_rekordbox_xml_logged(_cfg(tmp_path), no_telegram=True) is None
    (ok, found, error), = _runs(tmp_path)
    assert (ok, found) == (0, 0)
    assert "Rekordbox-XML konnte nicht geschrieben werden" in error


def test_ausgeschaltete_xml_ist_kein_lauf_und_kein_fehler(tmp_path, monkeypatch):
    aufrufe = []
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: aufrufe.append(1))
    assert m.write_rekordbox_xml_logged(_cfg(tmp_path, aktiv=False), no_telegram=True) is None
    assert aufrufe == [1]  # der Schreiber wird trotzdem genau einmal gefragt
    assert _runs(tmp_path) == []


def test_zweiter_fehlschlag_in_folge_alarmiert_und_erfolg_entwarnt(tmp_path, monkeypatch):
    gesendet = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs, *a, **k: gesendet.append(msgs))
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    m.write_rekordbox_xml_logged(cfg, no_telegram=False)
    assert gesendet == []
    m.write_rekordbox_xml_logged(cfg, no_telegram=False)
    assert len(gesendet) == 1 and "rekordbox_xml" in "\n".join(gesendet[0])
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: tmp_path / "ok.xml")
    m.write_rekordbox_xml_logged(cfg, no_telegram=False)
    assert len(gesendet) == 2 and "läuft wieder" in "\n".join(gesendet[1])


def test_ohne_state_db_wird_nichts_protokolliert(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    cfg = Config({"rekordbox": {"xml_enabled": True}})
    assert m.write_rekordbox_xml_logged(cfg, no_telegram=True) is None


def test_cli_rekordbox_protokolliert_den_fehlschlag(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    monkeypatch.setattr(m.Config, "load", classmethod(lambda cls, path=None: cfg))
    monkeypatch.setattr(sys, "argv", ["sc_digger.main", "rekordbox", "--no-telegram"])
    m.cli()  # Exit-Code bleibt 0, wie bisher
    assert len(_runs(tmp_path)) == 1 and _runs(tmp_path)[0][0] == 0


def test_statusseite_kennt_den_modus():
    assert ALERT_LABELS["rekordbox_xml"] == "Rekordbox-XML"
    assert m.REKORDBOX_XML_MODE == "rekordbox_xml"
