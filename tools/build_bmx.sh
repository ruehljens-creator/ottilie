#!/bin/bash
# bmx (BSD-3-Clause; BBC R&D, seit 2026 von der EBU weitergeführt) aus dem Quelltext bauen – nach build/bmx-<Architektur>.
# Quelle: https://github.com/ebu/bmx (Jens 06.10.2026: „Die aktuelle bmx-Version findet sich im Git der EBU“; das
# BBC-Repository ist archiviert). Release v1.7 vom 09.05.2026.
# Apple-Silicon-Umstellung (Jens 06.10.2026): auf dem Silicon-Mac genauso wie hier auf dem Intel-Mac.
# Braucht: Xcode-Befehlszeilenwerkzeuge, cmake, uriparser, pkgconf (Homebrew: brew install cmake uriparser pkgconf);
# expat vom System. Ohne pkg-config bricht cmake in bmx' Ersatzweg für uriparser ab (Mac mini, 12.10.2026).
# Statisch gebaut (keine libbmx-Bibliothek), uriparser dynamisch aus Homebrew – tools/bundle_tools.py bündelt sie
# samt Lizenz. tools/build_app.sh nimmt build/bmx-<Architektur>/bin vor allen anderen bmx-Programmen.
# Aufruf: tools/build_bmx.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH=$(uname -m)
REPO=https://github.com/ebu/bmx.git
TAG=v1.7
REV=346ba45dfd41f24f7582460caa56c9ef47dd9b4b      # v1.7 – Tag und Commit müssen zusammenpassen (geprüft)
SRC="$ROOT/build/bmx-src"
WORK="$ROOT/build/bmx-work-$ARCH"
PREFIX="$ROOT/build/bmx-$ARCH"

if [ ! -d "$SRC/.git" ]; then
  git clone --quiet "$REPO" "$SRC"
fi
git -C "$SRC" remote set-url origin "$REPO"
git -C "$SRC" fetch --quiet --tags origin
[ "$(git -C "$SRC" rev-parse "$TAG^{commit}")" = "$REV" ] || { echo "Tag $TAG zeigt nicht auf $REV – abgebrochen"; exit 1; }
git -C "$SRC" -c advice.detachedHead=false checkout --quiet "$REV"

echo "bmx $(git -C "$SRC" rev-parse --short "$REV") für $ARCH bauen …"
rm -rf "$WORK" "$PREFIX"                          # nur eigene Bauordner unter build/
cmake -S "$SRC" -B "$WORK" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_OSX_ARCHITECTURES="$ARCH" -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 \
  -DBUILD_SHARED_LIBS=OFF -DBMX_BUILD_TESTING=OFF -DBMX_BUILD_TOOLS=OFF -DBMX_BUILD_WITH_LIBCURL=OFF >/dev/null
cmake --build "$WORK" --parallel "$(sysctl -n hw.ncpu)" >/dev/null
cmake --install "$WORK" >/dev/null

for p in bmxtranswrap mxf2raw; do
  "$PREFIX/bin/$p" -v 2>&1 | head -1
  lipo -archs "$PREFIX/bin/$p"
done
echo "Fertig: $PREFIX/bin"
