#!/bin/bash
# Paket der Zentrale zum Ausrollen (Server ohne Git-Zugang): build/ottilie-zentrale-<Version>.tar.gz mit zentrale/
# und den Ottilie-Modulen, die das Abbild braucht (siehe Dockerfile). Auf dem Server:
#   tar xzf ottilie-zentrale-<Version>.tar.gz && cd ottilie-zentrale/zentrale && docker compose up -d --build
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=$(cd "$ROOT/zentrale" && python3 -c "from app import VERSION; print(VERSION)")
COMMIT=$(git -C "$ROOT" rev-parse --short=12 HEAD)
OUT="$ROOT/build/ottilie-zentrale-$VERSION-$COMMIT.tar.gz"
mkdir -p "$ROOT/build"
cd "$ROOT"
FILES=(zentrale/Dockerfile zentrale/compose.yaml zentrale/requirements.txt zentrale/README.md zentrale/app zentrale/web
       zentrale/nginx tonmischung/__init__.py tonmischung/core/__init__.py tonmischung/core/fileio.py
       tonmischung/core/mixstate.py tonmischung/helmut/__init__.py tonmischung/helmut/mapping.py
       tonmischung/dropfolder/__init__.py tonmischung/dropfolder/handover.py)
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata --exclude='__pycache__' -czf "$OUT" -s ',^,ottilie-zentrale/,' "${FILES[@]}"
echo "$OUT"
