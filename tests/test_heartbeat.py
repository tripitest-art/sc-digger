"""Eigene Tests zu Lebenszeichen (sc_digger.heartbeat) und deren Prüfung im Healthcheck."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import yaml

from sc_digger import healthcheck as hc
from sc_digger import heartbeat as hb
from sc_digger.heartbeat import (
    STATUS_AUS, STATUS_FEHLER, STATUS_OK, HeartbeatThread, read_heartbeat, write_heartbeat,
)

T0 = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


def test_detail_wird_von_zugangsdaten_bereinigt(tmp_path, monkeypatch):
    secret = "GEHEIMER_TOKEN_123456"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    assert write_heartbeat(tmp_path, "bot", status=STATUS_FEHLER, detail=f"Fehler mit {secret}", now=T0)
    text = (tmp_path / "heartbeat-bot.json").read_text(encoding="utf-8")
    assert secret not in text
    assert "***" in read_heartbeat(tmp_path, "bot").detail


def test_schreiben_hinterlaesst_keine_temp_dateien(tmp_path):
    for _ in range(3):
        assert write_heartbeat(tmp_path, "web", now=T0)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["heartbeat-web.json"]


def test_schreiben_in_datei_statt_ordner_wirft_nicht(tmp_path):
    datei = tmp_path / "keinordner"
    datei.write_text("x", encoding="utf-8")
    assert write_heartbeat(datei, "bot", now=T0) is False


def test_naive_zeit_wird_als_utc_gelesen(tmp_path):
    (tmp_path / "heartbeat-bot.json").write_text('{"ts": "2026-11-26T12:00:00", "status": 5}',
                                                 encoding="utf-8")
    lz = read_heartbeat(tmp_path, "bot")
    assert lz.ts == T0 and lz.status == STATUS_OK and lz.detail == ""


def test_lebenszeichen_ohne_status_aus_ohne_detail(tmp_path):
    write_heartbeat(tmp_path, "bot", status=STATUS_AUS, now=T0)
    assert hc.check_heartbeat(tmp_path, "bot", 300, now=T0).detail == "aus"
    write_heartbeat(tmp_path, "bot", status=STATUS_FEHLER, now=T0)
    res = hc.check_heartbeat(tmp_path, "bot", 300, now=T0)
    assert (res.ok, res.detail) == (False, "meldet einen Fehler")


def test_alter_knapp_ueber_der_schwelle_meldet_mindestens_eine_minute(tmp_path):
    write_heartbeat(tmp_path, "bot", now=T0 - timedelta(seconds=31))
    res = hc.check_heartbeat(tmp_path, "bot", 30, now=T0)
    assert (res.ok, res.detail) == (False, "letztes Lebenszeichen vor 1 Minuten")
    # Genau auf der Schwelle ist noch ok.
    write_heartbeat(tmp_path, "bot", now=T0 - timedelta(seconds=30))
    assert hc.check_heartbeat(tmp_path, "bot", 30, now=T0).ok is True


def test_thread_erneuert_das_lebenszeichen(tmp_path, monkeypatch):
    """Der Thread schreibt im Takt erneut; gewartet wird auf ein Ereignis, nicht auf eine Uhrzeit."""
    schreibvorgaenge: list[str] = []
    genug = threading.Event()
    original = hb.write_heartbeat

    def zaehlen(*a, **k):
        schreibvorgaenge.append(k.get("status", STATUS_OK))
        if len(schreibvorgaenge) >= 3:
            genug.set()
        return original(*a, **k)

    monkeypatch.setattr(hb, "write_heartbeat", zaehlen)
    t = HeartbeatThread(tmp_path, "web", interval_s=0.01)
    t.start()
    try:
        assert genug.wait(timeout=10)
    finally:
        t.stop()
    assert read_heartbeat(tmp_path, "web").status == STATUS_OK
    # Nach stop() wird nichts mehr geschrieben.
    anzahl = len(schreibvorgaenge)
    t.set_status(STATUS_FEHLER, "zu spät")
    assert len(schreibvorgaenge) == anzahl
    assert read_heartbeat(tmp_path, "web").status == STATUS_OK


def test_thread_ohne_ordner_laeuft_still(tmp_path):
    t = HeartbeatThread(None, "bot", interval_s=3600)
    t.start()
    t.set_status(STATUS_FEHLER, "x")
    t.stop()
    assert list(tmp_path.iterdir()) == []


def _cfg_file(tmp_path):
    cfg = {"state": {"db_path": str(tmp_path / "seen.sqlite"),
                     "track_db_path": str(tmp_path / "tracks.sqlite")}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _proc(tmp_path):
    proc = tmp_path / "proc" / "1"
    proc.mkdir(parents=True)
    (proc / "comm").write_text("cron\n")
    return tmp_path / "proc"


def test_main_meldet_bot_fehler_einmal_und_entwarnt(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.delenv("SC_DIGGER_WEB_PASSWORD", raising=False)
    gesendet: list[str] = []
    monkeypatch.setattr(hc, "telegram_call",
                        lambda tok, method, **kw: gesendet.append(kw["json"]["text"]))
    cfg_path = _cfg_file(tmp_path)
    proc = _proc(tmp_path)

    write_heartbeat(tmp_path, "bot", status=STATUS_FEHLER,
                    detail="Telegram lehnt den Bot-Token ab (401)")
    assert hc.main(["--config", str(cfg_path)], proc_root=proc) == 1
    assert "bot: FEHLER" in capsys.readouterr().out
    assert gesendet == ["bot: Telegram lehnt den Bot-Token ab (401)"]

    # Gleicher Zustand: keine zweite Meldung.
    assert hc.main(["--config", str(cfg_path)], proc_root=proc) == 1
    assert len(gesendet) == 1

    # Bot meldet sich wieder: Entwarnung.
    write_heartbeat(tmp_path, "bot")
    assert hc.main(["--config", str(cfg_path)], proc_root=proc) == 0
    assert len(gesendet) == 2 and "wieder" in gesendet[1]
    out = capsys.readouterr().out
    assert "bot: ok" in out and "web: ok - nicht eingeschaltet" in out
