import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_CAPS = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "SETGID", "SETUID", "AUDIT_WRITE"}


def _code() -> list[str]:
    lines = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    return [ln.split("#", 1)[0].rstrip() for ln in lines]


def _items(key: str) -> list[str] | None:
    """Werte eines Schlüssels auf Dienst-Ebene (4 Leerzeichen), als Block- oder Inline-Liste."""
    lines = _code()
    for i, line in enumerate(lines):
        m = re.match(rf"^    {key}:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("["):
            return [v.strip().strip("'\"") for v in rest.strip("[]").split(",") if v.strip()]
        items = []
        for nxt in lines[i + 1:]:
            lm = re.match(r"^\s+-\s*(.+?)\s*$", nxt)
            if lm:
                items.append(lm.group(1).strip("'\""))
            elif nxt.strip():
                break
        return items
    return None


def test_no_new_privileges_is_set():
    assert "no-new-privileges:true" in (_items("security_opt") or [])


def test_all_capabilities_dropped():
    assert _items("cap_drop") == ["ALL"]


def test_only_needed_capabilities_added_back():
    added = _items("cap_add") or []
    assert {"SETUID", "SETGID"} <= set(added), "cron braucht SETUID und SETGID"
    assert set(added) <= ALLOWED_CAPS, sorted(set(added) - ALLOWED_CAPS)


def test_no_privileged_host_network_or_docker_socket():
    text = "\n".join(_code())
    assert not re.search(r"^\s*privileged:\s*true", text, re.M)
    assert not re.search(r"^\s*network_mode:\s*host", text, re.M)
    assert "docker.sock" not in text


def test_read_only_mounts_stay_read_only():
    mounts = _items("volumes") or []
    assert mounts
    for mount in mounts:
        if "inbox" in mount or mount.startswith("./data"):
            continue
        assert mount.endswith(":ro"), mount
    assert any(m.startswith("/") and m.endswith(":ro") for m in mounts), "Sammlung muss :ro bleiben"


def test_existing_settings_are_untouched():
    text = "\n".join(_code())
    assert re.search(r"^    restart:\s*unless-stopped", text, re.M)
    assert re.search(r"^    init:\s*true", text, re.M)
    assert re.search(r"^    env_file:\s*\.env", text, re.M)
