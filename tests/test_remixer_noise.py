"""Eigene Tests zu ``REMIX_NOISE`` (Issue #302: Werbe-Zusätze sind kein Remixer)."""
from sc_digger.collection import REMIX_NOISE, remixer_tokens


def test_remix_noise_ist_frozenset():
    assert isinstance(REMIX_NOISE, frozenset)
    assert {"exclu", "premiere", "out", "now"} <= REMIX_NOISE


def test_rauschklammer_mit_echtem_wort_bleibt_vollstaendig():
    # Ein einziges Nicht-Rauschwort genügt: die Klammer wird wie bisher ganz übernommen.
    assert remixer_tokens("Hit (Final Round Edit)") == {"final", "round"}


def test_mehrere_klammern_liefern_nur_den_remixer():
    title = "Track (EXCLU) (Might Be Twins Remix) [Out Now]"
    assert remixer_tokens(title) == {"might", "be", "twins"}


def test_leere_klammer_wirft_nicht():
    assert remixer_tokens("Track ()") == set()
    assert remixer_tokens("Track () [ ] (EXCLU)") == set()
    assert remixer_tokens("Track") == set()


def test_rauschwoerter_werden_nicht_uebernommen():
    for title in (
        "Track (EXCLU)",
        "Track (FINAL MASTER)",  # "master" ist Stoppwort, übrig bleibt nur "final"
        "Track [Premiere]",
        "Track (Out Now)",
        "Track (Exclusive Preview)",
        "Track [Release Snippet]",
    ):
        assert remixer_tokens(title) == set(), title
