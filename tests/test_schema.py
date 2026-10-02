"""Tests für sc_digger/schema.py (Issue #143): Feldtypen, Validierung, deep_merge, CLI."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from sc_digger.schema import (
    FIELDS,
    Field,
    Problem,
    deep_merge,
    get_field,
    main as schema_main,
    validate_config,
)

ROOT = Path(__file__).resolve().parents[1]


def shipped_raw() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def with_value(path: str, value):
    raw = shipped_raw()
    node = raw
    *parents, last = path.split(".")
    for part in parents:
        node = node[part]
    node[last] = value
    return raw


def errors(raw, path):
    return [p for p in validate_config(raw) if p.path == path and p.level == "error"]


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


# ------------------------------------------------------------------ jeder kind einmal
def test_bool_kind():
    assert get_field("organize.write_tags").kind == "bool"
    assert validate_config(with_value("organize.write_tags", True)) == []
    assert errors(with_value("organize.write_tags", "ja"), "organize.write_tags")


def test_int_kind():
    assert get_field("search.max_age_days").kind == "int"
    assert validate_config(with_value("search.max_age_days", 7)) == []
    assert errors(with_value("search.max_age_days", 1.5), "search.max_age_days")


def test_float_kind():
    assert get_field("scoring.min_percentile").kind == "float"
    assert validate_config(with_value("scoring.min_percentile", 55.5)) == []
    assert errors(with_value("scoring.min_percentile", "55"), "scoring.min_percentile")


def test_str_kind():
    assert get_field("organize.default_genre").kind == "str"
    assert validate_config(with_value("organize.default_genre", "Techno")) == []
    assert errors(with_value("organize.default_genre", 3), "organize.default_genre")


def test_choice_kind():
    field = get_field("search.bpm_unknown_policy")
    assert field.kind == "choice" and field.choices == ("keep", "drop")
    assert validate_config(with_value("search.bpm_unknown_policy", "drop")) == []
    assert errors(with_value("search.bpm_unknown_policy", "weg"), "search.bpm_unknown_policy")


def test_list_kind():
    assert get_field("search.tags").kind == "list"
    assert validate_config(with_value("search.tags", ["acid"])) == []
    assert errors(with_value("search.tags", "acid"), "search.tags")
    assert errors(with_value("search.tags", [1]), "search.tags")


def test_map_kind():
    field = get_field("rekordbox.path_map")
    assert field.kind == "map"
    assert validate_config(with_value("rekordbox.path_map", {"/a": "Z:/a"})) == []
    assert errors(with_value("rekordbox.path_map", ["a"]), "rekordbox.path_map")
    assert errors(with_value("rekordbox.path_map", {"/a": 1}), "rekordbox.path_map")


# ------------------------------------------------------------------ Regex-Diagnose
def test_invalid_regex_message_contains_pattern():
    raw = with_value("search.set_title_patterns", [r"\bmix\b", "([unvollständig"])
    probs = [p for p in validate_config(raw) if p.path == "search.set_title_patterns"]
    assert [p.level for p in probs] == ["error"]
    assert "([unvollständig" in probs[0].message


def test_valid_regex_is_accepted():
    assert validate_config(with_value("search.set_title_patterns", [r"\bmix\b", r"^set\d+"])) == []


# ------------------------------------------------------------------ deep_merge
def test_deep_merge_nested_lists_replaced_not_merged():
    base = {"a": {"b": {"l": [1, 2, 3]}}}
    over = {"a": {"b": {"l": [9]}}}
    assert deep_merge(base, over) == {"a": {"b": {"l": [9]}}}


def test_deep_merge_result_is_independent_of_inputs():
    base = {"a": {"b": {"l": [1]}}, "keep": {"x": 1}}
    over = {"a": {"b": {"l": [2]}, "neu": {"y": 2}}}
    base_before, over_before = copy.deepcopy(base), copy.deepcopy(over)
    result = deep_merge(base, over)
    result["a"]["b"]["l"].append(3)
    result["keep"]["x"] = 99
    result["a"]["neu"]["y"] = 98
    assert base == base_before and over == over_before


def test_deep_merge_adds_new_keys_deep():
    base = {"a": {"x": 1}}
    over = {"a": {"y": 2}, "b": {"z": 3}}
    assert deep_merge(base, over) == {"a": {"x": 1, "y": 2}, "b": {"z": 3}}


# ------------------------------------------------------------------ Schema-Konsistenz
def test_labels_are_short_and_help_filled():
    for field in FIELDS:
        assert isinstance(field, Field)
        assert len(field.label) <= 40, field.path
        assert field.help.strip(), field.path


def test_editable_flag_defaults_true():
    assert get_field("digest.sunday_summary").editable is True
    for path in ("state.db_path", "download.collection_dir", "rekordbox.path_map"):
        assert get_field(path).editable is False


# ------------------------------------------------------------------ CLI
def test_cli_reports_warning_and_exit_zero(tmp_path, capsys):
    local = tmp_path / "local.yaml"
    local.write_text(yaml.safe_dump({"search": {"bpm_mn": 150}}), encoding="utf-8")
    code = schema_main(["--config", str(ROOT / "config.yaml"), "--local", str(local)])
    out = capsys.readouterr().out
    assert code == 0 and "WARNUNG search.bpm_mn" in out


def test_cli_missing_config_returns_1_and_prints_error(capsys):
    code = schema_main(["--config", str(ROOT / "gibt-es-nicht.yaml")])
    out = capsys.readouterr().out
    assert code == 1 and out.startswith("FEHLER")
