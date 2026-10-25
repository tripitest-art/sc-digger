"""Eigene Tests: ein Fehler beim Installieren der Cron-Jobs bricht den Start ab (Regel 7)."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_JOB = "30 7 * * * . /app/cron.env; cd /app && python -m sc_digger.main >> /proc/1/fd/1 2>&1"
# Absolut aufrufen: der PATH der Testumgebung enthält absichtlich kein /bin.
SH = shutil.which("sh")


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _prepare(tmp_path: Path, crontab_text: str | None):
    """Kopiert entrypoint.sh mit Fakes: crontab/cron/python aus tmp/bin, Pfade umgebogen."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "bot-started"
    if crontab_text is not None:
        _executable(bin_dir / "crontab", crontab_text)
    _executable(bin_dir / "cron", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "python", f'#!/bin/sh\necho "$@" > "{marker}"\nexit 0\n')
    # Nur bin_dir im PATH: sonst greift ein echtes crontab des Rechners und der Test
    # würde die Crontab des ausführenden Nutzers verändern.
    for tool in ("grep", "chmod"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    cron_file = tmp_path / "sc-digger"
    cron_file.write_text(BASE_JOB + "\n", encoding="utf-8")
    script = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    script = script.replace("/app/cron.env", str(tmp_path / "cron.env"))
    script = script.replace("/etc/cron.d/sc-digger", str(cron_file))
    script_path = tmp_path / "entrypoint-test.sh"
    script_path.write_text(script, encoding="utf-8")
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    return script_path, env, marker


def _run(script_path: Path, env: dict):
    return subprocess.run([SH, str(script_path)], env=env, capture_output=True, text=True, timeout=60)


def test_missing_crontab_aborts_start(tmp_path):
    """crontab fehlt komplett im PATH: Exit 127 muss wie ein Fehler behandelt werden."""
    script_path, env, marker = _prepare(tmp_path, crontab_text=None)
    proc = _run(script_path, env)
    assert proc.returncode != 0
    assert "crontab" in (proc.stderr + proc.stdout).lower()
    assert not marker.exists(), "Bot darf ohne installierte Cron-Jobs nicht starten"


def test_crontab_failure_reports_on_stderr(tmp_path):
    """Die Meldung landet auf stderr, damit sie im Container-Log auffällt."""
    script_path, env, _marker = _prepare(tmp_path, crontab_text="#!/bin/sh\nexit 3\n")
    proc = _run(script_path, env)
    assert proc.returncode == 1
    assert "crontab" in proc.stderr.lower()


def test_crontab_success_keeps_bot_as_last_step(tmp_path):
    """Bei Erfolg läuft der Start wie bisher durch, der Bot bleibt der Hauptprozess."""
    script_path, env, marker = _prepare(tmp_path, crontab_text="#!/bin/sh\nexit 0\n")
    proc = _run(script_path, env)
    assert proc.returncode == 0, proc.stderr
    assert marker.read_text(encoding="utf-8").strip() == "-m sc_digger.bot"
