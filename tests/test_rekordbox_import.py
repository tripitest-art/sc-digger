"""Eigene Tests zu Issue #201: Randfälle des Rekordbox-Imports (Parser und DB-Schicht)."""
import json

import pytest

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.rekordbox import (
    import_rekordbox_xml,
    parse_rekordbox_xml,
)

PATH_MAP = {
    "/music/inbox": "Z:/Highres/_sc-digger-inbox",
    "/music/Schranz": "C:/Users/user/Music/Highres/Schranz",
}


def _xml(track_attrs: str, mark: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<DJ_PLAYLISTS Version="1.0.0">'
        f'<COLLECTION Entries="1"><TRACK {track_attrs}>{mark}</TRACK></COLLECTION>'
        "</DJ_PLAYLISTS>"
    )


# ------------------------------------------------------------------ Parser

def test_rating_schwellen_werden_gerundet():
    """Alle 51er-Stufen des Rekordbox-Ratings ergeben genau 0..5 Sterne."""
    for raw, stars in [("0", 0), ("51", 1), ("102", 2), ("153", 3), ("204", 4), ("255", 5)]:
        xml = _xml(f'Name="T" Rating="{raw}" Location="file://localhost/Z:/x/a.wav"')
        assert parse_rekordbox_xml(xml)[0].rating == stars


def test_fehlendes_rating_ist_none_und_null_ist_null():
    fehlt = parse_rekordbox_xml(_xml('Name="T" Location="file://localhost/Z:/x/a.wav"'))[0]
    assert fehlt.rating is None

    null = parse_rekordbox_xml(_xml('Name="T" Rating="0" Location="file://localhost/Z:/x/a.wav"'))[0]
    assert null.rating == 0


def test_rating_ausserhalb_des_bereichs_wird_begrenzt():
    hoch = parse_rekordbox_xml(_xml('Name="T" Rating="999" Location="file://localhost/Z:/x/a.wav"'))[0]
    assert hoch.rating == 5
    negativ = parse_rekordbox_xml(_xml('Name="T" Rating="-51" Location="file://localhost/Z:/x/a.wav"'))[0]
    assert negativ.rating == 0


def test_ungueltiges_rating_ergibt_none():
    xml = _xml('Name="T" Rating="abc" Location="file://localhost/Z:/x/a.wav"')
    assert parse_rekordbox_xml(xml)[0].rating is None


@pytest.mark.parametrize(
    "location, erwartet",
    [
        # file:/// ohne localhost
        ("file:///C:/Musik/A.wav", "C:/Musik/A.wav"),
        # Backslashes werden zu "/"
        ("file://localhost/Z:\\Highres\\A.wav", "Z:/Highres/A.wav"),
        # Prozent-Escapes werden dekodiert (%20, %C3%A4 = ä)
        ("file://localhost/Z:/High/a%20b%C3%A4.wav", "Z:/High/a bä.wav"),
        # Ohne Schema bleibt der Pfad unverändert
        ("Z:/Highres/A.wav", "Z:/Highres/A.wav"),
    ],
)
def test_location_dekodierung(location, erwartet):
    tracks = parse_rekordbox_xml(_xml(f'Name="T" Location="{location}"'))
    assert tracks[0].local_path == erwartet


def test_rueckwaerts_mapping_praefixgrenze_und_laengstes_praefix():
    """"/music/inbox" darf nicht auf "Z:/Highres/_sc-digger-inbox2" passen."""
    xml = _xml(
        'Name="T" Location="file://localhost/Z:/Highres/_sc-digger-inbox2/A.wav"'
    )
    assert parse_rekordbox_xml(xml, PATH_MAP)[0].local_path == (
        "Z:/Highres/_sc-digger-inbox2/A.wav"
    )


def test_rueckwaerts_mapping_ohne_treffer_bleibt_erhalten():
    xml = _xml('Name="T" Location="file://localhost/D:/Unbekannt/A.wav"')
    tracks = parse_rekordbox_xml(xml, PATH_MAP)
    assert tracks[0].local_path == "D:/Unbekannt/A.wav"


