"""macOS-Dateidialoge an einem Ort (Review C); der Einstellungsexport zeigte „Spurprofile exportieren“."""
import types

from tonmischung.app import choosers


def test_skripte_und_abbrechen(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[-1])
        return types.SimpleNamespace(returncode=1 if "folder" in cmd[-1] else 0, stdout="/Users/x/Einstellungen.json\n")
    monkeypatch.setattr(choosers.subprocess, "run", run)
    assert choosers.choose_save('Einstellungen "neu" exportieren', "Ottilie.json") == "/Users/x/Einstellungen.json"
    assert choosers.choose_file("Spurprofile importieren", ("public.json",)) == "/Users/x/Einstellungen.json"
    assert choosers.choose_folder("Übergabepaket wählen") == ""                         # abgebrochen
    assert calls == ['POSIX path of (choose file name with prompt "Einstellungen \'neu\' exportieren" '
                     'default name "Ottilie.json")',
                     'POSIX path of (choose file of type {"public.json"} with prompt "Spurprofile importieren")',
                     'POSIX path of (choose folder with prompt "Übergabepaket wählen")']


def test_einstellungsexport_mit_eigener_beschriftung(monkeypatch):
    from tonmischung.app import settings_transfer_app as STA
    asked = []
    monkeypatch.setattr(STA, "choose_save", lambda name, prompt: asked.append((name, prompt)) or "")
    settings = types.SimpleNamespace(local_values=lambda: {"a": 1}, user_values=lambda: {}, user_active=False,
                                     is_user_path=lambda p: False)
    ctx = types.SimpleNamespace(room="Ton 3", settings=settings)
    assert STA.export_all(ctx)["message"] == "Export abgebrochen"
    assert asked == [("Ottilie Einstellungen Ton 3.json", "Einstellungen exportieren")]
