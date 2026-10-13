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


def test_gesperrt_ohne_eintrag_ist_konflikt():
    with pytest.raises(ConflictError):
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
