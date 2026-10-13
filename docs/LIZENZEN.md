# Ottilie 0.1 – mitgelieferte Software

Erzeugt mit `python3 -m tonmischung lizenzen`. Lizenz, Urheber, Projekt und – bei Werkzeugen und Bibliotheken – der Quelltext genau der mitgelieferten Version (unverändert). Die Lizenztexte liegen im Paket unter `bin/licenses/`, die der Schrift unter `ui/fonts/`.

## Grundlagen

| Software | Version | Lizenz | Urheber | Projekt | Quelltext | Wofür |
|---|---|---|---|---|---|---|
| Python | 3.14.4 | PSF-2.0 | Python Software Foundation | https://www.python.org | – | Laufzeit der App |
| PyInstaller | 6.19.0 | GPL-2.0-or-later mit Bootloader-Ausnahme | Hartmut Goebel, Giovanni Bajo u. a. | https://pyinstaller.org | – | Paketbau (Startprogramm der App) – Die Ausnahme erlaubt das Mitliefern des Startprogramms ohne GPL-Pflichten für die App. |
| PunchBuddy | 2.1.1 | MIT | Jens Rühl (ruehljens-creator) | https://github.com/ruehljens-creator/punchbuddy-protools | – | Punch-In, Transport, Exporte, Lautheit, Stream Deck, Vocaster |
| Hedwig (AAF-Erkennung) | – | alle Rechte vorbehalten | Jens Rühl | – | – | AAF-Triage: Paare, Inhalt |
| Pro Tools Scripting Library (PTSL) | – | Avid PTSL SDK | Avid Technology, Inc. | https://developer.avid.com | – | Steuerung von Pro Tools |

## Python-Pakete

| Software | Version | Lizenz | Urheber | Projekt | Quelltext | Wofür |
|---|---|---|---|---|---|---|
| py-ptsl | 601.0.0 | BSD-3-Clause | Jamie Hardt | https://github.com/iluvcapra/py-ptsl | – | PTSL-Anbindung |
| grpcio | 1.76.0 | Apache-2.0 | The gRPC Authors | https://grpc.io | – | Verbindung zu Pro Tools (gRPC) |
| protobuf | 6.31.1 | BSD-3-Clause | Google LLC | https://protobuf.dev | – | PTSL-Nachrichten |
| pyaaf2 | 1.7.1 | MIT | Mark Reid | https://github.com/markreidvfx/pyaaf2 | – | AAF lesen und schreiben |
| NumPy | 2.4.4 | BSD-3-Clause (mit 0BSD, MIT, Zlib) | NumPy Developers | https://numpy.org | – | Signalanalyse (Triage, Lautheit) |
| SciPy | 1.17.1 | BSD-3-Clause | SciPy Developers | https://scipy.org | – | Signalanalyse (Triage) |
| python-soundfile | 0.13.1 | BSD-3-Clause | Bastian Bechtold | https://github.com/bastibe/python-soundfile | – | WAV lesen und schreiben (mit libsndfile, LGPL-2.1) |
| cffi | 2.0.0 | MIT | Armin Rigo, Maciej Fijalkowski | https://cffi.readthedocs.io | – | für python-soundfile |
| pyloudnorm | 0.2.0 | MIT | Christian Steinmetz | https://github.com/csteinmetz1/pyloudnorm | – | Lautheit nach EBU R 128 |
| rumps | 0.4.0 | BSD-3-Clause | Jared Suttles | https://github.com/jaredks/rumps | – | Menüleiste |
| PyObjC | 12.2 | MIT | Ronald Oussoren u. a. | https://github.com/ronaldoussoren/pyobjc | – | Fenster, Dialoge, Bonjour, Schlüsselbund (macOS) |
| PyUSB | 1.3.1 | BSD-3-Clause | Jonas Malaco u. a. | https://pyusb.github.io/pyusb | – | Focusrite Vocaster |
| hidapi (cython-hidapi) | 0.15.0 | BSD-3-Clause (wahlweise GPL-3.0/HIDAPI) | Pavol Rusnak u. a. | https://github.com/trezor/cython-hidapi | – | Stream Deck / Vocaster |
| MCP Python SDK | 1.27.2 | MIT | Anthropic, PBC | https://modelcontextprotocol.io | – | Schnittstelle für KI-Assistenten (optional) |

## Werkzeuge (Havarie-Auslieferung)

