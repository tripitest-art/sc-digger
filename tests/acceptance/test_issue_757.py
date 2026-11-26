"""Akzeptanztests: Weck-Timer weckt nicht doppelt und schweigt im Cooldown."""
from __future__ import annotations

from sc_digger.db import TrackDB, TrackRecord, TrackStatus, enqueue_cue_job
from sc_digger.gpu_wake import run_wake

WAKE_URL = "http://wake.invalid:11434/api/show"
JETZT = 1_800_000_000.0


class Resp:
    def __init__(self, status_code=200):
        self.status_code = status_code


def _queue(tmp_path, cues=1):
    path = tmp_path / "tracks.sqlite"
    with TrackDB(path) as db:
        for i in range(cues):
            t = db.upsert_track(
                TrackRecord(path=f"/inbox/{i}.wav", mtime=1.0, size=1, status=TrackStatus.INBOX)
            )
            enqueue_cue_job(db, t)
    return path


def _zaehler(status=200):
    rufe, meldungen = [], []

    def post(url, **kw):
        rufe.append(url)
        return Resp(status)

    return rufe, meldungen, post


def _jetzt(zeit):
    return lambda: zeit


def test_cooldown_verhindert_zweiten_weckruf(tmp_path):
    rufe, _, post = _zaehler()
    state = tmp_path / "wake.json"
    assert run_wake(_queue(tmp_path), WAKE_URL, post=post, state_path=state,
                    now=_jetzt(JETZT)) == 0
    assert len(rufe) == 1
    assert run_wake(_queue(tmp_path), WAKE_URL, post=post, state_path=state,
                    now=_jetzt(JETZT + 300)) == 0
    assert len(rufe) == 1


def test_nach_cooldown_wird_wieder_geweckt(tmp_path):
    rufe, _, post = _zaehler()
    state = tmp_path / "wake.json"
    run_wake(_queue(tmp_path), WAKE_URL, post=post, state_path=state, now=_jetzt(JETZT))
    run_wake(_queue(tmp_path), WAKE_URL, post=post, state_path=state,
             now=_jetzt(JETZT + 3600))
    assert len(rufe) == 2


def test_ohne_zustandspfad_wird_immer_geweckt(tmp_path):
    rufe, _, post = _zaehler()
    run_wake(_queue(tmp_path), WAKE_URL, post=post, now=_jetzt(JETZT))
    run_wake(_queue(tmp_path), WAKE_URL, post=post, now=_jetzt(JETZT + 60))
    assert len(rufe) == 2


def test_keine_jobs_weckt_nicht_und_schreibt_nichts(tmp_path):
    rufe, _, post = _zaehler()
    state = tmp_path / "wake.json"
    assert run_wake(_queue(tmp_path, cues=0), WAKE_URL, post=post, state_path=state,
                    now=_jetzt(JETZT)) == 0
    assert rufe == []
    assert not state.exists()


def test_fehlversuch_meldet_einmal_und_bremst_den_naechsten_lauf(tmp_path):
    rufe, meldungen, post = _zaehler(status=503)
    state = tmp_path / "wake.json"
    assert run_wake(_queue(tmp_path), WAKE_URL, post=post, notify=meldungen.append,
                    state_path=state, now=_jetzt(JETZT)) == 1
    assert len(meldungen) == 1
    assert run_wake(_queue(tmp_path), WAKE_URL, post=post, notify=meldungen.append,
                    state_path=state, now=_jetzt(JETZT + 300)) == 0
    assert len(rufe) == 1
    assert len(meldungen) == 1


def test_kaputte_zustandsdatei_weckt_trotzdem(tmp_path):
    rufe, _, post = _zaehler()
    state = tmp_path / "wake.json"
    state.write_text("kein json")
    assert run_wake(_queue(tmp_path), WAKE_URL, post=post, state_path=state,
                    now=_jetzt(JETZT)) == 0
    assert len(rufe) == 1
