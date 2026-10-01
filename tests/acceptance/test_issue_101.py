"""Akzeptanztests: SoundCloud-OAuth-Token auch ohne Umgebungsvariable aus Texten entfernen."""
import pytest

from sc_digger.redact import redact

TOKEN = "2-293847-1234567-AbCdEfGhIjKlMn"


@pytest.fixture(autouse=True)
def _no_env_tokens(monkeypatch):
    # Die Muster sollen auch greifen, wenn der Token nicht in der Umgebung steht.
    monkeypatch.delenv("SOUNDCLOUD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)


@pytest.mark.parametrize("text, expected", [
    (f"Authorization: OAuth {TOKEN}", "Authorization: OAuth ***"),
    (f"authorization: oauth {TOKEN}", "authorization: oauth ***"),
    (f"GET https://api-v2.soundcloud.com/me?oauth_token={TOKEN}&limit=5",
     "GET https://api-v2.soundcloud.com/me?oauth_token=***&limit=5"),
    (f"scdl -l URL --auth-token {TOKEN} --only-original", "scdl -l URL --auth-token *** --only-original"),
    (f"scdl -l URL --auth-token={TOKEN}", "scdl -l URL --auth-token=***"),
])
def test_soundcloud_token_patterns_are_masked(text, expected):
    assert redact(text) == expected
    assert TOKEN not in redact(text)


@pytest.mark.parametrize("text", [
    "OAuth required",
    "Fehler: OAuth abgelaufen, bitte neu anmelden",
    "oauth_token=",
    "--auth-token fehlt",
])
def test_normal_words_stay_unchanged(text):
    assert redact(text) == text


def test_existing_telegram_redaction_still_works():
    assert redact("https://api.telegram.org/bot123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabc/sendMessage") == \
        "https://api.telegram.org/bot***/sendMessage"