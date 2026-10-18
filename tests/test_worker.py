"""Tests für den GPU-Worker-Client (Issue #332)."""
from __future__ import annotations

import requests

from sc_digger import worker
from sc_digger.worker import JobWorkerClient, execute_job, run_worker_loop


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {"ok": True}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Fehler")

    def json(self) -> dict:
        return self.payload


class FakeSession:
    """Ersetzt requests.Session; zeichnet Aufrufe auf und liefert feste Antworten."""

    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None):
        self.auth = None
        self.response = response or FakeResponse()
        self.error = error
        self.calls: list[dict] = []

    def post(self, url: str, json: dict | None = None, timeout: float | None = None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


def make_client(session: FakeSession) -> JobWorkerClient:
    client = JobWorkerClient("http://fake-server:8080/", password="secret", worker_id="w1")
    client._session = session
    return client


class FakeStopEvent:
    """threading.Event-Ersatz, der nach N Wartezeiten von selbst stoppt."""

    def __init__(self, stop_after: int = 1):
        self.waits: list[float] = []
        self.stop_after = stop_after
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        if len(self.waits) >= self.stop_after:
            self._set = True
        return self._set


class LoopClient:
    """Minimaler Client-Ersatz für die Schleifentests."""

    def __init__(self, jobs: list[dict] | None = None, claim_error: Exception | None = None):
        self.jobs = list(jobs or [])
        self.claim_error = claim_error
        self.claims = 0
        self.heartbeats: list[int] = []
        self.completed: dict[int, dict] = {}
        self.complete_ok = True

    def claim_job(self, job_type: str | None = None) -> dict | None:
        self.claims += 1
        if self.claim_error is not None:
            raise self.claim_error
        return self.jobs.pop(0) if self.jobs else None

    def heartbeat(self, job_id: int) -> bool:
        self.heartbeats.append(job_id)
        return True

    def complete_job(self, job_id, status, result=None, error=None) -> bool:
        if not self.complete_ok:
            return False
        self.completed[job_id] = {"status": status, "result": result, "error": error}
        return True


# --- JobWorkerClient -------------------------------------------------------------


def test_client_uses_basic_auth_with_worker_id():
    client = JobWorkerClient("http://server:8080", password="geheim", worker_id="gpu-1")
    assert client.api_url == "http://server:8080"
    assert client._session.auth.username == "gpu-1"
    assert client._session.auth.password == "geheim"


def test_client_rejects_empty_api_url():
    try:
        JobWorkerClient("", password="geheim")
    except ValueError as exc:
        assert "api_url" in str(exc)
    else:  # pragma: no cover - Guard gegen stilles Durchrutschen
        raise AssertionError("ValueError erwartet")


def test_claim_job_posts_job_type_and_returns_job():
    job = {"id": 7, "job_type": "embedding", "payload": {"track_path": "a.wav"}}
    session = FakeSession(FakeResponse(200, {"ok": True, "job": job}))
    client = make_client(session)

    assert client.claim_job(job_type="embedding") == job
    assert session.calls == [
        {"url": "http://fake-server:8080/api/jobs/claim", "json": {"job_type": "embedding"}, "timeout": 10.0}
    ]


def test_claim_job_without_type_sends_empty_payload():
    session = FakeSession(FakeResponse(200, {"ok": True, "job": None}))
    client = make_client(session)

    assert client.claim_job() is None
    assert session.calls[0]["json"] == {}


def test_claim_job_connection_error_is_raised():
    session = FakeSession(error=requests.ConnectionError("Server nicht erreichbar"))
    client = make_client(session)

    try:
        client.claim_job()
    except requests.ConnectionError as exc:
        assert "nicht erreichbar" in str(exc)
    else:  # pragma: no cover - Guard gegen stilles Durchrutschen
        raise AssertionError("ConnectionError erwartet")


def test_claim_job_401_raises_without_password_in_message():
    session = FakeSession(FakeResponse(401, {"ok": False}))
    client = make_client(session)

    try:
        client.claim_job()
    except RuntimeError as exc:
        assert "401" in str(exc)
        assert "secret" not in str(exc)
    else:  # pragma: no cover - Guard gegen stilles Durchrutschen
        raise AssertionError("RuntimeError erwartet")


def test_heartbeat_returns_true_on_200_and_calls_expected_url():
    session = FakeSession(FakeResponse(200, {"ok": True}))
    client = make_client(session)

    assert client.heartbeat(3) is True
    assert session.calls[0]["url"] == "http://fake-server:8080/api/jobs/3/heartbeat"


def test_heartbeat_returns_false_on_404_and_never_raises():
    session = FakeSession(FakeResponse(404, {"ok": False}))
    client = make_client(session)
    assert client.heartbeat(3) is False

    failing = make_client(FakeSession(error=requests.Timeout("zu langsam")))
    assert failing.heartbeat(3) is False


def test_complete_job_sends_status_result_and_error():
    session = FakeSession(FakeResponse(200, {"ok": True}))
    client = make_client(session)

    assert client.complete_job(9, "failed", error="Audio corrupted") is True
    assert session.calls[0]["url"] == "http://fake-server:8080/api/jobs/9/complete"
    assert session.calls[0]["json"] == {
        "status": "failed",
        "result": None,
        "error": "Audio corrupted",
    }


def test_complete_job_rejects_unknown_status():
    client = make_client(FakeSession())
    try:
        client.complete_job(9, "laufend")
    except ValueError as exc:
        assert "completed" in str(exc)
    else:  # pragma: no cover - Guard gegen stilles Durchrutschen
        raise AssertionError("ValueError erwartet")


def test_complete_job_returns_false_on_server_error_and_connection_error():
    session = FakeSession(FakeResponse(500, {"ok": False}))
    client = make_client(session)
    assert client.complete_job(9, "completed", result={"dims": 512}) is False

    failing = make_client(FakeSession(error=requests.ConnectionError("weg")))
    assert failing.complete_job(9, "completed") is False


# --- execute_job -----------------------------------------------------------------


def test_execute_job_passes_payload_to_handler():
    seen = {}

    def handler(payload):
        seen.update(payload)
        return {"ok": True}

    result = execute_job(
        {"id": 1, "job_type": "embedding", "payload": {"track_path": "a.wav"}},
        handler_map={"embedding": handler},
    )
    assert result == {"status": "completed", "result": {"ok": True}}
    assert seen == {"track_path": "a.wav"}


def test_execute_job_without_handler_fails_instead_of_raising():
    result = execute_job({"id": 2, "job_type": "unbekannt", "payload": {}}, handler_map={})
    assert result["status"] == "failed"
    assert "unbekannt" in result["error"]


def test_execute_job_tolerates_missing_or_invalid_payload():
    received = []

    def handler(payload):
        received.append(payload)
        return None

    assert execute_job({"id": 3, "job_type": "caption"}, {"caption": handler})["status"] == "completed"
    assert execute_job({"id": 4, "job_type": "caption", "payload": "kaputt"}, {"caption": handler})[
        "status"
    ] == "completed"
    assert received == [{}, {}]


# --- run_worker_loop -------------------------------------------------------------


def test_run_worker_loop_backs_off_when_queue_is_empty():
    client = LoopClient()
    stop = FakeStopEvent(stop_after=1)

    processed = run_worker_loop(client, poll_interval=2.5, stop_event=stop)

    assert processed == 0
    assert client.claims == 1
    assert stop.waits == [2.5]


def test_run_worker_loop_survives_claim_connection_error():
    client = LoopClient(claim_error=requests.ConnectionError("Server weg"))
    stop = FakeStopEvent(stop_after=1)

    processed = run_worker_loop(client, poll_interval=1.0, stop_event=stop)

    assert processed == 0
    assert client.claims == 1
    assert stop.waits == [1.0]


def test_run_worker_loop_honours_stop_event_before_claiming():
    client = LoopClient()
    stop = FakeStopEvent()
    stop._set = True

    assert run_worker_loop(client, stop_event=stop) == 0
    assert client.claims == 0


def test_run_worker_loop_heartbeats_during_job_and_reports_result():
    job = {"id": 5, "job_type": "embedding", "payload": {"track_path": "a.wav"}}
    client = LoopClient(jobs=[job])

    processed = run_worker_loop(
        client, poll_interval=0.0, max_jobs=1, handler_map={"embedding": lambda p: {"dims": 512}}
    )

    assert processed == 1
    assert client.heartbeats == [5]
    assert client.completed[5] == {"status": "completed", "result": {"dims": 512}, "error": None}


def test_run_worker_loop_reports_failing_handler_and_counts_it():
    job = {"id": 6, "job_type": "embedding", "payload": {}}

    def broken(payload):
        raise RuntimeError("GPU OOM")

    client = LoopClient(jobs=[job])
    processed = run_worker_loop(
        client, poll_interval=0.0, max_jobs=1, handler_map={"embedding": broken}
    )

    assert processed == 1
    assert client.completed[6]["status"] == "failed"
    assert "GPU OOM" in client.completed[6]["error"]


def test_run_worker_loop_retries_when_result_cannot_be_reported():
    job = {"id": 8, "job_type": "embedding", "payload": {}}
    client = LoopClient(jobs=[job])
    client.complete_ok = False
    stop = FakeStopEvent(stop_after=1)

    processed = run_worker_loop(
        client, poll_interval=0.5, stop_event=stop, handler_map={"embedding": lambda p: {}}
    )

    assert processed == 0
    assert stop.waits == [0.5]


def test_run_worker_loop_skips_job_without_id():
    client = LoopClient(jobs=[{"job_type": "embedding", "payload": {}}])
    stop = FakeStopEvent(stop_after=1)

    processed = run_worker_loop(client, poll_interval=0.25, stop_event=stop)

    assert processed == 0
    assert client.completed == {}
    assert stop.waits == [0.25]


# --- CLI --------------------------------------------------------------------------


def test_cli_argument_parsing_defaults():
    args = worker._build_parser().parse_args(["--api-url", "http://server:8080", "--password", "p"])
    assert args.api_url == "http://server:8080"
    assert args.password == "p"
    assert args.job_type is None
    assert args.poll_interval == 5.0
    assert args.once is False


def test_cli_argument_parsing_once_and_job_type():
    args = worker._build_parser().parse_args(
        ["--api-url", "http://server:8080", "--job-type", "embedding", "--poll-interval", "0.5", "--once"]
    )
    assert args.job_type == "embedding"
    assert args.poll_interval == 0.5
    assert args.once is True


def test_cli_reads_password_from_environment(monkeypatch):
    monkeypatch.setenv("SC_DIGGER_WEB_PASSWORD", "aus-der-umgebung")
    args = worker._build_parser().parse_args(["--api-url", "http://server:8080"])
    assert args.password == "aus-der-umgebung"


def test_main_without_password_returns_2(monkeypatch, capsys):
    monkeypatch.delenv("SC_DIGGER_WEB_PASSWORD", raising=False)

    assert worker.main(["--api-url", "http://server:8080"]) == 2
    assert "kein Passwort" in capsys.readouterr().err


def test_main_once_runs_single_job(monkeypatch):
    calls = {}

    class FakeClient:
        def __init__(self, api_url, password, worker_id=None, timeout=10.0):
            calls["client"] = {"api_url": api_url, "password": password, "worker_id": worker_id}

    def fake_loop(client, **kwargs):
        calls["loop"] = kwargs
        return 1

    monkeypatch.setattr(worker, "JobWorkerClient", FakeClient)
    monkeypatch.setattr(worker, "run_worker_loop", fake_loop)

    assert worker.main(["--api-url", "http://server:8080", "--password", "p", "--once"]) == 0
    assert calls["client"]["api_url"] == "http://server:8080"
    assert calls["loop"]["max_jobs"] == 1
