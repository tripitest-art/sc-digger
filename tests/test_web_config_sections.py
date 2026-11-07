"""Konfigeditor in Abschnittsseiten (/config/<abschnitt>) und Zahnrad im Header."""
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.schema import FIELDS
from sc_digger.web.app import config_sections, create_app, slugify_section
from sc_digger.web.configedit import format_form_value, get_path

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "config.yaml"
PASSWORD = "geheim-4711"
AUTH = ("sc", PASSWORD)
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


def make_client(tmp_path, local=None):
    local_path = tmp_path / "config.local.yaml"
    if local is not None:
        local_path.write_text(yaml.safe_dump(local), encoding="utf-8")
    cfg = Config.load(BASE_PATH, local=local_path)
    app = create_app(cfg, password=PASSWORD, base_path=BASE_PATH, local_path=local_path)
    return TestClient(app), local_path


def section_form(section, **overrides):
    raw = yaml.safe_load(BASE_PATH.read_text(encoding="utf-8"))
    form = {f.path: format_form_value(f, get_path(raw, f.path)) for f in section.fields}
    form["_section"] = section.slug
    form.update(overrides)
    return form


# ------------------------------------------------------------------ Slugs
def test_slugs_sind_ascii_stabil_und_eindeutig():
    slugs = [s.slug for s in config_sections()]
    assert len(slugs) == len(set(slugs))
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug) for slug in slugs)
    assert slugify_section("Sortieren & Taggen") == "sortieren-taggen"
    assert slugify_section("Qualitätsprüfung") == "qualitaetspruefung"
    assert slugify_section("Bewertung: Gewichte") == "bewertung-gewichte"
    assert slugify_section("Verfügbarkeits-Wächter") == "verfuegbarkeits-waechter"


def test_kein_feld_geht_verloren():
    in_sections = [f.path for s in config_sections() for f in s.fields]
    assert sorted(in_sections) == sorted(f.path for f in FIELDS if f.editable)
    # Jede Überschrift aus dem Schema, die editierbare Felder hat, bekommt eine Seite.
    titles = {s.title for s in config_sections()}
    assert {"Suche", "Download", "Rekordbox", "Telegram"} <= titles


