"""Akzeptanztests: „Mehr davon“ per SoundCloud-URL (Download gefaked). """
from pathlib import Path

import pytest

from sc_digger import bot
from sc_digger.db import TrackDB


@pytest.fixture
def db(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as d:
        yield d


def test_similar_reply_from_url_uses_fakes_and_returns_empty_reply(db):
    def fake_fetch(url, dest):
        dest.write_bytes(b"fake-audio")
        return dest

    def fake_fp(path):
        return [0] * 200

    reply = bot.similar_reply_from_url(
        None, db, "https://soundcloud.com/a/t1",
        fetch_audio=fake_fetch, compute_fp=fake_fp,
    )
    assert reply == "Keine ähnlichen Aufnahmen gefunden."


def test_similar_reply_from_url_fetch_error_returns_standard_text(db):
    def boom(url, dest):
        raise RuntimeError("kaputt")

    reply = bot.similar_reply_from_url(
        None, db, "https://soundcloud.com/a/t1",
        fetch_audio=boom,
    )
    assert reply == "Ähnliche-Aufnahmen-Suche fehlgeschlagen, siehe Container-Log."
