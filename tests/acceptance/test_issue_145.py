"""Akzeptanztests: Web-Konfigurationseditor mit lokaler Override-Datei (Issue #<N>)."""
import copy
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.schema import FIELDS, get_field
from sc_digger.web.app import create_app
from sc_digger.web.configedit import (Change, apply_form, diff_config, format_form_value,
                                      get_path, minimal_override, parse_form_value, remove_key,
                                      save_local)

ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "config.yaml"
PASSWORD = "geheim-4711"
AUTH = ("sc", PASSWORD)
ORIGIN = {"Origin": "http://testserver"}


def base_raw():
    return yaml.safe_load(BASE_PATH.read_text(encoding="utf-8"))


def errors(problems):
    return [p for p in problems if p.level == "error"]


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


# ------------------------------------------------------------------ parse_form_value / format_form_value
def test_parse_bool():
    f = get_field("organize.write_tags")
    assert parse_form_value(f, "true") is True
    assert parse_form_value(f, "false") is False
    with pytest.raises(ValueError):
        parse_form_value(f, "ja")


def test_parse_int():
    f = get_field("search.max_age_days")
    assert parse_form_value(f, "14") == 14
    assert parse_form_value(f, " 14 ") == 14
    for bad in ("1.5", "abc", ""):
        with pytest.raises(ValueError):
            parse_form_value(f, bad)


def test_parse_float_accepts_german_decimal_comma():
    f = get_field("scoring.min_like_ratio")
    assert parse_form_value(f, "0.35") == 0.35
    assert parse_form_value(f, "0,35") == 0.35
    assert parse_form_value(f, "1") == 1
    with pytest.raises(ValueError):
        parse_form_value(f, "viel")


def test_parse_str_and_choice():
    assert parse_form_value(get_field("organize.default_genre"), "  Hard Techno ") == "Hard Techno"
    f = get_field("search.bpm_unknown_policy")
    assert parse_form_value(f, "drop") == "drop"
    with pytest.raises(ValueError):
        parse_form_value(f, "vielleicht")


def test_parse_list_one_item_per_line():
    f = get_field("search.tags")
    assert parse_form_value(f, "schranz\n\n  acid techno \r\nhard techno\n") == ["schranz", "acid techno", "hard techno"]
    assert parse_form_value(f, "") == []
    assert parse_form_value(f, " \n \n") == []


def test_parse_map_is_not_editable_by_form():
    with pytest.raises(ValueError):
        parse_form_value(get_field("rekordbox.path_map"), "a: b")


def test_format_roundtrip_for_all_editable_fields():
    raw = base_raw()
    for f in FIELDS:
        if not f.editable:
            continue
        value = get_path(raw, f.path)
        text = format_form_value(f, value)
        assert isinstance(text, str)
        assert parse_form_value(f, text) == value, f.path


def test_format_bool_and_list():
    assert format_form_value(get_field("organize.write_tags"), True) == "true"
    assert format_form_value(get_field("organize.write_tags"), False) == "false"
    assert format_form_value(get_field("search.tags"), ["a", "b"]) == "a\nb"


# ------------------------------------------------------------------ minimal_override / diff_config / remove_key
def test_minimal_override_keeps_only_differences():
    base = {"a": {"x": 1, "y": 2}, "l": [1], "k": 5}
    eff = {"a": {"x": 1, "y": 3}, "l": [1, 2], "k": 5}
    assert minimal_override(base, eff) == {"a": {"y": 3}, "l": [1, 2]}
    assert minimal_override(base, copy.deepcopy(base)) == {}


def test_minimal_override_does_not_alias_input():
    eff = {"l": [1, 2]}
    out = minimal_override({"l": [1]}, eff)
    out["l"].append(3)
    assert eff == {"l": [1, 2]}


def test_diff_config():
    old = {"a": {"x": 1, "y": 2}, "l": [1], "gone": 1}
    new = {"a": {"x": 1, "y": 3}, "l": [1, 2], "added": 7}
    assert diff_config(old, new) == [
        Change("a.y", 2, 3),
        Change("added", None, 7),
        Change("gone", 1, None),
        Change("l", [1], [1, 2]),
    ]
    assert diff_config(old, copy.deepcopy(old)) == []


def test_remove_key_prunes_empty_parents():
    assert remove_key({"a": {"b": 1, "c": 2}}, "a.b") == {"a": {"c": 2}}
    assert remove_key({"a": {"b": {"c": 1}}}, "a.b.c") == {}
    local = {"a": {"b": 1}}
    assert remove_key(local, "x.y") == local
    assert local == {"a": {"b": 1}}


