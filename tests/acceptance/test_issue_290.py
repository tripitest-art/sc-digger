"""Akzeptanztests: gemeinsamer Namens-Abgleich und bessere Normalisierung."""
import sqlite3
import unicodedata
from pathlib import Path

import pytest

from sc_digger.collection import Collection, NameIndex, name_key, normalize, remixer_tokens
from sc_digger.web.database import query_digest
from tests.test_modes import mk


def sc(i, artist, title):
    return mk(i, title=title, user={"username": artist, "permalink_url": "https://soundcloud.com/x"})


@pytest.mark.parametrize("a, b", [
    ("Überrest", "Ueberrest"),
    (unicodedata.normalize("NFD", "Für Immer"), unicodedata.normalize("NFC", "Für Immer")),
    ("Beyoncé", "Beyonce"),
    ("Søren", "Soren"),
    ("Łódź", "Lodz"),
    ("Straße", "Strasse"),
])
def test_normalize_folds_unicode(a, b):
    assert normalize(a) == normalize(b)
    assert normalize(a) != []


def test_normalize_drops_artist_separators_and_features():
    assert normalize("A x B vs C feat. D") == ["a", "b", "c", "d"]


def test_normalize_keeps_words_when_only_stopwords_remain():
    assert normalize("Original Mix") == ["original", "mix"]
    assert normalize("") == []


def test_remixer_tokens_ignore_stopword_only_brackets():
    assert remixer_tokens("Surrender (Original Mix)") == set()
    assert remixer_tokens("Surrender (DJØ Edit)") == {"djo"}


def test_name_key_fields():
    key = name_key("Ueberrest", "Surrender (DJØ Edit)")
    assert {"ueberrest", "surrender", "djo"} <= set(key.tokens)
    assert set(key.remix) == {"djo"}
    assert name_key(None, "Surrender").remix == frozenset()


def test_name_index_find_and_find_all_keep_insertion_order():
    idx = NameIndex()
    assert idx.find(name_key("A", "Track")) is None
    idx.add("one", name_key("Ueberrest", "Surrender (DJØ Edit)"))
    idx.add("two", name_key("Ueberrest", "Surrender (DJØ Edit)"))
    idx.add("other", name_key("Foo", "Completely Different Thing"))
    assert len(idx) == 3
    key = name_key("Überrest", "Surrender (DJØ Edit)")
    assert idx.find_all(key) == ["one", "two"]
    assert idx.find(key) == "one"
    assert idx.find_all(name_key(None, "")) == []


@pytest.mark.parametrize("known, query", [
    ("Surrender (DJØ Edit)", "Surrender (VRTGØ Edit)"),   # anderer Remixer
    ("Surrender (DJØ Edit)", "Surrender"),                # Edit vorhanden, Original gesucht
    ("Surrender", "Surrender (DJØ Edit)"),                # Original vorhanden, Edit gesucht
])
def test_name_index_remixer_rules_are_symmetric(known, query):
    idx = NameIndex()
    idx.add("x", name_key("Ueberrest", known))
    assert idx.find(name_key("Ueberrest", query)) is None


def test_name_index_original_mix_equals_plain_title():
    idx = NameIndex()
    idx.add("x", name_key("Svetec", "Acid Storm"))
    assert idx.find(name_key("Svetec", "Acid Storm (Original Mix)")) == "x"


def test_collection_matches_nfd_file_and_returns_only_the_name(tmp_path):
    folder = tmp_path / "sub"
    folder.mkdir()
    name = unicodedata.normalize("NFD", "Ueberrest - Für Immer.wav")
    (folder / name).write_bytes(b"x")
    coll = Collection(tmp_path)
    assert coll.find_duplicate(sc(1, "Ueberrest", "Für immer")) == name
    assert coll.find_duplicate(sc(2, "Foo", "Completely Different Thing")) is None


def test_collection_edit_in_library_is_not_the_original(tmp_path):
    (tmp_path / "Ueberrest - Surrender (DJØ Edit).wav").write_bytes(b"x")
    coll = Collection(tmp_path)
    assert coll.find_duplicate(sc(1, "Ueberrest", "Surrender (DJØ Edit)")) is not None
    assert coll.find_duplicate(sc(2, "Ueberrest", "Surrender")) is None


def _digest_dbs(tmp_path, title):
    state_db = tmp_path / "seen.sqlite"
    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (1, 'u', ?, '2026-10-14 06:00:00')", (title,))
    return state_db, tmp_path / "keine-tracks.sqlite"


def test_query_digest_uses_shared_matching_for_inbox_files(tmp_path):
    inbox = tmp_path / "inbox" / "150" / "8A"
    inbox.mkdir(parents=True)
    (inbox / unicodedata.normalize("NFD", "Ueberrest - Für Immer.wav")).write_bytes(b"x")
    state_db, track_db = _digest_dbs(tmp_path, "Überrest - Für immer (Original Mix)")
    item = query_digest(state_db, track_db, inbox_dir=tmp_path / "inbox").items[0]
    assert item.in_inbox is True


def test_query_digest_edit_in_inbox_is_not_the_original(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "Ueberrest - Surrender (DJØ Edit).wav").write_bytes(b"x")
    state_db, track_db = _digest_dbs(tmp_path, "Ueberrest - Surrender")
    item = query_digest(state_db, track_db, inbox_dir=inbox).items[0]
    assert item.in_inbox is False
