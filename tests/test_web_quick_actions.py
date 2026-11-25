"""Eigene Tests zu Issue #721: Aktionen und SoundCloud-Links in der Schnellsuche."""
from __future__ import annotations

import re

from sc_digger.web.navigation import SC_LINK_PATTERN, QuickAction, quick_actions
from sc_digger.web.tasks import action_labels


def test_aktionen_beginnen_mit_xml_und_workstation():
    a = quick_actions()
    assert a[0] == QuickAction("Rekordbox-XML neu schreiben", "/api/rekordbox/sync")
    assert a[1] == QuickAction("Workstation-Lauf starten", "/api/gpu-wake")
    assert len(a) == 2 + len(action_labels())


def test_alle_endpunkte_sind_eindeutig():
    endpoints = [a.endpoint for a in quick_actions()]
    assert len(endpoints) == len(set(endpoints))


def test_muster_nimmt_keine_fremden_hosts_an():
    treffer = re.compile(SC_LINK_PATTERN, re.IGNORECASE)
    assert not treffer.match("https://soundcloud.com.evil.example/a")
    assert not treffer.match("ftp://soundcloud.com/a/b")
    assert treffer.match("https://soundcloud.com/a/b")
