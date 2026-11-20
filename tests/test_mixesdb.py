"""Tests für den MixesDB-Abgleich (Tracklisten aus dem Szene-Wiki)."""
from __future__ import annotations

import json

import pytest

from sc_digger import mixesdb
from sc_digger.collection import name_key
from sc_digger.mixesdb import (
    Setlist,
    SetlistEntry,
    check_setlist,
    entries_from_lines,
    fetch_setlist,
    format_setlist,
    missing_tracks,
    parse_entry_line,
    parse_position,
    parse_setlist,
    search_sets,
    setlist_url,
)

# Ausschnitt einer echten Seite (2006-03-03 - Robert Natus @ Schranz Total, PPC, Graz).
NATUS_WIKITEXT = """== File details ==

{|{{NormalTableFormat}}
! dur
! MB
! kbps
|-
| 2:05:00
| 171
| 192
|}

{{Player|https://www.mixcloud.com/djKorus/robert-natus-schranz-total-2006-03-03/}}

== Tracklist ==

<list>
[000] Robert Natus - Dead People [Frown - 04]
[006] Robert Natus - We Are Abstract [Abstract - 4260186570209]
[008] ?
[011] Arkus P - Habsucht [Schlachthof - 2006001]
[015] ?
...
[048] Sven Wittekind - Sunstorm [Abstract - 4260186570223]
[097] Dennis Kae - Helden Der Nacht (Frank Kvitta Remix) [Schlachthof - SCHLACHTHOFCOMP 002]
</list>

[[Category:2006]]
[[Category:Robert Natus]]
[[Category:Schranz Total]]
[[Category:Hard Techno]]
"""

NUMMERN_WIKITEXT = """== Tracklist ==

# [02] SveTec - ADHD [Kube]
# Fl-X - Pursuit Of Life [Animasola]
# O.B.I. - Mind Blow
[[Category:2025]]
"""


# ------------------------------------------------------------------ Zeilen parsen


def test_parse_entry_line_liest_position_artist_titel_label_und_katalog():
    entry = parse_entry_line("[048] Sven Wittekind - Sunstorm [Abstract - 4260186570223]")

    assert entry == SetlistEntry(
        position_s=48 * 60,
        artist="Sven Wittekind",
        title="Sunstorm",
        label="Abstract",
        catalog="4260186570223",
    )


def test_parse_entry_line_ohne_position_und_mit_label_ohne_katalog():
    entry = parse_entry_line("# O.B.I. - Mind Blow [Audiocode]")

    assert entry is not None
    assert entry.position_s is None
    assert entry.artist == "O.B.I."
    assert entry.title == "Mind Blow"
    assert entry.label == "Audiocode"
    assert entry.catalog is None


def test_parse_entry_line_offener_eintrag_ist_unbekannt():
    entry = parse_entry_line("[008] ?")

    assert entry is not None
    assert entry.position_s == 8 * 60
    assert entry.unknown is True
    assert entry.track() == "?"


@pytest.mark.parametrize("line", ["", "   ", "<list>", "</list>", "...", "[[Category:2006]]", "{|"])
def test_parse_entry_line_ignoriert_zeilen_ohne_eintrag(line):
    assert parse_entry_line(line) is None


def test_parse_entry_line_ohne_artist_ist_titel_mit_label():
    entry = parse_entry_line("[035] Trip [Kube]")

    assert entry is not None
    assert entry.artist == ""
    assert entry.title == "Trip"
    assert entry.label == "Kube"
    # Ohne Artist bleibt der Eintrag benannt (unbekannt ist nur "?").
    assert entry.unknown is False


def test_parse_entry_line_entfernt_wikilinks():
    entry = parse_entry_line("[010] [[Sven Wittekind|Wittekind]] - [[Sunstorm]] [Abstract]")

    assert entry is not None
    assert entry.artist == "Wittekind"
    assert entry.title == "Sunstorm"


