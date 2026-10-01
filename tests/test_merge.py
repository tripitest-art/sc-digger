"""Tests für die Zusammenführung von Server-Zweig (Bot, Referenzen, Export, Lautheit)
und GitHub-Zweig (Analyse, Organize, Health), inkl. der auf dem Server gefundenen Fehler."""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from sc_digger import bot
from sc_digger import main as m
from sc_digger import output as out
from sc_digger.models import Config, DownloadKind
from sc_digger.soundcloud import SoundCloudError
from tests.test_modes import mk

ROOT = Path(__file__).parent.parent
CFG = Config.load(ROOT / "config.yaml")


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    cfg.raw["state"] = {"db_path": str(tmp_path / "state.sqlite")}
    cfg.raw["search"] = {**CFG["search"], "reference_accounts": [], "followed_users": []}
    return cfg


# ---------------- Cron-Umgebung (Server-Bug: "python: not found", kein Token) ----------------
def test_dockerfile_cron_job_sources_env_file():
    cron_line = next(l for l in (ROOT / "Dockerfile").read_text().splitlines() if "30 7 * * *" in l)
    assert ". /app/cron.env;" in cron_line
    assert "export -p > /app/cron.env" in (ROOT / "entrypoint.sh").read_text()


@pytest.mark.skipif(
    shutil.which("sh") is None or shutil.which("env") is None,
    reason="Benötigt POSIX sh und env (Cron-Umgebungstest auf Server/CI)",
)
def test_exported_env_restores_path_and_secrets_in_clean_cron_env(tmp_path):
    """Nachstellung: entrypoint schreibt export -p, cron startet mit env -i und lädt die Datei."""
    envfile = tmp_path / "cron.env"
    # Unter Windows (Git-sh) verschluckt sh die Backslashes eines nativen Pfads; die Datei
    # (voller Umgebungs-Dump!) landete dann im Arbeitsverzeichnis statt in tmp_path.
    # Daher POSIX-Schreibweise (C:/…) und gequotet.
    sh_envfile = shlex.quote(envfile.as_posix())
    secret = "123:ab'c d\"e$f"                     # Sonderzeichen dürfen nichts kaputtmachen
    subprocess.run(["sh", "-c", f"export -p > {sh_envfile}"], check=True,
                   env={**os.environ, "PATH": f"/opt/fake/bin:{os.environ['PATH']}",
                        "TELEGRAM_BOT_TOKEN": secret})
    assert envfile.is_file()                       # wirklich in tmp_path, nicht im cwd
    r = subprocess.run(
        ["env", "-i", "HOME=/root", "PATH=/usr/bin:/bin", "sh", "-c",
         f'. {sh_envfile}; printf "%s\\n%s" "$PATH" "$TELEGRAM_BOT_TOKEN"'],
        capture_output=True, text=True, check=True,
    )
    path, token = r.stdout.split("\n", 1)
    assert path.startswith("/opt/fake/bin:")
    assert token == secret


# ---------------- Download: nur Originale, nie Stream-Rips ----------------
def test_no_token_means_no_download_attempt(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(out.subprocess, "run", lambda *a, **k: called.append(a))
    t = mk(1, downloadable=True)
    assert out.download_native(t, tmp_path, None) is None
    assert called == []
    assert any("SOUNDCLOUD_AUTH_TOKEN" in n for n in t.notes)


def test_download_uses_only_original_and_detects_silent_failure(tmp_path, monkeypatch, caplog):
    """scdl meldet 'format not available' mit Exit-Code 0 -> Erfolg nur über neue Datei."""
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "ERROR: Requested format is not available tok123")
    monkeypatch.setattr(out.subprocess, "run", fake_run)
    t = mk(1, downloadable=True)
    assert out.download_native(t, tmp_path, "tok123") is None
    assert "--only-original" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--auth-token") + 1] == "tok123"
    assert "tok123" not in caplog.text                     # Token nie im Log
    assert any("fehlgeschlagen" in n for n in t.notes)


def test_download_finds_new_file_in_subfolder(tmp_path, monkeypatch):
    (tmp_path / "150-155" / "5A").mkdir(parents=True)
    (tmp_path / "150-155" / "5A" / "old.wav").write_bytes(b"x")

    def fake_run(cmd, **kw):
        (tmp_path / "new.wav").write_bytes(b"y")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(out.subprocess, "run", fake_run)
    assert out.download_native(mk(1), tmp_path, "tok").name == "new.wav"


# ---------------- finalize_quality ----------------
@pytest.mark.parametrize("report, expected_dir", [
    ({"ok": True, "clipped": False}, None),
    ({"ok": False}, "_rejected"),
    ({"ok": True, "clipped": True}, "_rejected/clipped"),
])
def test_finalize_quality_routes_files(tmp_path, monkeypatch, report, expected_dir):
    f = tmp_path / "t.wav"
    f.write_bytes(b"x")
    monkeypatch.setattr(out, "check_file", lambda p, cfg: report)
    result = out.finalize_quality(mk(1), f, tmp_path, CFG)
    if expected_dir is None:
        assert result == f and f.exists()
    else:
        assert result is None and (tmp_path / expected_dir / "t.wav").exists()


