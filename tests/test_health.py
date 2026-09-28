from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.health import Health
from sc_digger.models import Config

CFG = Config.load(Path(__file__).parent.parent / "config.yaml")


# ---------------- Health-Modul ----------------
def test_no_alert_on_single_bad_run(tmp_path):
    with Health(tmp_path / "h.sqlite") as h:
        h.record("discover", ok=False, found=0, error="boom")
        assert h.evaluate("discover", threshold=2) is None


def test_alert_after_threshold_then_only_once(tmp_path):
    with Health(tmp_path / "h.sqlite") as h:
        h.record("discover", ok=False, found=0, error="SoundCloudError: client_id konnte nicht ermittelt werden")
        h.evaluate("discover", 2)
        h.record("discover", ok=False, found=0, error="SoundCloudError: client_id konnte nicht ermittelt werden")
        msg = h.evaluate("discover", 2)
        assert msg and "🚨" in msg and "client_id" in msg and "_fetch_client_id" in msg
        h.record("discover", ok=False, found=0, error="noch kaputt")
        assert h.evaluate("discover", 2) is None          # kein Alarm-Spam


def test_zero_hits_without_error_also_alerts(tmp_path):
    with Health(tmp_path / "h.sqlite") as h:
        for _ in range(2):
            h.record("discover", ok=True, found=0)
            msg = h.evaluate("discover", 2)
        assert msg and "0 Rohtreffer" in msg and "search_tag" in msg


def test_recovery_message_once(tmp_path):
    with Health(tmp_path / "h.sqlite") as h:
        for _ in range(2):
            h.record("discover", ok=False, found=0, error="x")
            h.evaluate("discover", 2)
        h.record("discover", ok=True, found=120)
        msg = h.evaluate("discover", 2)
        assert msg and "läuft wieder" in msg and "120" in msg
        h.record("discover", ok=True, found=130)
        assert h.evaluate("discover", 2) is None


def test_mixed_runs_do_not_alert(tmp_path):
    with Health(tmp_path / "h.sqlite") as h:
        h.record("discover", ok=False, found=0, error="x")
        h.record("discover", ok=True, found=50)
        assert h.evaluate("discover", 2) is None


def test_alert_state_survives_restart(tmp_path):
    db = tmp_path / "h.sqlite"
    with Health(db) as h:
        for _ in range(2):
            h.record("discover", ok=False, found=0, error="x")
            h.evaluate("discover", 2)
    with Health(db) as h:                                    # neuer Cron-Lauf = neuer Prozess
        h.record("discover", ok=False, found=0, error="x")
        assert h.evaluate("discover", 2) is None


def test_error_text_is_html_escaped(tmp_path):
    with Health(tmp_path / "h.sqlite") as h:
        for _ in range(2):
            h.record("discover", ok=False, found=0, error="HTTPError <403> & co")
            msg = h.evaluate("discover", 2)
        assert "&lt;403&gt; &amp; co" in msg


# ---------------- Einbindung in run_discover ----------------
def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {"db_path": str(tmp_path / "state.sqlite")}
    cfg.raw["health"] = {"alert_after_bad_runs": 2}
    return cfg


def test_run_discover_alerts_after_repeated_crashes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    sent = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs: sent.extend(msgs))

    def boom(*a, **k):
        raise RuntimeError("client_id konnte nicht ermittelt werden")
    monkeypatch.setattr(m, "_discover", boom)

    for _ in range(2):
        with pytest.raises(RuntimeError):                  # Fehler bleibt sichtbar (Exit-Code)
            m.run_discover(cfg, dry_run=False, no_telegram=False)
    assert len(sent) == 1 and "🚨" in sent[0]


def test_run_discover_quiet_day_is_not_an_alarm(tmp_path, monkeypatch):
    """0 NEUE Tracks nach Filter ist normal; nur 0 ROHtreffer zählt als Ausfall."""
    cfg = _cfg(tmp_path)
    sent = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs: sent.extend(msgs))
    monkeypatch.setattr(m, "_discover", lambda *a: 250)   # viele Rohtreffer, alle schon gesehen
    for _ in range(3):
        m.run_discover(cfg, dry_run=False, no_telegram=False)
    assert sent == []


def test_failing_telegram_does_not_mask_original_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    def tg_down(*a, **k):
        raise ConnectionError("telegram down")
    monkeypatch.setattr(m, "send_telegram", tg_down)

    def boom(*a, **k):
        raise ValueError("original")
    monkeypatch.setattr(m, "_discover", boom)
    for _ in range(2):
        with pytest.raises(ValueError, match="original"):
            m.run_discover(cfg, dry_run=False, no_telegram=False)


def test_dry_run_is_not_recorded(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(m, "_discover", lambda *a: 0)
    for _ in range(3):
        m.run_discover(cfg, dry_run=True, no_telegram=True)
    assert not (tmp_path / "state.sqlite").exists()


def test_real_discover_path_shares_db_with_state(tmp_path, monkeypatch, capsys):
    """_discover + State + Health auf derselben SQLite-Datei, Rohtreffer werden korrekt gezählt."""
    from tests.test_modes import mk
    cfg = _cfg(tmp_path)
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}

    class FakeSC:
        def search_tag(self, tag, age, limit):
            return [mk(i, playback_count=2000, likes_count=40 + i) for i in range(40)]
        def user_uploads(self, url, age):
            return []
    monkeypatch.setattr(m, "SoundCloudClient", FakeSC)

    m.run_discover(cfg, dry_run=False, no_telegram=True)
    import sqlite3
    db = sqlite3.connect(tmp_path / "state.sqlite")
    assert db.execute("SELECT found, ok FROM runs").fetchall() == [(40, 1)]   # dedupe über 4 Tags
    assert db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] > 0
