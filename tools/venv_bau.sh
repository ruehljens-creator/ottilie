#!/bin/bash
# Bauumgebung einrichten: build/venv-<Architektur> mit genau den Paketversionen aus tools/requirements-bau.txt.
# Apple-Silicon-Umstellung (Jens 06.10.2026): Intel- und Silicon-Teil des Universal-Pakets entstehen aus denselben
# Versionen. Grundlage ist das Python vom python.org-Installer (3.14, universal2) – auf beiden Macs dasselbe.
# Der Ordner wird bei jedem Aufruf neu angelegt (eigener Bauordner unter build/).
# Aufruf: tools/venv_bau.sh                       (PYTHON_BASIS=/pfad/zu/python3 für ein anderes Python)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH=$(uname -m)
BASE="${PYTHON_BASIS:-/Library/Frameworks/Python.framework/Versions/3.14/bin/python3}"
[ -x "$BASE" ] || { echo "Kein Python unter $BASE – python.org-Installer 3.14 (universal2) installieren"; exit 1; }

LOCK="$ROOT/tools/requirements-bau.txt"
WANT=$(sed -n 's/.*unter Python \([0-9.]*\).*/\1/p' "$LOCK" | head -1)
HAVE=$("$BASE" -c 'import platform; print(platform.python_version())')
if [ "${WANT%.*}" != "${HAVE%.*}" ]; then
  echo "Python $HAVE – die Paketversionen sind für Python $WANT festgeschrieben"; exit 1
fi
[ "$WANT" = "$HAVE" ] || echo "Hinweis: Python $HAVE statt $WANT (gleiche Hauptversion)"

VENV="$ROOT/build/venv-$ARCH"
echo "Bauumgebung $VENV (Python $HAVE, $ARCH) …"
"$BASE" -m venv --clear "$VENV"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --prefer-binary -r "$LOCK"
"$VENV/bin/python" "$ROOT/tools/lock_requirements.py" --pruefen
echo "Fertig. Bauen: tools/build_app.sh"
