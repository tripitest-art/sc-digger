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
