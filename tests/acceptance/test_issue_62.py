"""Akzeptanztests: Container-Healthcheck erkennt toten Cron, ausbleibende Läufe und kaputte DBs."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from sc_digger import healthcheck as hc
from sc_digger.health import Health
from sc_digger.healthcheck import CheckResult

ROOT = Path(__file__).resolve().parents[2]


def fake_proc(tmp_path, names):
    root = tmp_path / "proc"
    for pid, name in enumerate(names, start=1):
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "comm").write_text(name + "\n")
    (root / "self").mkdir(parents=True, exist_ok=True)   # kein PID-Ordner, darf nicht stören
    return root


def write_cfg(tmp_path):
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["state"] = {"db_path": str(tmp_path / "data" / "seen.sqlite"),
                    "track_db_path": str(tmp_path / "data" / "tracks.sqlite")}
    raw["health"] = {**raw.get("health", {}), "max_hours_since_run": 36}
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return p


def test_config_default():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["health"]["max_hours_since_run"] == 36


def test_dockerfile_declares_healthcheck():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in text and "sc_digger.healthcheck" in text


def test_cron_process_detected(tmp_path):
    assert hc.check_cron(fake_proc(tmp_path / "a", ["docker-init", "python", "cron"])).ok
    r = hc.check_cron(fake_proc(tmp_path / "b", ["docker-init", "python"]))
    assert not r.ok and r.name == "cron"


def test_last_run_age(tmp_path):
    db = tmp_path / "seen.sqlite"
    assert hc.check_last_run(db, 36).ok                  # Neuinstallation: noch kein Lauf
    assert not db.exists()                               # Prüfung legt nichts an
    with Health(db) as h:
        h.record("discover", ok=False, found=0, error="x")   # gescheiterter Lauf zählt: Cron lief
    now = datetime.now(timezone.utc)
    assert hc.check_last_run(db, 36, now=now).ok
    late = hc.check_last_run(db, 36, now=now + timedelta(hours=40))
    assert not late.ok and late.name == "letzter_lauf"


def test_sqlite_quick_check(tmp_path):
    good = tmp_path / "good.sqlite"
    sqlite3.connect(good).execute("CREATE TABLE t (x)").connection.commit()
    assert hc.check_sqlite(good, "state_db").ok
    missing = tmp_path / "missing.sqlite"
    assert hc.check_sqlite(missing, "track_db").ok
    assert not missing.exists()
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"kein sqlite " * 400)
    r = hc.check_sqlite(broken, "track_db")
    assert not r.ok and r.name == "track_db"


def test_alert_once_then_recovery_once(tmp_path):
    state = tmp_path / "container_health.json"
    bad = [CheckResult("cron", False, "cron läuft nicht"), CheckResult("state_db", True, "ok")]
    good = [CheckResult("cron", True, "ok"), CheckResult("state_db", True, "ok")]
    msg = hc.alert_message(state, bad)
    assert msg and "cron läuft nicht" in msg
    assert hc.alert_message(state, bad) is None
    back = hc.alert_message(state, good)
    assert back and "wieder" in back
    assert hc.alert_message(state, good) is None


def test_main_exit_code_and_single_telegram_alert(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "A" * 30)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []
    monkeypatch.setattr(hc, "telegram_call", lambda token, method, **kw: sent.append(kw) or {"ok": True})

    no_cron = fake_proc(tmp_path / "p1", ["docker-init", "python"])
    assert hc.main(["--config", str(cfg)], proc_root=no_cron) == 1
    assert hc.main(["--config", str(cfg)], proc_root=no_cron) == 1
    assert len(sent) == 1

    with_cron = fake_proc(tmp_path / "p2", ["docker-init", "python", "cron"])
    assert hc.main(["--config", str(cfg)], proc_root=with_cron) == 0
    assert len(sent) == 2


def test_telegram_failure_does_not_change_exit_code(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "A" * 30)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    def down(*a, **k):
        raise hc.TelegramError("sendMessage: Telegram 502")
    monkeypatch.setattr(hc, "telegram_call", down)
    assert hc.main(["--config", str(cfg)], proc_root=fake_proc(tmp_path, ["python"])) == 1
