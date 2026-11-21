"""Akzeptanztests: /help nach Bereichen wie im Dashboard."""
from sc_digger import bot

ERWARTET = {
    "Start": ["/heute", "/stats"],
    "Entdecken": ["/kaufliste", "/curator_mining", "/preview", "/preview", "/similar"],
    "Eingang": ["/rejected", "/retry", "/cleanup_inbox"],
    "Bibliothek": ["/is_duplicate", "/not_duplicate"],
    "Auflegen": ["/mix", "/sync_xml"],
}


def test_bereiche_in_der_reihenfolge_des_dashboards():
    assert [name for name, _ in bot.HELP_SECTIONS] == [
        "Start", "Entdecken", "Eingang", "Bibliothek", "Auflegen",
    ]


def test_jeder_befehl_steht_in_seinem_bereich():
    for name, zeilen in bot.HELP_SECTIONS:
        assert [z.split()[0] for z in zeilen] == ERWARTET[name], name


def test_hilfetext_zeigt_die_bereiche_als_ueberschriften():
    text = bot.HELP_TEXT
    positionen = [text.index(f"<b>{name}</b>\n") for name, _ in bot.HELP_SECTIONS]
    assert positionen == sorted(positionen)
    for name, zeilen in bot.HELP_SECTIONS:
        start = text.index(f"<b>{name}</b>\n")
        for zeile in zeilen:
            assert f"• {zeile}\n" in text[start:] or text.endswith(f"• {zeile}")
            assert text.index(f"• {zeile}") > start


def test_bisherige_zeilen_bleiben_wortgleich():
    for zeile in (
        "• /kaufliste -> zeigt die aktuelle Kaufliste offener Store-Tracks",
        "• /mix 5A 155 -> harmonisch passende Tracks aus der Sammlung (±3 BPM)",
        "• /preview &lt;Suchtext oder id&gt; -> 20s-Snippet eines heruntergeladenen Tracks als Sprachnachricht",
        "• /sync_xml -> Rekordbox-XML der Inbox sofort neu schreiben",
        "• Audio-/Sprachnachricht -> die Top-5 ähnlichen Aufnahmen aus der Sammlung",
    ):
        assert zeile in bot.HELP_TEXT, zeile
    assert bot.HELP_TEXT.startswith("sc-digger Bot\n\n")
    assert bot.HELP_TEXT.endswith("Kein täglicher Filter, du bekommst die volle Liste mit Stats und Download-Einordnung.")
    assert bot.HELP_TEXT.count("• /") == sum(len(z) for _, z in bot.HELP_SECTIONS)
