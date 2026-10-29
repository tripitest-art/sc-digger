"""Akzeptanztests: täglicher Übernommen-Abgleich per Cron (audit --check-adopted, 06:45)."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_JOB = "30 7 * * * . /app/cron.env; cd /app && python -m sc_digger.main >> /proc/1/fd/1 2>&1"
ADOPTED_CMD = "python -m sc_digger.main audit --check-adopted >> /proc/1/fd/1 2>&1"
SH = shutil.which("sh")


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _setup(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    installed = tmp_path / "installed.txt"
    marker = tmp_path / "bot-started"
    _executable(bin_dir / "crontab", f'#!/bin/sh\ncat "$1" > "{installed}"\nexit 0\n')
    _executable(bin_dir / "cron", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "python", f'#!/bin/sh\necho "$@" > "{marker}"\nexit 0\n')
    for tool in ("grep", "chmod", "cat"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    cron_file = tmp_path / "sc-digger"
    cron_file.write_text(BASE_JOB + "\n", encoding="utf-8")
    cron_env = tmp_path / "cron.env"
    script = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    script = script.replace("/app/cron.env", str(cron_env))
    script = script.replace("/etc/cron.d/sc-digger", str(cron_file))
    script_path = tmp_path / "entrypoint-test.sh"
    script_path.write_text(script, encoding="utf-8")
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    return script_path, env, cron_file, installed, marker, cron_env


def _run(script_path: Path, env: dict):
    return subprocess.run([SH, str(script_path)], env=env, capture_output=True, text=True, timeout=60)


def _adopted_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "audit --check-adopted" in line]


def test_start_traegt_genau_einen_abgleich_um_0645_ein(tmp_path):
    script_path, env, cron_file, installed, marker, cron_env = _setup(tmp_path)
    proc = _run(script_path, env)
    assert proc.returncode == 0, proc.stderr
    lines = _adopted_lines(cron_file.read_text(encoding="utf-8"))
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("45 6 * * * ")
    assert f". {cron_env};" in line
    assert "cd /app && " in line
    assert line.endswith(ADOPTED_CMD)
    assert _adopted_lines(installed.read_text(encoding="utf-8")) == [line]
    assert marker.read_text(encoding="utf-8").strip() == "-m sc_digger.bot"


def test_zweiter_start_dupliziert_den_abgleich_nicht(tmp_path):
    script_path, env, cron_file, _installed, _marker, _cron_env = _setup(tmp_path)
    assert _run(script_path, env).returncode == 0
    assert _run(script_path, env).returncode == 0
    text = cron_file.read_text(encoding="utf-8")
    assert len(_adopted_lines(text)) == 1
    assert text.count(BASE_JOB) == 1


def test_abgleich_laeuft_vor_dem_discover_lauf(tmp_path):
    script_path, env, cron_file, _installed, _marker, _cron_env = _setup(tmp_path)
    assert _run(script_path, env).returncode == 0
    line = _adopted_lines(cron_file.read_text(encoding="utf-8"))[0]
    minute, hour = (int(x) for x in line.split()[:2])
    assert (hour, minute) < (7, 30)
    assert line.split()[2:5] == ["*", "*", "*"]


def test_doku_nennt_den_abgleich():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    betrieb = (ROOT / "BETRIEB.md").read_text(encoding="utf-8")
    assert "45 6 * * *" in readme
    assert "audit --check-adopted" in readme
    assert "06:45" in betrieb
