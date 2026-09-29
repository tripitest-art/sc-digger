"""Tests für resiliente SoundCloud-Quellen-Abfrage in Discovery."""
from pathlib import Path
import pytest

from sc_digger import main as m
from sc_digger.models import Config, Track
from sc_digger.soundcloud import ClientIdError, RateLimitError, SoundCloudError
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")


def _cfg(tmp_path, *, tags=("schranz",), followed=(), reference=()):
    cfg = Config(dict(CFG.raw))
    cfg.raw["search"] = {
        **CFG["search"],
        "tags": list(tags),
        "followed_users": list(followed),
        "reference_accounts": list(reference),
    }
    cfg.raw["state"] = {
        **CFG["state"],
        "db_path": str(tmp_path / "state.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["download"] = {
        **CFG["download"],
        "collection_dir": str(tmp_path / "coll"),
        "inbox_dir": str(tmp_path / "inbox"),
    }
    return cfg


class DummySC:
    def __init__(self, *, tag_errors=None, user_errors=None, ref_errors=None):
        self.tag_errors = tag_errors or {}
        self.user_errors = user_errors or {}
        self.ref_errors = ref_errors or {}
        self.calls = []

    def search_tag(self, tag, age, limit):
        self.calls.append(("tag", tag))
        if tag in self.tag_errors:
            raise self.tag_errors[tag]
        return [mk(1, playback_count=1000, likes_count=50)]

    def user_uploads(self, url, age):
        self.calls.append(("user", url))
        if url in self.user_errors:
            raise self.user_errors[url]
        return [mk(2, playback_count=1000, likes_count=50)]

    def reference_activity(self, url, age, limit=50):
        self.calls.append(("ref", url))
        if url in self.ref_errors:
            raise self.ref_errors[url]
        t = mk(3, playback_count=1000, likes_count=50)
        t.genre = "Schranz"
        return [t]


def test_reference_account_error_recorded_in_failed():
    sc = DummySC(ref_errors={"https://soundcloud.com/curator": SoundCloudError("Profile 404")})
    search_cfg = {
        "tags": ["schranz"],
        "followed_users": [],
        "reference_accounts": ["https://soundcloud.com/curator"],
        "max_age_days": 7,
        "limit_per_tag": 10,
    }
    d = m.collect_sources(sc, search_cfg)
    assert d.total_sources == 2
    assert d.succeeded == 1
    assert len(d.failed) == 1
    assert "https://soundcloud.com/curator: SoundCloudError: Profile 404" in d.failed[0]
    assert d.aborted is None
    assert len(d.tracks) == 1


def test_followed_user_error_recorded_in_failed():
    sc = DummySC(user_errors={"https://soundcloud.com/artist": SoundCloudError("User not found")})
    search_cfg = {
        "tags": ["schranz"],
        "followed_users": ["https://soundcloud.com/artist"],
        "reference_accounts": [],
        "max_age_days": 7,
        "limit_per_tag": 10,
    }
    d = m.collect_sources(sc, search_cfg)
    assert d.total_sources == 2
    assert d.succeeded == 1
    assert len(d.failed) == 1
    assert "https://soundcloud.com/artist: SoundCloudError: User not found" in d.failed[0]
    assert d.aborted is None


def test_failed_entry_redacts_secrets(monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", "super_secret_oauth_token_12345")
    sc = DummySC(tag_errors={"schranz": RuntimeError("API failed with super_secret_oauth_token_12345")})
    search_cfg = {
        "tags": ["schranz"],
        "followed_users": [],
        "reference_accounts": [],
        "max_age_days": 7,
        "limit_per_tag": 10,
    }
    d = m.collect_sources(sc, search_cfg)
    assert len(d.failed) == 1
    assert "super_secret_oauth_token_12345" not in d.failed[0]
    assert "***" in d.failed[0]


def test_failed_entry_max_200_chars():
    long_msg = "x" * 500
    sc = DummySC(tag_errors={"schranz": RuntimeError(long_msg)})
    search_cfg = {
        "tags": ["schranz"],
        "followed_users": [],
        "reference_accounts": [],
        "max_age_days": 7,
        "limit_per_tag": 10,
    }
    d = m.collect_sources(sc, search_cfg)
    assert len(d.failed) == 1
    assert len(d.failed[0]) <= 200


def test_source_footer_formatting():
    # 1. Kein Fehler -> None
    d_ok = m.Discovery(
        tracks=[],
        reference_ids=set(),
        total_sources=3,
        succeeded=3,
        failed=[],
        aborted=None,
        first_error=None,
    )
    assert m.source_footer(d_ok) is None

    # 2. Fehler ohne Abbruch -> 1 Zeile
    d_err = m.Discovery(
        tracks=[],
        reference_ids=set(),
        total_sources=3,
        succeeded=2,
        failed=["hardtechno: SoundCloudError: 500"],
        aborted=None,
        first_error=RuntimeError(),
    )
    footer_err = m.source_footer(d_err)
    assert footer_err == "⚠️ 1 von 3 Quellen fehlgeschlagen: hardtechno: SoundCloudError: 500"

    # 3. Fehler mit Abbruch -> 2 Zeilen
    d_abort = m.Discovery(
        tracks=[],
        reference_ids=set(),
        total_sources=3,
        succeeded=1,
        failed=["hardtechno: RateLimitError: 429"],
        aborted="RateLimitError",
        first_error=RateLimitError(),
    )
    footer_abort = m.source_footer(d_abort)
    lines = footer_abort.split("\n")
    assert len(lines) == 2
    assert lines[0] == "⚠️ 1 von 3 Quellen fehlgeschlagen: hardtechno: RateLimitError: 429"
    assert lines[1] == "Restliche Quellen übersprungen (RateLimitError)"


def test_deliver_appends_footer_and_respects_max_message_length(monkeypatch, tmp_path):
    sent_messages = []

    def fake_send_digest(cfg, messages, chat_id=None, buttons=True):
        sent_messages.extend(messages)

    monkeypatch.setattr(m, "send_digest", fake_send_digest)
    cfg = _cfg(tmp_path)

    # 1. Normaler Footer wird an letzte Nachricht angehängt
    track = mk(10, artist="DJ A", title="Track 1")
    m.deliver(
        "Header",
        [track],
        [],
        cfg,
        dry_run=False,
        no_telegram=False,
        footer="⚠️ 1 von 2 Quellen fehlgeschlagen: test",
    )
    assert len(sent_messages) == 1
    assert "⚠️ 1 von 2 Quellen fehlgeschlagen: test" in sent_messages[0].text
    assert len(sent_messages[0].text) <= 4096

    # 2. Langer Footer, der 3900 Zeichen überschreiten würde, wird als eigene Nachricht gesendet
    sent_messages.clear()
    long_footer = "⚠️ Fehler: " + ("A" * 3850)
    m.deliver(
        "Header",
        [track],
        [],
        cfg,
        dry_run=False,
        no_telegram=False,
        footer=long_footer,
    )
    # Sollte in eigene Nachricht(en) ausgelagert werden
    assert len(sent_messages) >= 2
    for msg in sent_messages:
        assert len(msg.text) <= 4096

    # 3. Sehr langer Footer (> 4096 Zeichen) sprengt keine Nachricht (alle <= 4096)
    sent_messages.clear()
    giant_footer = "⚠️ Riesiger Fehler: " + ("Z" * 8000)
    m.deliver(
        "Header",
        [track],
        [],
        cfg,
        dry_run=False,
        no_telegram=False,
        footer=giant_footer,
    )
    assert len(sent_messages) >= 3
    for msg in sent_messages:
        assert len(msg.text) <= 4096


def test_collect_sources_order_and_stop_on_ratelimit():
    sc = DummySC(
        tag_errors={"hardtechno": RateLimitError("429")},
    )
    search_cfg = {
        "tags": ["schranz", "hardtechno", "industrial techno"],
        "followed_users": ["https://soundcloud.com/artist"],
        "reference_accounts": ["https://soundcloud.com/curator"],
        "max_age_days": 7,
        "limit_per_tag": 10,
    }
    d = m.collect_sources(sc, search_cfg)
    assert d.total_sources == 5
    assert d.succeeded == 1
    assert d.aborted == "RateLimitError"
    # "schranz" und "hardtechno" wurden aufgerufen, der Rest nicht
    assert sc.calls == [("tag", "schranz"), ("tag", "hardtechno")]
