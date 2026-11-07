"""Akzeptanztests: Betriebskarte auf der Statusseite."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.web.ops import OpsItem, collect_ops

ROOT = Path(__file__).resolve().parents[2]
JETZT = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
GB = 1024 ** 3


def _cfg(tmp_path, *, min_free_gb=0, backup_dir=""):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["download"] = {**cfg.raw["download"], "inbox_dir": str(tmp_path / "inbox"), "min_free_gb": min_free_gb}
    cfg.raw["backup"] = {"target_dir": backup_dir}
    cfg.raw["state"] = {**cfg.raw.get("state", {}), "db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    return cfg


def _frei(gb):
    return lambda pfad: SimpleNamespace(free=int(gb * GB))


def _nach_key(items):
    return {i.key: i for i in items}


def test_immer_drei_eintraege_in_fester_reihenfolge(tmp_path):
    items = collect_ops(_cfg(tmp_path), now=JETZT)
    assert [i.key for i in items] == ["disk", "backup", "canary"]
    assert all(isinstance(i, OpsItem) for i in items)
    assert [i.label for i in items] == ["Speicherplatz", "Sicherung", "API-Kanarienvogel"]


def test_alles_aus_ohne_konfiguration(tmp_path):
    d = _nach_key(collect_ops(_cfg(tmp_path), now=JETZT))
    assert (d["disk"].state, d["disk"].text) == ("off", "Wächter aus (download.min_free_gb = 0)")
    assert (d["backup"].state, d["backup"].text) == ("off", "nicht eingerichtet")
    assert (d["canary"].state, d["canary"].text) == ("off", "noch kein Lauf")


def test_speicherplatz_ok_und_knapp(tmp_path):
    ok = _nach_key(collect_ops(_cfg(tmp_path, min_free_gb=10), usage=_frei(50), now=JETZT))["disk"]
    assert (ok.state, ok.text) == ("ok", "50.0 GB frei (Minimum 10 GB)")
    knapp = _nach_key(collect_ops(_cfg(tmp_path, min_free_gb=10), usage=_frei(4.5), now=JETZT))["disk"]
    assert (knapp.state, knapp.text) == ("warn", "Nur 4.5 GB frei (Minimum 10 GB)")


def test_speicherplatz_messfehler(tmp_path):
    def kaputt(pfad):
        raise OSError("weg")
    d = _nach_key(collect_ops(_cfg(tmp_path, min_free_gb=10), usage=kaputt, now=JETZT))["disk"]
    assert (d.state, d.text) == ("warn", "Messung fehlgeschlagen")


def test_sicherung_alter(tmp_path):
    ziel = tmp_path / "bak"
    for name in ("2026-11-04", "2026-11-05", "kein-datum"):
        (ziel / name).mkdir(parents=True)
    d = _nach_key(collect_ops(_cfg(tmp_path, backup_dir=str(ziel)), now=JETZT))["backup"]
    assert (d.state, d.text) == ("ok", "Letzte Sicherung: 2026-11-05 (gestern)")
    alt = tmp_path / "alt"
    (alt / "2026-11-01").mkdir(parents=True)
    d = _nach_key(collect_ops(_cfg(tmp_path, backup_dir=str(alt)), now=JETZT))["backup"]
    assert (d.state, d.text) == ("warn", "Letzte Sicherung: 2026-11-01 (vor 5 Tagen)")


def test_sicherung_ohne_ordner(tmp_path):
    d = _nach_key(collect_ops(_cfg(tmp_path, backup_dir=str(tmp_path / "gibt-es-nicht")), now=JETZT))["backup"]
    assert (d.state, d.text) == ("warn", "Keine Sicherung gefunden")


def test_kanarienvogel_ok_und_fehler(tmp_path):
    cfg = _cfg(tmp_path)
    with Health(cfg.raw["state"]["db_path"]) as h:
        h.record("api_schema", ok=True, found=1)
    d = _nach_key(collect_ops(cfg, now=JETZT))["canary"]
    assert d.state == "ok"
    assert d.text.startswith("API-Antwort vollständig (Stand ")
    with Health(cfg.raw["state"]["db_path"]) as h:
        h.record("api_schema", ok=False, found=0, error="user.id")
    d = _nach_key(collect_ops(cfg, now=JETZT))["canary"]
    assert (d.state, d.text) == ("warn", "Felder fehlen: user.id")