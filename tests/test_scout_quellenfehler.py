"""Eigene Tests zu #762: ausgefallene Scout-Quellen als Fehllauf, Alarm und Entwarnung."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import sc_digger.main as m
from sc_digger.main import ScoutErgebnis, ScoutQuellenFehler, pruefe_scout_ergebnis, run_scout
from sc_digger.models import Config
from tests.test_modes import mk

CFG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


def _cfg(tmp_path: Path, scout: dict | None = None, schwelle: int = 2) -> Config:
    cfg = Config.load(CFG_PATH)
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"] = {**cfg.raw.get("download", {}), "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    (tmp_path / "coll").mkdir(exist_ok=True)
    cfg.raw["scout"] = scout if scout is not None else {
        "bandcamp": {"feeds": ["https://x.bandcamp.com/feed"]},
        "beatport": {"charts": ["https://www.beatport.com/chart/x"]}}
    cfg.raw["health"] = {"alert_after_bad_runs": schwelle}
    return cfg


def _fake(wert):
    def f(urls):
        if isinstance(wert, Exception):
            raise wert
        return list(wert)
    return f


def _cli(monkeypatch, cfg: Config, bandcamp, beatport) -> None:
    monkeypatch.setattr(m.Config, "load", classmethod(lambda cls, path=None: cfg))
    monkeypatch.setattr(sys, "argv", ["sc_digger.main", "scout"])
    with patch("sc_digger.main.fetch_bandcamp_feeds", side_effect=_fake(bandcamp)), \
            patch("sc_digger.main.fetch_beatport_charts", side_effect=_fake(beatport)), \
            patch("sc_digger.output.telegram_call"):
        m.cli()


def _runs(tmp_path: Path) -> list[tuple]:
    con = sqlite3.connect(str(tmp_path / "seen.sqlite"))
    try:
        return con.execute("SELECT ok, found, error FROM runs WHERE mode='scout' ORDER BY id").fetchall()
    finally:
        con.close()


def test_ohne_konfigurierte_quellen_kein_fehler(tmp_path):
    # Leere Quellen sind kein Ausfall: die Fetcher werden mit leerer Liste aufgerufen.
    with patch("sc_digger.main.fetch_bandcamp_feeds", return_value=[]) as bc, \
            patch("sc_digger.main.fetch_beatport_charts", return_value=[]) as bp, \
            patch("sc_digger.output.telegram_call"):
        ergebnis = run_scout(_cfg(tmp_path, scout={}))
    assert isinstance(ergebnis, ScoutErgebnis)
    assert ergebnis == 0 and ergebnis.fehler == []
    bc.assert_called_once_with([])
    bp.assert_called_once_with([])
    assert pruefe_scout_ergebnis(ergebnis) == 0


def test_cli_ohne_quellen_schreibt_ok_lauf(tmp_path, monkeypatch):
    _cli(monkeypatch, _cfg(tmp_path, scout={}), [], [])
    assert _runs(tmp_path) == [(1, 0, None)]


def test_nur_beatport_faellt_aus(tmp_path):
    with patch("sc_digger.main.fetch_bandcamp_feeds", side_effect=_fake([mk(1)])), \
            patch("sc_digger.main.fetch_beatport_charts", side_effect=_fake(RuntimeError("x"))), \
            patch("sc_digger.output.telegram_call"):
        ergebnis = run_scout(_cfg(tmp_path))
    assert ergebnis == 1 and ergebnis.fehler == ["Beatport"]
    with pytest.raises(ScoutQuellenFehler, match=r"^Scout-Quellen fehlgeschlagen: Beatport$"):
        pruefe_scout_ergebnis(ergebnis)


def test_fehlerliste_ist_eine_kopie():
    quelle = ["Bandcamp"]
    e = ScoutErgebnis(1, quelle)
    quelle.append("Beatport")
    assert e.fehler == ["Bandcamp"]
    assert ScoutErgebnis(1, iter(["Beatport"])).fehler == ["Beatport"]


def test_pruefe_gibt_reines_int_zurueck():
    wert = pruefe_scout_ergebnis(ScoutErgebnis(5, []))
    assert type(wert) is int and wert == 5


def test_entwarnung_nach_sauberem_lauf_auf_alarm(tmp_path, monkeypatch):
    gesendet: list[list[str]] = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs, *a, **k: gesendet.append(msgs))
    cfg = _cfg(tmp_path, schwelle=2)
    for _ in range(2):
        with pytest.raises(SystemExit) as exc:
            _cli(monkeypatch, cfg, RuntimeError("Bandcamp down"), [])
        assert exc.value.code == 1
    assert len(gesendet) == 1  # Alarm nach zwei Fehlschlägen in Folge

    _cli(monkeypatch, cfg, [mk(1)], [mk(2)])
    assert len(gesendet) == 2
    text = "\n".join(gesendet[1])
    assert "scout" in text and "läuft wieder" in text

    runs = _runs(tmp_path)
    assert [r[0] for r in runs] == [0, 0, 1]
    assert runs[-1] == (1, 2, None)
    assert all("Bandcamp" in r[2] for r in runs[:2])
