"""Tests für Retry-Queue und retry_downloads (Issue #50)."""
import sqlite3
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.models import Config, DownloadKind, Track
from sc_digger.output import State
from sc_digger.retry import RetryItem, RetryQueue
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parent.parent / "config.yaml")
TOKEN = "secret-token-xyz-12345"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = Config(dict(CFG.raw))
    c.raw["search"] = {**CFG["search"], "tags": ["schranz"], "followed_users": [],
                       "reference_accounts": []}
    c.raw["state"] = {**CFG["state"], "db_path": str(tmp_path / "state.sqlite"),
                      "track_db_path": str(tmp_path / "tracks.sqlite")}
    c.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                         "inbox_dir": str(tmp_path / "inbox")}
    c.raw["organize"] = {**CFG["organize"], "detect_bpm": False, "detect_key": False,
                         "write_tags": False, "enabled": False}
    c.raw["rekordbox"] = {"xml_enabled": False}
    c.raw["retry"] = {"max_attempts": 3}
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(m, "finalize_quality", lambda t, path, inbox, cfg: path)
    return c


def _download_ok(tmp_path, monkeypatch):
    f = tmp_path / "inbox" / "ok.wav"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"audio-data")
    monkeypatch.setattr(m, "download_native", lambda t, inbox, token=None: f)


def _download_fails(monkeypatch):
    def fail(t, inbox, token=None):
        t.notes.append("Netzwerkfehler beim Download")
        return None
    monkeypatch.setattr(m, "download_native", fail)


class FakeSC:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.resolved = []

    def resolve_track(self, url):
        self.resolved.append(url)
        if url in self.routes:
            val = self.routes[url]
            if isinstance(val, Exception):
                raise val
            return val
        sc_id = int(url.rsplit("t", 1)[1])
        return mk(sc_id, downloadable=True)


def test_resolve_track_exception_counts_attempt(cfg):
    """Wenn resolve_track wirft, wird ein Fehlversuch gezählt."""
    with RetryQueue(cfg["state"]["db_path"], max_attempts=3) as q:
        q.record_failure(mk(1), "Initialer Fehler")

    sc = FakeSC(routes={"https://soundcloud.com/a/t1": RuntimeError("SoundCloud 500 Interner Fehler")})
    lines = m.retry_downloads(sc, cfg)
    # 2. Versuch -> noch pending, keine Zeile
    assert lines == []
    with RetryQueue(cfg["state"]["db_path"], max_attempts=3) as q:
        item = q.due()[0]
        assert item.attempts == 2
        assert "RuntimeError" in item.last_error

    # 3. Versuch -> nun failed und gemeldet
    lines = m.retry_downloads(sc, cfg)
    assert any("endgültig" in line and "Track 1" in line for line in lines)
    with RetryQueue(cfg["state"]["db_path"], max_attempts=3) as q:
        assert q.status(1) == "failed"
        assert q.due() == []


def test_track_now_in_collection_removes_entry_without_digest_line(cfg, monkeypatch):
    """Ist der Track inzwischen in der Sammlung (t.duplicate_of), wird er entfernt (keine Zeile)."""
    with RetryQueue(cfg["state"]["db_path"]) as q:
        q.record_failure(mk(1), "Download fehlgeschlagen")

    # Mocken, dass collection mark_duplicates t.duplicate_of setzt
    def fake_mark(self, tracks):
        for t in tracks:
            if t.id == 1:
                t.duplicate_of = "/music/Schranz/Track 1.wav"

    monkeypatch.setattr(m.Collection, "mark_duplicates", fake_mark)

    sc = FakeSC()
    lines = m.retry_downloads(sc, cfg)
    assert lines == []
    with RetryQueue(cfg["state"]["db_path"]) as q:
        assert q.status(1) is None
        assert q.due() == []


def test_track_no_longer_native_removes_entry_without_digest_line(cfg):
    """Track hat keinen Free Download mehr (z. B. Stream-only) -> entfernt, keine Zeile."""
    with RetryQueue(cfg["state"]["db_path"]) as q:
        q.record_failure(mk(1), "Download fehlgeschlagen")

    # Track ist nicht mehr downloadable (DownloadKind wird NONE)
    sc = FakeSC(routes={"https://soundcloud.com/a/t1": mk(1, downloadable=False)})
    lines = m.retry_downloads(sc, cfg)
    assert lines == []
    with RetryQueue(cfg["state"]["db_path"]) as q:
        assert q.status(1) is None
        assert q.due() == []


