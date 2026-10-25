#!/bin/sh
# Erzeugt requirements.lock (exakte Versionen + Hashes) aus requirements.txt neu.
# Ziel ist das Docker-Image: Python 3.12, Linux x86_64. Nach einem Dependabot-Bump auf
# requirements.txt ausführen und requirements.lock mit in den Pull Request nehmen.
# Aufruf aus dem Repo-Root:  scripts/update-lock.sh        (braucht uv: pip install uv)
set -eu
cd "$(dirname "$0")/.."
uv pip compile requirements.txt \
    --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
    --generate-hashes --no-header -o requirements.lock
echo "requirements.lock aktualisiert ($(grep -c '^[A-Za-z]' requirements.lock) Pakete)"
