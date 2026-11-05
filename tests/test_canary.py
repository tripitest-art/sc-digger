"""Eigene Tests für den API-Kanarienvogel (Issue #457)."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
import pytest
import requests

from sc_digger import canary
from sc_digger.canary import check_search, check_track, missing_fields, run_canary
from sc_digger.schema import get_field
from sc_digger.soundcloud import SoundCloudError
from tests.test_entrypoint_cron import SH, _prepare
from tests.test_modes import FakeSC, raw_track

ROOT = Path(__file__).resolve().parents[1]


def _full(i=1):
    d = raw_track(i)
    d["duration"] = 360000
    d["media"] = {"transcodings": []}
    return d


def _cfg_file(tmp_path: Path, tag: str = "", track_url: str = "") -> str:
    p = tmp_path / "config.yaml"
    p.write_text(
        f"search:\n  tags: [schranz, hardtechno]\nstate:\n  db_path: {tmp_path / 'seen.sqlite'}\n"
        f"canary:\n  tag: '{tag}'\n  track_url: '{track_url}'\n",
        encoding="utf-8",
    )
    return str(p)


def test_telegram_send_failure_is_only_logged(tmp_path: Path, monkeypatch, caplog):
    """Fehler beim Senden der Telegram-Nachricht bricht den Lauf nicht ab, sondern wird geloggt."""
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    t = _full()
    del t["likes_count"]
    sc = FakeSC({"/search/tracks": {"collection": [t]}})
    monkeypatch.setattr(canary, "SoundCloudClient", lambda *a, **k: sc)

    def _failing_telegram(*a, **k):
        raise RuntimeError("Telegram API nicht erreichbar")

    monkeypatch.setattr(canary, "send_telegram", _failing_telegram)

    with caplog.at_level(logging.WARNING):
        res = canary.main(["--config", _cfg_file(tmp_path)])

    assert res == 1
    assert any("Telegram-Benachrichtigung fehlgeschlagen" in rec.message for rec in caplog.records)


def test_entrypoint_cron_installs_canary_job(tmp_path: Path):
    """entrypoint.sh trägt den Canary-Cron-Job (07:15) ordnungsgemäß ein."""
    script_path, env, _marker = _prepare(tmp_path, crontab_text="#!/bin/sh\nexit 0\n")
    proc = subprocess.run([SH, str(script_path)], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr

    cron_file = tmp_path / "sc-digger"
    content = cron_file.read_text(encoding="utf-8")
    assert "15 7 * * *" in content
    assert "python -m sc_digger.canary" in content


def test_canary_schema_fields_are_defined():
    """Prüft, ob die Schema-Felder für canary korrekt registriert sind."""
    tag_field = get_field("canary.tag")
    assert tag_field.kind == "str"
    assert tag_field.editable is True
    assert tag_field.label == "Such-Tag"

    url_field = get_field("canary.track_url")
    assert url_field.kind == "str"
    assert url_field.editable is True
    assert url_field.label == "Referenz-Track-URL"


def test_run_canary_propagates_request_exception():
    """Netzwerkfehler (requests.RequestException) werden aus run_canary unverändert geworfen."""
    class FailingSC:
        def _get(self, *a, **k):
            raise requests.ConnectionError("Netzwerk getrennt")

    with pytest.raises(requests.RequestException):
        run_canary(FailingSC(), tag="schranz")


def test_missing_fields_handles_non_dict_subvalues():
    """missing_fields erkennt fehlende Felder, wenn Zwischenschlüssel kein dict sind."""
    data = {"user": "string_statt_dict", "media": None}
    missing = missing_fields(data, ["user.username", "media.transcodings", "title"])
    assert missing == ["user.username", "media.transcodings", "title"]


def test_check_search_with_mixed_collection():
    """check_search findet den ersten Track in einer gemischten Liste (z. B. Playlist zuerst)."""
    coll = [{"kind": "playlist"}, _full(1)]
    assert check_search({"collection": coll}) == []


def test_check_track_custom_label():
    """check_track nutzt das übergebene Label für Fehlermeldungen."""
    assert check_track("kein_dict", label="custom_track") == ["custom_track.kind"]
