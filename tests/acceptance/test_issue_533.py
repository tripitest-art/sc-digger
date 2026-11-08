"""Akzeptanztests: Reste ohne Vertrag entfernt, genutzter Exportweg intakt."""


def test_tote_funktionen_sind_entfernt():
    from sc_digger import db, dj_export

    assert not hasattr(db.TrackDB, "count_tracks")
    for name in ("write_m3u8", "export_report", "load_entries"):
        assert not hasattr(dj_export, name), name


def test_genutzter_exportweg_bleibt():
    from sc_digger import dj_export

    for name in ("Entry", "Playlist", "order_harmonic", "build_dj_set"):
        assert hasattr(dj_export, name), name


def test_dj_export_bleibt_importierbar():
    import importlib

    importlib.import_module("sc_digger.dj_export")