# ------------------------------------------------------------------ apply_form
def test_apply_form_sets_changed_value_only():
    new_local, problems = apply_form(base_raw(), {}, {"search.bpm_min": "140", "search.bpm_max": "165"})
    assert errors(problems) == []
    assert new_local == {"search": {"bpm_min": 140}}


def test_apply_form_keeps_existing_local_values():
    local = {"scoring": {"min_plays": 500}}
    new_local, problems = apply_form(base_raw(), local, {"scoring.min_percentile": "50"})
    assert errors(problems) == []
    assert new_local == {"scoring": {"min_plays": 500, "min_percentile": 50.0}}


def test_apply_form_back_to_default_removes_the_override():
    local = {"search": {"bpm_min": 140}}
    new_local, problems = apply_form(base_raw(), local, {"search.bpm_min": "150"})
    assert errors(problems) == []
    assert new_local == {}


def test_apply_form_with_unchanged_values_changes_nothing():
    raw = base_raw()
    form = {f.path: format_form_value(f, get_path(raw, f.path)) for f in FIELDS if f.editable}
    new_local, problems = apply_form(raw, {}, form)
    assert errors(problems) == []
    assert new_local == {}


def test_apply_form_list_value():
    new_local, _ = apply_form(base_raw(), {}, {"search.tags": "schranz\nacid techno"})
    assert new_local == {"search": {"tags": ["schranz", "acid techno"]}}


def test_apply_form_errors_leave_local_untouched():
    local = {"scoring": {"min_plays": 500}}
    before = copy.deepcopy(local)
    cases = {
        "search.bpm_min": "schnell",                    # nicht parsebar
        "search.exploration_probability": "1.5",        # außerhalb des Bereichs
        "state.db_path": "/tmp/x.sqlite",               # nicht editierbar
        "rekordbox.path_map": "a: b",                   # nicht editierbar
        "search.gibt_es_nicht": "1",                    # unbekannt
    }
    for path, text in cases.items():
        new_local, problems = apply_form(base_raw(), local, {path: text})
        assert new_local == before, path
        assert [p.path for p in errors(problems)] == [path], path
    assert local == before


def test_apply_form_checks_cross_field_rules():
    new_local, problems = apply_form(base_raw(), {}, {"search.bpm_min": "170"})   # Maximum ist 165
    assert [p.path for p in errors(problems)] == ["search.bpm_min"]
    assert new_local == {}


def test_apply_form_warnings_do_not_block():
    new_local, problems = apply_form(base_raw(), {}, {"scoring.weights.like_ratio": "0.9"})
    assert errors(problems) == []
    assert [p.level for p in problems if p.path == "scoring.weights"] == ["warning"]
    assert new_local == {"scoring": {"weights": {"like_ratio": 0.9}}}


def test_apply_form_does_not_mutate_inputs():
    base, local = base_raw(), {"search": {"bpm_min": 140}}
    base_before, local_before = copy.deepcopy(base), copy.deepcopy(local)
    apply_form(base, local, {"search.bpm_min": "145", "search.tags": "x"})
    assert base == base_before and local == local_before


# ------------------------------------------------------------------ save_local
def test_save_local_writes_yaml_with_header(tmp_path):
    p = tmp_path / "config.local.yaml"
    assert save_local(p, {"search": {"bpm_min": 140}, "organize": {"default_genre": "Schränz"}}) is None
    text = p.read_text(encoding="utf-8")
    assert text.startswith("#")
    assert "Schränz" in text                              # Umlaute bleiben lesbar
    assert yaml.safe_load(text) == {"search": {"bpm_min": 140}, "organize": {"default_genre": "Schränz"}}
    assert Config.load(BASE_PATH, local=p)["search"]["bpm_min"] == 140
    assert [f.name for f in tmp_path.iterdir()] == ["config.local.yaml"]   # keine Temp-Reste


def test_save_local_backs_up_the_previous_file(tmp_path):
    p = tmp_path / "config.local.yaml"
    save_local(p, {"search": {"bpm_min": 140}})
    backup = save_local(p, {"search": {"bpm_min": 145}}, now=datetime(2026, 10, 2, 12, 30, 5))
    assert backup is not None
    assert backup.parent == tmp_path
    assert backup.name == "config.local.yaml.bak-20261002-123005"
    assert yaml.safe_load(backup.read_text(encoding="utf-8")) == {"search": {"bpm_min": 140}}
    assert yaml.safe_load(p.read_text(encoding="utf-8")) == {"search": {"bpm_min": 145}}


