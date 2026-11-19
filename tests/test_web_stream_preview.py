"""Eigene Tests zu den Hörproben aus dem Stream (Issue #622): Randfälle, kein Netz."""
import os
from pathlib import Path

from sc_digger.models import Config
from sc_digger.web import stream_preview

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path, **web):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "data" / "seen.sqlite")}
    cfg.raw["web"] = web
    return cfg


def test_schalter_fehlt_ist_aus(tmp_path):
    cfg = _cfg(tmp_path)
    assert stream_preview.preview_enabled(cfg) is False
    del cfg.raw["web"]
    assert stream_preview.preview_enabled(cfg) is False


def test_limit_standard_und_ungueltig(tmp_path):
    mb = 1024 * 1024
    assert stream_preview.cache_limit_bytes(_cfg(tmp_path)) == 500 * mb
    assert stream_preview.cache_limit_bytes(_cfg(tmp_path, preview_cache_mb="viel")) == 500 * mb
    assert stream_preview.cache_limit_bytes(_cfg(tmp_path, preview_cache_mb=-3)) == 500 * mb
    assert stream_preview.cache_limit_bytes(_cfg(tmp_path, preview_cache_mb=2)) == 2 * mb
    assert stream_preview.cache_limit_bytes(_cfg(tmp_path, preview_cache_mb=0)) == 0


def test_cache_dir_neben_state_db(tmp_path):
    assert stream_preview.cache_dir(_cfg(tmp_path)) == tmp_path / "data" / "preview_cache"


def test_prune_fehlender_ordner_wirft_nie(tmp_path):
    assert stream_preview.prune_cache(tmp_path / "gibt-es-nicht", 0) == 0


def test_prune_gleicher_zeitstempel_nach_name(tmp_path):
    for name in ["b.ogg", "a.ogg", "c.ogg"]:
        p = tmp_path / name
        p.write_bytes(b"x" * 10)
        os.utime(p, (5_000, 5_000))
    assert stream_preview.prune_cache(tmp_path, 10) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["c.ogg"]


def test_neue_probe_raeumt_den_speicher_auf(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, stream_preview=True, preview_cache_mb=0)
    monkeypatch.setattr(stream_preview, "resolve_stream_url", lambda sc_id: "https://x/s")

    def extract(url, target, *, start_s, duration_s, bitrate_kbps):
        Path(target).write_bytes(b"OggS")
        return True

    monkeypatch.setattr(stream_preview, "extract_preview_from_url", extract)
    # Limit 0: die frisch gebaute Datei wird sofort wieder entfernt, also keine Hörprobe.
    assert stream_preview.get_or_build_preview(cfg, 3) is None


def test_leere_cache_datei_wird_neu_gebaut(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, stream_preview=True)
    root = stream_preview.cache_dir(cfg)
    root.mkdir(parents=True)
    (root / "4.ogg").write_bytes(b"")
    monkeypatch.setattr(stream_preview, "resolve_stream_url", lambda sc_id: "https://x/s")

    def extract(url, target, *, start_s, duration_s, bitrate_kbps):
        Path(target).write_bytes(b"OggS")
        return True

    monkeypatch.setattr(stream_preview, "extract_preview_from_url", extract)
    assert stream_preview.get_or_build_preview(cfg, 4) == root / "4.ogg"
    assert (root / "4.ogg").read_bytes() == b"OggS"


def test_ausnahme_im_netz_wird_zu_none(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, stream_preview=True)

    def kaputt(sc_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(stream_preview, "resolve_stream_url", kaputt)
    assert stream_preview.get_or_build_preview(cfg, 5) is None


def test_resolve_stream_url_faengt_ausnahmen(monkeypatch):
    class Kaputt:
        def preview_url(self, sc_id):
            raise RuntimeError("https://api?client_id=GEHEIM")

    monkeypatch.setattr(stream_preview, "SoundCloudClient", Kaputt)
    assert stream_preview.resolve_stream_url(1) is None
