#!/bin/bash
# Ottilie.app bauen (Auftrag M9, Paketbau). Eine Architektur: die dieses Macs (bzw. der Shell – unter
# `arch -x86_64` auf Apple Silicon die Intel-Fassung). Universal-Paket: tools/build_universal.sh.
# Ohne Signatur – die folgt mit dem festen Zertifikat. Ordner dist/ und build/ sind von git ausgenommen.
# Voraussetzungen: tools/venv_bau.sh (Paketversionen), Homebrew-Werkzeuge, tools/build_bmx.sh (bmx).
# Aufruf: tools/build_app.sh [Zielordner]          (PYTHON=… baut mit einem anderen Python, ohne Bauumgebung)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
ARCH=$(uname -m)
WORK="$ROOT/build/work-$ARCH"
mkdir -p "$WORK" "$OUT"
export PYINSTALLER_CONFIG_DIR="$ROOT/build/pyinstaller-config-$ARCH"

PY="${PYTHON:-$ROOT/build/venv-$ARCH/bin/python}"
if [ ! -x "$PY" ]; then
  echo "Keine Bauumgebung für $ARCH – erst tools/venv_bau.sh (oder PYTHON=python3 setzen)"; exit 1
fi
PY_ARCH=$("$PY" -c 'import platform; print(platform.machine())')
[ "$PY_ARCH" = "$ARCH" ] || { echo "Python läuft als $PY_ARCH, die Shell als $ARCH – abgebrochen"; exit 1; }
"$PY" "$ROOT/tools/lock_requirements.py" --pruefen

VERSION=$(cd "$ROOT" && "$PY" -c "from tonmischung import __version__; print(__version__)")
echo "Ottilie $VERSION – Architektur $ARCH"

if [ -e "$OUT/Ottilie.app" ]; then
  # Nie überschreiben: vorhandenes Paket beiseite legen
  mv "$OUT/Ottilie.app" "$OUT/Ottilie-vorher-$(date +%Y%m%d-%H%M%S).app"
fi

# bmx aus tools/build_bmx.sh vor allen anderen (gleicher Stand auf Intel und Apple Silicon)
if [ -x "$ROOT/build/bmx-$ARCH/bin/bmxtranswrap" ]; then
  export PATH="$ROOT/build/bmx-$ARCH/bin:$PATH"
else
  echo "Hinweis: kein build/bmx-$ARCH – bmx aus PATH/Homebrew (tools/build_bmx.sh baut den festen Stand)"
fi
echo "Werkzeuge bündeln (ffmpeg, ffprobe, mediainfo, bmx) …"
(cd "$ROOT" && "$PY" tools/bundle_tools.py --ziel "$ROOT/build/bin")

# Bauangabe ins Paket: Stand (Commit), Paketliste, Architektur – das Zusammenfügen prüft, dass beide Hälften passen
INFO_DIR="$ROOT/build/info-$ARCH"
mkdir -p "$INFO_DIR"
COMMIT=$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo "?")
[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ] || COMMIT="$COMMIT+geändert"
"$PY" - "$INFO_DIR/build_info.json" "$VERSION" "$COMMIT" "$ARCH" "$ROOT/tools/requirements-bau.txt" <<'PYEOF'
import datetime, hashlib, json, sys
out, version, commit, arch, lock = sys.argv[1:6]
info = {"version": version, "commit": commit, "archs": [arch],
        "lock": hashlib.sha256(open(lock, "rb").read()).hexdigest()[:16],
        "built": {arch: datetime.datetime.now().strftime("%d.%m.%Y %H:%M")}}
json.dump(info, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PYEOF
export TM_BUILD_INFO="$INFO_DIR/build_info.json"

"$PY" -m PyInstaller --noconfirm --workpath "$WORK" --distpath "$OUT" "$ROOT/tools/Ottilie.spec"

APP="$OUT/Ottilie.app"
echo "Gebaut: $APP ($(du -sh "$APP" | cut -f1))"
# Kurzprüfung: Paket startet, meldet Version und Werkzeuge, Selbsttest (Architektur, Module, Werkzeuge)
"$APP/Contents/MacOS/Ottilie" version
"$APP/Contents/MacOS/Ottilie" selbsttest