# ---------------- Digest / Export ----------------
def test_native_without_file_gets_manual_link():
    t = mk(1, downloadable=True)
    t.download_kind = DownloadKind.NATIVE
    text = out.build_digest([t], None, header="T")[0]
    assert "Original manuell laden" in text


def test_reference_hit_marked_in_digest_and_export():
    t = mk(1)
    t.reference_hit = True
    assert "⭐" in out.build_digest([t], None, header="T")[0]
    assert "[REF: " in out.build_export_txt([t])


def test_export_contains_every_track():
    tracks = [mk(i) for i in range(30)]
    txt = out.build_export_txt(tracks, folder_name="X")
    assert txt.count("soundcloud.com/a/t") == 30 and "Total Tracks Exported: 30" in txt


# ---------------- Referenz-Accounts in der Discovery ----------------
def test_discover_merges_reference_activity_with_genre_filter(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    cfg.raw["search"]["reference_accounts"] = ["https://soundcloud.com/ref"]

    class FakeSC:
        def search_tag(self, tag, age, limit):
            return [mk(i, playback_count=2000, likes_count=40 + i) for i in range(20)]
        def reference_activity(self, url, age, limit):
            return [mk(500, title="Schranz Tool", playback_count=2000, likes_count=200),
                    mk(501, title="Deep House Chill", playback_count=2000, likes_count=200)]
    monkeypatch.setattr(m, "SoundCloudClient", FakeSC)
    captured = {}
    real_process = m.process

    def spy(tracks, cfg, **kw):
        captured["tracks"] = list(tracks)
        return real_process(tracks, cfg, **kw)
    monkeypatch.setattr(m, "process", spy)

    found = m._discover(cfg, dry_run=True, no_telegram=True)
    assert found == 21                                   # 20 + 1 genre-relevanter Ref-Track
    by_id = {t.id: t for t in captured["tracks"]}
    assert 501 not in by_id                              # House-Repost fliegt raus
    assert by_id[500].reference_hit                      # Ref-Track markiert und dank Boost dabei
    assert not any(t.reference_hit for t in captured["tracks"] if t.id != 500)


# ---------------- On-Demand (Bot/check) ----------------
class FakeLinkSC:
    def __init__(self, kind):
        self.kind = kind
    def resolve_kind(self, url):
        return self.kind
    def playlist_tracks(self, url, limit=None):
        return "Liste", [mk(1, playback_count=5, likes_count=0), mk(2)]
    def track_station(self, url, limit=50):
        return "A – Seed", [mk(3), mk(4)]


def test_run_link_playlist_keeps_low_play_tracks_and_marks_seen(tmp_path):
    cfg = _cfg(tmp_path)
    source, fresh, dupes = m.run_link(cfg, "u", no_telegram=True, sc=FakeLinkSC("playlist"))
    assert source == "Playlist: Liste" and fresh == 2      # kein Perzentil-Filter
    import sqlite3
    assert sqlite3.connect(tmp_path / "state.sqlite").execute("select count(*) from seen").fetchone()[0] == 2


def test_run_link_station(tmp_path):
    source, fresh, _ = m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=FakeLinkSC("track"))
    assert source == "Station zu: A – Seed" and fresh == 2


def test_run_link_rejects_user_links(tmp_path):
    with pytest.raises(SoundCloudError):
        m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=FakeLinkSC("user"))


