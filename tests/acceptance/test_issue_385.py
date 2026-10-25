import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_JOB = "30 7 * * * . /app/cron.env; cd /app && python -m sc_digger.main >> /proc/1/fd/1 2>&1"
JOBS = ("30 7 * * *", "0 20 * * 0", "0 18 * * *")


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _setup(tmp_path: Path, crontab_exit: int):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    installed = tmp_path / "installed.txt"
    marker = tmp_path / "bot-started"
    _executable(
        bin_dir / "crontab",
        f'#!/bin/sh\nif [ {crontab_exit} -eq 0 ]; then cat "$1" > "{installed}"; fi\nexit {crontab_exit}\n',
    )
    _executable(bin_dir / "cron", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "python", f'#!/bin/sh\necho "$@" > "{marker}"\nexit 0\n')
    cron_file = tmp_path / "sc-digger"
    cron_file.write_text(BASE_JOB + "\n", encoding="utf-8")
    script = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    script = script.replace("/app/cron.env", str(tmp_path / "cron.env"))
    script = script.replace("/etc/cron.d/sc-digger", str(cron_file))
    script_path = tmp_path / "entrypoint-test.sh"
    script_path.write_text(script, encoding="utf-8")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return script_path, env, cron_file, installed, marker


def _run(script_path: Path, env: dict):
    return subprocess.run(["sh", str(script_path)], env=env, capture_output=True, text=True, timeout=60)


def test_failing_crontab_aborts_start_with_message(tmp_path):
    script_path, env, _cron_file, _installed, marker = _setup(tmp_path, crontab_exit=1)
    proc = _run(script_path, env)
    assert proc.returncode != 0
    assert "crontab" in (proc.stderr + proc.stdout).lower()
    assert not marker.exists(), "Bot darf bei fehlenden Cron-Jobs nicht starten"


def test_working_crontab_installs_all_three_jobs_and_starts_bot(tmp_path):
    script_path, env, cron_file, installed, marker = _setup(tmp_path, crontab_exit=0)
    proc = _run(script_path, env)
    assert proc.returncode == 0, proc.stderr
    assert "-m sc_digger.bot" in marker.read_text(encoding="utf-8")
    for job in JOBS:
        assert cron_file.read_text(encoding="utf-8").count(job) == 1, job
        assert installed.read_text(encoding="utf-8").count(job) == 1, job


def test_second_start_does_not_duplicate_jobs(tmp_path):
    script_path, env, cron_file, _installed, _marker = _setup(tmp_path, crontab_exit=0)
    assert _run(script_path, env).returncode == 0
    assert _run(script_path, env).returncode == 0
    text = cron_file.read_text(encoding="utf-8")
    for job in JOBS:
        assert text.count(job) == 1, job


def test_no_crontab_line_is_silenced():
    for line in (ROOT / "entrypoint.sh").read_text(encoding="utf-8").splitlines():
        if "crontab" in line and not line.lstrip().startswith("#"):
            assert "|| true" not in line, line
            assert "2>/dev/null" not in line, line
