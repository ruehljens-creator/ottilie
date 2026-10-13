#!/bin/bash
# Universal-Paket von Ottilie (Apple Silicon + Intel in einer App) – auf einem Apple-Silicon-Mac.
# Apple-Silicon-Umstellung (Jens 06.10.2026: „Wir stellen mit Einführung von Helmut auf Silicon-Macs um“).
#
# Aufruf: tools/build_universal.sh <Intel-Paket>   Intel-Teil fertig mitgebracht – auf dem Intel-Mac aus demselben
#                                                  Stand gebaut: tools/build_app.sh dist/x86_64
#         tools/build_universal.sh --intel-hier    Intel-Teil hier unter Rosetta bauen (braucht Intel-Homebrew unter
#                                                  /usr/local, arch -x86_64 tools/venv_bau.sh, arch -x86_64 tools/build_bmx.sh)
# Ergebnis: dist/Ottilie.app (universal), danach tools/build_dmg.sh --ohne-bau.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ "$(uname -m)" = "arm64" ] || { echo "Nur auf einem Apple-Silicon-Mac (dieser: $(uname -m))"; exit 1; }
INTEL="${1:-}"
[ -n "$INTEL" ] || { sed -n '2,10p' "$0"; exit 1; }

if [ "$INTEL" = "--intel-hier" ]; then
  echo "=== Intel-Teil unter Rosetta ==="
  arch -x86_64 /bin/bash -c "export PATH=/usr/local/bin:\$PATH; '$ROOT/tools/build_app.sh' '$ROOT/dist/x86_64'"
  INTEL="$ROOT/dist/x86_64/Ottilie.app"
fi
[ -d "$INTEL/Contents/MacOS" ] || { echo "Kein Paket: $INTEL"; exit 1; }

echo "=== Apple-Silicon-Teil ==="
"$ROOT/tools/build_app.sh" "$ROOT/dist/arm64"

OUT="$ROOT/dist/Ottilie.app"
if [ -e "$OUT" ]; then
  mv "$OUT" "$ROOT/dist/Ottilie-vorher-$(date +%Y%m%d-%H%M%S).app"     # nie überschreiben
fi
echo "=== Zusammenfügen ==="
"$ROOT/build/venv-arm64/bin/python" "$ROOT/tools/merge_universal.py" "$ROOT/dist/arm64/Ottilie.app" "$INTEL" "$OUT"

echo "=== Selbsttest Apple Silicon (nativ) ==="
"$OUT/Contents/MacOS/Ottilie" selbsttest
if arch -x86_64 /usr/bin/true 2>/dev/null; then
  echo "=== Selbsttest Intel-Teil (Rosetta) ==="
  arch -x86_64 "$OUT/Contents/MacOS/Ottilie" selbsttest
else
  echo "Rosetta fehlt – Intel-Teil hier nicht prüfbar (auf einem Intel-Mac: Ottilie selbsttest)"
fi
echo "Fertig: $OUT ($(du -sh "$OUT" | cut -f1)) – DMG: tools/build_dmg.sh --ohne-bau"
