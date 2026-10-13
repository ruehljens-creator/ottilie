#!/bin/bash
# DMG mit allem, was zum Betrieb nötig ist (Jens 04.10.2026): Ottilie.app (Werkzeuge mit Lizenztexten, Schrift,
# Logo), Verknüpfung „Programme“, „Lies mich.txt“, „Lizenzen.md“ und „Für die IT“ (Schlüssel und Vorlage des Konfigurationsprofils,
# LaunchAgent, Verteilungsanleitung). Vorhandene Dateien werden nie überschrieben, sondern beiseitegelegt.
# Name mit Architektur: Ottilie-<Version>-universal|apple-silicon|intel.dmg (Universal: tools/build_universal.sh).
# Lizenzliste und Unterlagen für die Geräteverwaltung kommen aus dem Paket selbst (dessen Werkzeuge und Versionen).
# Aufruf: tools/build_dmg.sh            – baut die App neu und packt sie
#         tools/build_dmg.sh --ohne-bau – packt das vorhandene dist/Ottilie.app
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION=$(python3 -c "from tonmischung import __version__; print(__version__)")
APP="$ROOT/dist/Ottilie.app"

if [ "${1:-}" != "--ohne-bau" ]; then
  "$ROOT/tools/build_app.sh"
fi
[ -d "$APP" ] || { echo "Kein $APP – erst tools/build_app.sh"; exit 1; }

STAGE_ROOT="$ROOT/build/dmg"
STAGE="$STAGE_ROOT/Ottilie $VERSION"
rm -rf "$STAGE_ROOT"                                  # nur der eigene Bauordner
mkdir -p "$STAGE/Für die IT"

echo "Inhalt zusammenstellen …"
ditto "$APP" "$STAGE/Ottilie.app"
ln -s /Applications "$STAGE/Programme"
# Mindestversion und Architektur aus dem Paket (beim Bau ermittelt) ins „Lies mich“
MACOS=$(/usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" "$APP/Contents/Info.plist")
ARCHS=$(lipo -archs "$APP/Contents/MacOS/Ottilie")
case "$ARCHS" in
  *arm64*x86_64*|*x86_64*arm64*) KIND="universal"; ARCH_TEXT="läuft auf Apple-Silicon- und Intel-Macs (Universal)";;
  arm64) KIND="apple-silicon"; ARCH_TEXT="nur Apple-Silicon-Macs";;
  *) KIND="intel"; ARCH_TEXT="nur Intel-Macs (auf Apple Silicon über Rosetta)";;
esac
sed -e "s/{MACOS}/$MACOS/" -e "s/{ARCH}/$ARCH_TEXT/" "$ROOT/tools/dmg/Lies mich.txt" > "$STAGE/Lies mich.txt"
"$APP/Contents/MacOS/Ottilie" lizenzen --datei "$STAGE/Lizenzen.md" >/dev/null
BUNDLE_ID=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$APP/Contents/Info.plist")
cp "$ROOT/docs/MDM.md" "$STAGE/Für die IT/Konfigurationsprofil-Schlüssel.md"
"$APP/Contents/MacOS/Ottilie" mdm-doku --plist > "$STAGE/Für die IT/$BUNDLE_ID (Profil-Vorlage).plist"
"$APP/Contents/MacOS/Ottilie" launchagent > "$STAGE/Für die IT/$BUNDLE_ID.plist"
cp "$ROOT/docs/VERTEILUNG.md" "$STAGE/Für die IT/Verteilung.md"

DMG="$ROOT/dist/Ottilie-$VERSION-$KIND.dmg"
if [ -e "$DMG" ]; then
  mv "$DMG" "$ROOT/dist/Ottilie-$VERSION-vorher-$(date +%Y%m%d-%H%M%S).dmg"
fi
echo "DMG bauen …"
hdiutil create -quiet -volname "Ottilie $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO "$DMG"
hdiutil verify -quiet "$DMG"
echo "Gebaut: $DMG ($(du -h "$DMG" | cut -f1))"
