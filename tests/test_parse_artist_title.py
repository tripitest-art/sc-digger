"""Tests für _parse_artist_title: Bereinigung führender Gratis-Hinweise."""
import pytest
from sc_digger.web.database import _parse_artist_title


@pytest.mark.parametrize(
    "raw, expected_artist, expected_title",
    [
        # Standard mit Groß-/Kleinschreibung und runden/eckigen Klammern
        ("(free dl) Crystal Castles - Vanished", "Crystal Castles", "Vanished"),
        ("[FREE DL] Crystal Castles - Vanished", "Crystal Castles", "Vanished"),
        ("(Free Download) Scooter - Weekend!", "Scooter", "Weekend!"),
        ("[free download] Scooter - Weekend!", "Scooter", "Weekend!"),
        ("(freedl) Test - Title", "Test", "Title"),
        ("[FREEDL] Test - Title", "Test", "Title"),
        # Gemischte Groß-/Kleinschreibung
        ("[FrEe Dl] Artist - Title", "Artist", "Title"),
        ("(fReE dOwNlOaD) Artist - Title", "Artist", "Title"),
        ("[FreEDL] Artist - Title", "Artist", "Title"),
        # Innen- und Außenleerraum in den Klammern
        ("(   free   dl   ) Artist - Title", "Artist", "Title"),
        ("[  free    download   ] Artist - Title", "Artist", "Title"),
        ("  (free dl)   Artist - Title", "Artist", "Title"),
        # Mehrere Hinweise hintereinander
        ("[FREE DL] (free download) [freedl] Multiple - Track", "Multiple", "Track"),
        ("(FREE DL)  (free dl)  (free download) Multiple - Track", "Multiple", "Track"),
        # Unicode-Gedankenstrich (" – ")
        ("(FREE DL) Artist – Title with en-dash", "Artist", "Title with en-dash"),
        ("[Free Download] Artist – Another Title", "Artist", "Another Title"),
        # Künstlerteil besteht nur aus Gratis-Hinweisen -> wird zu None
        ("(FREE DL) - Nur Titel", None, "Nur Titel"),
        ("[FREE DL] [Free Download] - Nur Titel", None, "Nur Titel"),
        # Hinweis steht nicht am Anfang des Künstlers -> bleibt erhalten
        ("Artist (FREE DL) - Titel", "Artist (FREE DL)", "Titel"),
        ("My [Free Download] Artist - Titel", "My [Free Download] Artist", "Titel"),
        # Name beginnt mit 'Free', ist aber kein Gratis-Klammerhinweis -> bleibt erhalten
        ("Free Spirit - Track", "Free Spirit", "Track"),
        ("Free Download Club - Track", "Free Download Club", "Track"),
        ("Freedl Beats - Track", "Freedl Beats", "Track"),
        # Andere Klammern am Anfang -> bleiben erhalten
        ("(SA14): Artist - Title", "(SA14): Artist", "Title"),
        ("[EXCLU] Artist - Title", "[EXCLU] Artist", "Title"),
        ("(Remix) Artist - Title", "(Remix) Artist", "Title"),
        # Ohne Trenner -> (None, raw.strip())
        ("(FREE DL) Track Without Separator", None, "(FREE DL) Track Without Separator"),
        ("Just A Title", None, "Just A Title"),
    ],
)
def test_parse_artist_title_cases(raw: str, expected_artist: str | None, expected_title: str):
    artist, title = _parse_artist_title(raw)
    assert artist == expected_artist
    assert title == expected_title
