"""Tests zum Prüfbericht `_eval/REVIEW-2026-10-14.md`: Kapitel 6 (6.1 unmessbar, 6.4 Quarantäne)
und die Schritte 3 bis 6 der Tabelle in Kapitel 10 (BPM-Konfidenz, TKEY = Camelot,
ID3-Reserve, WAV nach FLAC).

Alles synthetisch per ffmpeg, kein Netzwerk, Schreiben nur in tmp_path.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from sc_digger import analysis as A
from sc_digger import main as m
from sc_digger.models import Config, Track
from sc_digger.organize import write_tags
from sc_digger.output import State, _fmt_track, finalize_quality
from sc_digger.quality import check_file, convert_wav_to_flac
from sc_digger.soundcloud import SoundCloudClient

ROOT = Path(__file__).resolve().parent.parent
CFG = Config.load(ROOT / "config.yaml")


def _cfg(tmp_path: Path, **organize) -> Config:
    cfg = Config(dict(Config.load(ROOT / "config.yaml").raw))
    cfg.raw["download"] = {**cfg.raw["download"], "inbox_dir": str(tmp_path / "inbox"),
                           "collection_dir": str(tmp_path / "coll")}
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite")}
    cfg.raw["organize"] = {**cfg.raw["organize"], "enabled": False, **organize}
    return cfg


def _track(i: int = 1, **kw) -> Track:
    raw = dict(id=i, kind="track", title=f"Track {i}", permalink_url=f"https://soundcloud.com/a/t{i}",
               user={"username": "Artist", "permalink_url": "https://soundcloud.com/a"},
               created_at=datetime.now(timezone.utc).isoformat(), playback_count=1000,
               likes_count=50, reposts_count=5, comment_count=1, downloadable=True,
               tag_list="", description="")
    raw.update(kw)
    return SoundCloudClient._to_track(raw)


def _audio(path: Path, lavfi: str, *args: str, duration: float = 5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{lavfi}:d={duration}",
                    "-ac", "2", "-ar", "44100", *args, str(path)], check=True)
    return path


def _noise(path: Path, *args: str, duration: float = 5, af: str | None = None) -> Path:
    extra = ["-af", af] if af else []
    return _audio(path, "anoisesrc=c=white:a=0.3:s=1", *extra, *args, duration=duration)


def _fake_wav(path: Path) -> Path:
    """Typischer Fake: 128-kbps-MP3 (harte Kante bei ~16 kHz), zurück in WAV dekodiert."""
    src = _noise(path.with_suffix(".src.wav"), "-c:a", "pcm_s16le")
    mp3 = path.with_suffix(".128.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:a", "libmp3lame", "-b:a", "128k",
                    str(mp3)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-c:a", "pcm_s16le", str(path)], check=True)
    src.unlink()
    mp3.unlink()
    return path


def _stub_analysis(monkeypatch, **result):
    full = {"bpm": None, "bpm_confidence": 0.0, "bpm_octave": 1, "key_camelot": "6A",
            "key_name": "Gm", "key_confidence": 0.5, "key_margin": 0.1, **result}
    monkeypatch.setattr(m, "analyze_track", lambda p: dict(full))
    monkeypatch.setattr(m, "finalize_quality", lambda t, p, inbox, cfg: p)
    monkeypatch.setattr(m, "find_first_kick", lambda p: None)
    monkeypatch.setattr(m, "analyze_structure", lambda p: [])


# ======================================================================== 6.1 unmessbar
def test_stille_ist_unmessbar_und_kein_fake(tmp_path):
    silent = _audio(tmp_path / "s.wav", "anullsrc=r=44100", "-c:a", "pcm_s16le")
    r = check_file(silent, CFG)
    assert r["ok"] is False
    assert r["unmeasurable"] is True
    assert "nicht messbar" in r["reason"]


def test_echter_fake_ist_nicht_unmessbar(tmp_path):
    r = check_file(_fake_wav(tmp_path / "fake.wav"), CFG)
    assert r["ok"] is False
    assert not r.get("unmeasurable")


def test_finalize_quality_legt_unmessbares_in_eigenen_ordner(tmp_path):
    cfg = _cfg(tmp_path)
    inbox = tmp_path / "inbox"
    silent = _audio(inbox / "[1] still.wav", "anullsrc=r=44100", "-c:a", "pcm_s16le")
    fake = _fake_wav(inbox / "[2] fake.wav")

    assert finalize_quality(_track(1), silent, inbox, cfg) is None
    assert finalize_quality(_track(2), fake, inbox, cfg) is None

    assert (inbox / "_rejected" / "unmeasurable" / "[1] still.wav").exists()
    assert (inbox / "_rejected" / "[2] fake.wav").exists()
    assert not (inbox / "_rejected" / "[1] still.wav").exists()


# ======================================================================== 6.4 Quarantäne
def test_rejection_reason_unterscheidet_abgelehnt_von_ok():
    ok = _track(1)
    ok.quality_report = {"ok": True, "reason": "echte Qualität"}
    assert m._rejection_reason(ok) is None

    fake = _track(2)
    fake.quality_report = {"ok": False, "reason": "Spektrum endet bei 14 kHz"}
    assert m._rejection_reason(fake) == "Spektrum endet bei 14 kHz"

    still = _track(3)
    still.quality_report = {"ok": False, "unmeasurable": True,
                            "reason": "Spektrum nicht messbar (Stille oder kein Signal)"}
    assert "nicht messbar" in m._rejection_reason(still)

    kaputt = _track(4)
    kaputt.download_error = "403 vom Server"
    assert m._rejection_reason(kaputt) == "403 vom Server"

    ohne_pruefung = _track(5)
    ohne_pruefung.notes.append("Qualitätsprüfung fehlgeschlagen: ffprobe")
    assert "Qualitätsprüfung fehlgeschlagen" in m._rejection_reason(ohne_pruefung)

    nicht_geladen = _track(6)          # Gate/Stream: nie ein Download -> normal gemeldet
    assert m._rejection_reason(nicht_geladen) is None


def test_record_outcomes_verbrennt_abgelehnte_nicht_in_seen(tmp_path):
    ok, fake = _track(1), _track(2)
    ok.quality_report = {"ok": True, "reason": "echte Qualität"}
    fake.quality_report = {"ok": False, "reason": "Spektrum endet bei 14 kHz", "cutoff_hz": 14000}
    with State(tmp_path / "seen.sqlite") as state:
        m.record_outcomes(state, [ok, fake])
        assert state.is_seen(1) is True
        assert state.is_seen(2) is False          # taucht beim nächsten Lauf wieder auf
        assert [r["id"] for r in state.list_rejected()] == [2]
        assert state.list_rejected()[0]["reason"] == "Spektrum endet bei 14 kHz"


# ======================================================================== Schritt 5: BPM-Konfidenz
@pytest.mark.parametrize("conf, erwartet", [
    (0.9, 155.0),     # sicher: Audio schlägt die falsche Uploader-Angabe
    (0.6, 155.0),     # Schwelle inklusive
    (0.59, 142.0),    # unsicher: Text-BPM bleibt
    (None, 142.0),    # ohne Konfidenz wie bisher
])
def test_resolve_bpm_audio_schlaegt_text_nur_mit_konfidenz(conf, erwartet):
    # Text 142 hat keinen Oktavbezug zu Audio 155 (Kandidaten 77,5 / 155 / 310): die Konfidenz entscheidet
    bpm, _ = A.resolve_bpm(155.0, 142.0, window=(150, 165), audio_confidence=conf)
    assert bpm == erwartet


def test_resolve_bpm_nah_am_text_bleibt_bestaetigt():
    bpm, why = A.resolve_bpm(156.0, 154.0, window=(150, 165), audio_confidence=0.2)
    assert bpm == 156.0 and "bestätigt" in why


def test_resolve_bpm_audio_gewinnt_loest_trotzdem_oktave_auf():
    # Text sagt 100 (falsch), Audio hört die Halbzeit 77,5 sicher: Ergebnis liegt im Suchfenster
    bpm, why = A.resolve_bpm(77.5, 100.0, window=(150, 165), audio_confidence=0.8)
    assert bpm == 155.0
    assert "überstimmt" in why and "0.80" in why


def test_resolve_bpm_schwelle_ist_einstellbar():
    bpm, _ = A.resolve_bpm(155.0, 142.0, window=(150, 165), audio_confidence=0.7, min_confidence=0.9)
    assert bpm == 142.0


def test_finish_file_audio_ueberstimmt_falsche_uploader_bpm(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _stub_analysis(monkeypatch, bpm=155.0, bpm_confidence=0.8)
    captured: list[dict] = []
    monkeypatch.setattr(m, "write_tags", lambda *a, **k: captured.append(k) or True)
    f = tmp_path / "inbox" / "x.wav"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    t = _track(1, title="Banger 142 BPM")

    m.finish_file(t, f, tmp_path / "inbox", cfg)

    assert t.bpm == 155.0
    assert t.text_bpm == 142.0
    assert captured[0]["text_bpm"] == 142.0
    assert captured[0]["key_camelot"] == "6A" and captured[0]["key_name"] == "Gm"
    assert captured[0]["padding_kb"] == 4.0          # Standard aus config.yaml


def test_finish_file_unsichere_audio_bpm_laesst_text_stehen(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _stub_analysis(monkeypatch, bpm=155.0, bpm_confidence=0.3)
    captured: list[dict] = []
    monkeypatch.setattr(m, "write_tags", lambda *a, **k: captured.append(k) or True)
    f = tmp_path / "inbox" / "x.wav"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    t = _track(1, title="Banger 142 BPM")

    m.finish_file(t, f, tmp_path / "inbox", cfg)

    assert t.bpm == 142.0
    assert t.text_bpm is None and captured[0]["text_bpm"] is None


def test_finish_file_schreibt_beide_notierungen_bei_oktavkorrektur(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _stub_analysis(monkeypatch, bpm=79.0, bpm_confidence=0.9, bpm_octave=0)
    captured: list[dict] = []
    monkeypatch.setattr(m, "write_tags", lambda *a, **k: captured.append(k) or True)
    f = tmp_path / "inbox" / "x.wav"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    t = _track(1)

    m.finish_file(t, f, tmp_path / "inbox", cfg)

    assert t.bpm == 158.0
    assert "BPM: 158 (Audio roh 79.0)" in captured[0]["comment"]


def test_schwelle_aus_der_config_wird_benutzt(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, bpm_audio_min_confidence=0.95)
    _stub_analysis(monkeypatch, bpm=155.0, bpm_confidence=0.8)
    monkeypatch.setattr(m, "write_tags", lambda *a, **k: True)
    f = tmp_path / "inbox" / "x.wav"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    t = _track(1, title="Banger 142 BPM")
    m.finish_file(t, f, tmp_path / "inbox", cfg)
    assert t.bpm == 142.0


def test_digest_markiert_ueberstimmte_uploader_bpm():
    t = _track(1)
    t.bpm = 155.0
    assert "⚠️ Uploader" not in _fmt_track(t)
    t.text_bpm = 142.0
    assert "155 BPM ⚠️ Uploader: 142" in _fmt_track(t)


# ======================================================================== Schritt 6: Tags
# Veröffentlichte Camelot-Belegung, unabhängig vom Code aufgeschrieben.
_WHEEL = {
    "G#m": "1A", "D#m": "2A", "A#m": "3A", "Fm": "4A", "Cm": "5A", "Gm": "6A",
    "Dm": "7A", "Am": "8A", "Em": "9A", "Bm": "10A", "F#m": "11A", "C#m": "12A",
    "B": "1B", "F#": "2B", "C#": "3B", "G#": "4B", "D#": "5B", "A#": "6B",
    "F": "7B", "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B",
}


def test_camelot_tabelle_stimmt_fuer_alle_24_tonarten():
    gefunden = {}
    for (pc, mode), code in A._CAMELOT.items():
        name = (A._KEY_NAMES_MAJOR if mode else A._KEY_NAMES_MINOR)[pc]
        gefunden[name] = code
    assert gefunden == _WHEEL


def test_a_dur_wird_als_11b_erkannt():
    # A-Dur-Dreiklang (A, C#, E) mit Kadenz-Tonika; vorher kam fälschlich "1B"
    sr = A.SR
    t = np.arange(int(sr * 12)) / sr
    y = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.18, 329.63, 440.0)).astype(np.float32)
    key = A._detect_key_from_audio(y / np.max(np.abs(y)))
    assert (key["key_name"], key["camelot"]) == ("A", "11B")


def _mp3(path: Path) -> Path:
    return _audio(path, "sine=frequency=440", "-c:a", "libmp3lame", "-b:a", "128k", duration=2)


def test_mp3_tkey_ist_camelot_klarname_in_initialkey(tmp_path):
    p = _mp3(tmp_path / "a.mp3")
    assert write_tags(p, artist="A", title="T", bpm=150.0, key_name="Cm", key_camelot="5A")
    from mutagen.id3 import ID3
    tags = ID3(str(p))
    assert tags["TKEY"].text[0] == "5A"
    assert tags["TXXX:INITIALKEY"].text[0] == "Cm"
    # erneutes Schreiben ersetzt, statt zu verdoppeln
    write_tags(p, artist="A", title="T", key_name="Dm", key_camelot="7A")
    tags = ID3(str(p))
    assert tags["TKEY"].text == ["7A"] and tags["TXXX:INITIALKEY"].text == ["Dm"]
    assert len(tags.getall("TXXX:INITIALKEY")) == 1


def test_ohne_camelot_bleibt_tkey_der_klarname(tmp_path):
    p = _mp3(tmp_path / "a.mp3")
    write_tags(p, artist="A", title="T", key_name="Cm")
    from mutagen.id3 import ID3
    tags = ID3(str(p))
    assert tags["TKEY"].text[0] == "Cm"
    assert "TXXX:INITIALKEY" not in tags


def test_text_bpm_wird_nur_mit_wert_geschrieben(tmp_path):
    p = _mp3(tmp_path / "a.mp3")
    write_tags(p, artist="A", title="T", bpm=155.0)
    from mutagen.id3 import ID3
    assert "TXXX:TEXTBPM" not in ID3(str(p))
    write_tags(p, artist="A", title="T", bpm=155.0, text_bpm=142.0)
    assert ID3(str(p))["TXXX:TEXTBPM"].text[0] == "142"


@pytest.mark.parametrize("suffix, args", [
    (".mp3", ("-c:a", "libmp3lame", "-b:a", "128k")),
    (".wav", ("-c:a", "pcm_s16le")),
    (".aiff", ("-c:a", "pcm_s16be")),
])
def test_id3_reserve_haelt_spaetere_tag_erweiterung_in_der_datei(tmp_path, suffix, args):
    """Mit padding_kb wächst die Datei nicht, wenn danach (wie Rekordbox) Tags dazukommen."""
    from mutagen.id3 import COMM, ID3
    from mutagen.aiff import AIFF
    from mutagen.wave import WAVE
    p = _audio(tmp_path / f"a{suffix}", "sine=frequency=440", *args, duration=2)
    write_tags(p, artist="A", title="T", bpm=150.0, key_name="Cm", key_camelot="5A", padding_kb=4)
    size = p.stat().st_size

    audio = {".mp3": ID3, ".wav": WAVE, ".aiff": AIFF}[suffix](str(p))
    tags = audio if suffix == ".mp3" else audio.tags
    tags.add(COMM(encoding=3, lang="eng", desc="rb", text=["x" * 2000]))
    audio.save()
    assert p.stat().st_size == size


def test_ohne_reserve_waechst_die_datei(tmp_path):
    from mutagen.id3 import COMM, ID3
    p = _mp3(tmp_path / "a.mp3")
    write_tags(p, artist="A", title="T", bpm=150.0)
    size = p.stat().st_size
    tags = ID3(str(p))
    tags.add(COMM(encoding=3, lang="eng", desc="rb", text=["x" * 2000]))
    tags.save(str(p))
    assert p.stat().st_size > size


def test_flac_initialkey_camelot_und_textbpm(tmp_path):
    from mutagen.flac import FLAC
    p = _audio(tmp_path / "a.flac", "sine=frequency=440", "-c:a", "flac", duration=2)
    assert write_tags(p, artist="A", title="T", bpm=155.0, key_name="Cm", key_camelot="5A", text_bpm=142.0)
    audio = FLAC(str(p))
    assert audio["initialkey"] == ["5A"]
    assert audio["textbpm"] == ["142"]


# ======================================================================== Schritt 6: WAV -> FLAC
def _wav(path: Path, duration: float = 3) -> Path:
    return _audio(path, "sine=frequency=440", "-c:a", "pcm_s24le", duration=duration)


def test_convert_wav_to_flac_ersetzt_wav_verlustfrei(tmp_path):
    wav = _wav(tmp_path / "t.wav")
    size = wav.stat().st_size
    out = convert_wav_to_flac(wav)
    assert out == tmp_path / "t.flac"
    assert out.exists() and not wav.exists()
    assert out.stat().st_size < size
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                           "stream=codec_name,bits_per_raw_sample:format=duration", "-of", "default=nw=1",
                           str(out)], capture_output=True, text=True, check=True).stdout
    assert "codec_name=flac" in info and "bits_per_raw_sample=24" in info
    assert float(info.split("duration=")[1].split()[0]) == pytest.approx(3.0, abs=0.1)
    assert not list(tmp_path.glob(".*"))                      # keine Temp-Reste


def test_convert_wav_to_flac_ueberschreibt_nichts(tmp_path):
    wav = _wav(tmp_path / "t.wav")
    (tmp_path / "t.flac").write_bytes(b"vorhanden")
    assert convert_wav_to_flac(wav) is None
    assert wav.exists() and (tmp_path / "t.flac").read_bytes() == b"vorhanden"


def test_convert_wav_to_flac_kaputte_datei_bleibt_liegen(tmp_path):
    kaputt = tmp_path / "t.wav"
    kaputt.write_bytes(b"RIFF....nicht wirklich")
    assert convert_wav_to_flac(kaputt) is None
    assert kaputt.read_bytes() == b"RIFF....nicht wirklich"
    assert not (tmp_path / "t.flac").exists() and not list(tmp_path.glob(".*"))


def test_convert_wav_to_flac_ignoriert_andere_endungen(tmp_path):
    mp3 = _mp3(tmp_path / "t.mp3")
    assert convert_wav_to_flac(mp3) is None and mp3.exists()


def test_finish_file_wandelt_wav_nach_flac_und_taggt_dort(tmp_path, monkeypatch):
    from mutagen.flac import FLAC
    cfg = _cfg(tmp_path, wav_to_flac=True)
    _stub_analysis(monkeypatch, bpm=155.0, bpm_confidence=0.8)
    inbox = tmp_path / "inbox"
    wav = _wav(inbox / "t.wav")
    t = _track(1)
    t.quality_report = {"ok": True, "ext": "wav", "codec": "pcm_s24le"}   # setzt finalize_quality (gestubbt) sonst

    out = m.finish_file(t, wav, inbox, cfg)

    assert out == inbox / "t.flac" and not wav.exists()
    audio = FLAC(str(out))
    assert audio["initialkey"] == ["6A"] and audio["bpm"] == ["155"]
    assert t.quality_report["ext"] == "flac" and t.quality_report["codec"] == "flac"
    assert any("nach FLAC" in n for n in t.notes)


def test_finish_file_laesst_wav_standardmaessig_unveraendert(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    assert cfg.raw["organize"].get("wav_to_flac") is not True        # Standard aus
    _stub_analysis(monkeypatch, bpm=155.0, bpm_confidence=0.8)
    inbox = tmp_path / "inbox"
    wav = _wav(inbox / "t.wav")
    out = m.finish_file(_track(1), wav, inbox, cfg)
    assert out == wav and wav.exists() and not (inbox / "t.flac").exists()


def test_finish_file_behaelt_wav_wenn_umwandlung_scheitert(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, wav_to_flac=True)
    _stub_analysis(monkeypatch, bpm=155.0, bpm_confidence=0.8)
    monkeypatch.setattr(m, "convert_wav_to_flac", lambda p: None)
    inbox = tmp_path / "inbox"
    wav = _wav(inbox / "t.wav")
    t = _track(1)
    out = m.finish_file(t, wav, inbox, cfg)
    assert out == wav and wav.exists()
    assert any("WAV→FLAC fehlgeschlagen" in n for n in t.notes)
