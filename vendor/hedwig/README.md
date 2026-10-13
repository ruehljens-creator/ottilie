# Hedwig – übernommene Module

Kopie aus Jens' Projekt Hedwig (privat, `github.com/ruehljens-creator/Hedwig`, Ordner `python/`),
Stand Commit `b36dd743c4e9b6acec6ec6ba1408247f92a406a3` (30.07.2026). **Unverändert** bis auf neutrale
Kommentare (13.10.2026: kein Sendername) – Fehler und Verbesserungen gehören zuerst nach Hedwig, dann hierher neu
kopieren.

| Datei | Zweck |
|---|---|
| `hedwig_aaf_essenz.py` | SourceClip → Essenz (eingebettet PCM/WAVE, verlinkte MXF/WAV), Gains |
| `hedwig_klassifikation.py` | Sprache / Atmo / Musik / Stille aus dem Signal |
| `hedwig_mxf.py`, `hedwig_wavio.py` | MXF- und WAV-Lesen (von `hedwig_aaf_essenz` gebraucht) |
| `hedwig_log.py` | **eigener Ersatz** (nicht aus Hedwig): Meldungen an Pythons `logging` |

Einbinden: `tonmischung.vendor.use_hedwig()` legt diesen Ordner in den Suchpfad.
Abhängigkeiten: numpy, pyaaf2 (vorhanden). FFmpeg nur für verlinkte Medien außerhalb PCM/MXF.
