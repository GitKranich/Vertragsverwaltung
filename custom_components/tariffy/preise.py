"""Preise eines Vertrags: Staffelpreise, Preisaenderungen im laufenden Vertrag (Segmente) und die Kosten nach Arbeitspreis.

Reine Funktionen ohne Home Assistant: sie bekommen Werte und geben Werte zurueck, damit sie sich ohne
laufendes System testen lassen. Der Coordinator (coordinator.py) ruft sie auf.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .const import (
    CONF_ABSCHLAG,
    CONF_ARBEITSPREIS,
    CONF_GRUNDPREIS,
    CONF_PREISAENDERUNGEN,
    CONF_TIER_LIMITS,
    CONF_TIER_PRICES,
    MAX_TIERS,
)
from .werte import f, parse_date


def tiered_cost(verbrauch: float, limits: list, prices: list) -> float | None:
    """Berechnet gestaffelte Kosten für gegebenen Jahresverbrauch.

    limits: Obergrenzen je Block (letzter Block = unbegrenzt wenn None)
    prices: Preis je Block
    Beispiel: limits=[5,10], prices=[0.5,0.8,1.2]
    → Block1: 0-5, Block2: 5-10, Block3: >10
    """
    if not limits or not prices:
        return None
    total = 0.0
    remaining = verbrauch
    prev = 0.0
    for i, price in enumerate(prices):
        if remaining <= 0:
            break
        if i < len(limits) and limits[i] is not None:
            block_size = min(remaining, limits[i] - prev)
            total += block_size * price
            remaining -= block_size
            prev = limits[i]
        else:
            # Letzter Block: unbegrenzt
            total += remaining * price
            remaining = 0
    return round(total, 2)


def extract_tiers(data: dict) -> tuple[list, list]:
    """Extrahiert Tier-Listen aus entry.data (tier_limit_0..N, tier_price_0..N)."""
    limits = []
    prices = []
    # Zuerst prüfen ob alte Listen-Form vorhanden
    if data.get(CONF_TIER_LIMITS):
        return list(data[CONF_TIER_LIMITS]), list(data[CONF_TIER_PRICES] or [])
    # Neue Form: einzelne Felder
    for i in range(MAX_TIERS):
        lim = f(data.get(f"tier_limit_{i}"))
        prc = f(data.get(f"tier_price_{i}"))
        if prc is None:
            break
        limits.append(lim)  # None = unbegrenzt
        prices.append(prc)
    return limits, prices


PREIS_FELDER = ("arbeitspreis", "grundpreis", "abschlag")


def preis_segmente(data: dict[str, Any], beginn: date) -> list[dict[str, Any]]:
    """Zeitabschnitte des Vertrags mit den jeweils gueltigen Preisen.

    Der erste Abschnitt beginnt am Vertragsbeginn mit den Werten aus den Vertragsdaten; jede
    Preisaenderung (CONF_PREISAENDERUNGEN) beginnt einen neuen Abschnitt. Ein leerer Wert in
    einer Preisaenderung uebernimmt den Wert des vorherigen Abschnitts. Aenderungen am oder vor
    dem Vertragsbeginn werden ignoriert (sie gehoeren zu keinem Abschnitt dieses Vertrags)."""
    basis = {
        "von": beginn,
        "arbeitspreis": f(data.get(CONF_ARBEITSPREIS)),
        "grundpreis": f(data.get(CONF_GRUNDPREIS)),
        "abschlag": f(data.get(CONF_ABSCHLAG)),
        "zaehlerstand": None,
    }
    aenderungen = []
    for item in data.get(CONF_PREISAENDERUNGEN) or []:
        if not isinstance(item, dict):
            continue
        tag = parse_date(item.get("datum"))
        if tag is None or tag <= beginn:
            continue
        aenderungen.append((tag, item))
    aenderungen.sort(key=lambda paar: paar[0])
    segmente = [basis]
    for tag, item in aenderungen:
        vorher = segmente[-1]
        segment = {"von": tag, "zaehlerstand": f(item.get("zaehlerstand"))}
        for feld in PREIS_FELDER:
            wert = f(item.get(feld))
            segment[feld] = wert if wert is not None else vorher[feld]
        if vorher["von"] == tag:  # zwei Aenderungen am selben Tag: die spaetere zaehlt
            segmente[-1] = segment
        else:
            segmente.append(segment)
    return segmente


def segment_am(segmente: list[dict[str, Any]], tag: date) -> dict[str, Any]:
    """Der am `tag` gueltige Abschnitt (vor dem ersten Abschnitt: der erste)."""
    aktiv = segmente[0]
    for segment in segmente:
        if segment["von"] <= tag:
            aktiv = segment
    return aktiv


def arbeitspreis_kosten(
    segmente: list[dict[str, Any]],
    grenzen: list[float],
    faktor: float,
) -> float | None:
    """Arbeitspreis-Kosten des bisherigen Verbrauchs: `grenzen` ist der kumulierte Verbrauch
    seit Vertragsbeginn zu Beginn jedes Abschnitts, mit dem Verbrauch bis heute als letztem
    Wert (len(grenzen) == Anzahl der bisherigen Abschnitte + 1)."""
    summe = 0.0
    for index in range(len(grenzen) - 1):
        menge = grenzen[index + 1] - grenzen[index]
        if menge == 0:
            continue
        preis = segmente[index]["arbeitspreis"]
        if preis is None:
            return None
        summe += menge * faktor * preis
    return summe


def rest_arbeitspreis_kosten(
    segmente: list[dict[str, Any]],
    heute: date,
    vertrag_ende: date,
    rest_menge: float,
) -> float | None:
    """Arbeitspreis-Kosten des fuer die Restlaufzeit hochgerechneten Verbrauchs, gleichmaessig
    auf die verbleibenden Tage verteilt; ein kuenftiger Abschnitt rechnet ab seinem Datum mit
    seinem Preis."""
    rest_tage = (vertrag_ende - heute).days
    if rest_tage <= 0 or rest_menge <= 0:
        return 0.0
    summe = 0.0
    for index, segment in enumerate(segmente):
        start = segment["von"] if index else date.min
        ende = segmente[index + 1]["von"] if index + 1 < len(segmente) else date.max
        tage = (min(ende, vertrag_ende) - max(start, heute)).days
        if tage <= 0:
            continue
        if segment["arbeitspreis"] is None:
            return None
        summe += rest_menge * tage / rest_tage * segment["arbeitspreis"]
    return summe


def preisaenderungen_anzeige(
    data: dict[str, Any], beginn: date | None, heute: date
) -> list[dict[str, Any]]:
    """Die gespeicherten Preisaenderungen fuer Panel und Attribute, in gespeicherter Reihenfolge
    (der Index ist der zum Bearbeiten), mit den ab dem Datum gueltigen Werten und dem Status
    (aktiv, kuenftig, vergangen, ungueltig = am oder vor dem Vertragsbeginn)."""
    liste = [item for item in data.get(CONF_PREISAENDERUNGEN) or [] if isinstance(item, dict)]
    if not liste:
        return []
    segmente = preis_segmente(data, beginn) if beginn is not None else []
    aktiv = segment_am(segmente, heute) if segmente else None
    ergebnis = []
    for index, item in enumerate(liste):
        tag = parse_date(item.get("datum"))
        segment = next((s for s in segmente[1:] if s["von"] == tag), None)
        if segment is None:
            status = "ungueltig"
        elif segment is aktiv:
            status = "aktiv"
        else:
            status = "kuenftig" if tag > heute else "vergangen"
        ergebnis.append(
            {
                "index": index,
                "datum": tag.isoformat() if tag else None,
                **{feld: f(item.get(feld)) for feld in (*PREIS_FELDER, "zaehlerstand")},
                "gilt": {feld: segment[feld] for feld in PREIS_FELDER} if segment else None,
                "status": status,
            }
        )
    return ergebnis
