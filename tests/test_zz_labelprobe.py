"""Wegwerf-Test: hält den Test-PR unmergebar.

Der PR dient nur der Prüfung, ob der PR-Watch ein fehlendes Worker-Label
automatisch nachzieht. Das Gate mergt freigegebene PRs selbst — ein bewusst
roter Check verhindert, dass dieser Test-PR auf `main` landet.
"""


def test_absichtlich_rot_als_merge_schutz():
    assert False, "Test-PR zur Label-Prüfung: absichtlich rot, damit nichts gemergt wird"
