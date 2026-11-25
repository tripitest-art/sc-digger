"""Leerzustände nennen den nächsten Schritt (Issue 733), ergänzend zu den Akzeptanztests."""
from __future__ import annotations

from sc_digger.web.ops import OPS_SETTINGS_HREF, OpsItem


def test_settings_href_nur_fuer_bekannte_schluessel():
    assert OpsItem("canary", "x", "off", "t").settings_href == OPS_SETTINGS_HREF["canary"]
    assert OpsItem("unbekannt", "x", "off", "t").settings_href is None


def test_opsitem_bleibt_unveraenderlich():
    import dataclasses
    import pytest
    item = OpsItem("disk", "x", "ok", "t")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.state = "off"  # type: ignore[misc]
