import json

from sc_digger import mixesdb
from sc_digger.mixesdb import (
    check_setlist,
    entries_from_lines,
    fetch_setlist,
    format_setlist,
    missing_tracks,
    parse_entry_line,
    parse_setlist,
    search_sets,
    setlist_url,
)

WIKITEXT = """== File details ==

{|{{NormalTableFormat}}
! dur
|-
| 2:05:00
|}

== Tracklist ==

<list>
[000] Robert Natus - Dead People [Frown - 04]
[008] ?
[048] Sven Wittekind - Sunstorm [Abstract - 4260186570223]
...
</list>

[[Category:2006]]
"""


def test_akz_zeile_mit_position_label_und_katalog():
    entry = parse_entry_line("[048] Sven Wittekind - Sunstorm [Abstract - 4260186570223]")
    assert entry.position_s == 48 * 60
    assert entry.artist == "Sven Wittekind"
    assert entry.title == "Sunstorm"
    assert entry.label == "Abstract"
    assert entry.catalog == "4260186570223"
    assert entry.unknown is False


def test_akz_offener_eintrag_und_ignorierte_zeilen():
    assert parse_entry_line("[008] ?").unknown is True
    for line in ("", "<list>", "...", "[[Category:2006]]", "{|"):
        assert parse_entry_line(line) is None


def test_akz_seite_parst_nur_tracklist_und_dauer():
    setlist = parse_setlist(WIKITEXT, page="Natus")
    assert len(setlist.entries) == 3
    assert setlist.duration_s == 7500
    assert setlist.entries[0].track() == "Robert Natus - Dead People"
    assert [e.position_s for e in setlist.open_entries] == [8 * 60]


def test_akz_leere_tracklist_ist_leer():
    setlist = parse_setlist("== Tracklist ==\n\n<list>\n\n</list>\n")
    assert setlist.entries == ()
    assert setlist.duration_s is None


def test_akz_abgleich_markiert_vorhanden_fehlt_offen():
    setlist = parse_setlist(WIKITEXT, page="Natus")
    check = check_setlist(setlist, [("Robert Natus", "Dead People")])
    assert check.owned == (True, None, False)
    assert [e.title for e in check.present] == ["Dead People"]
    assert [e.title for e in check.missing] == ["Sunstorm"]
    assert missing_tracks(check) == ["Sven Wittekind - Sunstorm [Abstract - 4260186570223]"]


def test_akz_remix_deckt_original_nicht_ab():
    setlist = entries_from_lines(["[000] Dennis Kae - Helden Der Nacht [Schlachthof]"])
    original = check_setlist(setlist, [("Dennis Kae", "Helden Der Nacht")])
    remix = check_setlist(setlist, [("Dennis Kae", "Helden Der Nacht (Frank Kvitta Remix)")])
    assert original.missing == ()
    assert len(remix.missing) == 1


def test_akz_format_zeigt_marken_und_kopfzeile():
    setlist = entries_from_lines(["[000] A - B [C - 1]", "[002] ?", "[004] D - E"])
    text = format_setlist(check_setlist(setlist, [("A", "B")]))
    assert "3 Einträge" in text and "1 fehlen" in text and "1 offen" in text
    assert "✓" in text and "✗   4:00  D - E" in text and "?   2:00  ?" in text
    ohne = format_setlist(check_setlist(setlist, [("A", "B")]), show_owned=False)
    assert "A - B" not in ohne and "D - E" in ohne


def test_akz_netz_nur_ueber_fetcher_und_fehler_tolerant():
    gerufen = []

    def fake(url):
        gerufen.append(url)
        return json.dumps({"parse": {"wikitext": {"*": WIKITEXT}}})

    setlist = fetch_setlist("2006-03-03 - Robert Natus", fetch=fake)
    assert setlist is not None and len(setlist.entries) == 3
    assert "action=parse" in gerufen[0]
    assert fetch_setlist("kaputt", fetch=lambda url: "kein json") is None
    assert search_sets("Schranz", fetch=lambda url: None) == []
    assert search_sets("  ", fetch=lambda url: "egal") == []
    assert setlist_url("a b") == "https://www.mixesdb.com/w/a_b"
