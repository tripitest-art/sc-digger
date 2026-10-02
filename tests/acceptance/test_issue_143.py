"""Akzeptanztests: Config-Schema, Validierung und lokale Override-Datei (Issue #<N>)."""
import copy
from pathlib import Path

import pytest
import yaml

from sc_digger.models import Config
from sc_digger.schema import FIELDS, Field, Problem, deep_merge, get_field, main as schema_main, validate_config

ROOT = Path(__file__).resolve().parents[2]
KINDS = {"bool", "int", "float", "str", "choice", "list", "map"}


def shipped_raw():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def with_value(path, value):
    """Kopie der ausgelieferten Config, in der der Punkt-Pfad `path` den Wert `value` hat."""
    raw = shipped_raw()
    node = raw
    *parents, last = path.split(".")
    for part in parents:
        node = node[part]
    node[last] = value
    return raw


def problems_at(raw, path, level=None):
    return [p for p in validate_config(raw)
            if p.path == path and (level is None or p.level == level)]


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    monkeypatch.delenv("SC_DIGGER_CONFIG_LOCAL", raising=False)


# ------------------------------------------------------------------ Felder
def test_shipped_config_is_valid():
    assert validate_config(shipped_raw()) == []


def test_fields_are_unique_and_documented():
    paths = [f.path for f in FIELDS]
    assert len(paths) == len(set(paths))
    for f in FIELDS:
        assert isinstance(f, Field)
        assert f.kind in KINDS
        assert f.label.strip() and f.help.strip()
        if f.kind == "choice":
            assert f.choices


def test_every_field_exists_in_shipped_config():
    raw = shipped_raw()
    for f in FIELDS:
        node = raw
        for part in f.path.split("."):
            assert isinstance(node, dict) and part in node, f"{f.path} fehlt in config.yaml"
            node = node[part]


def test_get_field():
    f = get_field("scoring.weights.like_ratio")
    assert f is not None and f.kind == "float"
    assert get_field("search.gibt_es_nicht") is None


def test_operational_paths_are_not_editable():
    for path in ["state.db_path", "state.track_db_path", "download.collection_dir",
                 "download.inbox_dir", "download.intake_dir", "rekordbox.xml_path",
                 "rekordbox.path_map"]:
        assert get_field(path).editable is False, path
    assert get_field("scoring.min_percentile").editable is True


# ------------------------------------------------------------------ Validierung
def test_missing_keys_are_fine():
    assert validate_config({}) == []
    assert validate_config({"search": {"bpm_min": 150}}) == []


def test_validate_does_not_mutate_input():
    raw = with_value("search.bpm_min", "viel")
    before = copy.deepcopy(raw)
    validate_config(raw)
    assert raw == before


def test_wrong_types_are_errors():
    assert problems_at(with_value("organize.write_tags", "ja"), "organize.write_tags", "error")
    assert problems_at(with_value("search.max_age_days", 1.5), "search.max_age_days", "error")
    assert problems_at(with_value("search.max_age_days", "14"), "search.max_age_days", "error")
    assert problems_at(with_value("search.tags", "schranz"), "search.tags", "error")
    assert problems_at(with_value("search.tags", ["schranz", 5]), "search.tags", "error")
    assert problems_at(with_value("download.collection_dir", 7), "download.collection_dir", "error")
    assert problems_at(with_value("rekordbox.path_map", ["a"]), "rekordbox.path_map", "error")


def test_bool_is_not_a_number():
    assert problems_at(with_value("search.bpm_min", True), "search.bpm_min", "error")
    assert problems_at(with_value("scoring.reference_boost", False), "scoring.reference_boost", "error")


def test_int_is_accepted_for_float_fields():
    assert validate_config(with_value("scoring.min_percentile", 40)) == []
    assert validate_config(with_value("search.exploration_probability", 1)) == []


def test_ranges_are_errors():
    assert problems_at(with_value("search.exploration_probability", 1.5),
                       "search.exploration_probability", "error")
    assert problems_at(with_value("search.exploration_probability", -0.1),
                       "search.exploration_probability", "error")
    assert problems_at(with_value("scoring.min_percentile", 101), "scoring.min_percentile", "error")
    assert problems_at(with_value("search.max_age_days", 0), "search.max_age_days", "error")
    assert validate_config(with_value("search.exploration_probability", 0.2)) == []


def test_choice_is_checked():
    assert problems_at(with_value("search.bpm_unknown_policy", "vielleicht"),
                       "search.bpm_unknown_policy", "error")
    assert validate_config(with_value("search.bpm_unknown_policy", "drop")) == []


def test_invalid_regex_in_title_patterns_is_an_error():
    raw = with_value("search.set_title_patterns", [r"\bmix\b", "([unvollständig"])
    assert problems_at(raw, "search.set_title_patterns", "error")


def test_unknown_keys_are_warnings_not_errors():
    raw = shipped_raw()
    raw["search"]["bpm_mn"] = 150
    raw["gibt_es_nicht"] = {"x": 1}
    found = validate_config(raw)
    assert [p.level for p in found if p.path == "search.bpm_mn"] == ["warning"]
    assert [p.level for p in found if p.path == "gibt_es_nicht"] == ["warning"]
    assert not [p for p in found if p.level == "error"]


def test_section_that_is_not_a_mapping_is_an_error():
    assert problems_at({"search": 5}, "search", "error")