def test_parse_position_minuten_und_minuten_sekunden():
    assert parse_position("[048] Rest") == (48 * 60, "Rest")
    assert parse_position("[48:30] Rest") == (48 * 60 + 30, "Rest")
    assert parse_position("ohne Zeit") == (None, "ohne Zeit")


# ------------------------------------------------------------------ Seite parsen


def test_parse_setlist_liest_nur_den_tracklist_abschnitt():
    setlist = parse_setlist(NATUS_WIKITEXT, page="Natus")

    assert setlist.page == "Natus"
    assert len(setlist.entries) == 7
    assert setlist.entries[0].title == "Dead People"
    # Der Player-Block aus "File details" ist kein Eintrag.
    assert all("mixcloud" not in e.title for e in setlist.entries)
    assert setlist.entries[-1].artist == "Dennis Kae"


def test_parse_setlist_liest_die_setdauer():
    assert parse_setlist(NATUS_WIKITEXT).duration_s == 2 * 3600 + 5 * 60


def test_parse_setlist_zaehlt_offene_stellen():
    setlist = parse_setlist(NATUS_WIKITEXT)

    assert [e.position_s for e in setlist.open_entries] == [8 * 60, 15 * 60]
    assert len(setlist.named_entries()) == 5


def test_parse_setlist_versteht_nummerierte_listen():
    setlist = parse_setlist(NUMMERN_WIKITEXT)

    assert [e.artist for e in setlist.entries] == ["SveTec", "Fl-X", "O.B.I."]
    assert setlist.entries[0].position_s == 2 * 60
    assert setlist.duration_s is None


def test_parse_setlist_ohne_tracklist_ist_leer():
    wikitext = "== Tracklist ==\n\n<list>\n\n</list>\n\n[[Category:2002]]\n"

    setlist = parse_setlist(wikitext, page="Leer")

    assert setlist.entries == ()
    assert setlist.open_entries == ()


def test_entries_from_lines_baut_setlist_aus_notizen():
    setlist = entries_from_lines(["[000] A - B [C - 1]", "", "?"], page="Notiz")

    assert len(setlist.entries) == 2
    assert setlist.entries[0].label == "C"
    assert setlist.entries[1].unknown is True


def test_setlist_url_ersetzt_leerzeichen():
    assert (
        setlist_url("2006-03-03 - Robert Natus @ Schranz Total, PPC, Graz")
        == "https://www.mixesdb.com/w/2006-03-03_-_Robert_Natus_@_Schranz_Total,_PPC,_Graz"
    )
    assert Setlist(page="x").url.endswith("/x")


# ------------------------------------------------------------------ Netzwerk (Fakes)


def _api_antwort(wikitext: str) -> str:
    return json.dumps({"parse": {"title": "X", "wikitext": {"*": wikitext}}})


def test_fetch_setlist_parst_die_api_antwort():
    gesehen: list[str] = []

    def fake_fetch(url: str) -> str | None:
        gesehen.append(url)
        return _api_antwort(NATUS_WIKITEXT)

    setlist = fetch_setlist("2006-03-03 - Robert Natus", fetch=fake_fetch)

    assert setlist is not None
    assert len(setlist.entries) == 7
    assert "action=parse" in gesehen[0]
    assert "format=json" in gesehen[0]


def test_fetch_setlist_fehlt_bei_unbekannter_seite():
    def fake_fetch(url: str) -> str | None:
        return json.dumps({"error": {"code": "missingtitle"}})

    assert fetch_setlist("gibt es nicht", fetch=fake_fetch) is None


@pytest.mark.parametrize("antwort", [None, "", "kein json", "{}"])
def test_fetch_setlist_fehlt_bei_kaputter_antwort(antwort):
    assert fetch_setlist("Seite", fetch=lambda url: antwort) is None


