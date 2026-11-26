"""Akzeptanztests: Lebenszeichen für Bot und Web im Container-Healthcheck."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sc_digger import bot
from sc_digger import healthcheck as hc
from sc_digger.heartbeat import (
    STATUS_AUS, STATUS_FEHLER, STATUS_OK, HeartbeatThread, heartbeat_file, read_heartbeat,
    write_heartbeat,
)
from sc_digger.models import Config
from sc_digger.output import TelegramError

T0 = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


def test_schreiben_und_lesen_ergibt_dasselbe(tmp_path):
    assert write_heartbeat(tmp_path, "bot", status=STATUS_FEHLER, detail="Token abgelehnt", now=T0)
    lz = read_heartbeat(tmp_path, "bot")
    assert (lz.ts, lz.status, lz.detail) == (T0, STATUS_FEHLER, "Token abgelehnt")
    assert heartbeat_file(tmp_path, "bot") == tmp_path / "heartbeat-bot.json"
    assert json.loads((tmp_path / "heartbeat-bot.json").read_text(encoding="utf-8"))["status"] == "fehler"


def test_schreiben_legt_keinen_ordner_an_und_wirft_nie(tmp_path):
    assert write_heartbeat(tmp_path / "gibt-es-nicht", "bot", now=T0) is False
    assert not (tmp_path / "gibt-es-nicht").exists()
    assert write_heartbeat(None, "bot", now=T0) is False


def test_detail_wird_auf_200_zeichen_gekuerzt(tmp_path):
    write_heartbeat(tmp_path, "web", detail="x" * 500, now=T0)
    assert len(read_heartbeat(tmp_path, "web").detail) == 200


@pytest.mark.parametrize("inhalt", ["", "{kaputt", "[1, 2]", "{}", '{"ts": "gestern"}'])
def test_lesen_ignoriert_kaputte_dateien(tmp_path, inhalt):
    (tmp_path / "heartbeat-bot.json").write_text(inhalt, encoding="utf-8")
    assert read_heartbeat(tmp_path, "bot") is None
    assert read_heartbeat(None, "bot") is None
    assert read_heartbeat(tmp_path, "unbekannt") is None


def test_fehlender_status_gilt_als_ok(tmp_path):
    (tmp_path / "heartbeat-bot.json").write_text(
        json.dumps({"ts": T0.isoformat()}), encoding="utf-8")
    lz = read_heartbeat(tmp_path, "bot")
    assert lz.status == STATUS_OK and lz.detail == ""


def test_thread_schreibt_sofort_und_setzt_status_sofort(tmp_path):
    hb = HeartbeatThread(tmp_path, "bot", interval_s=3600)
    hb.start()
    try:
        assert read_heartbeat(tmp_path, "bot").status == STATUS_OK
        hb.set_status(STATUS_FEHLER, "Token abgelehnt")
        lz = read_heartbeat(tmp_path, "bot")
        assert (lz.status, lz.detail) == (STATUS_FEHLER, "Token abgelehnt")
    finally:
        hb.stop()


def _lz(tmp_path, name, *, alter_s=0, status=STATUS_OK, detail=""):
    write_heartbeat(tmp_path, name, status=status, detail=detail,
                    now=T0 - timedelta(seconds=alter_s))


def test_check_frisches_lebenszeichen_ist_ok(tmp_path):
    _lz(tmp_path, "bot", alter_s=60)
    res = hc.check_heartbeat(tmp_path, "bot", 300, now=T0)
    assert (res.name, res.ok, res.detail) == ("bot", True, "ok")


def test_check_altes_lebenszeichen_ist_fehler(tmp_path):
    _lz(tmp_path, "web", alter_s=1500)
    res = hc.check_heartbeat(tmp_path, "web", 300, now=T0)
    assert res.ok is False
    assert res.detail == "letztes Lebenszeichen vor 25 Minuten"


def test_check_status_fehler_nennt_den_grund(tmp_path):
    _lz(tmp_path, "bot", status=STATUS_FEHLER, detail="Telegram lehnt den Bot-Token ab (401)")
    res = hc.check_heartbeat(tmp_path, "bot", 300, now=T0)
    assert res.ok is False and "401" in res.detail


def test_check_status_aus_ist_kein_fehler(tmp_path):
    _lz(tmp_path, "bot", status=STATUS_AUS, detail="TELEGRAM_BOT_TOKEN/CHAT_ID fehlen")
    res = hc.check_heartbeat(tmp_path, "bot", 300, now=T0)
    assert res.ok is True and res.detail.startswith("aus")


def test_check_ohne_datei_und_nicht_erwartet_sind_ok(tmp_path):
    assert hc.check_heartbeat(tmp_path, "bot", 300, now=T0).detail == "noch kein Lebenszeichen"
    _lz(tmp_path, "web", alter_s=99999)
    res = hc.check_heartbeat(tmp_path, "web", 300, expected=False, now=T0)
    assert res.ok is True and res.detail == "nicht eingeschaltet"


def _cfg(tmp_path, **health):
    return Config({"state": {"db_path": str(tmp_path / "seen.sqlite"),
                             "track_db_path": str(tmp_path / "tracks.sqlite")},
                   "health": health})


def test_run_heartbeat_checks_liefert_bot_und_web_in_dieser_reihenfolge(tmp_path):
    _lz(tmp_path, "bot")
    _lz(tmp_path, "web", alter_s=2000)
    res = hc.run_heartbeat_checks(_cfg(tmp_path), now=T0, web_expected=True)
    assert [(r.name, r.ok) for r in res] == [("bot", True), ("web", False)]
    res = hc.run_heartbeat_checks(_cfg(tmp_path), now=T0, web_expected=False)
    assert [(r.name, r.ok) for r in res] == [("bot", True), ("web", True)]


def test_schwelle_kommt_aus_der_konfiguration(tmp_path):
    _lz(tmp_path, "bot", alter_s=400)
    assert hc.run_heartbeat_checks(_cfg(tmp_path), now=T0, web_expected=False)[0].ok is False
    cfg = _cfg(tmp_path, max_seconds_since_heartbeat=600)
    assert hc.run_heartbeat_checks(cfg, now=T0, web_expected=False)[0].ok is True


def test_web_wird_nur_mit_passwort_erwartet(tmp_path, monkeypatch):
    _lz(tmp_path, "web", alter_s=5000)
    monkeypatch.delenv("SC_DIGGER_WEB_PASSWORD", raising=False)
    assert hc.run_heartbeat_checks(_cfg(tmp_path), now=T0)[1].ok is True
    monkeypatch.setenv("SC_DIGGER_WEB_PASSWORD", "x")
    assert hc.run_heartbeat_checks(_cfg(tmp_path), now=T0)[1].ok is False


def test_ohne_state_db_keine_pruefungen():
    assert hc.run_heartbeat_checks(Config({}), now=T0) == []


def test_run_checks_bleibt_unveraendert(tmp_path):
    proc = tmp_path / "proc" / "1"
    proc.mkdir(parents=True)
    (proc / "comm").write_text("cron\n")
    names = [r.name for r in hc.run_checks(_cfg(tmp_path), proc_root=tmp_path / "proc", now=T0)]
    assert names == ["cron", "letzter_lauf", "state_db", "track_db"]


class _Idled(Exception):
    pass


def _listen(monkeypatch, tmp_path, *, token, getupdates_fehler=None):
    cfg = Config({"state": {"db_path": str(tmp_path / "seen.sqlite")}})
    for var, wert in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", "123" if token else None)):
        if wert is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, wert)

    def idle(reason):
        raise _Idled(reason)

    def call(tok, method, **kw):
        raise getupdates_fehler

    monkeypatch.setattr(bot, "_idle_forever", idle)
    monkeypatch.setattr(bot, "telegram_call", call)
    monkeypatch.setattr(bot, "SoundCloudClient", lambda: None)
    with pytest.raises(_Idled):
        bot.listen(cfg)


def test_bot_ohne_token_meldet_status_aus(monkeypatch, tmp_path):
    _listen(monkeypatch, tmp_path, token=None)
    lz = read_heartbeat(tmp_path, "bot")
    assert lz.status == STATUS_AUS and "fehlen" in lz.detail
    assert hc.check_heartbeat(tmp_path, "bot", 300).ok is True


def test_bot_mit_abgelehntem_token_meldet_status_fehler(monkeypatch, tmp_path):
    _listen(monkeypatch, tmp_path, token="abc", getupdates_fehler=TelegramError("Telegram 401: Unauthorized"))
    lz = read_heartbeat(tmp_path, "bot")
    assert lz.status == STATUS_FEHLER and "401" in lz.detail
    res = hc.check_heartbeat(tmp_path, "bot", 300)
    assert res.ok is False and "401" in res.detail


def test_web_start_schreibt_ein_lebenszeichen(monkeypatch, tmp_path):
    import sc_digger.web.__main__ as web_main

    cfg = Config({"state": {"db_path": str(tmp_path / "seen.sqlite")}})
    monkeypatch.setenv("SC_DIGGER_WEB_PASSWORD", "x")
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    monkeypatch.setattr(web_main.Config, "load", classmethod(lambda cls, path=None: cfg))
    monkeypatch.setattr(web_main, "create_app", lambda *a, **k: object())
    gestartet = []
    monkeypatch.setattr(web_main.uvicorn, "run", lambda app, **k: gestartet.append(k))
    assert web_main.main([]) == 0
    assert gestartet and read_heartbeat(tmp_path, "web").status == STATUS_OK
