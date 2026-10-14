"""Eigene Tests zu Issue #52: Energie-Klassifizierung (1..5) und Energie-Tag.

Ergänzen die Akzeptanztests um Grenzwerte, das Verhalten von `rms`, das Ersetzen
(kein Anhäufen) des Energie-Tags sowie die Konsistenz des Migrations-Eintrags.
Alle Tests laufen ohne Netz; Audio wird synthetisch per ffmpeg bzw. wave erzeugt.
"""
import subprocess
import wave
from pathlib import Path

import numpy as np
from mutagen.flac import FLAC
from mutagen.id3 import ID3

from sc_digger import db as db_mod
from sc_digger.analysis import compute_energy_features
from sc_digger.organize import read_energy_tag, write_tags
from sc_digger.style import Subgenre, calculate_energy, classify_track


def _make_synth_wav(path: Path, freq: float = 1000.0, duration: float = 6.0,
                    sr: int = 22050) -> Path:
    """Erzeugt eine synthetische WAV-Datei mit Sinuston bestimmter Frequenz."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(sig.tobytes())
    return path


def _make_audio_file(path: Path) -> Path:
    """Erzeugt eine kurze Testdatei (MP3, FLAC, WAV oder AIFF) via ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-b:a", "320k"] if path.suffix == ".mp3" else []
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "2", *args, str(path)],
        check=True,
    )
    return path


def test_energy_grenzen_lra_und_centroid_exakt():
    """Die Schwellwerte greifen erst echt über/unter dem Wert, nicht auf ihm."""
    # LRA = 3.0 ist noch keine Brickwall (Bedingung ist strikt <), LRA = 6.5
    # ist noch kein Dynamik-Malus (Bedingung ist strikt >).
    assert calculate_energy(148.0, lra=3.0) == 3
    assert calculate_energy(148.0, lra=6.5) == 3
    # Centroid genau auf den Schwellen bleibt neutral.
    assert calculate_energy(148.0, spectral_centroid_hz=3500.0) == 3
    assert calculate_energy(148.0, spectral_centroid_hz=1800.0) == 3
    # LUFS genau -6.0 ist nicht lauter als die Schwelle.
    assert calculate_energy(148.0, lufs=-6.0) == 3
    assert calculate_energy(148.0, lufs=-6.1) == 3


def test_energy_rms_ohne_wirkung():
    """`rms` wird entgegengenommen, verändert das Ergebnis aber nicht."""
    basis = calculate_energy(148.0, spectral_centroid_hz=2500.0)
    assert calculate_energy(148.0, spectral_centroid_hz=2500.0, rms=0.0) == basis
    assert calculate_energy(148.0, spectral_centroid_hz=2500.0, rms=0.9) == basis
    assert calculate_energy(148.0, rms=1e-9) == calculate_energy(148.0)
    assert calculate_energy(148.0, rms=123456.0) == calculate_energy(148.0)


def test_energy_modifikatoren_addieren_sich():
    """Mehrere Zuschläge/Abzüge summieren sich vor der Klammerung."""
    # Basis 3 (148 BPM) + lauter + Brickwall + hell + Terror = 7 -> 5.
    assert calculate_energy(
        148.0, lufs=-4.0, lra=2.0, spectral_centroid_hz=5000.0,
        subgenre=Subgenre.TERROR,
    ) == 5
    # Basis 3 - Dynamik(Melodic) - dunkel - Dynamik = -1 -> 1 (Melodic zieht nur einmal).
    assert calculate_energy(
        130.0, lufs=-14.0, lra=9.0, spectral_centroid_hz=1000.0,
        subgenre=Subgenre.MELODIC,
    ) == 1
    # Ohne jedes Merkmal zählt allein das Tempo.
    assert calculate_energy(155.0) == 4


def test_classify_track_reicht_lra_an_energy_weiter():
    """classify_track bildet die Energie aus denselben Regeln wie calculate_energy."""
    assert classify_track(148.0, lra=2.5).energy == 4
    assert classify_track(148.0, lra=9.0).energy == 2
    # gleiche Achsenlogik: 148 BPM bleibt Basis 3, ohne Merkmale.
    assert classify_track(148.0).energy == 3
    # Subgenre aus Tags wird an die Energie weitergereicht (Terror hebt an).
    assert classify_track(148.0, tags=["terror"]).energy == 4


