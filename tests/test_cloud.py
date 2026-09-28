"""Unit-Tests für sc_digger/cloud.py."""
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sc_digger.cloud import direct_url, download_cloud
from sc_digger.models import DownloadKind
from sc_digger.soundcloud import SoundCloudClient

NOW = datetime.now(timezone.utc).isoformat()
DBX = "https://www.dropbox.com/s/abc123/Track.wav?dl=0"
DBX_DL = "https://www.dropbox.com/s/abc123/Track.wav?dl=1"
DBX_FINAL = "https://uc123.dl.dropboxusercontent.com/cd/0/get/xyz/file"
WAV = b"RIFF" + b"\x00" * 60


def mk(link: str, i: int = 1):
    t = SoundCloudClient._to_track(dict(
        id=i, kind="track", title=f"Track {i}", permalink_url=f"https://soundcloud.com/a/t{i}",
        user={"username": "Artist", "permalink_url": "https://soundcloud.com/a"},
        created_at=NOW, playback_count=1000, likes_count=50, reposts_count=5, comment_count=1,
        downloadable=False, tag_list="", description=f"Free DL: {link}"))
    t.download_kind, t.download_link = DownloadKind.CLOUD, link
    return t


class FakeResponse:
    def __init__(self, body=b"", *, status=200, headers=None, url=DBX_FINAL):
        self.body, self.status_code, self.url = body, status, url
        self.headers = {"Content-Type": "application/octet-stream", **(headers or {})}
        self.closed = False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.body), 64):
            yield self.body[i:i + 64]

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        r = self.routes[url]
        if isinstance(r, Exception):
            raise r
        return r


def attach(name: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


def zipped(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return buf.getvalue()


# ---------------- Eigene Tests nach Issue #4 ----------------

def test_filename_without_content_disposition_from_url_path(tmp_path):
    """Dateiname ohne Content-Disposition wird aus dem URL-Pfad extrahiert."""
    # Keine Content-Disposition, URL hat Endung .wav
    final_url = "https://uc123.dl.dropboxusercontent.com/cd/0/get/xyz/Custom%20Track%20Name.wav"
    sess = FakeSession({DBX_DL: FakeResponse(WAV, headers={}, url=final_url)})
    t = mk(DBX)
    p = download_cloud(t, tmp_path, session=sess)
    assert p == tmp_path / "Custom Track Name.wav"
    assert p.read_bytes() == WAV
    assert t.download_error is None


def test_filename_without_content_disposition_from_orig_url_fallback(tmp_path):
    """Wenn resp.url nur ein generisches /file ist, nimmt download_cloud den Pfad der Download-URL."""
    sess = FakeSession({DBX_DL: FakeResponse(WAV, headers={}, url="https://uc123.dl.dropboxusercontent.com/cd/0/get/xyz/file")})
    t = mk(DBX)
    p = download_cloud(t, tmp_path, session=sess)
    assert p == tmp_path / "Track.wav"
    assert p.read_bytes() == WAV
    assert t.download_error is None


def test_zip_prefers_m4a_over_mp3(tmp_path):
    """ZIP mit nur .mp3 und .m4a wählt .m4a, selbst wenn das MP3 größer ist."""
    mp3_data = b"ID3" + b"\x00" * 2000
    m4a_data = b"ftypM4A " + b"\x00" * 500
    body = zipped({"Release/Audio.mp3": mp3_data, "Release/Audio.m4a": m4a_data})
    t = mk(DBX)
    sess = FakeSession({DBX_DL: FakeResponse(body, headers=attach("Release.zip"))})
    p = download_cloud(t, tmp_path, session=sess)
    assert p == tmp_path / "Audio.m4a"
    assert p.read_bytes() == m4a_data
    assert any("Audio.m4a" in n for n in t.notes)
    assert t.download_error is None


def test_download_error_remains_none_on_success(tmp_path):
    """Bei erfolgreichem Download bleibt download_error None."""
    sess = FakeSession({DBX_DL: FakeResponse(WAV, headers=attach("Valid.wav"))})
    t = mk(DBX)
    t.download_error = "Alter Fehler"
    p = download_cloud(t, tmp_path, session=sess)
    assert p == tmp_path / "Valid.wav"
    assert t.download_error is None


def test_direct_url_invalid_or_empty():
    """direct_url liefert None bei leeren oder ungültigen Eingaben."""
    assert direct_url("") is None
    assert direct_url("   ") is None
    assert direct_url("ftp://dropbox.com/file.wav") is None
    assert direct_url("not a url") is None
    assert direct_url("https://") is None


def test_zip_with_wav_and_mp3_prefers_wav_byte_identical(tmp_path):
    """ZIP mit WAV und MP3 liefert die verlustfreie WAV, byte-identisch."""
    wav_data = b"RIFF" + bytes(range(256)) * 8
    mp3_data = b"ID3" + b"\x00" * 5000
    body = zipped({"EP/Track.mp3": mp3_data, "EP/Track.wav": wav_data})
    t = mk(DBX)
    sess = FakeSession({DBX_DL: FakeResponse(body, headers=attach("EP.zip"))})
    p = download_cloud(t, tmp_path, session=sess)
    assert p == tmp_path / "Track.wav"
    assert p.read_bytes() == wav_data
    assert any("Track.wav" in n for n in t.notes)
    assert t.download_error is None