| Software | Version | Lizenz | Urheber | Projekt | Quelltext | Wofür |
|---|---|---|---|---|---|---|
| FFmpeg | 9.0.1 | GPL-3.0-or-later (dieser Build: --enable-gpl --enable-version3) | the FFmpeg developers | https://ffmpeg.org | https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz | Sendefile bauen, prüfen (ffmpeg, ffprobe) – GPL wegen x264/x265 (AVC-Intra-Kodierung). Unverändert; Quelltext genau dieser Version unter „Quelltext“, Lizenztexte im Paket (bin/licenses). |
| MediaInfo | 26.05 | BSD-2-Clause | MediaArea.net SARL | https://mediaarea.net/MediaInfo | https://mediaarea.net/download/source/mediainfo/26.05/mediainfo_26.05.tar.xz | Sendefile prüfen |
| bmx | 1.7.0 | BSD-3-Clause | British Broadcasting Corporation (BBC R&D), weitergeführt von der EBU | https://github.com/ebu/bmx | https://github.com/ebu/bmx/tree/v1.7 | MXF nach ARD-ZDF-HDF verpacken (bmxtranswrap, mxf2raw) |

## Bibliotheken in den Werkzeugen

| Software | Version | Lizenz | Urheber | Projekt | Quelltext | Wofür |
|---|---|---|---|---|---|---|
| x264 | r3222 | GPL-2.0-or-later | VideoLAN / x264 project | https://www.videolan.org/developers/x264.html | https://code.videolan.org/videolan/x264.git | AVC-Intra kodieren (in FFmpeg) |
| x265 | 4.3 | GPL-2.0-or-later | MulticoreWare Inc. | https://www.x265.org | https://github.com/Multicorewareinc/x265/releases/download/4.3/x265_4.3.tar.gz | in FFmpeg |
| libvpx | 1.17.0 | BSD-3-Clause | The WebM Project | https://www.webmproject.org | https://github.com/webmproject/libvpx/archive/refs/tags/v1.17.0.tar.gz | in FFmpeg |
| dav1d | 1.5.4 | BSD-2-Clause | VideoLAN | https://code.videolan.org/videolan/dav1d | https://code.videolan.org/videolan/dav1d/-/archive/1.5.4/dav1d-1.5.4.tar.bz2 | in FFmpeg |
| SVT-AV1 | 4.2.0 | BSD-3-Clause-Clear | Alliance for Open Media | https://gitlab.com/AOMediaCodec/SVT-AV1 | https://gitlab.com/AOMediaCodec/SVT-AV1/-/archive/v4.2.0/SVT-AV1-v4.2.0.tar.bz2 | in FFmpeg |
| LAME | 4.0 | LGPL-2.0-or-later | The LAME Project | https://lame.sourceforge.io | https://downloads.sourceforge.net/project/lame/lame/4.0/lame-4.0.tar.gz | in FFmpeg |
| mpg123 | 1.33.7 | LGPL-2.1 | Michael Hipp, Thomas Orgis u. a. | https://www.mpg123.de | https://www.mpg123.de/download/mpg123-1.33.7.tar.bz2 | in FFmpeg |
| Opus | 1.6.1 | BSD-3-Clause | Xiph.Org Foundation u. a. | https://opus-codec.org | https://ftp.osuosl.org/pub/xiph/releases/opus/opus-1.6.1.tar.gz | in FFmpeg |
| libvmaf | 3.2.1 | BSD-2-Clause-Patent | Netflix, Inc. | https://github.com/Netflix/vmaf | https://github.com/Netflix/vmaf/archive/refs/tags/v3.2.1.tar.gz | in FFmpeg |
| OpenSSL | 3.6.4 | Apache-2.0 | The OpenSSL Project | https://www.openssl.org | https://github.com/openssl/openssl/releases/download/openssl-3.6.4/openssl-3.6.4.tar.gz | in FFmpeg |
| XZ Utils (liblzma) | 5.8.4 | 0BSD | Lasse Collin u. a. | https://tukaani.org/xz/ | https://github.com/tukaani-project/xz/releases/download/v5.8.4/xz-5.8.4.tar.gz | in FFmpeg |
| MediaInfoLib | 26.05 | BSD-2-Clause | MediaArea.net SARL | https://mediaarea.net/MediaInfo | https://mediaarea.net/download/source/libmediainfo/26.05/libmediainfo_26.05.tar.xz | in MediaInfo |
| ZenLib | 0.4.41 | Zlib | MediaArea.net SARL | https://github.com/MediaArea/ZenLib | https://mediaarea.net/download/source/libzen/0.4.41/libzen_0.4.41.tar.bz2 | in MediaInfo |
| uriparser | 1.0.2 | BSD-3-Clause | Weijia Song, Sebastian Pipping | https://uriparser.github.io | https://github.com/uriparser/uriparser/releases/download/uriparser-1.0.2/uriparser-1.0.2.tar.bz2 | in bmx |

## Schrift

| Software | Version | Lizenz | Urheber | Projekt | Quelltext | Wofür |
|---|---|---|---|---|---|---|
| IBM Plex Sans / Mono | Sans 1.1.0 · Mono 2.5.0 | OFL-1.1 | IBM Corp. (Mike Abbink, Bold Monday) | https://github.com/IBM/plex | – | Hausschrift der Oberfläche (Lizenz: ui/fonts/LICENSE-IBM-Plex.txt) |