def test_energy_features_nutzt_echte_datei(tmp_path):
    """compute_energy_features liefert für synthetisches Audio plausible Werte."""
    res = compute_energy_features(_make_synth_wav(tmp_path / "tone.wav", freq=3000.0))
    assert set(res) == {"spectral_centroid_hz", "rms"}
    assert res["spectral_centroid_hz"] is not None and res["spectral_centroid_hz"] > 0.0
    assert res["rms"] is not None and res["rms"] > 0.0


def test_write_tags_ohne_energy_laesst_vorhandenes_tag_unberuehrt(tmp_path):
    """Ein erneutes Tagging ohne energy darf ein vorhandenes Tag nicht löschen."""
    p = _make_audio_file(tmp_path / "t.mp3")
    assert write_tags(p, artist="A", title="T", bpm=150.0, energy=4) is True
    assert read_energy_tag(p) == 4

    assert write_tags(p, artist="A", title="T", bpm=150.0) is True
    assert read_energy_tag(p) == 4

    frisch = _make_audio_file(tmp_path / "neu.mp3")
    assert write_tags(frisch, artist="A", title="T", bpm=150.0) is True
    assert read_energy_tag(frisch) is None


def test_write_tags_ersetzt_energy_statt_zu_haeufen(tmp_path):
    """Erneutes Schreiben ersetzt den Wert, es entsteht kein zweiter Frame."""
    p = _make_audio_file(tmp_path / "ersetz.mp3")
    assert write_tags(p, artist="A", title="T", bpm=150.0, energy=2) is True
    assert write_tags(p, artist="A", title="T", bpm=150.0, energy=5) is True
    assert read_energy_tag(p) == 5

    frame = ID3(str(p)).get("TXXX:SCDIGGER_ENERGY")
    assert frame is not None and frame.text == ["5"]

    f = _make_audio_file(tmp_path / "ersetz.flac")
    assert write_tags(f, artist="A", title="T", bpm=150.0, energy=1) is True
    assert write_tags(f, artist="A", title="T", bpm=150.0, energy=3) is True
    assert read_energy_tag(f) == 3
    assert FLAC(str(f)).get("scdigger_energy") == ["3"]


def test_write_tags_energy_grenzwerte_und_none(tmp_path):
    """Gültige Stufen 1 und 5 werden geschrieben, None setzt kein Tag."""
    p1 = _make_audio_file(tmp_path / "eins.mp3")
    assert write_tags(p1, artist="A", title="T", energy=1) is True
    assert read_energy_tag(p1) == 1

    p5 = _make_audio_file(tmp_path / "fuenf.mp3")
    assert write_tags(p5, artist="A", title="T", energy=5) is True
    assert read_energy_tag(p5) == 5

    pn = _make_audio_file(tmp_path / "keins.mp3")
    assert write_tags(pn, artist="A", title="T", energy=None) is True
    assert read_energy_tag(pn) is None


def test_read_energy_tag_nicht_unterstuetzte_formate(tmp_path):
    """Der Leser deckt nur MP3 und FLAC ab; andere Formate liefern unauffällig None."""
    wav = _make_audio_file(tmp_path / "x.wav")
    assert read_energy_tag(wav) is None

    aiff = _make_audio_file(tmp_path / "x.aiff")
    assert read_energy_tag(aiff) is None

    unbekannt = tmp_path / "x.ogg"
    unbekannt.write_bytes(b"kein audio")
    assert read_energy_tag(unbekannt) is None


def test_migration_energie_eindeutig_und_angelegt(tmp_path):
    """Die Energie-Migration ist eindeutig versioniert und legt die Spalte an."""
    versionen = [v for v, _, _ in db_mod.MIGRATIONS]
    assert len(versionen) == len(set(versionen))

    namen = {name for _, name, _ in db_mod.MIGRATIONS}
    assert "0011_tracks_energy" in namen
    assert "0010_gate_items" in namen

    with db_mod.TrackDB(tmp_path / "tracks.sqlite") as db:
        spalten = {row[1] for row in db.db.execute("PRAGMA table_info(tracks)").fetchall()}
        assert "energy" in spalten
        indizes = {row[1] for row in db.db.execute("PRAGMA index_list(tracks)").fetchall()}
        assert "idx_tracks_energy" in indizes