# ---------------- Bot ----------------
def test_bot_without_credentials_idles_instead_of_exiting(monkeypatch):
    """Server-Bug: Bot beendete sich -> Container-Neustartschleife -> Cron lief nie."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    class Idled(Exception):
        pass

    def fake_idle(reason):
        raise Idled(reason)
    monkeypatch.setattr(bot, "_idle_forever", fake_idle)
    with pytest.raises(Idled, match="fehlen"):
        bot.listen(CFG)


def test_bot_routes_links_and_errors(monkeypatch):
    sent, calls = [], []
    monkeypatch.setattr(bot, "_send_text", lambda cfg, chat, text: sent.append(text))
    monkeypatch.setattr(bot, "run_link", lambda cfg, url, **kw: calls.append(url))

    bot.handle_message(CFG, None, "1", "/help")
    bot.handle_message(CFG, None, "1", "hallo")
    bot.handle_message(CFG, None, "1", "schau mal https://on.soundcloud.com/abc123 bitte")
    assert "sc-digger Bot" in sent[0] and "Kein SoundCloud-Link" in sent[1]
    assert calls == ["https://on.soundcloud.com/abc123"]

    def boom(*a, **k):
        raise SoundCloudError("Kein Track")
    monkeypatch.setattr(bot, "run_link", boom)
    bot.handle_message(CFG, None, "1", "https://soundcloud.com/x/y")
    assert sent[-1] == "Fehler: Kein Track"


def test_bot_logs_foreign_chat_id_and_warns_on_non_numeric_chat(monkeypatch, caplog):
    """Server-Befund: TELEGRAM_CHAT_ID enthielt den Bot-Namen -> alle Nachrichten still ignoriert."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "Curationscbot.")
    monkeypatch.setattr(bot, "SoundCloudClient", lambda: None)
    handled = []
    monkeypatch.setattr(bot, "handle_message", lambda *a: handled.append(a))

    class Resp:
        status_code = 200
        def __init__(self, result): self._r = result
        def raise_for_status(self): pass
        def json(self): return {"ok": True, "result": self._r}

    calls = iter([
        Resp([]),                                                        # Backlog beim Start
        Resp([{"update_id": 5, "message": {"chat": {"id": 4242, "type": "private",
                                                     "first_name": "Stephan"}, "text": "GEHEIMTEXT-123"}}]),
    ])

    def fake_get(*a, **k):
        try:
            return next(calls)
        except StopIteration:
            raise KeyboardInterrupt                                      # Loop beenden
    monkeypatch.setattr(out.requests, "get", fake_get)
    with pytest.raises(KeyboardInterrupt):
        bot.listen(Config.load(ROOT / "config.yaml"))
    assert "keine Zahl" in caplog.text
    assert "id=4242" in caplog.text and "Stephan" in caplog.text
    assert "GEHEIMTEXT" not in caplog.text                               # kein Nachrichtentext
    assert handled == []


# ---------------- Zugangsdaten nie in Logs / Health / Fehlern ----------------
from sc_digger.redact import redact

TG_TOKEN = "1234567890:AAFakeTokenFakeTokenFakeTokenFake0"


def test_redact_telegram_url_and_env_secrets(monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", "2-123456-9999-SECRETsecret")
    text = (f"400 Client Error for url: https://api.telegram.org/bot{TG_TOKEN}/sendMessage "
            f"cmd: scdl --auth-token 2-123456-9999-SECRETsecret")
    r = redact(text)
    assert TG_TOKEN not in r and "SECRETsecret" not in r
    assert "bot***" in r and "--auth-token ***" in r


def test_telegram_error_has_description_not_token(monkeypatch):
    class R:
        status_code = 400
        def json(self): return {"ok": False, "description": "Bad Request: chat not found"}
    monkeypatch.setattr(out.requests, "post", lambda url, **kw: R())
    with pytest.raises(out.TelegramError) as ei:
        out.telegram_call(TG_TOKEN, "sendMessage", json={})
    assert "chat not found" in str(ei.value) and TG_TOKEN not in str(ei.value)
    assert ei.value.__cause__ is None                       # kein angehängter HTTPError mit URL


def test_telegram_network_error_is_redacted(monkeypatch):
    import requests as rq

    def boom(url, **kw):
        raise rq.ConnectionError(f"Max retries exceeded with url: {url}")
    monkeypatch.setattr(out.requests, "post", boom)
    with pytest.raises(out.TelegramError) as ei:
        out.telegram_call(TG_TOKEN, "sendMessage", json={})
    assert TG_TOKEN not in str(ei.value)


def test_health_record_redacts(tmp_path):
    from sc_digger.health import Health
    with Health(tmp_path / "h.sqlite") as h:
        h.record("discover", ok=False, found=0, error=f"HTTPError url: .../bot{TG_TOKEN}/sendMessage")
        stored = h.db.execute("select error from runs").fetchone()[0]
    assert TG_TOKEN not in stored and "bot***" in stored


def test_scdl_timeout_does_not_leak_token(tmp_path, monkeypatch, caplog):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 300)
    monkeypatch.setattr(out.subprocess, "run", slow)
    t = mk(1)
    assert out.download_native(t, tmp_path, "2-SECRET-TOKEN-xyz") is None
    assert "2-SECRET-TOKEN-xyz" not in caplog.text and "Timeout" in t.notes[-1]


def test_cli_failure_goes_through_redacting_log(tmp_path, monkeypatch, capsys):
    """Uncaught Tracebacks (cron!) würden an der Bereinigung vorbeigehen -> cli fängt und loggt."""
    import logging
    monkeypatch.setattr("sys.argv", ["sc-digger", "--config", str(ROOT / "config.yaml")])

    def boom(*a, **k):
        raise RuntimeError(f"failed url https://api.telegram.org/bot{TG_TOKEN}/sendMessage")
    monkeypatch.setattr(m, "run_discover", boom)
    root = logging.getLogger()
    old = root.handlers[:]
    root.handlers = [logging.StreamHandler()]                # frischer Handler -> stderr
    try:
        with pytest.raises(SystemExit) as ei:
            m.cli()
    finally:
        root.handlers = old
    err = capsys.readouterr().err
    assert ei.value.code == 1
    assert "Lauf fehlgeschlagen" in err and TG_TOKEN not in err and "bot***" in err