def test_search_sets_liefert_seitentitel():
    def fake_fetch(url: str) -> str | None:
        assert "list=search" in url
        assert "srsearch=Schranz" in url
        return json.dumps(
            {"query": {"search": [{"title": "A @ B"}, {"title": "C - D"}]}}
        )

    assert search_sets("Schranz", fetch=fake_fetch) == ["A @ B", "C - D"]


def test_search_sets_ohne_anfrage_und_ohne_netz():
    assert search_sets("  ", fetch=lambda url: pytest.fail("kein Netz nötig")) == []
    assert search_sets("Schranz", fetch=lambda url: None) == []
    assert search_sets("Schranz", fetch=lambda url: "kein json") == []


# ------------------------------------------------------------------ Abgleich


def test_check_setlist_markiert_vorhandene_und_fehlende_tracks():
    setlist = parse_setlist(NATUS_WIKITEXT, page="Natus")

    check = check_setlist(
        setlist,
        [("Robert Natus", "Dead People"), ("Sven Wittekind", "Sunstorm")],
    )

    vorhanden = [e.title for e in check.present]
    fehlend = [e.title for e in check.missing]
    assert vorhanden == ["Dead People", "Sunstorm"]
    assert "We Are Abstract" in fehlend
    assert "Habsucht" in fehlend
    # Offene Stellen sind weder vorhanden noch fehlend.
    assert check.owned[2] is None
    assert len(check.missing) == 3


def test_check_setlist_unterscheidet_original_und_remix():
    """Wer das Original hat, besitzt nicht den Remix — sonst gelten Promos als vorhanden."""
    setlist = parse_setlist(
        "== Tracklist ==\n[000] Dennis Kae - Helden Der Nacht [Schlachthof]\n"
    )

    original = check_setlist(setlist, [("Dennis Kae", "Helden Der Nacht")])
    remix = check_setlist(
        setlist, [("Dennis Kae", "Helden Der Nacht (Frank Kvitta Remix)")]
    )

    assert original.missing == ()
    # Der Remix in der Sammlung deckt den Original-Eintrag nicht ab.
    assert len(remix.missing) == 1


def test_check_setlist_ohne_sammlung_ist_alles_fehlend():
    setlist = parse_setlist(NATUS_WIKITEXT)

    check = check_setlist(setlist, [])

    assert len(check.missing) == 5
    assert check.present == ()


def test_check_setlist_nimmt_auch_einen_index():
    index = mixesdb.build_name_index([("Arkus P", "Habsucht")])
    setlist = parse_setlist(NATUS_WIKITEXT)

    check = check_setlist(setlist, index)

    assert [e.title for e in check.present] == ["Habsucht"]


def test_build_name_index_ignoriert_leere_titel():
    index = mixesdb.build_name_index([("A", ""), ("B", "   "), ("C", "Titel")])

    assert len(index) == 1
    assert index.find(name_key("C", "Titel")) == "C - Titel"


def test_missing_tracks_liefert_zeilen_im_wiki_stil():
    setlist = entries_from_lines(["[000] A - B [C - 1]", "[002] ?"])
    check = check_setlist(setlist, [])

    assert missing_tracks(check) == ["A - B [C - 1]"]


def test_format_setlist_zeigt_marken_und_kopfzeile():
    setlist = entries_from_lines(["[000] A - B [C - 1]", "[002] ?", "[004] D - E"])
    check = check_setlist(setlist, [("A", "B")])

    text = format_setlist(check)

    assert "3 Einträge" in text
    assert "1 fehlen" in text
    assert "✓   0:00  A - B  [C - 1]" in text
    assert "✗   4:00  D - E" in text
    assert "?   2:00  ?" in text


def test_format_setlist_kann_vorhandene_ausblenden():
    setlist = entries_from_lines(["[000] A - B", "[004] D - E"])
    check = check_setlist(setlist, [("A", "B")])

    text = format_setlist(check, show_owned=False)

    assert "A - B" not in text
    assert "D - E" in text
