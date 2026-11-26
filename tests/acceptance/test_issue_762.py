"""Akzeptanztests: ausgefallene Scout-Quellen machen den Lauf scout zum Fehlschlag."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import sc_digger.main as m
from sc_digger.db import TrackDB
from sc_digger.main import ScoutErgebnis, ScoutQuellenFehler, pruefe_scout_ergebnis, run_scout
from sc_digger.models import Config
from tests.test_modes import mk

CFG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
if not CFG_PATH.is_file():  # issuecheck legt den Block außerhalb des Repos ab
    CFG_PATH = Path.cwd() / "config.yaml"


def _cfg(tmp_path: Path, schwelle: int = 2) -> Config:
    cfg = Config.load(CFG_PATH)
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"] = {**cfg.raw.get("download", {}), "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    (tmp_path / "coll").mkdir()
    cfg.raw["scout"] = {"bandcamp": {"feeds": ["https://x.bandcamp.com/feed"]},
                        "beatport": {"charts": ["https://www.beatport.com/chart/x"]}}
    cfg.raw["health"] = {"alert_after_bad_runs": schwelle}
    return cfg


def _runs(tmp_path: Path) -> list[tuple]:
    db = tmp_path / "seen.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT ok, found, error FROM runs WHERE mode='scout' ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _quellen(bandcamp, beatport):
    """patch-Kontext: Liste = Treffer, Exception-Instanz = Ausfall der Quelle."""
    def fake(wert):
        def f(urls):
            if isinstance(wert, Exception):
                raise wert
            return list(wert)
        return f
    return (patch("sc_digger.main.fetch_bandcamp_feeds", side_effect=fake(bandcamp)),
            patch("sc_digger.main.fetch_beatport_charts", side_effect=fake(beatport)),
            patch("sc_digger.output.telegram_call"))


def _scout(cfg, bandcamp, beatport, **kw):
    a, b, c = _quellen(bandcamp, beatport)
    with a, b, c:
        return run_scout(cfg, **kw)


def test_ergebnis_ist_ein_int_mit_fehlerliste():
    e = ScoutErgebnis(3, ["Bandcamp"])
    assert isinstance(e, int) and e == 3 and e.fehler == ["Bandcamp"]
    assert ScoutErgebnis(0).fehler == []


def test_ein_ausfall_wird_gemeldet_der_rest_trotzdem_geschrieben(tmp_path):
    cfg = _cfg(tmp_path)
    ergebnis = _scout(cfg, RuntimeError("Bandcamp down"), [mk(100), mk(200)])
    assert ergebnis == 2 and ergebnis.fehler == ["Bandcamp"]
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        assert len(db.get_store_items()) == 2


def test_beide_ausfaelle_stehen_in_reihenfolge_in_der_liste(tmp_path):
    ergebnis = _scout(_cfg(tmp_path), RuntimeError("a"), RuntimeError("b"))
    assert ergebnis == 0 and ergebnis.fehler == ["Bandcamp", "Beatport"]


def test_ohne_ausfall_ist_die_liste_leer(tmp_path):
    ergebnis = _scout(_cfg(tmp_path), [mk(1)], [mk(2)])
    assert ergebnis == 2 and ergebnis.fehler == []


def test_probelauf_meldet_keine_ausfaelle(tmp_path):
    ergebnis = _scout(_cfg(tmp_path), RuntimeError("a"), RuntimeError("b"), dry_run=True)
    assert ergebnis == 0 and ergebnis.fehler == []


def test_pruefe_scout_ergebnis():
    assert pruefe_scout_ergebnis(ScoutErgebnis(4, [])) == 4
    assert pruefe_scout_ergebnis(7) == 7  # einfaches int (z. B. ersetzter run_scout)
    with pytest.raises(ScoutQuellenFehler) as exc:
        pruefe_scout_ergebnis(ScoutErgebnis(1, ["Bandcamp", "Beatport"]))
    assert str(exc.value) == "Scout-Quellen fehlgeschlagen: Bandcamp, Beatport"


def _cli(monkeypatch, cfg: Config, *args: str) -> None:
    monkeypatch.setattr(m.Config, "load", classmethod(lambda cls, path=None: cfg))
    monkeypatch.setattr(sys, "argv", ["sc_digger.main", "scout", *args])
    m.cli()


def test_cli_ohne_ausfall_schreibt_ok_lauf(tmp_path, monkeypatch):
    a, b, c = _quellen([mk(1)], [mk(2)])
    with a, b, c:
        _cli(monkeypatch, _cfg(tmp_path), "--no-telegram")
    assert _runs(tmp_path) == [(1, 2, None)]


def test_cli_mit_ausfall_schreibt_fehllauf_und_beendet_mit_1(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    a, b, c = _quellen(RuntimeError("Bandcamp down"), [mk(2)])
    with a, b, c, pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, cfg, "--no-telegram")
    assert exc.value.code == 1
    (ok, found, error), = _runs(tmp_path)
    assert (ok, found) == (0, 0)
    assert "ScoutQuellenFehler" in error and "Bandcamp" in error and "Beatport" not in error
    with TrackDB(cfg["state"]["track_db_path"]) as db:  # die andere Quelle wurde trotzdem geschrieben
        assert len(db.get_store_items()) == 1


def test_cli_probelauf_mit_ausfall_schreibt_nichts_und_wirft_nicht(tmp_path, monkeypatch):
    a, b, c = _quellen(RuntimeError("a"), RuntimeError("b"))
    with a, b, c:
        _cli(monkeypatch, _cfg(tmp_path), "--dry-run", "--no-telegram")
    assert _runs(tmp_path) == []


def test_zweiter_ausfall_in_folge_loest_telegram_alarm_aus(tmp_path, monkeypatch):
    gesendet = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs, *a, **k: gesendet.append(msgs))
    cfg = _cfg(tmp_path, schwelle=2)
    for _ in range(2):
        a, b, c = _quellen(RuntimeError("Bandcamp down"), [])
        with a, b, c, pytest.raises(SystemExit):
            _cli(monkeypatch, cfg)
    assert len(gesendet) == 1
    assert "scout" in "\n".join(gesendet[0])
