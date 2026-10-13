#!/bin/bash
# Ist dieser Mac bereit für den Bau von Ottilie? Prüft nur, installiert und ändert nichts.
# Apple-Silicon-Umstellung (Jens 08.10.2026: Bau auf dem Mac mini, auf dem schon PunchBuddy kompiliert wurde).
# Aufruf: tools/pruefe_baumac.sh        Anleitung: docs/BAU_APPLE_SILICON.md
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH=$(uname -m)
BAD=0
ok()   { echo "  ✓ $1"; }
fehlt() { echo "  ✕ $1"; echo "      → $2"; BAD=$((BAD + 1)); }

echo "Mac: $(sysctl -n hw.model) · $ARCH · macOS $(sw_vers -productVersion)"
[ "$ARCH" = "arm64" ] && ok "Apple Silicon" || echo "  ! kein Apple-Silicon-Mac – hier entsteht nur ein $ARCH-Teil"

xcode-select -p >/dev/null 2>&1 && ok "Xcode-Befehlszeilenwerkzeuge ($(xcode-select -p))" \
  || fehlt "Xcode-Befehlszeilenwerkzeuge" "xcode-select --install"

BREW=/opt/homebrew/bin/brew; [ "$ARCH" = "x86_64" ] && BREW=/usr/local/bin/brew
if [ -x "$BREW" ]; then
  ok "Homebrew ($BREW)"
  for f in ffmpeg mediainfo cmake uriparser pkgconf; do
    v=$("$BREW" list --versions "$f" 2>/dev/null)
    [ -n "$v" ] && ok "$v" || fehlt "$f (Homebrew)" "$BREW install $f"
  done
else
  fehlt "Homebrew unter ${BREW%/bin/brew}" "Installer von https://brew.sh"
fi

PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
WANT=$(sed -n 's/.*unter Python \([0-9.]*\).*/\1/p' "$ROOT/tools/requirements-bau.txt" | head -1)
if [ -x "$PY" ]; then
  HAVE=$("$PY" -c 'import platform; print(platform.python_version())')
  ARCHS=$(lipo -archs "$(dirname "$PY")/python3.14" 2>/dev/null)
  [ "${HAVE%.*}" = "${WANT%.*}" ] && ok "Python $HAVE vom python.org-Installer ($ARCHS)" \
    || fehlt "Python $HAVE – gebraucht $WANT" "python.org-Installer 3.14 (universal2)"
else
  fehlt "Python $WANT vom python.org-Installer" "https://www.python.org/downloads/macos/ – „universal2 installer“"
fi

[ -f "$ROOT/vendor/punchbuddy/punchbuddy/__init__.py" ] && ok "Unterprojekt PunchBuddy da" \
  || fehlt "Unterprojekt PunchBuddy fehlt" "git -C \"$ROOT\" submodule update --init"
[ -x "$ROOT/build/venv-$ARCH/bin/python" ] && ok "Bauumgebung build/venv-$ARCH" \
  || echo "  · Bauumgebung noch nicht angelegt – tools/venv_bau.sh"
[ -x "$ROOT/build/bmx-$ARCH/bin/bmxtranswrap" ] && ok "bmx in build/bmx-$ARCH" \
  || echo "  · bmx noch nicht gebaut – tools/build_bmx.sh"
if [ "$ARCH" = "arm64" ]; then
  arch -x86_64 /usr/bin/true 2>/dev/null && ok "Rosetta (Intel-Teil lässt sich hier mitprüfen)" \
    || echo "  · Rosetta fehlt – Intel-Teil nur auf dem Intel-Mac prüfbar (optional: softwareupdate --install-rosetta)"
fi
FREE=$(df -g "$ROOT" | awk 'NR==2{print $4}')
[ "${FREE:-0}" -ge 10 ] && ok "$FREE GB frei" || fehlt "nur ${FREE:-?} GB frei" "mindestens 10 GB für Bau und DMG"

echo
[ "$BAD" -eq 0 ] && echo "Bereit. Weiter: tools/venv_bau.sh, tools/build_bmx.sh, tools/build_universal.sh <Intel-Paket>" \
  || echo "$BAD Punkt(e) fehlen noch."
exit $([ "$BAD" -eq 0 ] && echo 0 || echo 1)
