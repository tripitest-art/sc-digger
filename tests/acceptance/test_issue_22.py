"""Akzeptanztests: ZIP-Einträge aus Cloud-Downloads streamend entpacken (Speicher, keine Reste)."""
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sc_digger.cloud import download_cloud
from sc_digger.models import DownloadKind
from sc_digger.soundcloud import SoundCloudClient

DBX = "https://www.dropbox.com/s/abc123/EP.zip?dl=0"
DBX_DL = "https://www.dropbox.com/s/abc123/EP.zip?dl=1"
WAV = b"RIFF" + bytes(range(256)) * 40


def mk():
    t = SoundCloudClient._to_track(dict(
        id=1, kind="track", title="T", permalink_url="https://soundcloud.com/a/t1",
        user={"username": "A", "permalink_url": "https://soundcloud.com/a"},
        created_at=datetime.now(timezone.utc).isoformat(), playback_count=1, likes_count=1,
        reposts_count=0, comment_count=0, downloadable=False, tag_list="", description=DBX))
    t.download_kind, t.download_link = DownloadKind.CLOUD, DBX
    return t


class Resp:
    def __init__(self, body):
        self.body, self.status_code = body, 200
        self.url = "https://uc1.dl.dropboxusercontent.com/cd/0/get/x/file"
        self.headers = {"Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="EP.zip"'}

    def iter_content(self, chunk_size=1):
        yield self.body

    def close(self):
        pass


class Sess:
    def __init__(self, body):
        self.body = body

    def get(self, url, **kw):
        assert url == DBX_DL
        return Resp(self.body)


def zipped() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("EP/Track.wav", WAV)
    return buf.getvalue()


def files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_zip_member_is_not_read_into_memory(tmp_path, monkeypatch):
    def forbidden(self, *a, **k):
        raise AssertionError("ZipFile.read lädt den ganzen Eintrag in den Speicher")
    monkeypatch.setattr(zipfile.ZipFile, "read", forbidden)
    t = mk()
    p = download_cloud(t, tmp_path, session=Sess(zipped()))
    assert p == tmp_path / "Track.wav", t.download_error
    assert p.read_bytes() == WAV
    assert files(tmp_path) == ["Track.wav"]


def test_failure_while_extracting_leaves_no_partial_file(tmp_path, monkeypatch):
    orig = zipfile.ZipExtFile.read
    calls = {"n": 0}

    def flaky(self, n=-1):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("Datenträger voll")
        return orig(self, 16)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", flaky)
    t = mk()
    assert download_cloud(t, tmp_path, session=Sess(zipped())) is None
    assert t.download_error
    assert files(tmp_path) == []
