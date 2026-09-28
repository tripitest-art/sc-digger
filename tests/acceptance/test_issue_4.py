"""Akzeptanztests Issue #4: Cloud-Links (Dropbox, Google Drive) laden, sicher und nie still."""
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
import yaml

from sc_digger import main as m
from sc_digger.cloud import direct_url, download_cloud
from sc_digger.models import Config, DownloadKind
from sc_digger.output import build_digest
from sc_digger.soundcloud import SoundCloudClient

ROOT = Path(__file__).resolve().parents[2]
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
        for i in range(0, len(self.body), 7):
            yield self.body[i:i + 7]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

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


def audio_in(inbox: Path) -> list[str]:
    exts = {".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a"}
    return sorted(str(p.relative_to(inbox)) for p in inbox.rglob("*") if p.is_file() and p.suffix.lower() in exts)


# ---------------- Links umschreiben ----------------
DRIVE = "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOp&export=download&confirm=t"


@pytest.mark.parametrize("link, expected", [
    (DBX, DBX_DL),
    ("https://www.dropbox.com/s/abc123/Track.wav", DBX_DL),
    ("http://dropbox.com/s/abc123/Track.wav?dl=0", "https://dropbox.com/s/abc123/Track.wav?dl=1"),
    ("https://www.dropbox.com/scl/fi/xyz/Track.wav?rlkey=k9&dl=0",
     "https://www.dropbox.com/scl/fi/xyz/Track.wav?rlkey=k9&dl=1"),
    ("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing", DRIVE),
    ("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp", DRIVE),
    ("https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOp&export=download", DRIVE),
    ("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp", None),
    ("https://mega.nz/file/abc#key", None),
    ("https://wetransfer.com/downloads/abc", None),
    ("https://evil.example/dropbox.com/s/abc/Track.wav", None),
])
def test_direct_url(link, expected):
    assert direct_url(link) == expected


# ---------------- Einzeldatei ----------------
def test_downloads_file_into_inbox(tmp_path):
    sess = FakeSession({DBX_DL: FakeResponse(WAV, headers=attach("Artist - Track.wav"))})
    t = mk(DBX)
    p = download_cloud(t, tmp_path, session=sess)
    assert p == tmp_path / "Artist - Track.wav" and p.read_bytes() == WAV
    assert t.download_error is None
    assert audio_in(tmp_path) == ["Artist - Track.wav"]
    assert [x.name for x in tmp_path.iterdir()] == ["Artist - Track.wav"]   # kein Temp-Ordner übrig
    assert sess.calls[0][1].get("stream") is True and sess.calls[0][1].get("timeout")


def test_utf8_filename_from_header(tmp_path):
    h = {"Content-Disposition": "attachment; filename=\"x.wav\"; filename*=UTF-8''N%C3%98SS%20-%20Join%20me.wav"}
    p = download_cloud(mk(DBX), tmp_path, session=FakeSession({DBX_DL: FakeResponse(WAV, headers=h)}))
    assert p.name == "NØSS - Join me.wav"


def test_filename_cannot_escape_inbox(tmp_path):
    inbox = tmp_path / "inbox"
    sess = FakeSession({DBX_DL: FakeResponse(WAV, headers=attach("../../evil.wav"))})
    p = download_cloud(mk(DBX), inbox, session=sess)
    assert p == inbox / "evil.wav"
    assert not (tmp_path / "evil.wav").exists()


def test_existing_file_is_not_overwritten(tmp_path):
    (tmp_path / "Track.wav").write_bytes(b"alt")
    p = download_cloud(mk(DBX), tmp_path, session=FakeSession({DBX_DL: FakeResponse(WAV, headers=attach("Track.wav"))}))
    assert p == tmp_path / "Track_1.wav"
    assert (tmp_path / "Track.wav").read_bytes() == b"alt"


# ---------------- ZIP ----------------
def test_zip_keeps_one_lossless_file(tmp_path):
    body = zipped({"EP/Track.mp3": b"ID3" + b"\x00" * 90, "EP/Track.wav": WAV,
                   "__MACOSX/EP/._Track.wav": b"junk", "EP/readme.txt": b"hi"})
    t = mk(DBX)
    p = download_cloud(t, tmp_path, session=FakeSession({DBX_DL: FakeResponse(body, headers=attach("EP.zip"))}))
    assert p == tmp_path / "Track.wav" and p.read_bytes() == WAV
    assert audio_in(tmp_path) == ["Track.wav"]
    assert not list(tmp_path.glob("*.zip"))


