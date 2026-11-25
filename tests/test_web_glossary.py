"""Eigene Tests für Status-Plaketten und Glossar (Issue #612)."""
from sc_digger.web.glossary import GLOSSAR, STATUS_BADGES, badge_for


def test_plaketten_nutzen_nur_vorhandene_klassen():
    from pathlib import Path
    base = (Path(__file__).resolve().parents[1] / "sc_digger" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    for _wort, klasse in STATUS_BADGES.values():
        assert f".{klasse} {{" in base, klasse


def test_badge_for_wandelt_zahlen_in_text():
    assert badge_for(7) == ("7", "badge-muted")  # type: ignore[arg-type]


def test_glossar_vermeidet_eigene_begriffe_nicht():
    # Ein Wort darf nicht zugleich Begriff und „Nicht“-Wort eines anderen Begriffs sein,
    # außer bewusst getrennten Paaren (Inbox/Eingang).
    begriffe = set(GLOSSAR)
    for begriff, (_e, nicht) in GLOSSAR.items():
        for wort in nicht:
            if wort in begriffe:
                assert (begriff, wort) == ("Inbox", "Eingang"), (begriff, wort)


def test_forbidden_words_ordnet_alt_zu_neu():
    from sc_digger.web.glossary import forbidden_words

    w = forbidden_words()
    assert w["Run"] == "Lauf"
    assert w["abgelehnt"] == "Quarantäne"
    assert "Eingang" not in w


def test_find_forbidden_endungen_und_grenzen():
    from sc_digger.web.glossary import find_forbidden

    assert find_forbidden("<p>Abgewiesene Tracks, Künstlern, Runs</p>") == ["Abgewiesene", "Künstlern", "Runs"]
    assert find_forbidden("<p>Runde und Archivierung</p>") == []
    assert find_forbidden("<p>Eingang und Upload-Ordner</p>") == []


def test_find_forbidden_wirft_nie():
    from sc_digger.web.glossary import find_forbidden

    assert find_forbidden("") == []
    assert find_forbidden(None) == []  # type: ignore[arg-type]
