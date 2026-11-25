"""Verbrauchsprognose: Monatsprofile (eigene Statistik oder Standardprofil) und die Verteilung des Restverbrauchs auf die Monate bis zum Vertragsende.

Reine Funktionen ohne Home Assistant: sie bekommen Werte und geben Werte zurueck, damit sie sich ohne
laufendes System testen lassen. Der Coordinator (coordinator.py) ruft sie auf.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

from .const import GAS_SPARTE
from .preise import rest_arbeitspreis_kosten


# ------------------------------------------------- Preisaenderungen im Vertrag
MONAT_TAGE = 30.44


# ------------------------------------------------------------ Verbrauchsprognose
# Standardprofile als relative Tagesintensitaet je Kalendermonat (1 = Jahresmittel), fuer
# Vertraege ohne genug eigene Statistik. Strom: Naeherung an das Haushaltsprofil (Winter etwa
# 15 % ueber, Sommer etwa 15 % unter dem Mittel). Gas: 80 % Heizung nach Gradtagzahlen
# (VDI 2067, Jahressumme 1000) plus 20 % gleichmaessiger Anteil fuer Warmwasser und Kochen.
PROFIL_STROM = {
    1: 1.15, 2: 1.08, 3: 1.02, 4: 0.93, 5: 0.88, 6: 0.85,
    7: 0.85, 8: 0.86, 9: 0.90, 10: 1.00, 11: 1.10, 12: 1.18,
}


GRADTAGE = {1: 170, 2: 150, 3: 130, 4: 80, 5: 40, 6: 13.3, 7: 13.3, 8: 13.4, 9: 30, 10: 80, 11: 120, 12: 160}


def standardprofil(sparte: str | None) -> dict[int, float]:
    if sparte == GAS_SPARTE:
        return {
            monat: 0.8 * (GRADTAGE[monat] / 1000) * 365 / calendar.monthrange(2001, monat)[1] + 0.2
            for monat in range(1, 13)
        }
    return dict(PROFIL_STROM)


def monatsprofil_aus_statistik(
    monate: dict[tuple[int, int], float]
) -> dict[int, float]:
    """Mittlerer Tagesverbrauch je Kalendermonat aus vollstaendigen Monatswerten
    {(jahr, monat): verbrauch}, ueber alle vorhandenen Jahre gemittelt."""
    summen: dict[int, list[float]] = {}
    for (jahr, monat), menge in monate.items():
        if menge is None or menge < 0:
            continue
        summen.setdefault(monat, []).append(menge / calendar.monthrange(jahr, monat)[1])
    return {monat: sum(werte) / len(werte) for monat, werte in summen.items()}


def profil_summe(profil: dict[int, float], von: date, bis: date) -> float | None:
    """Summe der Tagesintensitaeten von `von` (einschliesslich) bis `bis` (ausschliesslich).
    None, wenn ein beruehrter Kalendermonat im Profil fehlt."""
    summe = 0.0
    tag = von
    while tag < bis:
        naechster = date(tag.year + (tag.month == 12), tag.month % 12 + 1, 1)
        ende = min(naechster, bis)
        wert = profil.get(tag.month)
        if wert is None:
            return None
        summe += wert * (ende - tag).days
        tag = ende
    return summe


def prognose_abschnitte(
    profil: dict[int, float],
    beginn: date,
    heute: date,
    vertrag_ende: date,
    verbrauch_bisher: float,
) -> list[tuple[date, date, float]] | None:
    """Restverbrauch von heute bis Vertragsende je Kalendermonat, nach dem Profil verteilt und an
    den bisherigen Verbrauch angepasst: Rest = bisher x Profil(Rest) / Profil(bisher)."""
    bisher_gewicht = profil_summe(profil, beginn, heute)
    if not bisher_gewicht or bisher_gewicht <= 0:
        return None
    abschnitte: list[tuple[date, date, float]] = []
    tag = heute
    while tag < vertrag_ende:
        naechster = date(tag.year + (tag.month == 12), tag.month % 12 + 1, 1)
        ende = min(naechster, vertrag_ende)
        gewicht = profil_summe(profil, tag, ende)
        if gewicht is None:
            return None
        abschnitte.append((tag, ende, verbrauch_bisher * gewicht / bisher_gewicht))
        tag = ende
    return abschnitte


def rest_arbeitspreis_kosten_abschnitte(
    segmente: list[dict[str, Any]],
    abschnitte: list[tuple[date, date, float]],
    faktor: float,
) -> float | None:
    """Arbeitspreis-Kosten eines je Monat verteilten Restverbrauchs: jeder Monatsabschnitt
    rechnet tagesanteilig mit den in ihm gueltigen Preisabschnitten."""
    summe = 0.0
    for von, bis, menge in abschnitte:
        teil = rest_arbeitspreis_kosten(segmente, von, bis, menge * faktor)
        if teil is None:
            return None
        summe += teil
    return summe


def monat_von(beginn: date, tag: date) -> float:
    return (tag - beginn).days / MONAT_TAGE


def integral_monate(
    segmente: list[dict[str, Any]], feld: str, beginn: date, von_m: float, bis_m: float
) -> float | None:
    """Summe eines Monatsbetrags (Grundpreis, Abschlag) ueber den Zeitraum [von_m, bis_m] in
    Monaten ab Vertragsbeginn, je Abschnitt mit dessen Wert. None, wenn ein Abschnitt im
    Zeitraum keinen Wert hat."""
    if bis_m <= von_m:
        return 0.0
    summe = 0.0
    for index, segment in enumerate(segmente):
        start = 0.0 if index == 0 else monat_von(beginn, segment["von"])
        ende = (
            monat_von(beginn, segmente[index + 1]["von"])
            if index + 1 < len(segmente)
            else float("inf")
        )
        anteil = min(ende, bis_m) - max(start, von_m)
        if anteil <= 0:
            continue
        wert = segment[feld]
        if wert is None:
            return None
        summe += wert * anteil
    return summe
