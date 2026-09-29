"""Akzeptanztests: fehlgeschlagene Original-Downloads werden bis zu 3-mal versucht."""
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.models import Config
from sc_digger.retry import RetryQueue
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
TOKEN = "test-token-12345"


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
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(m, "finalize_quality", lambda t, path, inbox, cfg: path)
    return c


def _download_ok(tmp_path, monkeypatch):
    f = tmp_path / "inbox" / "x.wav"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    monkeypatch.setattr(m, "download_native", lambda t, inbox, token=None: f)


def _download_fails(monkeypatch):
    def fail(t, inbox, token=None):
        t.notes.append("Original-Download fehlgeschlagen")
        return None
    monkeypatch.setattr(m, "download_native", fail)


class FakeSC:
    def __init__(self):
        self.resolved = []

    def resolve_track(self, url):
        self.resolved.append(url)
        return mk(int(url.rsplit("t", 1)[1]), downloadable=True)


def _queue(cfg):
    return RetryQueue(cfg["state"]["db_path"], max_attempts=3)


def test_config_defaults():
    import yaml
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "config.yaml").read_text(encoding="utf-8"))
    assert raw["retry"]["max_attempts"] == 3


def test_queue_counts_attempts_and_gives_up_after_three(tmp_path):
    with RetryQueue(tmp_path / "s.sqlite", max_attempts=3) as q:
        t = mk(1)
        assert q.record_failure(t, "Timeout") == "pending"
        assert q.record_failure(t, "Timeout") == "pending"
        assert [i.sc_id for i in q.due()] == [1]
        assert q.record_failure(t, "Timeout") == "failed"
        assert q.due() == []
        assert q.status(1) == "failed"


def test_success_removes_entry(tmp_path):
    with RetryQueue(tmp_path / "s.sqlite") as q:
        q.record_failure(mk(1), "Timeout")
        assert q.record_success(1) is True
        assert q.status(1) is None
        assert q.record_success(2) is False


def test_stored_error_is_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", TOKEN)
    with RetryQueue(tmp_path / "s.sqlite") as q:
        q.record_failure(mk(1), f"scdl --auth-token {TOKEN} abgebrochen")
        item = q.due()[0]
    assert TOKEN not in (item.last_error or "")


def test_process_queues_failed_native_download(cfg, monkeypatch):
    _download_fails(monkeypatch)
    m.process([mk(1, downloadable=True)], cfg, dry_run=False)
    with _queue(cfg) as q:
        assert [i.sc_id for i in q.due()] == [1]


def test_no_queue_without_token_or_in_dry_run(cfg, monkeypatch):
    _download_fails(monkeypatch)
    m.process([mk(1, downloadable=True)], cfg, dry_run=True)
    monkeypatch.delenv("SOUNDCLOUD_AUTH_TOKEN")
    m.process([mk(2, downloadable=True)], cfg, dry_run=False)
    with _queue(cfg) as q:
        assert q.due() == []


def test_retry_success_is_reported(cfg, tmp_path, monkeypatch):
    with _queue(cfg) as q:
        q.record_failure(mk(1), "Timeout")
    _download_ok(tmp_path, monkeypatch)
    sc = FakeSC()
    lines = m.retry_downloads(sc, cfg)
    assert sc.resolved == ["https://soundcloud.com/a/t1"]
    assert any("nachgeholt" in line and "Track 1" in line for line in lines)
    with _queue(cfg) as q:
        assert q.status(1) is None


def test_final_failure_is_reported_once(cfg, monkeypatch):
    with _queue(cfg) as q:
        q.record_failure(mk(1), "Timeout")
        q.record_failure(mk(1), "Timeout")
    _download_fails(monkeypatch)
    lines = m.retry_downloads(FakeSC(), cfg)
    assert any("endgültig" in line and "Track 1" in line for line in lines)
    assert m.retry_downloads(FakeSC(), cfg) == []
    with _queue(cfg) as q:
        assert q.status(1) == "failed"


def test_discover_runs_retry_first_and_shows_it_in_digest(cfg, monkeypatch, capsys):
    order = []

    class SC(FakeSC):
        def search_tag(self, tag, age, limit):
            order.append("search")
            return [mk(i, playback_count=2000, likes_count=40 + i) for i in range(40)]

    def fake_retry(sc, cfg):
        order.append("retry")
        return ["🔄 1 nachgeholt: Artist9 – Track 9"]

    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: SC())
    monkeypatch.setattr(m, "retry_downloads", fake_retry)
    m._discover(cfg, False, True)
    assert order == ["retry", "search"]
    assert "nachgeholt: Artist9" in capsys.readouterr().out


def test_dry_run_does_not_retry(cfg, monkeypatch):
    class SC(FakeSC):
        def search_tag(self, tag, age, limit):
            return [mk(i, playback_count=2000, likes_count=40 + i) for i in range(40)]

    called = []
    monkeypatch.setattr(m, "SoundCloudClient", lambda *a, **k: SC())
    monkeypatch.setattr(m, "retry_downloads", lambda sc, cfg: called.append(1) or [])
    m._discover(cfg, True, True)
    assert called == []
