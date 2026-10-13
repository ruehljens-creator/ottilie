# PyInstaller-Spec für Ottilie.app (Auftrag M9: Paketbau). Bauen: tools/build_app.sh
# Eine Architektur (die des bauenden Macs); Universal: tools/build_universal.sh fügt arm64 und x86_64 zusammen.
# Signatur folgt mit dem Zertifikat (festes Zertifikat, Auftrag M9).
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.dirname(SPECPATH)
sys.path.insert(0, ROOT)
from tonmischung import __version__  # noqa: E402
from tonmischung.paths import BUNDLE_ID  # noqa: E402
from tonmischung.selftest import min_macos, version_text  # noqa: E402

datas = [
    (os.path.join(ROOT, "tonmischung", "app", "ui"), "tonmischung/app/ui"),
    (os.path.join(ROOT, "tonmischung", "config", "einstellungen.schema.json"), "tonmischung/config"),
    (os.path.join(ROOT, "tonmischung", "resources"), "tonmischung/resources"),
    # PunchBuddy wird zur Laufzeit aus vendor/ importiert (tonmischung.vendor) – als Quelltext mitliefern
    (os.path.join(ROOT, "vendor", "punchbuddy", "punchbuddy"), "vendor/punchbuddy/punchbuddy"),
    (os.path.join(ROOT, "vendor", "punchbuddy", "vocaster_control.py"), "vendor/punchbuddy"),
    (os.path.join(ROOT, "vendor", "punchbuddy", "vocaster_integration.py"), "vendor/punchbuddy"),
    (os.path.join(ROOT, "vendor", "hedwig"), "vendor/hedwig"),
]
# Bauangabe (Stand, Paketliste, Architektur) aus tools/build_app.sh
if os.path.isfile(os.environ.get("TM_BUILD_INFO", "")):
    datas.append((os.environ["TM_BUILD_INFO"], "tonmischung"))
# Mitgelieferte Werkzeuge (ffmpeg, ffprobe, mediainfo, bmx) – vorher tools/bundle_tools.py → build/bin
if os.path.isdir(os.path.join(ROOT, "build", "bin")):
    datas.append((os.path.join(ROOT, "build", "bin"), "bin"))
binaries = []
hiddenimports = ["ptsl.PTSL_pb2", "ptsl.ops", "scipy.signal", "scipy.ndimage", "numpy", "rumps", "usb", "hid",
                 "Quartz", "AppKit", "Foundation", "WebKit", "Security", "objc", "PyObjCTools.AppHelper"]
for pkg in ("soundfile", "pyloudnorm", "aaf2"):            # pywebview nutzt die App nicht (eigenes WKWebView)
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
for pkg in ("ptsl", "grpc", "tonmischung"):                 # pynput nur in PunchBuddy-Tests, nicht im Betrieb
    hiddenimports += collect_submodules(pkg)

a = Analysis([os.path.join(SPECPATH, "tonmischung_start.py")], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, noarchive=False,
             # nicht im Betrieb gebraucht (siehe „Über Ottilie“): optionale Hedwig-Dekodierung, alte Fensterbibliothek
             excludes=["tkinter", "pytest", "av", "imageio_ffmpeg", "webview", "pynput"])


def required_macos():
    """Höchste macOS-Anforderung aller mitgelieferten Programme und Bibliotheken (Homebrew baut für die macOS-Version
    des bauenden Macs, numpy/scipy verlangen macOS 14) – statt einer festen Angabe, die nicht stimmt."""
    paths = [entry[1] for entry in a.binaries]
    for dp, _d, files in os.walk(os.path.join(ROOT, "build", "bin")):
        paths += [os.path.join(dp, f) for f in files]
    return version_text(max([v for v in (min_macos(p) for p in paths) if v] + [(13, 0, 0)]))


MIN_MACOS = required_macos()
print(f"Ottilie braucht macOS {MIN_MACOS} oder neuer (höchste Anforderung der mitgelieferten Programme)")
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Ottilie", console=False, debug=False,
          strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Ottilie")
app = BUNDLE(coll, name="Ottilie.app", bundle_identifier=BUNDLE_ID, version=__version__,
             icon=os.path.join(ROOT, "tonmischung", "resources", "Ottilie.icns"),          # Ottilie-Logo (Jens 08.10.2026)
             info_plist={
                 "CFBundleName": "Ottilie",
                 "CFBundleDisplayName": "Ottilie",
                 "CFBundleShortVersionString": __version__,
                 "LSMinimumSystemVersion": MIN_MACOS,
                 "NSHighResolutionCapable": True,
                 # Dialog „Missing Files“ in Pro Tools beantworten (System Events), Pro Tools steuern
                 "NSAppleEventsUsageDescription": "Ottilie beantwortet Dialoge in Pro Tools nach Ihren "
                                                  "Einstellungen (z. B. „Missing Files“).",
                 "NSLocalNetworkUsageDescription": "Ottilie zeigt den Raumstatus der anderen Mischräume (Bonjour).",
                 "NSBonjourServices": ["_ottilie._tcp"],
             })
