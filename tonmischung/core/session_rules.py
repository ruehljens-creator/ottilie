"""Regeln vor dem Import als reine Entscheidungsfunktion (ohne Ein-/Ausgabe).

Ablauf nach Funktionsbeschreibung S. 36:
keine Session offen → anlegen; offen und ausgeliefert → speichern, schließen;
offen und nicht ausgeliefert → nachfragen, bei Ja speichern, schließen, bei Nein
Abbruch ohne Änderung. Gespeichert wird immer vor dem Schließen; ob das Speichern
geklappt hat, prüft die Ausführung (sessions.runner), nicht diese Funktion.
"""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


def same_session(a: str | None, b: str | None) -> bool:
    """Dieselbe Session-Datei? Pro Tools und Helmut schreiben Umlaute mal zerlegt (NFD), mal zusammengesetzt (NFC) –
    roh verglichen galt „Gespräch.ptx“ als andere Session, und die App fragte nach dem Schließen (Review B19)."""
    if not a or not b:
        return False

    def norm(p: str) -> str:
        return unicodedata.normalize("NFC", os.path.normpath(p))
    return norm(a) == norm(b)


class Step(Enum):
    WAIT_TRANSPORT = "warten, bis Wiedergabe/Aufnahme steht"
    SAVE = "Session speichern"
    CLOSE = "Session schließen"


class Outcome(Enum):
    PROCEED = "weiter"            # Schritte ausführen, dann importieren
    ASK = "nachfragen"            # Mischer fragen: speichern, schließen und importieren?
    ABORT = "abbrechen"           # nichts ändern


@dataclass(frozen=True)
class OpenSessionState:
    path: str
    delivered: bool               # Beitrag der offenen Session ist ausgeliefert


@dataclass(frozen=True)
class ImportPlan:
    outcome: Outcome
    steps: tuple[Step, ...] = field(default_factory=tuple)
    reason: str = ""


CLEAR_PRO_TOOLS = (Step.WAIT_TRANSPORT, Step.SAVE, Step.CLOSE)


def decide_before_import(open_session: OpenSessionState | None, *,
                         target_session_path: str | None = None,
                         confirmed: bool | None = None,
                         unattended: bool = False) -> ImportPlan:
    """Was vor einem Import mit der offenen Session geschieht.

    confirmed:  None = noch nicht gefragt, True/False = Antwort des Mischers.
    unattended: Auslösung per Stream Deck/Webtrigger ohne Rückfrage – vorbereitet, noch nicht angebunden (die
                Einstellung „Countdown“ ist noch ohne Funktion).
    """
    if open_session is None:
        return ImportPlan(Outcome.PROCEED, (), "keine Session offen")
    if target_session_path and same_session(open_session.path, target_session_path):
        return ImportPlan(Outcome.PROCEED, (), "die richtige Session ist schon offen")
    if open_session.delivered:
        return ImportPlan(Outcome.PROCEED, CLEAR_PRO_TOOLS, "offene Session ist ausgeliefert")
    if unattended:
        return ImportPlan(Outcome.PROCEED, CLEAR_PRO_TOOLS, "Auslösung ohne Rückfrage")
    if confirmed is None:
        return ImportPlan(Outcome.ASK, (), "offene Session ist noch nicht ausgeliefert")
    if confirmed:
        return ImportPlan(Outcome.PROCEED, CLEAR_PRO_TOOLS, "Mischer hat bestätigt")
    return ImportPlan(Outcome.ABORT, (), "Mischer hat abgelehnt – nichts geändert")