def test_save_local_keeps_only_the_newest_backups(tmp_path):
    p = tmp_path / "config.local.yaml"
    save_local(p, {"search": {"bpm_min": 140}})
    for minute in range(1, 5):
        save_local(p, {"search": {"bpm_min": 140 + minute}}, keep_backups=2,
                   now=datetime(2026, 10, 2, 12, minute, 0))
    names = sorted(f.name for f in tmp_path.iterdir() if ".bak-" in f.name)
    assert names == ["config.local.yaml.bak-20261002-120300", "config.local.yaml.bak-20261002-120400"]


def test_save_local_empty_removes_file_after_backup(tmp_path):
    p = tmp_path / "config.local.yaml"
    save_local(p, {"search": {"bpm_min": 140}})
    backup = save_local(p, {}, now=datetime(2026, 10, 2, 12, 0, 0))
    assert not p.exists()
    assert backup is not None and backup.exists()


def test_save_local_empty_without_file_creates_nothing(tmp_path):
    p = tmp_path / "config.local.yaml"
    assert save_local(p, {}) is None
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ Web: Editor
def make_app(tmp_path, local=None, **kw):
    local_path = tmp_path / "config.local.yaml"
    if local is not None:
        local_path.write_text(yaml.safe_dump(local), encoding="utf-8")
    cfg = Config.load(BASE_PATH, local=local_path)
    kw.setdefault("base_path", BASE_PATH)
    kw.setdefault("local_path", local_path)
    app = create_app(cfg, password=PASSWORD, **kw)
    return app, TestClient(app), local_path


def full_form(**overrides):
    raw = base_raw()
    form = {f.path: format_form_value(f, get_path(raw, f.path)) for f in FIELDS if f.editable}
    form.update(overrides)
    return form


def test_editor_is_off_without_paths(tmp_path):
    c = TestClient(create_app(Config.load(BASE_PATH), password=PASSWORD))
    assert c.get("/config", auth=AUTH).status_code == 404
    assert c.post("/config/save", auth=AUTH, data={}, headers=ORIGIN).status_code in (404, 405)


def test_editor_requires_auth(tmp_path):
    _, c, _ = make_app(tmp_path)
    assert c.get("/config").status_code == 401
    assert c.post("/config/save", data=full_form(), headers=ORIGIN).status_code == 401


