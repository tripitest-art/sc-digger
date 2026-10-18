"""Eigene Tests zu #286: Quarantäne-Tabelle und die Bot-Befehle /rejected und /retry.

Kein Netzwerk: Tracks kommen aus der Fabrik in tests/test_modes.py, Telegram wird nie
aufgerufen. Der Quarantäne-Pfad in _discover läuft gegen die echte State-DB in tmp_path.
"""
from pathlib import Path

from sc_digger import bot
from sc_digger import main as m
from sc_digger.models import Config
from sc_digger.output import State
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")


def _cfg(tmp_path) -> Config:
    cfg = Config({**CFG.raw, "state": {**CFG["state"],
                                       "db_path": str(tmp_path / "seen.sqlite"),
                                       "track_db_path": str(tmp_path / "tracks.sqlite")}})
    return cfg


# ---------------------------------------------------------------- State
def test_mark_rejected_haelt_track_aus_seen(tmp_path):
    t = mk(101, title="Track Alpha")
    with State(tmp_path / "seen.sqlite") as state:
        state.mark_rejected(t, reason="Spektrum endet bei 14 kHz", cutoff_hz=14000.0)
        assert state.is_seen(101) is False
        items = state.list_rejected()
    assert len(items) == 1
    assert items[0] == {
        "id": 101,
        "url": t.url,
        "title": "Track Alpha",
        "reason": "Spektrum endet bei 14 kHz",
        "cutoff_hz": 14000.0,
        "rejected_at": items[0]["rejected_at"],
    }
    assert items[0]["rejected_at"]


def test_mark_rejected_uebernimmt_cutoff_aus_quality_report(tmp_path):
    t = mk(102)
    t.quality_report = {"ok": False, "cutoff_hz": 12000.0, "reason": "MP3 mit nur 128 kbps"}
    with State(tmp_path / "seen.sqlite") as state:
        state.mark_rejected(t, reason="MP3 mit nur 128 kbps")
        item = state.list_rejected()[0]
    assert item["cutoff_hz"] == 12000.0


def test_mark_rejected_ersetzt_alten_eintrag(tmp_path):
    t = mk(103)
    with State(tmp_path / "seen.sqlite") as state:
        state.mark_rejected(t, reason="erster Grund")
        state.mark_rejected(t, reason="zweiter Grund")
        items = state.list_rejected()
    assert len(items) == 1
    assert items[0]["reason"] == "zweiter Grund"


def test_unmark_rejected_entfernt_auch_aus_seen(tmp_path):
    t = mk(104)
    with State(tmp_path / "seen.sqlite") as state:
        state.mark_one(t)
        state.mark_rejected(t, reason="Codec nicht akzeptiert")
        assert state.is_seen(104) is True  # mark_one hat den Track zusätzlich als gesehen markiert

        assert state.unmark_rejected(104) is True
        assert state.is_seen(104) is False
        assert state.list_rejected() == []
        assert state.unmark_rejected(104) is False


def test_list_rejected_limit_und_neueste_zuerst(tmp_path):
    with State(tmp_path / "seen.sqlite") as state:
        for i in range(1, 5):
            state.mark_rejected(mk(i), reason=f"Grund {i}")
        assert [x["id"] for x in state.list_rejected(limit=2)] == [4, 3]
        assert state.list_rejected(limit=0) == []


# ---------------------------------------------------------------- Bot-Antworten
def test_rejected_reply_listet_id_titel_und_grund(tmp_path):
    cfg = _cfg(tmp_path)
    with State(cfg["state"]["db_path"]) as state:
        state.mark_rejected(mk(201, title="Track Alpha"), reason="Codec nicht akzeptiert")
        state.mark_rejected(mk(202, title="Track Beta"), reason="Spektrum unmessbar")

    msg = bot.rejected_reply(cfg, "/rejected")
    assert "201" in msg and "202" in msg
    assert "Track Alpha" in msg and "Track Beta" in msg
    assert "Codec nicht akzeptiert" in msg
    assert "Spektrum unmessbar" in msg
    assert bot.rejected_reply(cfg, "/rejected 1").count("•") == 1


def test_rejected_reply_leer_und_ungueltig(tmp_path):
    cfg = _cfg(tmp_path)
    assert "Keine abgelehnten" in bot.rejected_reply(cfg, "/rejected")
    assert bot.rejected_reply(cfg, "/rejected 0") == bot.REJECTED_USAGE
    assert bot.rejected_reply(cfg, "/rejected 101") == bot.REJECTED_USAGE
    assert bot.rejected_reply(cfg, "/rejected zwei") == bot.REJECTED_USAGE
    assert bot.rejected_reply(cfg, "/rejected 1 2") == bot.REJECTED_USAGE