def test_cross_field_checks():
    raw = shipped_raw()
    raw["search"]["bpm_min"], raw["search"]["bpm_max"] = 170, 150
    assert problems_at(raw, "search.bpm_min", "error")

    raw = shipped_raw()
    raw["organize"]["bpm_plausible_min"], raw["organize"]["bpm_plausible_max"] = 200, 120
    assert problems_at(raw, "organize.bpm_plausible_min", "error")

    raw = shipped_raw()
    raw["scoring"]["weights"]["like_ratio"] = 0.9   # Summe 1.55
    assert problems_at(raw, "scoring.weights", "warning")
    assert not problems_at(raw, "scoring.weights", "error")


def test_problem_has_level_path_message():
    p = problems_at(with_value("search.bpm_min", "x"), "search.bpm_min")[0]
    assert isinstance(p, Problem)
    assert p.level == "error" and p.message.strip()


# ------------------------------------------------------------------ deep_merge
def test_deep_merge_merges_dicts_and_replaces_lists():
    base = {"a": {"x": 1, "y": 2, "l": [1, 2]}, "b": 5, "c": {"k": 1}}
    over = {"a": {"y": 20, "l": [9]}, "b": 6, "d": 7}
    assert deep_merge(base, over) == {"a": {"x": 1, "y": 20, "l": [9]}, "b": 6, "c": {"k": 1}, "d": 7}


def test_deep_merge_does_not_mutate_inputs():
    base = {"a": {"x": 1, "l": [1]}}
    over = {"a": {"l": [2]}}
    base_before, over_before = copy.deepcopy(base), copy.deepcopy(over)
    result = deep_merge(base, over)
    result["a"]["l"].append(3)
    assert base == base_before and over == over_before


# ------------------------------------------------------------------ Config.load mit lokaler Datei
def write_local(tmp_path, data, name="config.local.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_load_without_local_equals_shipped():
    assert Config.load(ROOT / "config.yaml").raw == shipped_raw()


def test_load_merges_local_file(tmp_path):
    local = write_local(tmp_path, {"search": {"bpm_min": 140}, "scoring": {"weights": {"like_ratio": 0.4}}})
    cfg = Config.load(ROOT / "config.yaml", local=local)
    base = shipped_raw()
    assert cfg["search"]["bpm_min"] == 140
    assert cfg["search"]["bpm_max"] == base["search"]["bpm_max"]
    assert cfg["scoring"]["weights"]["like_ratio"] == 0.4
    assert cfg["scoring"]["weights"]["recency"] == base["scoring"]["weights"]["recency"]
    assert cfg["state"] == base["state"]


def test_load_reads_path_from_environment(tmp_path, monkeypatch):
    local = write_local(tmp_path, {"search": {"bpm_min": 141}})
    monkeypatch.setenv("SC_DIGGER_CONFIG_LOCAL", str(local))
    assert Config.load(ROOT / "config.yaml")["search"]["bpm_min"] == 141


def test_explicit_local_wins_over_environment(tmp_path, monkeypatch):
    env_file = write_local(tmp_path, {"search": {"bpm_min": 141}}, "env.yaml")
    arg_file = write_local(tmp_path, {"search": {"bpm_min": 142}}, "arg.yaml")
    monkeypatch.setenv("SC_DIGGER_CONFIG_LOCAL", str(env_file))
    assert Config.load(ROOT / "config.yaml", local=arg_file)["search"]["bpm_min"] == 142


def test_missing_or_empty_local_file_is_ignored(tmp_path):
    assert Config.load(ROOT / "config.yaml", local=tmp_path / "gibt-es-nicht.yaml").raw == shipped_raw()
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert Config.load(ROOT / "config.yaml", local=empty).raw == shipped_raw()


def test_broken_local_file_fails_loudly(tmp_path):
    bad = tmp_path / "kaputt.yaml"
    bad.write_text("search: [unbalanced\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kaputt.yaml"):
        Config.load(ROOT / "config.yaml", local=bad)
    liste = tmp_path / "liste.yaml"
    liste.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="liste.yaml"):
        Config.load(ROOT / "config.yaml", local=liste)


def test_load_does_not_touch_files(tmp_path):
    local = write_local(tmp_path, {"search": {"bpm_min": 140}})
    base_before = (ROOT / "config.yaml").read_bytes()
    local_before = local.read_bytes()
    Config.load(ROOT / "config.yaml", local=local)
    assert (ROOT / "config.yaml").read_bytes() == base_before
    assert local.read_bytes() == local_before


# ------------------------------------------------------------------ Befehl `python -m sc_digger.schema`
def test_cli_ok_on_shipped_config(capsys):
    assert schema_main(["--config", str(ROOT / "config.yaml")]) == 0
    assert "OK" in capsys.readouterr().out


def test_cli_reports_errors_with_exit_code_1(tmp_path, capsys):
    local = write_local(tmp_path, {"search": {"bpm_min": 170, "bpm_max": 150}})
    code = schema_main(["--config", str(ROOT / "config.yaml"), "--local", str(local)])
    out = capsys.readouterr().out
    assert code == 1
    assert "FEHLER" in out and "search.bpm_min" in out


def test_cli_warnings_do_not_fail(tmp_path, capsys):
    local = write_local(tmp_path, {"search": {"bpm_mn": 150}})
    code = schema_main(["--config", str(ROOT / "config.yaml"), "--local", str(local)])
    out = capsys.readouterr().out
    assert code == 0
    assert "WARNUNG" in out and "search.bpm_mn" in out


def test_cli_broken_local_file_returns_1(tmp_path, capsys):
    bad = tmp_path / "kaputt.yaml"
    bad.write_text("search: [unbalanced\n", encoding="utf-8")
    assert schema_main(["--config", str(ROOT / "config.yaml"), "--local", str(bad)]) == 1
    assert "kaputt.yaml" in capsys.readouterr().out