def test_ohne_collection_leere_liste():
    xml = '<?xml version="1.0"?><DJ_PLAYLISTS Version="1.0.0"></DJ_PLAYLISTS>'
    assert parse_rekordbox_xml(xml, PATH_MAP) == []


def test_cues_ohne_farbe_tragen_keinen_color_schluessel():
    mark = '<POSITION_MARK Name="Kick" Type="0" Start="2.5" Num="-1"/>'
    xml = _xml('Name="T" Location="file://localhost/Z:/x/a.wav"', mark)
    cues = parse_rekordbox_xml(xml)[0].cues
    assert cues == [{"name": "Kick", "type": 0, "start": 2.5, "num": -1}]


# ------------------------------------------------------------------ TrackDB

def test_get_track_by_filename_escaped_like_sonderzeichen(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(
            path="/music/inbox/TrackA1.wav", mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))
        ziel = db.upsert_track(TrackRecord(
            path="/music/inbox/Track_1.wav", mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))
        # "_" ist ein LIKE-Platzhalter – darf hier aber nur den echten Unterstrich treffen.
        treffer = db.get_track_by_filename("Track_1.wav")
        assert treffer is not None and treffer.id == ziel.id


def test_get_track_by_filename_mehrere_treffer_kleinste_id(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        erst = db.upsert_track(TrackRecord(
            path="/music/inbox/a/Dup.wav", mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))
        db.upsert_track(TrackRecord(
            path="/music/inbox/b/Dup.wav", mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))
        treffer = db.get_track_by_filename("Dup.wav")
        assert treffer is not None and treffer.id == erst.id


def test_get_track_by_filename_ohne_treffer_none(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert db.get_track_by_filename("GibtEsNicht.wav") is None


def test_update_rekordbox_metadata_unbekannte_id_liefert_false(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert db.update_rekordbox_metadata(99999, rb_rating=5) is False


def test_import_zaehlt_playcount_null_nicht_als_gespielt(tmp_path):
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(
        _xml('Name="T" Rating="102" PlayCount="0" '
             'Location="file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/A.wav"'),
        encoding="utf-8",
    )
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(
            path="/music/inbox/150/1A/A.wav", mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))
        summary = import_rekordbox_xml(db, xml_file, PATH_MAP)
        assert summary.matched == 1
        assert summary.played_count == 0
        assert summary.ratings_count == 1
        assert summary.auto_likes_count == 0


def test_import_auto_like_nur_ueber_playcount(tmp_path):
    """Zwei Abspiele ohne Rating genügen für Auto-Like."""
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(
        _xml('Name="T" PlayCount="2" '
             'Location="file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/A.wav"'),
        encoding="utf-8",
    )
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        rec = db.upsert_track(TrackRecord(
            path="/music/inbox/150/1A/A.wav", mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))
        summary = import_rekordbox_xml(db, xml_file, PATH_MAP)
        assert summary.auto_likes_count == 1
        assert db.get_track_by_id(rec.id).feedback == "like"


def test_import_schreibt_cues_als_json(tmp_path):
    mark = ('<POSITION_MARK Name="Kick" Type="0" Start="1.0" Num="0" '
            'Red="0" Green="255" Blue="0"/>')
    xml_file = tmp_path / "export.xml"
    xml_file.write_text(
        _xml('Name="T" Location="file://localhost/Z:/Highres/_sc-digger-inbox/150/1A/A.wav"',
             mark),
        encoding="utf-8",
    )
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        rec = db.upsert_track(TrackRecord(
            path="/music/inbox/150/1A/A.wav", mtime=1.0, size=10, status=TrackStatus.INBOX,
        ))
        import_rekordbox_xml(db, xml_file, PATH_MAP)
        gespeichert = db.get_track_by_id(rec.id).rb_cues
        assert json.loads(gespeichert)[0]["color"] == {"red": 0, "green": 255, "blue": 0}
