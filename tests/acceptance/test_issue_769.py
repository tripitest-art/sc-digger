"""Akzeptanztests: run_link legt abgelehnte Downloads in die Quarantäne statt nach seen."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sc_digger import main as m
from sc_digger.models import Config
from tests.test_modes import mk

CFG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
if not CFG_PATH.is_file():  # issuecheck legt den Block außerhalb des Repos ab; pytest läuft dort im Repo
    CFG_PATH = Path.cwd() / "config.yaml"
CFG = Config.load(CFG_PATH)


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    cfg.raw["state"] = {"db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["search"] = {**CFG["search"], "reference_accounts": [], "followed_users": []}
    return cfg


class _LinkSC:
    """Nur die Methoden, die run_link für einen Playlist-Link aufruft."""

    def resolve_kind(self, url):
        return "playlist"

    def playlist_tracks(self, url, limit=None):
        return "Liste", [mk(11), mk(12), mk(13)]


def _ablehnen(monkeypatch):
    """process() ersetzen: Track 11 scheitert am Download, 12 an der Qualitätsprüfung, 13 ist sauber."""
    def fake_process(tracks, cfg, *, dry_run, skip_duplicates=True):
        by_id = {t.id: t for t in tracks}
        by_id[11].download_error = "Download nicht möglich (HTTP 403)"
        by_id[12].quality_report = {"ok": False, "reason": "Lossy-Quelle (Cutoff 16 kHz)",
                                    "cutoff_hz": 16000.0}
        return list(tracks), []

    monkeypatch.setattr(m, "process", fake_process)
    monkeypatch.setattr(m, "deliver", lambda *a, **k: None)


def _ids(tmp_path, tabelle):
    with sqlite3.connect(tmp_path / "state.sqlite") as db:
        return sorted(r[0] for r in db.execute(f"SELECT id FROM {tabelle}"))


def test_abgelehnte_tracks_landen_in_der_quarantaene_nicht_in_seen(tmp_path, monkeypatch):
    _ablehnen(monkeypatch)
    m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_LinkSC())
    assert _ids(tmp_path, "rejected") == [11, 12]
    assert _ids(tmp_path, "seen") == [13]


def test_quarantaene_traegt_den_grund_und_das_cutoff(tmp_path, monkeypatch):
    _ablehnen(monkeypatch)
    m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_LinkSC())
    with sqlite3.connect(tmp_path / "state.sqlite") as db:
        rows = dict((r[0], r[1:]) for r in db.execute("SELECT id, reason, cutoff_hz FROM rejected"))
    assert rows[11][0] == "Download nicht möglich (HTTP 403)"
    assert rows[12] == ("Lossy-Quelle (Cutoff 16 kHz)", 16000.0)


def test_bot_listet_den_abgelehnten_track_und_retry_gibt_ihn_frei(tmp_path, monkeypatch):
    from sc_digger import bot

    _ablehnen(monkeypatch)
    cfg = _cfg(tmp_path)
    m.run_link(cfg, "u", no_telegram=True, sc=_LinkSC())
    assert "Lossy-Quelle" in bot.rejected_reply(cfg, "/rejected")
    assert "zurückgesetzt" in bot.retry_reply(cfg, "/retry 12")
    assert _ids(tmp_path, "rejected") == [11]


def test_ohne_ablehnung_bleibt_alles_wie_bisher(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "process", lambda tracks, cfg, *, dry_run, skip_duplicates=True: (list(tracks), []))
    monkeypatch.setattr(m, "deliver", lambda *a, **k: None)
    quelle, neu, vorhanden = m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_LinkSC())
    assert (neu, vorhanden) == (3, 0)
    assert _ids(tmp_path, "seen") == [11, 12, 13]
    assert _ids(tmp_path, "rejected") == []


def test_abgelehnte_zaehlen_im_check_weiter_als_neu(tmp_path, monkeypatch):
    _ablehnen(monkeypatch)
    _, neu, vorhanden = m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_LinkSC())
    assert (neu, vorhanden) == (3, 0)


def test_probelauf_schreibt_weder_seen_noch_quarantaene(tmp_path, monkeypatch):
    _ablehnen(monkeypatch)
    m.run_link(_cfg(tmp_path), "u", dry_run=True, no_telegram=True, sc=_LinkSC())
    assert not (tmp_path / "state.sqlite").exists()
