"""Akzeptanztests: Läufe der Cron-Jobs protokollieren, Teil 2 (audit, taste, scout, kaufliste) (Issue <N>)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

import sc_digger.main as m
from sc_digger.models import Config
from sc_digger.schedule import JOBS
from sc_digger.web.status import ALERT_LABELS

ROOT = Path(__file__).resolve().parents[2]


def _runs(db: Path, mode: str) -> list[tuple]:
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT ok, found, error FROM runs WHERE mode=? ORDER BY id", (mode,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _cfg(tmp_path: Path) -> Config:
    return Config(raw={"state": {"db_path": str(tmp_path / "seen.sqlite"),
                                 "track_db_path": str(tmp_path / "tracks.sqlite")},
                       "health": {"alert_after_bad_runs": 2}})


def test_run_logged_protokolliert_die_anzahl(tmp_path):
    assert m.run_logged(_cfg(tmp_path), "scout", lambda: 3, no_telegram=True) == 3
    assert m.run_logged(_cfg(tmp_path), "scout", lambda: "fertig", no_telegram=True) == "fertig"
    assert _runs(tmp_path / "seen.sqlite", "scout") == [(1, 3, None), (1, 1, None)]


def test_run_logged_protokolliert_fehler_und_wirft_weiter(tmp_path):
    def kaputt():
        raise RuntimeError("Feed nicht erreichbar")

    with pytest.raises(RuntimeError):
        m.run_logged(_cfg(tmp_path), "scout", kaputt, no_telegram=True)
    (ok, found, error), = _runs(tmp_path / "seen.sqlite", "scout")
    assert (ok, found) == (0, 0)
    assert "RuntimeError" in error and "Feed nicht erreichbar" in error


def test_run_logged_ohne_state_laeuft_ohne_protokoll():
    assert m.run_logged(Config(raw={}), "scout", lambda: 2, no_telegram=True) == 2


def _config_file(tmp_path: Path) -> Path:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["state"]["db_path"] = str(tmp_path / "seen.sqlite")
    raw["state"]["track_db_path"] = str(tmp_path / "tracks.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def _cli(monkeypatch, tmp_path: Path, *args: str) -> None:
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    monkeypatch.setattr(sys, "argv", ["sc_digger.main", *args, "--config", str(_config_file(tmp_path)),
                                      "--no-telegram"])
    m.cli()


def test_scout_lauf_wird_protokolliert(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_scout", lambda cfg, dry_run=False: 4)
    _cli(monkeypatch, tmp_path, "scout")
    assert _runs(tmp_path / "seen.sqlite", "scout") == [(1, 4, None)]


def test_probelauf_wird_nicht_protokolliert(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_scout", lambda cfg, dry_run=False: 0)
    _cli(monkeypatch, tmp_path, "scout", "--dry-run")
    assert _runs(tmp_path / "seen.sqlite", "scout") == []


def test_taste_und_audit_werden_protokolliert(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_taste_cmd", lambda cfg, dry_run=False: "Taste-Score: nicht trainiert")
    monkeypatch.setattr(m, "run_audit", lambda cfg, **kw: object())
    _cli(monkeypatch, tmp_path, "taste")
    _cli(monkeypatch, tmp_path, "audit", "--check-adopted")
    assert _runs(tmp_path / "seen.sqlite", "taste") == [(1, 1, None)]
    assert _runs(tmp_path / "seen.sqlite", "audit") == [(1, 1, None)]


def test_kaufliste_ist_ein_cli_modus_und_wird_protokolliert(tmp_path, monkeypatch):
    gesendet = []
    monkeypatch.setattr("sc_digger.output.send_kaufliste",
                        lambda cfg, dry_run=False, chat_id=None: gesendet.append(dry_run))
    _cli(monkeypatch, tmp_path, "kaufliste")
    assert gesendet == [False]
    assert _runs(tmp_path / "seen.sqlite", "kaufliste") == [(1, 1, None)]


def test_zeitplan_kennt_die_protokollierten_modi():
    modes = {j.name: j.mode for j in JOBS}
    for name in m.LOGGED_MODES:
        assert modes[name] == name
        assert ALERT_LABELS.get(name)
    assert {j.name: j.kennung for j in JOBS}["kaufliste"] == "sc_digger.main kaufliste"


def test_entrypoint_startet_die_kaufliste_ueber_die_cli():
    text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert "python -m sc_digger.main kaufliste" in text
    zeilen = [z for z in text.splitlines() if z.strip().startswith("echo") and "send_kaufliste" in z]
    assert zeilen == []
