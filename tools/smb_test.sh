#!/bin/zsh
# SMB-Test der Betriebsart Übergabeordner gegen eine eingebundene Freigabe (tests/test_smb.py).
#
#   tools/smb_test.sh /Volumes/<Freigabe>          – Freigabe ist schon eingebunden (Finder oder mount_smbfs)
#   TONMISCHUNG_SMB_KEEP=1 tools/smb_test.sh …     – eigenen Testordner danach liegen lassen
#
# Aus der Ferne (SSH auf den Mac) einbinden – das Kennwort fragt mount_smbfs im eigenen Terminal ab, es steht nirgends:
#   mkdir -p ~/smbtest && mount_smbfs //<benutzer>@<server>/<freigabe> ~/smbtest && tools/smb_test.sh ~/smbtest
#
# Der Test legt nur <Freigabe>/_ottilie_smbtest-<Zeit>/ an und entfernt am Ende nur diesen Ordner.
set -eu
cd "$(dirname "$0")/.."
ziel="${1:-}"
if [[ -z "$ziel" || ! -d "$ziel" ]]; then
  echo "Aufruf: tools/smb_test.sh <eingebundene Freigabe>  (z. B. /Volumes/Uebergabe)"; exit 2
fi
if ! mount | grep -q " on $(cd "$ziel" && pwd -P)\b\| on $(cd "$ziel" && pwd -P) "; then
  echo "Hinweis: $ziel ist kein eigener Einhängepunkt – läuft trotzdem, aber prüft dann nicht unbedingt SMB."
fi
mount | grep -iE "smbfs|nfs" || echo "(keine SMB/NFS-Freigabe eingebunden)"
TONMISCHUNG_SMB_TEST_DIR="$ziel" .venv/bin/python -m pytest -m smb tests/test_smb.py -v -s
