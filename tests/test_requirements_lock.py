"""requirements.lock muss zu requirements.txt passen.

Das Docker-Image installiert nur aus dem Lockfile (``--require-hashes``). Hebt Dependabot eine
Untergrenze in requirements.txt an, ohne dass das Lockfile mitzieht, bliebe der Server still auf
der alten Version. Dieser Test macht daraus einen roten PR.
"""
from __future__ import annotations

import re
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _direct() -> list[Requirement]:
    reqs = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            reqs.append(Requirement(line))
    return reqs


def _locked() -> dict[str, str]:
    text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    return {_norm(m.group(1)): m.group(2)
            for m in re.finditer(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+) \\$", text, re.M)}


def test_every_locked_package_has_hashes():
    text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text.strip())
    assert len(blocks) >= 10
    for block in blocks:
        assert "--hash=sha256:" in block, block.splitlines()[0]


def test_lock_satisfies_every_requirement():
    locked = _locked()
    problems = []
    for req in _direct():
        version = locked.get(_norm(req.name))
        if version is None:
            problems.append(f"{req.name}: fehlt im Lockfile")
        elif not req.specifier.contains(version, prereleases=True):
            problems.append(f"{req.name}: Lockfile {version}, verlangt {req.specifier}")
    assert not problems, "scripts/update-lock.sh ausführen: " + "; ".join(problems)


def test_dockerfile_installs_from_lock_with_hashes():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements.lock" in text
