"""Akzeptanztests: Track-Tabelle dichter – Details auf Abruf, Qualität nur als Abweichung, fester Kopf."""
import re
from pathlib import Path

from tests.test_web_tracks_ui import PASSWORD, client_for, seed

ROOT = Path(__file__).resolve().parents[2]
TRACKS = (ROOT / "sc_digger" / "web" / "templates" / "tracks.html").read_text(encoding="utf-8")


def _html(tmp_path):
    seed(tmp_path)
    return client_for(tmp_path).get("/tracks", auth=("sc", PASSWORD)).text


def test_pfad_steht_nur_im_detailbereich(tmp_path):
    html = _html(tmp_path)
    zellen = re.findall(r'<td class="sticky-col" data-label="Titel" data-column="title">(.*?)</td>', html, re.S)
    assert zellen
    for zelle in zellen:
        vor_details, rest = zelle.split('<details class="track-details">', 1)
        assert "meta-path" not in vor_details
        details = rest.split("</details>", 1)[0]
        assert "<summary>Details</summary>" in details
        assert 'class="mono meta-path"' in details
    assert re.search(r'class="mono meta-path">[^<]*\.wav</div>', html), "Pfad fehlt im Detailbereich"


def test_qualitaet_ok_ist_unauffaellig(tmp_path):
    html = _html(tmp_path)
    assert '<span class="quality-ok" title="Qualität OK" aria-label="Qualität OK">✓</span>' in html
    assert '<span class="badge badge-ok">OK</span>' not in html


def test_abweichende_qualitaet_bleibt_plakette():
    assert '<span class="badge badge-warn">Clipped</span>' in TRACKS
    assert '<span class="badge badge-err">Fake-Transcode</span>' in TRACKS


def test_fester_tabellenkopf():
    css = TRACKS.split("<style>", 1)[1].split("</style>", 1)[0]
    scroll = re.search(r"\.card-tracks \.table-scroll\s*\{([^}]*)\}", css)
    assert scroll and "max-height: 78vh" in scroll.group(1) and "overflow: auto" in scroll.group(1)
    kopf = re.search(r"\.card-tracks thead th\s*\{([^}]*)\}", css)
    assert kopf and "position: sticky" in kopf.group(1) and "top: 0" in kopf.group(1)
    assert re.search(r"@media \(max-width: 900px\) \{ \.card-tracks \.table-scroll \{ max-height: none; \} \}", css)