# ------------------------------------------------------------------ Seiten
def test_config_leitet_auf_ersten_abschnitt_um(tmp_path):
    c, _ = make_client(tmp_path)
    r = c.get("/config", auth=AUTH, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/config/{config_sections()[0].slug}"


def test_jede_abschnittsseite_liefert_200_mit_feld_und_navigation(tmp_path):
    c, _ = make_client(tmp_path)
    for section in config_sections():
        r = c.get(f"/config/{section.slug}", auth=AUTH)
        assert r.status_code == 200, section.slug
        assert f'name="{section.fields[0].path}"' in r.text, section.slug
        # Abschnittsnavigation enthält alle Abschnitte, nur der aktive ist hervorgehoben
        for other in config_sections():
            assert f'href="/config/{other.slug}"' in r.text
        assert r.text.count('aria-current="page"') == 1
        assert f'href="/config/{section.slug}" class="active"' in r.text
        # Felder fremder Abschnitte stehen nicht auf der Seite
        foreign = next(s for s in config_sections() if s.slug != section.slug)
        assert f'name="{foreign.fields[0].path}"' not in r.text
        assert f'name="_section" value="{section.slug}"' in r.text


def test_unbekannter_abschnitt_ist_404(tmp_path):
    c, _ = make_client(tmp_path)
    assert c.get("/config/gibt-es-nicht", auth=AUTH).status_code == 404


def test_gespeichert_banner_ueberlebt_die_umleitung(tmp_path):
    c, _ = make_client(tmp_path)
    r = c.get("/config?saved=1", auth=AUTH)
    assert r.status_code == 200 and "Gespeichert." in r.text


# ------------------------------------------------------------------ Speichern
def test_abschnitts_save_aendert_nur_den_eigenen_abschnitt(tmp_path):
    # Lokal liegt schon ein Wert in einem anderen Abschnitt.
    c, local = make_client(tmp_path, local={"health": {"max_hours_since_run": 40}})
    search = next(s for s in config_sections() if s.title == "Suche")
    r = c.post("/config/save", auth=AUTH, headers=ORIGIN, follow_redirects=False,
               data=section_form(search, **{"search.bpm_min": "140"}))
    assert r.status_code == 303
    assert r.headers["location"] == f"/config/{search.slug}?saved=1"
    saved = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert saved == {"search": {"bpm_min": 140}, "health": {"max_hours_since_run": 40}}


def test_abschnitts_save_ignoriert_fremde_felder(tmp_path):
    c, local = make_client(tmp_path)
    search = next(s for s in config_sections() if s.title == "Suche")
    r = c.post("/config/save", auth=AUTH, headers=ORIGIN, follow_redirects=False,
               data=section_form(search, **{"health.max_hours_since_run": "99"}))
    assert r.status_code == 303
    assert not local.exists()


def test_abschnitts_save_ohne_aenderung_schreibt_nichts(tmp_path):
    c, local = make_client(tmp_path)
    for section in config_sections():
        r = c.post("/config/save", auth=AUTH, headers=ORIGIN, follow_redirects=False,
                   data=section_form(section))
        assert r.status_code == 303, section.slug
    assert not local.exists()


def test_save_mit_fehler_zeigt_den_abschnitt_wieder(tmp_path):
    c, local = make_client(tmp_path)
    search = next(s for s in config_sections() if s.title == "Suche")
    r = c.post("/config/save", auth=AUTH, headers=ORIGIN,
               data=section_form(search, **{"search.bpm_min": "schnell"}))
    assert r.status_code == 422
    assert 'value="schnell"' in r.text
    assert f'name="_section" value="{search.slug}"' in r.text
    assert not local.exists()


def test_vorschau_fuehrt_zurueck_auf_den_abschnitt(tmp_path):
    c, _ = make_client(tmp_path)
    search = next(s for s in config_sections() if s.title == "Suche")
    r = c.post("/config/preview", auth=AUTH, headers=ORIGIN,
               data=section_form(search, **{"search.bpm_min": "140"}))
    assert r.status_code == 200
    assert f'href="/config/{search.slug}"' in r.text and "search.bpm_min" in r.text


def test_reset_leitet_auf_den_abschnitt_des_feldes(tmp_path):
    c, local = make_client(tmp_path, local={"search": {"bpm_min": 140}})
    search = next(s for s in config_sections() if s.title == "Suche")
    r = c.post("/config/reset", auth=AUTH, headers=ORIGIN, follow_redirects=False,
               data={"path": "search.bpm_min", "_section": "health"})
    assert r.status_code == 303
    assert r.headers["location"] == f"/config/{search.slug}?saved=1"
    assert not local.exists() or not yaml.safe_load(local.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ Zahnrad
def test_zahnrad_auf_allen_seiten_und_nicht_im_mehr_menue(tmp_path):
    c, _ = make_client(tmp_path)
    sub = f"/config/{config_sections()[1].slug}"
    for path in ("/", "/tracks", sub):
        html = c.get(path, auth=AUTH).text
        assert re.search(r'<a href="/config" class="gear-link[^"]*" title="Konfiguration" '
                         r'aria-label="Konfiguration">', html), path
        mehr = html.split('<details class="nav-more">')[1].split("</details>")[0]
        assert 'href="/config"' not in mehr, path
    assert "gear-link active" in c.get(sub, auth=AUTH).text


def test_zahnrad_fehlt_ohne_editor(tmp_path):
    app = create_app(Config.load(BASE_PATH), password=PASSWORD)
    html = TestClient(app).get("/", auth=AUTH).text
    assert 'class="gear-link' not in html
    assert 'href="/config"' not in html
