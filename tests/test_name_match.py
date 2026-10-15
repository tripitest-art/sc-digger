"""Tests zur gemeinsamen Namens-Logik (Normalisierung, NameKey/NameIndex)."""
import sqlite3

import pytest

from sc_digger.collection import (
    STOP,
    NameIndex,
    NameKey,
    name_key,
    names_match,
    normalize,
    remixer_tokens,
)
from sc_digger.web.database import query_digest


# ---------------- normalize ----------------

def test_normalize_fallback_only_when_nothing_is_left():
    # Stoppwörter fliegen raus, solange danach noch etwas zu vergleichen bleibt
    assert normalize("Svetec Original Mix") == ["svetec"]
    # besteht der Name nur aus Stoppwörtern, kommen die ungefilterten Wörter zurück
    assert normalize("Original Mix") == ["original", "mix"]
    assert normalize("") == []
    assert normalize(" --- ... ") == []


def test_normalize_fallback_off_drops_stopwords_completely():
    assert normalize("Original Mix", fallback=False) == []
    assert normalize("Svetec Original Mix", fallback=False) == ["svetec"]


def test_normalize_folds_case_accents_and_umlauts():
    assert normalize("Łódź Straße") == ["lodz", "strasse"]
    assert normalize("Überrest") == normalize("Ueberrest") == ["ueberrest"]
    assert normalize("DJ Ø") == ["dj", "o"]


@pytest.mark.parametrize("sep", ["x", "vs", "with", "presents", "pres"])
def test_artist_separators_are_stopwords(sep):
    assert sep in STOP
    assert normalize(f"Svetec {sep} OBI") == ["svetec", "obi"]


def test_remixer_tokens_need_content_in_the_brackets():
    assert remixer_tokens("Surrender (DJØ Edit)") == {"djo"}
    assert remixer_tokens("Surrender [Svetec Rework]") == {"svetec"}
    # nur Stoppwörter in der Klammer -> kein Remixer
    assert remixer_tokens("Surrender (Original Mix)") == set()
    assert remixer_tokens("Surrender (Extended Version)") == set()
    assert remixer_tokens("Surrender") == set()


# ---------------- name_key / names_match ----------------

def test_name_key_treats_missing_artist_like_empty():
    assert name_key(None, "Surrender") == name_key("", "Surrender")
    assert name_key(None, "Surrender").tokens == frozenset({"surrender"})
    assert name_key(None, "Surrender").remix == frozenset()
    assert name_key(None, "").tokens == frozenset()
    assert name_key("Überrest", "Surrender").tokens == frozenset({"ueberrest", "surrender"})


def test_names_match_same_track_in_different_spelling():
    assert names_match(name_key("Überrest", "Für immer"), name_key("Ueberrest", "Fur Immer"))
    assert not names_match(name_key(None, ""), name_key(None, ""))
    assert not names_match(
        name_key("Svetec", "Acid Storm"), name_key("Foo", "Completely Different Thing")
    )


def test_names_match_first_rule_two_different_edits():
    a = name_key("Ueberrest", "Surrender (DJØ Edit)")
    b = name_key("Ueberrest", "Surrender (VRTGØ Edit)")
    assert not names_match(a, b)
    assert not names_match(b, a)


def test_names_match_second_rule_edit_against_original():
    edit = name_key("Ueberrest", "Surrender (DJØ Edit)")
    original = name_key("Ueberrest", "Surrender")
    assert not names_match(edit, original)
    assert not names_match(original, edit)


def test_names_match_third_rule_remixer_in_the_other_title():
    # Steht der Remixer-Name im anderen Titel mit drin, ist es dieselbe Fassung.
    edit = name_key("Ueberrest", "Surrender (DJØ Edit)")
    same = NameKey(tokens=frozenset({"ueberrest", "surrender", "djo"}), remix=frozenset())
    assert names_match(edit, same)
    assert names_match(same, edit)


def test_names_match_bracket_without_remixer_is_the_plain_title():
    assert name_key("Svetec", "Acid Storm (Original Mix)").remix == frozenset()
    assert names_match(
        name_key("Svetec", "Acid Storm"), name_key("Svetec", "Acid Storm (Extended Mix)")
    )


# ---------------- NameIndex ----------------

def test_name_index_keeps_insertion_order():
    idx = NameIndex()
    assert len(idx) == 0
    assert idx.find(name_key("Svetec", "Acid Storm")) is None

    idx.add("erste", name_key("Svetec", "Acid Storm"))
    idx.add("zweite", name_key("Svetec", "Acid Storm (Original Mix)"))
    idx.add("dritte", name_key("Svetec", "Acid Storm"))
    idx.add("unbeteiligt", name_key("Foo", "Completely Different Thing"))

    assert len(idx) == 4
    key = name_key("Svetec", "Acid Storm")
    assert idx.find_all(key) == ["erste", "zweite", "dritte"]
    assert idx.find(key) == "erste"


def test_name_index_never_matches_an_empty_key():
    idx = NameIndex()
    idx.add("leer", name_key(None, ""))
    assert idx.find_all(name_key(None, "")) == []
    assert idx.find(name_key("Svetec", "Acid Storm")) is None
    assert len(idx) == 1


def test_name_index_applies_the_remixer_rules():
    idx = NameIndex()
    idx.add("edit", name_key("Ueberrest", "Surrender (DJØ Edit)"))
    assert idx.find(name_key("Ueberrest", "Surrender (DJØ Edit)")) == "edit"
    assert idx.find(name_key("Ueberrest", "Surrender (VRTGØ Edit)")) is None
    assert idx.find(name_key("Ueberrest", "Surrender")) is None


# ---------------- query_digest ----------------

def _digest_dbs(tmp_path, title, sc_id=1):
    state_db = tmp_path / "seen.sqlite"
    with sqlite3.connect(state_db) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.execute(
            "INSERT INTO seen VALUES (?, 'u', ?, '2026-10-14 06:00:00')", (sc_id, title)
        )
    return state_db, tmp_path / "keine-tracks.sqlite"


def test_query_digest_recognizes_inbox_file_by_sc_id_prefix(tmp_path):
    """[<sc_id>] zählt weiter vorrangig, auch wenn der Dateiname nichts mit dem Titel zu tun hat."""
    inbox = tmp_path / "inbox" / "155-160" / "8A"
    inbox.mkdir(parents=True)
    (inbox / "[777] Voellig anderer Name.wav").write_bytes(b"x")
    state_db, track_db = _digest_dbs(tmp_path, "Svetec - Acid Storm", sc_id=777)
    item = query_digest(state_db, track_db, inbox_dir=tmp_path / "inbox").items[0]
    assert item.in_inbox is True


def test_query_digest_without_matching_sc_id_falls_back_to_the_name(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "[999] Voellig anderer Name.wav").write_bytes(b"x")
    state_db, track_db = _digest_dbs(tmp_path, "Svetec - Acid Storm", sc_id=777)
    item = query_digest(state_db, track_db, inbox_dir=inbox).items[0]
    assert item.in_inbox is False
