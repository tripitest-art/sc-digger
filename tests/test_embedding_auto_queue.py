"""Tests: Embedding-Jobs im täglichen Lauf einreihen (embeddings.auto_queue)."""
from pathlib import Path

from sc_digger.models import Config
from sc_digger.schema import FIELDS, validate_config

ROOT = Path(__file__).resolve().parents[1]


def test_embeddings_auto_queue_in_schema():
    field_paths = {f.path for f in FIELDS}
    assert "embeddings.auto_queue" in field_paths


def test_embeddings_auto_queue_ist_bool():
    field = {f.path: f for f in FIELDS}["embeddings.auto_queue"]
    assert field.kind == "bool"


def test_validate_config_mit_embeddings_block():
    cfg = Config.load(ROOT / "config.yaml")
    problems = validate_config(cfg.raw)
    emb_problems = [p for p in problems if "embeddings" in p.path]
    assert not emb_problems, f"Fehler/Warnungen für embeddings.*: {emb_problems}"