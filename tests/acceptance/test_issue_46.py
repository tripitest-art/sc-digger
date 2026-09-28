"""Akzeptanztests: WeTransfer und Mega erkennen, Bandcamp als STORE."""
import pytest
from sc_digger.models import DownloadKind
from sc_digger.pipeline import classify_download   # oder äquivalente Funktion


@pytest.mark.parametrize("url,expected_kind", [
    ("https://we.tl/t-ABC123",                         DownloadKind.WETRANSFER),
    ("https://wetransfer.com/downloads/abc/def",       DownloadKind.WETRANSFER),
    ("https://mega.nz/file/ABCD#key",                  DownloadKind.MEGA),
    ("https://mega.co.nz/file/ABCD#key",               DownloadKind.MEGA),
    ("https://someartist.bandcamp.com/track/tune",     DownloadKind.STORE),
    ("https://bandcamp.com/track/tune",                DownloadKind.STORE),
    ("https://dropbox.com/s/abc/tune.wav?dl=0",        DownloadKind.CLOUD),  # unverändert
    ("https://hypeddit.com/track/xyz",                 DownloadKind.HYPEDDIT),  # unverändert
])
def test_classify_url(url, expected_kind):
    kind, link = classify_download(url)
    assert kind == expected_kind


def test_wetransfer_digest_label():
    from sc_digger import output as out
    from sc_digger.models import Track
    t = Track(
        id=1, title="Tune", url="https://soundcloud.com/a/tune", artist="Artist",
        artist_url="", created_at="2026-01-01T00:00:00Z", duration_ms=300000,
        genre="", tags=[], description="", bpm=None, plays=1000, likes=50,
        reposts=10, comments=2, downloadable=False, has_downloads_left=False,
        purchase_url=None, purchase_title=None,
        download_kind=DownloadKind.WETRANSFER,
        download_link="https://we.tl/t-ABC123",
    )
    text = "".join(m.text for m in out.build_digest_messages([t], None))
    assert "⏳" in text or "WeTransfer" in text
    assert "läuft ab" in text or "manuell" in text


def test_mega_digest_label():
    from sc_digger import output as out
    from sc_digger.models import Track
    t = Track(
        id=1, title="Tune", url="https://soundcloud.com/a/tune", artist="Artist",
        artist_url="", created_at="2026-01-01T00:00:00Z", duration_ms=300000,
        genre="", tags=[], description="", bpm=None, plays=1000, likes=50,
        reposts=10, comments=2, downloadable=False, has_downloads_left=False,
        purchase_url=None, purchase_title=None,
        download_kind=DownloadKind.MEGA,
        download_link="https://mega.nz/file/ABCD#key",
    )
    text = "".join(m.text for m in out.build_digest_messages([t], None))
    assert "🔒" in text or "Mega" in text


def test_bandcamp_classified_as_store():
    kind, _ = classify_download("https://someartist.bandcamp.com/track/tune")
    assert kind == DownloadKind.STORE


def test_native_takes_priority_over_wetransfer():
    """Ein Track mit native-Download UND WeTransfer-Link bleibt NATIVE."""
    # classify_download wird pro Link aufgerufen; Priorität liegt in der Pipeline
    # Dieser Test prüft nur, dass NATIVE > WETRANSFER in der Prioritätsliste steht.
    from sc_digger.pipeline import DOWNLOAD_PRIORITY   # oder äquivalente Konstante
    assert DOWNLOAD_PRIORITY.index(DownloadKind.NATIVE) < DOWNLOAD_PRIORITY.index(DownloadKind.WETRANSFER)
