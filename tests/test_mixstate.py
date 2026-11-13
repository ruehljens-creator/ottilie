import pytest

from tonmischung.core.mixstate import (
    ConflictError, MixState, after_release, after_take, check_take, with_state)

FREI = MixState()
MEINER = MixState(state="in_mischung", mixer="Jens", room="Ton 3", session_path="/S/B.ptx")
FREMD = MixState(state="importiert", mixer="A. Wolf", room="Ton 2")


def test_freier_beitrag_darf_uebernommen_werden():
    check_take(FREI, locked=False, mixer="Jens", room="Ton 3")
    neu = after_take(FREI, "Jens", "Ton 3", "2026-10-02T10:00:00+00:00")
    assert (neu.state, neu.mixer, neu.room, neu.taken_at) == (
        "uebernommen", "Jens", "Ton 3", "2026-10-02T10:00:00+00:00")


def test_fremd_uebernommen_ist_konflikt():
    with pytest.raises(ConflictError, match="A. Wolf"):
        check_take(FREMD, locked=True, mixer="Jens", room="Ton 3")


def test_gesperrt_ohne_eintrag_ist_kein_konflikt_mehr():
    """Früher Konflikt; Jens 11.11.2026: Sperre ohne Besitzer darf übernommen werden (siehe unten)."""
    check_take(FREI, locked=True, mixer="Jens", room="Ton 3")


def test_erneut_uebernehmen_durch_denselben_ist_kein_konflikt():
    check_take(MEINER, locked=True, mixer="Jens", room="Ton 3")
    assert after_take(MEINER, "Jens", "Ton 3", "später") == MEINER


def test_derselbe_mischer_anderer_raum_ist_konflikt():
    with pytest.raises(ConflictError):
        check_take(MEINER, locked=True, mixer="Jens", room="Ton 1")


def test_zurueckgeben_haelt_grund_und_herkunft_fest_und_behaelt_session():
    neu = after_release(MEINER, "Workstation abgestürzt", "jetzt")
    assert (neu.state, neu.mixer, neu.room) == ("zurueckgegeben", "", "")
    assert (neu.return_reason, neu.returned_from_room) == ("Workstation abgestürzt", "Ton 3")
    assert neu.session_path == "/S/B.ptx"
    check_take(neu, locked=False, mixer="A. Wolf", room="Ton 2")


def test_nicht_uebernommen_kann_nicht_zurueckgegeben_werden():
    with pytest.raises(ConflictError):
        after_release(FREI, "versehentlich", "jetzt")


def test_unbekannter_zustand():
    with pytest.raises(ValueError):
        with_state(MEINER, "fertig")


def test_sperre_ohne_besitzer_darf_uebernommen_werden():
    """Jens 11.11.2026: gesperrt, aber niemandem zugeordnet (Sperre kam an, Antwort nicht) – übernehmen erlauben."""
    check_take(FREI, locked=True, mixer="Jens", room="Ton 3")
    check_take(MixState(state="zurueckgegeben"), locked=True, mixer="Jens", room="Ton 3")   # Freigeben leert Mischer
    for mixer in ("A. Wolf", "Jens"):        # „ausgeliefert“ mit Mischer bleibt gesperrt (z. B. Cutter sperrt danach)
        with pytest.raises(ConflictError, match=mixer):
            check_take(MixState(state="ausgeliefert", mixer=mixer, room="Ton 3"), locked=True, mixer="Jens",
                       room="Ton 3")


def test_havarie_blocker():
    from tonmischung.core.mixstate import havarie_blocker
    assert havarie_blocker(FREI, "Jens", "Ton 3") == ""
    assert "schon normal übernommen" in havarie_blocker(MEINER, "Jens", "Ton 3")
    assert havarie_blocker(MEINER, "Jens", "Ton 3", replay=True) == ""             # eigene, schon angekommene
    assert "A. Wolf" in havarie_blocker(FREMD, "Jens", "Ton 3", replay=True)
    eigen = MixState(state="ausgeliefert", mixer="Jens", room="Ton 3")
    assert havarie_blocker(eigen, "Jens", "Ton 3") == ""                           # gestern selbst ausgeliefert
    assert "ausgeliefert" in havarie_blocker(MixState(state="ausgeliefert", mixer="B", room="Ton 2"), "Jens", "Ton 3")
