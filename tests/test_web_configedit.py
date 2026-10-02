"""Eigene Tests des Web-Konfigeditors (Issue #145), ergänzend zu den Akzeptanztests."""
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.schema import FIELDS, SECTION_TITLES, get_field
from sc_digger.web.app import create_app
from sc_digger.web.configedit import (Change, apply_form, diff_config, format_form_value, get_path,
                                      parse_form_value, save_local)

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "config.yaml"
PASSWORD = "geheim-4711"
AUTH = ("sc", PASSWORD)
ORIGIN = {"Origin": "http://testserver"}


def base_raw():
    return yaml.safe_load(BASE_PATH.read_text(encoding="utf-8"))


def full_form(**overrides):
    raw = base_raw()
    form = {f.path: format_form_value(f, get_path(raw, f.path)) for f in FIELDS if f.editable}
    form.update(overrides)
    return form


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


# ------------------------------------------------------------------ Formularansicht
def make_app(tmp_path, local=None, **kw):
    local_path = tmp_path / "config.local.yaml"
    if local is not None:
        local_path.write_text(yaml.safe_dump(local), encoding="utf-8")
    cfg = Config.load(BASE_PATH, local=local_path)
    kw.setdefault("base_path", BASE_PATH)
    kw.setdefault("local_path", local_path)
    app = create_app(cfg, password=PASSWORD, **kw)
    return app, TestClient(app), local_path


def test_form_shows_section_headings(tmp_path):
    from markupsafe import escape
    _, c, _ = make_app(tmp_path)
    r = c.get("/config", auth=AUTH)
    assert r.status_code == 200
    # Jedes editierbare Feld hat einen Abschnitt; dessen Überschrift muss vorkommen.
    titles = set()
    for field in FIELDS:
        if not field.editable:
            continue
        part = field.path.split(".")
        for size in range(len(part) - 1, 0, -1):
            candidate = ".".join(part[:size])
            if candidate in SECTION_TITLES:
                titles.add(SECTION_TITLES[candidate])
                break
    assert titles
    for title in titles:
        assert str(escape(title)) in r.text, title


def test_form_marks_value_from_local_file(tmp_path):
    _, c, _ = make_app(tmp_path, local={"search": {"bpm_min": 140}})
    r = c.get("/config", auth=AUTH)
    assert "Wert stammt aus der lokalen Datei" in r.text
    assert 'value="140"' in r.text


def test_form_never_offers_non_editable_fields_as_input(tmp_path):
    _, c, _ = make_app(tmp_path)
    r = c.get("/config", auth=AUTH)
    for field in FIELDS:
        if not field.editable:
            assert f'name="{field.path}"' not in r.text, field.path


# ------------------------------------------------------------------ Diff: Listen als Ganzes
def test_diff_treats_lists_as_a_whole():
    old = {"search": {"tags": ["a", "b"]}}
    new = {"search": {"tags": ["a", "c"]}}
    # Ein Change für die ganze Liste, kein Eintrag je Index.
    assert diff_config(old, new) == [Change("search.tags", ["a", "b"], ["a", "c"])]


# ------------------------------------------------------------------ CSRF: Referer mit abweichendem Port
def test_referer_with_other_port_is_rejected(tmp_path):
    _, c, local = make_app(tmp_path)
    r = c.post("/config/preview", auth=AUTH, data=full_form(),
               headers={"Referer": "http://testserver:9999/config"})
    assert r.status_code == 403
    assert not local.exists()


def test_origin_with_other_port_is_rejected(tmp_path):
    _, c, local = make_app(tmp_path)
    r = c.post("/config/preview", auth=AUTH, data=full_form(),
               headers={"Origin": "http://testserver:9999"})
    assert r.status_code == 403
    assert not local.exists()


def test_referer_without_origin_is_accepted(tmp_path):
    _, c, _ = make_app(tmp_path)
    r = c.post("/config/preview", auth=AUTH, data=full_form(),
               headers={"Referer": "http://testserver/config"})
    assert r.status_code == 200


# ------------------------------------------------------------------ Reset des letzten Schlüssels
def test_reset_of_last_key_removes_the_file(tmp_path):
    app, c, local = make_app(tmp_path, local={"search": {"bpm_min": 140}})
    assert local.exists()
    r = c.post("/config/reset", auth=AUTH, data={"path": "search.bpm_min"}, headers=ORIGIN,
               follow_redirects=False)
    assert r.status_code == 303
    assert not local.exists()
    assert app.state.cfg["search"]["bpm_min"] == 150


# ------------------------------------------------------------------ Schreiben nur minimal
def test_save_writes_only_the_changed_leaf(tmp_path):
    _, c, local = make_app(tmp_path)
    c.post("/config/save", auth=AUTH, data=full_form(**{"search.bpm_min": "140"}), headers=ORIGIN)
    assert yaml.safe_load(local.read_text(encoding="utf-8")) == {"search": {"bpm_min": 140}}


# ------------------------------------------------------------------ Reine Logik: Grenzfälle
def test_parse_float_empty_is_an_error():
    with pytest.raises(ValueError):
        parse_form_value(get_field("scoring.min_like_ratio"), "   ")


def test_apply_form_unknown_field_does_not_touch_config(tmp_path):
    base = base_raw()
    new_local, problems = apply_form(base, {}, {"gibt.es.nicht": "1"})
    assert new_local == {}
    assert [p.path for p in problems if p.level == "error"] == ["gibt.es.nicht"]


def test_save_local_prunes_backups_to_keep_count(tmp_path):
    p = tmp_path / "config.local.yaml"
    save_local(p, {"a": 1})
    from datetime import datetime
    for minute in range(1, 6):
        save_local(p, {"a": minute}, keep_backups=1,
                   now=datetime(2026, 10, 2, 12, minute, 0))
    backups = sorted(f.name for f in tmp_path.iterdir() if ".bak-" in f.name)
    assert backups == ["config.local.yaml.bak-20261002-120500"]
