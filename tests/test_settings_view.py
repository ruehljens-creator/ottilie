

def test_keine_entwicklernotizen_in_der_oberflaeche():
    """Hinweise erscheinen im Fenster: keine offenen Punkte, M0-Vorschläge oder alte Namen (Review 09.10.2026)."""
    import json
    from pathlib import Path
    schema = json.loads((Path(__file__).resolve().parent.parent / "tonmischung" / "config"
                         / "einstellungen.schema.json").read_text(encoding="utf-8"))
    shown = []

    def walk(node):
        if isinstance(node, dict):
            shown.extend(str(node[k]) for k in ("description", "x-hinweis", "title") if k in node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema.get("properties"))                       # die Beschreibung des Schemas selbst erscheint nicht
    walk(schema.get("$defs"))
    bad = [t for t in shown if any(w in t for w in ("PunchBuddy", "punchbuddy", "Offener Punkt", "offener Punkt",
                                                     "Vorschlag M0", "M0-Frage"))]
    assert not bad, bad
