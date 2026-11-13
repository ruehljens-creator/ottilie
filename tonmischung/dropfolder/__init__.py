"""Betriebsart „Übergabeordner“ (Jens 11.11.2026): Filmordner statt Helmut-API.

Ein Wurzelordner, darin je Film ein Ordner mit AAF, MXF und der Übergabe-JSON (`uebergabe.json`, Format
„uebergabe/1“), die Helmut als letzte Datei schreibt. Ottilie listet die Filmordner, holt wie gewohnt, belegt den Film
über eine Sperrdatei im Filmordner und liefert nach „<Filmordner>/Audio Export“; danach geht eine Fertigmeldung an
einen HTTP-Dienst. `FolderClient` hat dieselbe Schnittstelle wie der Helmut-Client – Holen, Zurückliefern, Nachtrag
und Besitzprüfungen laufen unverändert.
"""
