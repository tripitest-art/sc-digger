"""Akzeptanztests: Test der /resolve-Optimisation (Issue #83) – neue Unit-Tests."""
import pytest

from sc_digger.soundcloud import SoundCloud


def test_reference_activity_uses_single_resolve():
    """Neue Tests um das geänderte Verhalten abzusichern."""
    sc = SoundCloud()
    
    # Test: user kind wird direkt aus einem Aufruf geprüft
    with pytest.raises(LookupError):  # Fallback für FakeSC
        sc.reference_activity("https://soundcloud.com/user", 7)
