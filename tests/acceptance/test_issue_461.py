"""Akzeptanztests: Cron-Läufe sehen dieselbe Override-Datei wie Bot und Web (SC_DIGGER_CONFIG_LOCAL)."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_JOB = "30 7 * * * . /app/cron.env; cd /app && python -m sc_digger.main >> /proc/1/fd/1 2>&1"
DEFAULT = "/data/config.local.yaml"
SH = shutil.which("sh")


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _setup(tmp_path: Path):
    """entrypoint.sh mit Fakes: crontab/cron/python aus tmp/bin, Pfade nach tmp_path umgebogen."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "python-calls.txt"
    _executable(bin_dir / "crontab", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "cron", "#!/bin/sh\nexit 0\n")
    # Jeder python-Aufruf hält Modul und gesehene Variable fest (Web läuft im Hintergrund).
    _executable(bin_dir / "python",
                f'#!/bin/sh\necho "$2|${{SC_DIGGER_CONFIG_LOCAL-<unset>}}" >> "{calls}"\nexit 0\n')
    for tool in ("grep", "chmod"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    cron_file = tmp_path / "sc-digger"
    cron_file.write_text(BASE_JOB + "\n", encoding="utf-8")
    cron_env = tmp_path / "cron.env"
    script = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    script = script.replace("/app/cron.env", str(cron_env))
    script = script.replace("/etc/cron.d/sc-digger", str(cron_file))
    # Log-Umleitung des Web-Starts zeigt im Container auf PID 1; im Test nicht beschreibbar.
    script = script.replace("/proc/1/fd/1", str(tmp_path / "container.log"))
    script_path = tmp_path / "entrypoint-test.sh"
    script_path.write_text(script, encoding="utf-8")
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    return script_path, env, cron_env, calls


def _run(script_path: Path, env: dict) -> None:
    proc = subprocess.run([SH, str(script_path)], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def _value_for_cron(cron_env: Path) -> str:
    """So sieht ein Cron-Job die Variable: leere Umgebung, dann `. cron.env`."""
    proc = subprocess.run(
        [SH, "-c", f'. "{cron_env}"; printf "%s" "${{SC_DIGGER_CONFIG_LOCAL-<unset>}}"'],
        env={}, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _seen_by(calls: Path, module: str) -> list[str]:
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return [line.split("|", 1)[1] for line in lines if line.split("|", 1)[0] == module]


def test_cron_sieht_standardpfad_ohne_web_passwort(tmp_path):
    script_path, env, cron_env, _calls = _setup(tmp_path)
    _run(script_path, env)
    assert _value_for_cron(cron_env) == DEFAULT


def test_cron_sieht_standardpfad_mit_web_passwort(tmp_path):
    script_path, env, cron_env, _calls = _setup(tmp_path)
    env["SC_DIGGER_WEB_PASSWORD"] = "test-passwort"
    _run(script_path, env)
    assert _value_for_cron(cron_env) == DEFAULT


def test_leerer_wert_bekommt_den_standardpfad(tmp_path):
    script_path, env, cron_env, _calls = _setup(tmp_path)
    env["SC_DIGGER_CONFIG_LOCAL"] = ""
    _run(script_path, env)
    assert _value_for_cron(cron_env) == DEFAULT


def test_eigener_pfad_bleibt_erhalten(tmp_path):
    script_path, env, cron_env, calls = _setup(tmp_path)
    env["SC_DIGGER_CONFIG_LOCAL"] = "/custom/local.yaml"
    env["SC_DIGGER_WEB_PASSWORD"] = "test-passwort"
    _run(script_path, env)
    assert _value_for_cron(cron_env) == "/custom/local.yaml"
    assert _seen_by(calls, "sc_digger.bot") == ["/custom/local.yaml"]


def test_bot_sieht_denselben_wert_wie_cron(tmp_path):
    script_path, env, cron_env, calls = _setup(tmp_path)
    _run(script_path, env)
    assert _seen_by(calls, "sc_digger.bot") == [_value_for_cron(cron_env)] == [DEFAULT]


def test_web_sieht_denselben_wert_wie_cron(tmp_path):
    script_path, env, cron_env, calls = _setup(tmp_path)
    env["SC_DIGGER_WEB_PASSWORD"] = "test-passwort"
    _run(script_path, env)
    # Web startet im Hintergrund; der Bot-Aufruf ist synchron (exec) und steht sicher in der Datei.
    for _ in range(50):
        if _seen_by(calls, "sc_digger.web"):
            break
        subprocess.run([SH, "-c", "sleep 0.1"], timeout=5)
    assert _seen_by(calls, "sc_digger.web") == [DEFAULT]
    assert _value_for_cron(cron_env) == DEFAULT


def test_cron_env_bleibt_nur_fuer_den_eigentuemer_lesbar(tmp_path):
    script_path, env, cron_env, _calls = _setup(tmp_path)
    _run(script_path, env)
    assert cron_env.stat().st_mode & 0o777 == 0o600