def test_zip_member_cannot_escape_inbox(tmp_path):
    inbox = tmp_path / "inbox"
    body = zipped({"../../evil.wav": WAV})
    p = download_cloud(mk(DBX), inbox, session=FakeSession({DBX_DL: FakeResponse(body, headers=attach("x.zip"))}))
    assert p == inbox / "evil.wav"
    assert not (tmp_path / "evil.wav").exists()


def test_zip_without_audio_fails_visibly(tmp_path):
    body = zipped({"cover.jpg": b"\xff\xd8", "readme.txt": b"hi"})
    t = mk(DBX)
    assert download_cloud(t, tmp_path, session=FakeSession({DBX_DL: FakeResponse(body, headers=attach("x.zip"))})) is None
    assert t.download_error and "Audio" in t.download_error
    assert list(tmp_path.iterdir()) == []


# ---------------- Fehlerfälle: nie still, nie Reste ----------------
def _fails(tmp_path, resp_or_exc, link=DBX, routes=None):
    t = mk(link)
    sess = FakeSession(routes or {direct_url(link): resp_or_exc})
    assert download_cloud(t, tmp_path, max_mb=1, session=sess) is None
    assert t.download_error, "Fehler muss am Track stehen"
    assert audio_in(tmp_path) == [] and not [p for p in tmp_path.rglob("*") if p.is_file()]
    return t


def test_html_page_instead_of_file(tmp_path):
    t = _fails(tmp_path, FakeResponse(b"<html>quota</html>", headers={"Content-Type": "text/html; charset=utf-8"}))
    assert "Webseite" in t.download_error


def test_too_large_by_header(tmp_path):
    t = _fails(tmp_path, FakeResponse(WAV, headers={**attach("a.wav"), "Content-Length": str(2 * 1024 * 1024)}))
    assert "MB" in t.download_error


def test_too_large_while_streaming(tmp_path):
    t = _fails(tmp_path, FakeResponse(b"\x00" * (1024 * 1024 + 50), headers=attach("a.wav")))
    assert "MB" in t.download_error


def test_http_error(tmp_path):
    t = _fails(tmp_path, FakeResponse(b"", status=404))
    assert "404" in t.download_error


def test_redirect_to_foreign_host_is_refused(tmp_path):
    _fails(tmp_path, FakeResponse(WAV, headers=attach("a.wav"), url="https://evil.example/a.wav"))


def test_network_error_does_not_raise(tmp_path):
    _fails(tmp_path, requests.ConnectionError("weg"))


def test_not_audio_file(tmp_path):
    t = _fails(tmp_path, FakeResponse(b"%PDF-1.4", headers=attach("flyer.pdf")))
    assert "Audio" in t.download_error


def test_unsupported_link_needs_no_request(tmp_path):
    t = mk("https://mega.nz/file/abc#key")
    sess = FakeSession({})
    assert download_cloud(t, tmp_path, session=sess) is None
    assert sess.calls == [] and "manuell" in t.download_error


# ---------------- Sichtbarkeit und Einbindung ----------------
def test_digest_shows_download_error():
    t = mk(DBX)
    t.download_error = "Cloud-Download fehlgeschlagen: HTTP 404"
    assert "⚠️ Cloud-Download fehlgeschlagen: HTTP 404" in build_digest([t], None, header="T")[0]


def test_config_defaults():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["download"]["cloud_max_mb"] == 500
    assert raw["download"]["auto_download_native_only"] is True   # Stephan schaltet Cloud bewusst ein


def _cfg(tmp_path, native_only: bool) -> Config:
    c = Config.load(ROOT / "config.yaml")
    c.raw["download"] = {**c["download"], "collection_dir": str(tmp_path / "coll"),
                         "inbox_dir": str(tmp_path / "inbox"), "auto_download_native_only": native_only,
                         "cloud_max_mb": 123}
    return c


def test_process_uses_cloud_only_when_enabled(tmp_path, monkeypatch):
    calls, checked = [], []
    monkeypatch.setattr(m, "download_cloud", lambda t, inbox, **kw: calls.append((t.id, inbox, kw)) or (inbox / "x.wav"))
    monkeypatch.setattr(m, "finalize_quality", lambda t, path, inbox, cfg: checked.append(path))

    m.process([mk(DBX)], _cfg(tmp_path, native_only=True), dry_run=False)
    assert calls == [] and checked == []

    m.process([mk(DBX)], _cfg(tmp_path, native_only=False), dry_run=False)
    assert calls == [(1, tmp_path / "inbox", {"max_mb": 123})]
    assert checked == [tmp_path / "inbox" / "x.wav"]
