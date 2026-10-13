"""Presets der Havarie-Auslieferung: Sendeprofil, Werkzeug, Startband, Dateiname (Plan M5).

Übrig aus `core/delivery_plan.py` (Exportplan und Sendefile-Bündelung nach dem Prototyp): Der Plan wurde nur von Tests
benutzt und widersprach bei der Kanalbelegung der Havarie-Auslieferung; Jens 09.10.2026: auf die Presets reduziert.
Die Kanalbelegung steht in `workflow.havarie_delivery.channel_tracks`.
"""
from __future__ import annotations

# ANNAHME: Presets wie im Prototyp, solange Helmut bzw. die Einstellungen keine liefern. Bild und Ton beschreibt
# das Profil (`delivery.profiles`), hier stehen nur Auswahl und Schalter.
DEFAULT_PRESETS = [
    {"id": "xdcam16", "name": "ARD/ZDF HD (XDCAM HD422)", "mxf_profil": "ARD_ZDF_HDF01b", "modus": "auto",
     "werkzeug": "bmx", "afd": 8, "monospuren": True, "startband": "entfernen",
     "dateiname_muster": "{sendung}_{datum}_{titel}_v{version}"},
    {"id": "xdcam8", "name": "ARD/ZDF HD 8 Kanäle", "mxf_profil": "ARD_ZDF_HDF01a", "modus": "auto",
     "werkzeug": "bmx", "afd": 8, "monospuren": True, "startband": "entfernen",
     "dateiname_muster": "{sendung}_{datum}_{titel}_8ch"},
    {"id": "avci", "name": "AVC-Intra 100", "mxf_profil": "ARD_ZDF_HDF02b", "modus": "recode",
     "werkzeug": "bmx", "afd": 8, "monospuren": True, "startband": "entfernen",
     "dateiname_muster": "{sendung}_{datum}_{titel}_avci"},
    # Jens 04.10.2026 („mach ein Preset daraus“): live mit dem DaVinci-Paket (DNxHD 720p50) geprüft – Sendefile,
    # Prüfung, Schreibtisch und USB. ANNAHME: HDF03 nicht gegen eine IRT-Referenz geprüft (Struktur wie HDF-02b)
    {"id": "avci720", "name": "AVC-Intra 100 720p50", "mxf_profil": "ARD_ZDF_HDF03b", "modus": "auto",
     "werkzeug": "bmx", "afd": 8, "monospuren": True, "startband": "entfernen",
     "dateiname_muster": "{sendung}_{datum}_{titel}_avci720"},
]