def test_rejected_reply_escaped_titel(tmp_path):
    cfg = _cfg(tmp_path)
    with State(cfg["state"]["db_path"]) as state:
        state.mark_rejected(mk(203, title="A & B <C>"), reason="Test")
    msg = bot.rejected_reply(cfg, "/rejected")
    assert "A &amp; B &lt;C&gt;" in msg


def test_retry_reply_setzt_track_zurueck(tmp_path):
    cfg = _cfg(tmp_path)
    with State(cfg["state"]["db_path"]) as state:
        state.mark_rejected(mk(301), reason="Grund")
        state.mark_rejected(mk(302), reason="Grund")

    res = bot.retry_reply(cfg, "/retry 301")
    assert "301" in res
    assert "zurückgesetzt" in res.lower() or "freigegeben" in res.lower()
    with State(cfg["state"]["db_path"]) as state:
        assert [x["id"] for x in state.list_rejected()] == [302]
        assert state.is_seen(301) is False


def test_retry_reply_ungueltig_und_unbekannt(tmp_path):
    cfg = _cfg(tmp_path)
    assert bot.retry_reply(cfg, "/retry") == bot.RETRY_USAGE
    assert bot.retry_reply(cfg, "/retry abc") == bot.RETRY_USAGE
    assert bot.retry_reply(cfg, "/retry 0") == bot.RETRY_USAGE
    assert "nicht in der Quarantäne" in bot.retry_reply(cfg, "/retry 999")


def test_handle_message_leitet_beide_befehle_weiter(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    with State(cfg["state"]["db_path"]) as state:
        state.mark_rejected(mk(401, title="Track Gamma"), reason="Grund")

    sent: list[str] = []
    monkeypatch.setattr(bot, "_send_text", lambda cfg, chat_id, msg: sent.append(msg))

    bot.handle_message(cfg, object(), "1", "/rejected")
    bot.handle_message(cfg, object(), "1", "/retry@sc_digger_bot 401")

    assert "Track Gamma" in sent[0]
    assert "401" in sent[1] and "Quarantäne" in sent[1]
    with State(cfg["state"]["db_path"]) as state:
        assert state.list_rejected() == []


# ---------------------------------------------------------------- _discover
def test_discover_quarantaene_statt_seen(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    gut = mk(501, title="Sauberer Track")
    gut.quality_report = {"ok": True, "reason": "echte Qualität", "cutoff_hz": 20000.0}
    kaputt = mk(502, title="Fake-Bitrate")
    kaputt.quality_report = {"ok": False, "reason": "MP3 mit nur 128 kbps", "cutoff_hz": 15000.0}
    fehlgeschlagen = mk(503, title="Download kaputt")
    fehlgeschlagen.notes.append("Original-Download fehlgeschlagen")

    discovery = m.Discovery(tracks=[], reference_ids=set(), total_sources=1, succeeded=1,
                            failed=[], aborted=None, first_error=None, exploration_used=[])
    monkeypatch.setattr(m, "SoundCloudClient", lambda: object())
    monkeypatch.setattr(m, "collect_sources", lambda sc, s: discovery)
    monkeypatch.setattr(m, "retry_downloads", lambda sc, cfg: [])
    monkeypatch.setattr(m, "run_intake", lambda cfg: [])
    monkeypatch.setattr(m, "snapshot_tracks", lambda cfg, tracks: None)
    monkeypatch.setattr(m, "process", lambda tracks, cfg, dry_run: ([gut, kaputt, fehlgeschlagen], []))
    monkeypatch.setattr(m, "deliver", lambda *a, **k: None)
    monkeypatch.setattr(m, "record_exploration_stats", lambda *a, **k: None)
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    monkeypatch.setattr(m, "is_sunday", lambda now=None: False)

    m._discover(cfg, dry_run=False, no_telegram=True)

    with State(cfg["state"]["db_path"]) as state:
        assert state.is_seen(501) is True
        assert state.is_seen(502) is False
        assert state.is_seen(503) is False
        items = {x["id"]: x for x in state.list_rejected()}
    assert items[502]["reason"] == "MP3 mit nur 128 kbps"
    assert items[502]["cutoff_hz"] == 15000.0
    assert "Original-Download fehlgeschlagen" in items[503]["reason"]
