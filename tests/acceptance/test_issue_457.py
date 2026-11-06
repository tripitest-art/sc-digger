"""Akzeptanztests: API-Kanarienvogel für SoundCloud-Antworten."""
from __future__ import annotations

import copy

from sc_digger import canary
from sc_digger.canary import (TRACK_FIELDS, check_search, check_track, missing_fields,
                              problems_text, record_canary, run_canary)
from sc_digger.soundcloud import SoundCloudError

from tests.test_modes import FakeSC, raw_track

TRACK_URL = "https://soundcloud.com/a/t1"


def _full(i=1):
    d = raw_track(i)
    d["duration"] = 360000
    d["media"] = {"transcodings": []}
    return d


def test_track_fields_cover_what_to_track_reads():
    for f in ("playback_count", "likes_count", "created_at", "user.username", "media.transcodings"):
        assert f in TRACK_FIELDS


def test_missing_fields_dotted_paths_and_none_values():
    obj = {"a": None, "b": {"c": 1}, "d": "kein dict"}
    assert missing_fields(obj, ["a", "b.c", "b.x", "d.e", "z"], "p.") == ["p.b.x", "p.d.e", "p.z"]
    assert missing_fields("kein dict", ["a"]) == ["a"]


def test_complete_answers_have_no_problems():
    assert check_search({"collection": [_full(1), _full(2)], "next_href": None}) == []
    assert check_track(_full()) == []


def test_search_reports_vanished_fields_by_name():
    t = _full()
    del t["playback_count"]
    del t["user"]["username"]
    assert check_search({"collection": [t]}) == ["search[0].playback_count", "search[0].user.username"]


def test_search_with_wrong_shape():
    assert check_search({"items": []}) == ["search.collection"]
    assert check_search({"collection": []}) == ["search.collection"]
    assert check_search([]) == ["search.collection"]
    assert check_search({"collection": [{"kind": "playlist"}]}) == ["search.collection[kind=track]"]


def test_track_with_wrong_kind():
    assert check_track({"kind": "playlist"}) == ["track.kind"]
    assert check_track(None, "resolve") == ["resolve.kind"]


def test_run_canary_asks_search_and_resolve_exactly_once():
    sc = FakeSC({"/search/tracks": {"collection": [_full()]}, TRACK_URL: _full()})
    assert run_canary(sc, tag="schranz", track_url=TRACK_URL) == []
    assert [c[0] for c in sc.calls] == ["/search/tracks", "/resolve"]
    assert sc.calls[0][1]["q"] == "schranz"
    assert sc.calls[1][1]["url"] == TRACK_URL


def test_run_canary_without_track_url_only_searches():
    t = _full()
    del t["media"]
    sc = FakeSC({"/search/tracks": {"collection": [t]}})
    assert run_canary(sc, tag="schranz") == ["search[0].media.transcodings"]
    assert len(sc.calls) == 1


def test_problems_text():
    assert problems_text(["search[0].likes_count"]) == (
        "SoundCloud-Antwort geändert, Felder fehlen: search[0].likes_count → soundcloud.py (_to_track) prüfen")


def test_record_canary_alarms_once_and_recovers_once(tmp_path):
    db = tmp_path / "seen.sqlite"
    first = record_canary(db, ["search[0].likes_count"])
    assert first is not None and "api_schema" in first and "search[0].likes_count" in first
    assert record_canary(db, ["search[0].likes_count"]) is None
    back = record_canary(db, [])
    assert back is not None and "läuft wieder" in back
    assert record_canary(db, []) is None


def _cfg_file(tmp_path, track_url=""):
    p = tmp_path / "config.yaml"
    p.write_text(
        f"search:\n  tags: [schranz, hardtechno]\nstate:\n  db_path: {tmp_path / 'seen.sqlite'}\n"
        f"canary:\n  tag: ''\n  track_url: '{track_url}'\n", encoding="utf-8")
    return str(p)


def test_main_sends_alarm_and_returns_1_on_missing_fields(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    t = _full()
    del t["likes_count"]
    sc = FakeSC({"/search/tracks": {"collection": [t]}})
    sent = []
    monkeypatch.setattr(canary, "SoundCloudClient", lambda *a, **k: sc)
    monkeypatch.setattr(canary, "send_telegram", lambda cfg, msgs, *a, **k: sent.append(msgs))
    assert canary.main(["--config", _cfg_file(tmp_path)]) == 1
    assert sc.calls[0][1]["q"] == "schranz"
    assert len(sent) == 1 and "search[0].likes_count" in "\n".join(sent[0])


def test_main_network_trouble_returns_3_without_health_entry(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    sc = FakeSC({})  # jede Route fehlt -> SoundCloudError
    sent = []
    monkeypatch.setattr(canary, "SoundCloudClient", lambda *a, **k: sc)
    monkeypatch.setattr(canary, "send_telegram", lambda cfg, msgs, *a, **k: sent.append(msgs))
    assert canary.main(["--config", _cfg_file(tmp_path)]) == 3
    assert sent == []
    assert record_canary(tmp_path / "seen.sqlite", []) is None  # kein offener Alarm, keine Entwarnung


def test_main_all_good_returns_0(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    full = copy.deepcopy(_full())
    sc = FakeSC({"/search/tracks": {"collection": [full]}, TRACK_URL: full})
    monkeypatch.setattr(canary, "SoundCloudClient", lambda *a, **k: sc)
    monkeypatch.setattr(canary, "send_telegram", lambda *a, **k: None)
    assert canary.main(["--config", _cfg_file(tmp_path, TRACK_URL), "--no-telegram"]) == 0
    assert [c[0] for c in sc.calls] == ["/search/tracks", "/resolve"]
