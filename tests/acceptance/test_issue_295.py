import re
from pathlib import Path

_REQ = Path(__file__).resolve().parents[2] / "requirements.txt"


def _namen() -> list[str]:
    namen = []
    for zeile in _REQ.read_text(encoding="utf-8").splitlines():
        zeile = zeile.split("#", 1)[0].strip()
        if zeile and not zeile.startswith("-"):
            namen.append(re.split(r"[<>=!~\[;\s]", zeile, maxsplit=1)[0].lower().replace("_", "-"))
    return namen


def test_scipy_ist_deklariert():
    assert "scipy" in _namen()


def test_scipy_steht_nur_einmal():
    assert _namen().count("scipy") == 1
