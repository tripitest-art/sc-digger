"""Tests for the new OAuth token patterns in redact function."""
import pytest

from sc_digger.redact import redact


def test_oauth_header_pattern():
    """Test that OAuth headers are masked correctly."""
    token = "2-293847-1234567-AbCdEfGhIjKlMn"
    
    # Test case sensitive
    text = f"Authorization: OAuth {token}"
    result = redact(text)
    assert result == "Authorization: OAuth ***"
    assert token not in result
    
    # Test case insensitive
    text = f"authorization: oauth {token}"
    result = redact(text)
    assert result == "authorization: oauth ***"
    assert token not in result


def test_oauth_url_param_pattern():
    """Test that oauth_token URL parameters are masked correctly."""
    token = "2-293847-1234567-AbCdEfGhIjKlMn"
    
    text = f"GET https://api-v2.soundcloud.com/me?oauth_token={token}&limit=5"
    result = redact(text)
    assert result == "GET https://api-v2.soundcloud.com/me?oauth_token=***&limit=5"
    assert token not in result


def test_auth_token_arg_pattern():
    """Test that --auth-token arguments are masked correctly."""
    token = "2-293847-1234567-AbCdEfGhIjKlMn"
    
    # Test with space
    text = f"scdl -l URL --auth-token {token} --only-original"
    result = redact(text)
    assert result == "scdl -l URL --auth-token *** --only-original"
    assert token not in result
    
    # Test with equals sign
    text = f"scdl -l URL --auth-token={token}"
    result = redact(text)
    assert result == "scdl -l URL --auth-token=***"
    assert token not in result


def test_multiple_tokens_in_text():
    """Test that multiple tokens in one text are all masked."""
    token1 = "2-293847-1234567-AbCdEfGhIjKlMn"
    token2 = "3-123456-7890123-XyZwVuTsRqPoNn"
    
    text = f"Authorization: OAuth {token1} and oauth_token={token2}"
    result = redact(text)
    assert result == "Authorization: OAuth *** and oauth_token=***"
    assert token1 not in result
    assert token2 not in result


def test_token_at_line_end():
    """Test that tokens at the end of a line are properly masked."""
    token = "2-293847-1234567-AbCdEfGhIjKlMn"
    
    text = f"oauth_token={token}"
    result = redact(text)
    assert result == "oauth_token=***"
    assert token not in result


def test_combined_with_env_token():
    """Test that the new patterns work together with environment variable tokens."""
    token = "2-293847-1234567-AbCdEfGhIjKlMn"
    
    # Set up environment variable
    import os
    old_token = os.environ.get("SOUNDCLOUD_AUTH_TOKEN")
    os.environ["SOUNDCLOUD_AUTH_TOKEN"] = token
    
    try:
        text = f"Authorization: OAuth {token} and scdl --auth-token={token}"
        result = redact(text)
        assert "OAuth ***" in result
        assert "--auth-token=***" in result
        # Token should not appear anywhere
        assert token not in result
    finally:
        # Restore original value
        if old_token is None:
            os.environ.pop("SOUNDCLOUD_AUTH_TOKEN", None)
        else:
            os.environ["SOUNDCLOUD_AUTH_TOKEN"] = old_token


def test_normal_words_unchanged():
    """Test that normal words are not affected by the patterns."""
    # Test with actual tokens - these should be replaced
    token = "2-293847-1234567-AbCdEfGhIjKlMn"  # This is a real token with >20 chars
    text_with_token = f"Authorization: OAuth {token}"
    result_with_token = redact(text_with_token)
    assert "OAuth ***" in result_with_token
    assert token not in result_with_token
    
    # Test that normal words are not affected - these should NOT match any patterns
    normal_cases = [
        "OAuth required",  # This is not a real token, so it shouldn't be replaced
        "Fehler: OAuth abgelaufen, bitte neu anmelden",  # Same here - no real token
        "oauth_token=",  # This is just the parameter name, not a value
        "--auth-token fehlt",  # This is just the parameter name, not a value
    ]
    
    for text in normal_cases:
        result = redact(text)
        # None of these should be replaced since they don't contain actual tokens
        assert text == result, f"Text '{text}' was incorrectly modified to '{result}'"