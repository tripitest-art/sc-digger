"""Eigene Tests für VBR-MP3-Erkennung und -Bewertung (Issue #39)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from sc_digger.quality import is_vbr_mp3


def _mp3(path: Path, *args: str) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=d=2:c=pink:r=44100:a=0.3",
            "-ac",
            "2",
            "-c:a",
            "libmp3lame",
            *args,
            str(path),
        ],
        check=True,
    )
    return path


def test_is_vbr_mp3_skips_id3v2_footer(tmp_path):
    """ID3v2-Tag mit Footer-Flag (0x10 in Byte 5) wird korrekt inklusive 10-Byte-Footer übersprungen."""
    p_with_vbr = tmp_path / "footer_vbr.mp3"
    hdr = b"ID3\x04\x00\x10\x00\x00\x00\x20"  # 10 Bytes Header, Footer-Flag 0x10 gesetzt, Tag-Größe 32 Bytes
    body = b"A" * 32  # 32 Bytes Tag-Inhalt
    footer = b"3DI\x04\x00\x10\x00\x00\x00\x20"  # 10 Bytes Footer
    audio = b"\xff\xfb\x90\x64" + b"\x00" * 32 + b"Xing" + b"\x00" * 100
    p_with_vbr.write_bytes(hdr + body + footer + audio)
    assert is_vbr_mp3(p_with_vbr) is True

    # Testet, dass Xing innerhalb des Footers nicht fälschlicherweise als Audio-Frame gewertet wird
    p_footer_xing = tmp_path / "footer_fake_xing.mp3"
    footer_with_xing = b"3DIXing123"
    audio_cbr = b"\xff\xfb\x90\x64" + b"\x00" * 32 + b"Info" + b"\x00" * 100
    p_footer_xing.write_bytes(hdr + body + footer_with_xing + audio_cbr)
    assert is_vbr_mp3(p_footer_xing) is False


def test_is_vbr_mp3_detects_vbri_header(tmp_path):
    """Datei mit VBRI statt Xing im ersten Frame (synthetisch: Bytes nach Frame-Kopf ersetzen) gilt als VBR."""
    cbr_path = _mp3(tmp_path / "cbr.mp3", "-b:a", "320k")
    cbr_bytes = cbr_path.read_bytes()
    assert b"Info" in cbr_bytes
    assert is_vbr_mp3(cbr_path) is False

    # Ersetze den LAME CBR 'Info'-Header durch Fraunhofer 'VBRI'
    vbri_bytes = cbr_bytes.replace(b"Info", b"VBRI", 1)
    vbri_path = tmp_path / "vbri.mp3"
    vbri_path.write_bytes(vbri_bytes)

    assert is_vbr_mp3(vbri_path) is True