def test_seen_table_remains_untouched_during_retry(cfg, tmp_path, monkeypatch):
    """Tabelle seen in der State-DB bleibt durch retry_downloads unberührt."""
    _download_ok(tmp_path, monkeypatch)
    with State(cfg["state"]["db_path"]) as state:
        state.mark_one(mk(99, title="Track 99"))

    with RetryQueue(cfg["state"]["db_path"]) as q:
        q.record_failure(mk(1), "Download fehlgeschlagen")

    sc = FakeSC()
    lines = m.retry_downloads(sc, cfg)
    assert any("nachgeholt" in line for line in lines)

    # Prüfen, dass seen unverändert ist: Track 99 ist drin, Track 1 NICHT
    with State(cfg["state"]["db_path"]) as state:
        assert state.is_seen(99) is True
        assert state.is_seen(1) is False

    conn = sqlite3.connect(cfg["state"]["db_path"])
    seen_ids = [r[0] for r in conn.execute("SELECT id FROM seen").fetchall()]
    conn.close()
    assert seen_ids == [99]


def test_error_truncated_to_300_chars_and_redacted(tmp_path, monkeypatch):
    """Fehlermeldung wird auf 300 Zeichen gekürzt und sensible Daten maskiert."""
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    long_error = f"Error with {TOKEN}: " + "X" * 400
    with RetryQueue(tmp_path / "test.sqlite") as q:
        q.record_failure(mk(1), long_error)
        item = q.due()[0]

    assert TOKEN not in item.last_error
    assert len(item.last_error) <= 300


def test_multiple_recovered_and_multiple_failed_in_digest(cfg, tmp_path, monkeypatch):
    """Mehrere nachgeholte und fehlgeschlagene Tracks werden komma-separiert formatiert."""
    with RetryQueue(cfg["state"]["db_path"], max_attempts=3) as q:
        # 1 & 2 sollen nachgeholt werden (1 Versuch bisher)
        q.record_failure(mk(1, title="Track 1", user={"username": "Artist1"}), "Err")
        q.record_failure(mk(2, title="Track 2", user={"username": "Artist2"}), "Err")
        # 3 & 4 sollen endgültig scheitern (bereits 2 Versuche)
        q.record_failure(mk(3, title="Track 3", user={"username": "Artist3"}), "Err")
        q.record_failure(mk(3, title="Track 3", user={"username": "Artist3"}), "Err")
        q.record_failure(mk(4, title="Track 4", user={"username": "Artist4"}), "Err")
        q.record_failure(mk(4, title="Track 4", user={"username": "Artist4"}), "Err")

    # Download für 1 & 2 klappt, für 3 & 4 schlägt fehl
    f = tmp_path / "inbox" / "ok.wav"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"audio")

    def selective_download(t, inbox, token=None):
        if t.id in (1, 2):
            return f
        return None

    monkeypatch.setattr(m, "download_native", selective_download)

    sc = FakeSC()
    lines = m.retry_downloads(sc, cfg)
    assert len(lines) == 2
    assert lines[0] == "🔄 2 nachgeholt: Artist1 – Track 1, Artist2 – Track 2"
    assert lines[1] == "❌ 2 endgültig fehlgeschlagen (3 Versuche): Artist3 – Track 3, Artist4 – Track 4"


def test_retry_queue_ordering_by_created_at(tmp_path):
    """due() liefert älteste Einträge zuerst."""
    with RetryQueue(tmp_path / "test.sqlite") as q:
        q.record_failure(mk(10), "Err")
        q.record_failure(mk(20), "Err")
        q.record_failure(mk(30), "Err")
        due_ids = [item.sc_id for item in q.due()]
        assert due_ids == [10, 20, 30]


def test_discover_combines_retry_and_source_footer(cfg, monkeypatch, capsys):
    """In _discover stehen retry_lines vor dem source_footer."""
    class SC(FakeSC):
        def search_tag(self, tag, age, limit):
            return [mk(100, playback_count=2000, likes_count=50)]

    def fake_retry(sc, cfg):
        return ["🔄 1 nachgeholt: Artist1 – Track 1"]

    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: SC())
    monkeypatch.setattr(m, "retry_downloads", fake_retry)

    # Eine Fake-Discovery simulieren, die eine fehlgeschlagene Quelle meldet
    orig_collect = m.collect_sources

    def fake_collect(sc, search_cfg):
        d = orig_collect(sc, search_cfg)
        d.failed.append("hardtechno")
        return d

    monkeypatch.setattr(m, "collect_sources", fake_collect)

    m._discover(cfg, False, True)
    out = capsys.readouterr().out
    assert "🔄 1 nachgeholt: Artist1 – Track 1" in out
    assert "⚠️ 1 von" in out
    assert out.index("nachgeholt") < out.index("⚠️ 1 von")