def test_form_lists_editable_fields_only(tmp_path):
    _, c, _ = make_app(tmp_path, local={"search": {"bpm_min": 140}})
    r = c.get("/config", auth=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    for f in FIELDS:
        if f.editable:
            assert f'name="{f.path}"' in r.text, f.path
        else:
            assert f'name="{f.path}"' not in r.text, f.path
    assert 'value="140"' in r.text                        # aktueller Wert aus der lokalen Datei


def test_form_escapes_values(tmp_path):
    _, c, _ = make_app(tmp_path, local={"search": {"tags": ["<b>x</b>", "schranz"]}})
    r = c.get("/config", auth=AUTH)
    assert "<b>x</b>" not in r.text
    assert "&lt;b&gt;x&lt;/b&gt;" in r.text


def test_post_needs_matching_origin(tmp_path):
    _, c, local = make_app(tmp_path)
    form = full_form(**{"search.bpm_min": "140"})
    for path in ("/config/preview", "/config/save"):
        assert c.post(path, auth=AUTH, data=form).status_code == 403                       # ohne Origin
        assert c.post(path, auth=AUTH, data=form,
                      headers={"Origin": "http://evil.example"}).status_code == 403         # fremder Origin
    assert c.post("/config/reset", auth=AUTH, data={"path": "search.bpm_min"}).status_code == 403
    assert not local.exists()


def test_post_accepts_matching_referer_instead_of_origin(tmp_path):
    _, c, _ = make_app(tmp_path)
    r = c.post("/config/preview", auth=AUTH, data=full_form(),
               headers={"Referer": "http://testserver/config"})
    assert r.status_code == 200


def test_preview_shows_diff_and_writes_nothing(tmp_path):
    _, c, local = make_app(tmp_path)
    r = c.post("/config/preview", auth=AUTH, data=full_form(**{"search.bpm_min": "140"}), headers=ORIGIN)
    assert r.status_code == 200
    assert "search.bpm_min" in r.text and "140" in r.text and "150" in r.text
    assert not local.exists()


def test_preview_without_changes_says_so(tmp_path):
    _, c, _ = make_app(tmp_path)
    r = c.post("/config/preview", auth=AUTH, data=full_form(), headers=ORIGIN)
    assert r.status_code == 200 and "Keine Änderungen" in r.text


def test_preview_with_errors_returns_422_and_lists_them(tmp_path):
    _, c, local = make_app(tmp_path)
    r = c.post("/config/preview", auth=AUTH, data=full_form(**{"search.exploration_probability": "1.5"}),
               headers=ORIGIN)
    assert r.status_code == 422
    assert "search.exploration_probability" in r.text
    assert not local.exists()


def test_save_writes_local_file_and_reloads_config(tmp_path):
    app, c, local = make_app(tmp_path)
    base_before = BASE_PATH.read_bytes()
    r = c.post("/config/save", auth=AUTH, data=full_form(**{"search.bpm_min": "140"}),
               headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/config?saved=1"
    assert yaml.safe_load(local.read_text(encoding="utf-8")) == {"search": {"bpm_min": 140}}
    assert app.state.cfg["search"]["bpm_min"] == 140
    assert BASE_PATH.read_bytes() == base_before          # config.yaml bleibt unangetastet


def test_second_save_creates_a_backup(tmp_path):
    _, c, local = make_app(tmp_path)
    c.post("/config/save", auth=AUTH, data=full_form(**{"search.bpm_min": "140"}), headers=ORIGIN)
    c.post("/config/save", auth=AUTH, data=full_form(**{"search.bpm_min": "145"}), headers=ORIGIN)
    backups = [f for f in tmp_path.iterdir() if ".bak-" in f.name]
    assert len(backups) == 1
    assert yaml.safe_load(backups[0].read_text(encoding="utf-8")) == {"search": {"bpm_min": 140}}


def test_save_with_errors_writes_nothing(tmp_path):
    _, c, local = make_app(tmp_path)
    base_before = BASE_PATH.read_bytes()
    for bad in ({"search.bpm_min": "schnell"}, {"search.bpm_min": "170"}, {"state.db_path": "/tmp/x"}):
        r = c.post("/config/save", auth=AUTH, data=full_form(**bad), headers=ORIGIN)
        assert r.status_code == 422
    assert not local.exists()
    assert BASE_PATH.read_bytes() == base_before


def test_save_with_unchanged_form_creates_no_file(tmp_path):
    _, c, local = make_app(tmp_path)
    r = c.post("/config/save", auth=AUTH, data=full_form(), headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    assert not local.exists()


def test_reset_removes_a_single_override(tmp_path):
    app, c, local = make_app(tmp_path, local={"search": {"bpm_min": 140}, "scoring": {"min_plays": 500}})
    r = c.post("/config/reset", auth=AUTH, data={"path": "search.bpm_min"}, headers=ORIGIN,
               follow_redirects=False)
    assert r.status_code == 303
    assert yaml.safe_load(local.read_text(encoding="utf-8")) == {"scoring": {"min_plays": 500}}
    assert app.state.cfg["search"]["bpm_min"] == 150


def test_reset_rejects_unknown_and_operational_paths(tmp_path):
    _, c, local = make_app(tmp_path, local={"search": {"bpm_min": 140}})
    before = local.read_bytes()
    for path in ("search.gibt_es_nicht", "state.db_path", ""):
        r = c.post("/config/reset", auth=AUTH, data={"path": path}, headers=ORIGIN)
        assert r.status_code == 422, path
    assert local.read_bytes() == before


def test_secrets_never_appear_in_the_editor(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:SECRETTOKENSECRETTOKENSECRETTOKEN12")
    _, c, _ = make_app(tmp_path)
    r = c.get("/config", auth=AUTH)
    assert "SECRETTOKEN" not in r.text and PASSWORD not in r.text
    assert not any("token" in f.path.lower() or "password" in f.path.lower() for f in FIELDS)


# ------------------------------------------------------------------ Startbefehl verdrahtet den Editor
def test_main_enables_editor_only_with_local_path_env(tmp_path, monkeypatch):
    import sc_digger.web.__main__ as entry
    monkeypatch.setenv("SC_DIGGER_WEB_PASSWORD", PASSWORD)
    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(app=app))

    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)
    assert entry.main(["--config", str(BASE_PATH)]) == 0
    assert TestClient(started["app"]).get("/config", auth=AUTH).status_code == 404

    monkeypatch.setenv("SC_DIGGER_CONFIG_LOCAL", str(tmp_path / "config.local.yaml"))
    assert entry.main(["--config", str(BASE_PATH)]) == 0
    assert TestClient(started["app"]).get("/config", auth=AUTH).status_code == 200
