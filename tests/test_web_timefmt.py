"""Eigene Tests für die Ortszeit-Umrechnung (Issue #621)."""
from sc_digger.web.timefmt import DEFAULT_TIMEZONE, to_local


def test_utc_eingabe_formate():
    for roh in ("2026-11-19 07:30:00", "2026-11-19T07:30:00", "2026-11-19 07:30",
                "2026-11-19T07:30:00Z", "2026-11-19 07:30:00.123456"):
        assert to_local(roh, "Europe/Berlin") == "2026-11-19 08:30"


def test_tageswechsel_und_sommerzeit():
    assert to_local("2026-06-30 22:30:00", DEFAULT_TIMEZONE) == "2026-07-01 00:30"


def test_none_und_leer():
    assert to_local(None, "UTC") == "—"
    assert to_local("   ", "UTC") == "—"


def test_ungueltige_zeitzone_faellt_auf_utc_zurueck():
    assert to_local("2026-11-19 07:30:00", "Kein/Ort") == "2026-11-19 07:30 UTC"


def test_unlesbarer_text_bleibt():
    assert to_local("irgendwann", "UTC") == "irgendwann"
