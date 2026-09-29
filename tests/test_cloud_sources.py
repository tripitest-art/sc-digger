"""Eigene Tests für WeTransfer-, Mega- und Bandcamp-Klassifizierung sowie Digest-Ausgabe."""
import pytest
from sc_digger import output as out
from sc_digger.models import DownloadKind, Track
from sc_digger.pipeline import (
    DOWNLOAD_PRIORITY,
    classify_download,
    classify_url,
)


def _make_track(**kwargs) -> Track:
    defaults = dict(
        id=1,
        title="Test Track",
        url="https://soundcloud.com/artist/test-track",
        artist="Test Artist",
        artist_url="https://soundcloud.com/artist",
        created_at="2026-01-01T00:00:00Z",
        duration_ms=240000,
        genre="Schranz",
        tags=["schranz"],
        description="",
        bpm=155.0,
        plays=5000,
        likes=250,
        reposts=50,
        comments=10,
        downloadable=False,
        has_downloads_left=False,
        purchase_url=None,
        purchase_title=None,
    )
    defaults.update(kwargs)
    return Track(**defaults)


# ================================================================= classify_url tests

@pytest.mark.parametrize("url,expected_kind", [
    ("https://we.tl/t-12345678", DownloadKind.WETRANSFER),
    ("http://we.tl/t-abcdef", DownloadKind.WETRANSFER),
    ("https://www.wetransfer.com/downloads/abcdef123456/7890", DownloadKind.WETRANSFER),
    ("https://mega.co.nz/#!abcd1234!keykey", DownloadKind.MEGA),
    ("https://mega.co.nz/file/ABCDEF#secret", DownloadKind.MEGA),
    ("https://mega.nz/folder/XYZ#folderkey", DownloadKind.MEGA),
    ("https://mega.io/file/1234#key", DownloadKind.MEGA),
    ("https://subdomain.bandcamp.com/album/lp", DownloadKind.STORE),
    ("https://bandcamp.com/track/solo", DownloadKind.STORE),
    ("https://beatport.com/track/foo/123", DownloadKind.STORE),
    ("https://drive.google.com/file/d/abc/view", DownloadKind.CLOUD),
    ("https://dropbox.com/s/xyz/tune.wav?dl=1", DownloadKind.CLOUD),
    ("https://hypeddit.com/track/1234", DownloadKind.HYPEDDIT),
    ("https://droploud.com/t/5678", DownloadKind.DROPLOUD),
    ("https://random-blog.com/post/1", DownloadKind.NONE),
])
def test_classify_url_sources(url, expected_kind):
    kind, returned_url = classify_url(url)
    assert kind == expected_kind
    assert returned_url == url


def test_classify_download_with_string_url():
    """classify_download akzeptiert auch URL-Strings direkt."""
    kind, link = classify_download("https://we.tl/t-testlink")
    assert kind == DownloadKind.WETRANSFER
    assert link == "https://we.tl/t-testlink"

    kind, link = classify_download("https://mega.co.nz/file/key123")
    assert kind == DownloadKind.MEGA
    assert link == "https://mega.co.nz/file/key123"


# ================================================================= Track classification tests

def test_track_wetransfer_in_description():
    t = _make_track(description="Free download via WeTransfer: https://we.tl/t-abcdef here!")
    classified = classify_download(t)
    assert classified.download_kind == DownloadKind.WETRANSFER
    assert classified.download_link == "https://we.tl/t-abcdef"


def test_track_mega_in_description():
    t = _make_track(description="DL link: https://mega.co.nz/file/test#key enjoy")
    classified = classify_download(t)
    assert classified.download_kind == DownloadKind.MEGA
    assert classified.download_link == "https://mega.co.nz/file/test#key"


def test_track_native_beats_wetransfer_and_mega():
    t = _make_track(
        downloadable=True,
        has_downloads_left=True,
        description="Also available on https://we.tl/t-abc and https://mega.nz/file/xyz",
    )
    classified = classify_download(t)
    assert classified.download_kind == DownloadKind.NATIVE
    assert classified.download_link == t.url


def test_track_gate_beats_wetransfer():
    t = _make_track(
        description="Gate: https://hypeddit.com/gate123 Mirror: https://we.tl/t-xyz"
    )
    classified = classify_download(t)
    assert classified.download_kind == DownloadKind.HYPEDDIT
    assert classified.download_link == "https://hypeddit.com/gate123"


def test_track_cloud_beats_wetransfer_and_mega():
    t = _make_track(
        purchase_url="https://drive.google.com/file/d/abc",
        description="Mirror https://we.tl/t-123 https://mega.nz/file/456",
    )
    classified = classify_download(t)
    assert classified.download_kind == DownloadKind.CLOUD
    assert classified.download_link == "https://drive.google.com/file/d/abc"


def test_track_store_beats_wetransfer():
    t = _make_track(
        purchase_url="https://artist.bandcamp.com/track/tune",
        description="Mirror https://we.tl/t-abc",
    )
    classified = classify_download(t)
    assert classified.download_kind == DownloadKind.STORE
    assert classified.download_link == "https://artist.bandcamp.com/track/tune"


def test_download_priority_ordering():
    assert DOWNLOAD_PRIORITY.index(DownloadKind.NATIVE) < DOWNLOAD_PRIORITY.index(DownloadKind.CLOUD)
    assert DOWNLOAD_PRIORITY.index(DownloadKind.CLOUD) < DOWNLOAD_PRIORITY.index(DownloadKind.STORE)
    assert DOWNLOAD_PRIORITY.index(DownloadKind.STORE) < DOWNLOAD_PRIORITY.index(DownloadKind.WETRANSFER)
    assert DOWNLOAD_PRIORITY.index(DownloadKind.WETRANSFER) < DOWNLOAD_PRIORITY.index(DownloadKind.MEGA)
    assert DOWNLOAD_PRIORITY.index(DownloadKind.MEGA) < DOWNLOAD_PRIORITY.index(DownloadKind.HYPEDDIT)


# ================================================================= Digest and Export formatting

def test_wetransfer_and_mega_digest_and_export():
    t_we = _make_track(
        id=10,
        title="WeTransfer Tune",
        download_kind=DownloadKind.WETRANSFER,
        download_link="https://we.tl/t-12345",
    )
    t_mega = _make_track(
        id=20,
        title="Mega Tune",
        download_kind=DownloadKind.MEGA,
        download_link="https://mega.co.nz/file/ABC",
    )

    messages = out.build_digest_messages([t_we, t_mega], max_items=None)
    full_text = "".join(m.text for m in messages)

    assert "⏳ <a href=\"https://we.tl/t-12345\">WeTransfer (läuft ab)</a>" in full_text
    assert "🔒 <a href=\"https://mega.co.nz/file/ABC\">Mega (manuell)</a>" in full_text
    assert "🛒 Store / Cloud-Link" in full_text

    # Export-Datei darf nicht abstürzen und muss Links enthalten
    export_txt = out.build_export_txt([t_we, t_mega])
    assert "https://we.tl/t-12345" in export_txt
    assert "https://mega.co.nz/file/ABC" in export_txt
