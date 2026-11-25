"""Abschlaege und Einmalposten: gezahlte Abschlaege bis heute, Abbuchungen ab der ersten Abschlagszahlung, Abschlags-Segmente und Boni.

Reine Funktionen ohne Home Assistant: sie bekommen Werte und geben Werte zurueck, damit sie sich ohne
laufendes System testen lassen. Der Coordinator (coordinator.py) ruft sie auf.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .const import (
    CONF_ABSCHLAG_AB_DATUM,
    CONF_ABSCHLAG_VORHERIGER_WERT,
    CONF_EINMALPOSTEN,
    CONF_ERSTE_ZAHLUNG,
)
from .werte import f, parse_date, plus_months
from .preise import segment_am


def abschlag_bisher_gezahlt(
    abschlag: float, vergangene_monate: float, beginn: date, data: dict[str, Any]
) -> float:
    """Tatsaechlich bis heute gezahlter Abschlag — beruecksichtigt eine
    Abschlags-Aenderung waehrend der laufenden Vertragslaufzeit (siehe
    config_flow.py::_stamp_abschlag_change), damit ein kuerzlich geaenderter
    Abschlag nicht rueckwirkend auf die Zeit VOR der Aenderung angewendet
    wird (sonst wuerden "Guthaben/Nachzahlung (Bisher)" und "Abschlag
    (Anpassung empfohlen)" faelschlich optimistischer/pessimistischer
    ausfallen, als tatsaechlich gezahlt wurde).
    """
    ab_datum = parse_date(data.get(CONF_ABSCHLAG_AB_DATUM))
    vorheriger_wert = data.get(CONF_ABSCHLAG_VORHERIGER_WERT)
    # Ein vorheriger Wert von 0 heisst: vorher war kein Abschlag eingetragen (aeltere Staende haben
    # so einen Stempel noch); dann gilt der Betrag fuer die ganze Laufzeit.
    if ab_datum is None or not vorheriger_wert or ab_datum <= beginn:
        return abschlag * vergangene_monate
    monate_alt = (ab_datum - beginn).days / 30.44
    if monate_alt >= vergangene_monate:
        # Aenderung liegt (rechnerisch) noch nicht in der Vergangenheit —
        # konservativ den alten Wert fuer die gesamte bisherige Zeit annehmen.
        return vorheriger_wert * vergangene_monate
    monate_neu = vergangene_monate - monate_alt
    return vorheriger_wert * monate_alt + abschlag * monate_neu


def einmal_summen(
    data: dict[str, Any], beginn: date | None, heute: date
) -> tuple[list[dict[str, Any]], float, float]:
    """Einmalposten des Vertrags: (Liste, Summe bis heute ueber alle Laufzeiten, Summe bis
    heute in der laufenden Laufzeit). Positiv = Gutschrift, negativ = Rechnung. Ein Posten
    mit Datum in der Zukunft zaehlt erst ab diesem Tag; einer ohne Datum zaehlt sofort."""
    posten: list[dict[str, Any]] = []
    gesamt = 0.0
    laufend = 0.0
    for item in data.get(CONF_EINMALPOSTEN) or []:
        betrag = f(item.get("betrag")) if isinstance(item, dict) else None
        if betrag is None:
            continue
        tag = parse_date(item.get("datum"))
        posten.append(
            {
                "bezeichnung": str(item.get("bezeichnung") or "")[:100],
                "betrag": round(betrag, 2),
                "datum": tag.isoformat() if tag else None,
            }
        )
        if tag is not None and tag > heute:
            continue
        gesamt += betrag
        if tag is None or beginn is None or tag >= beginn:
            laufend += betrag
    return posten, round(gesamt, 2), round(laufend, 2)


def abschlag_nach_zahlungen(
    abschlag: float,
    intervall: int,
    beginn: date,
    ende: date | None,
    heute: date,
    data: dict[str, Any],
    segmente: list[dict[str, Any]] | None = None,
) -> tuple[float, date | None]:
    """Bis heute tatsaechlich abgebuchte Betraege bei nicht monatlicher Zahlweise (zum Beispiel eine
    Versicherung, die jaehrlich im Voraus bezahlt wird): gezaehlt wird jede Zahlung ab der ersten
    Abbuchung (leer = Vertragsbeginn), nicht ein monatlicher Anteil. Bei monatlicher Zahlung zaehlt
    eine Rate, sobald ihr Monat begonnen hat, auch wenn der Tag im Monat noch aussteht. Gibt auch die naechste Zahlung
    zurueck. Eine Preisaenderung gilt fuer Zahlungen ab ihrem Datum."""
    erste = parse_date(data.get(CONF_ERSTE_ZAHLUNG)) or beginn
    monat_heute = (heute.year, heute.month)
    ab_datum = parse_date(data.get(CONF_ABSCHLAG_AB_DATUM))
    vorher = f(data.get(CONF_ABSCHLAG_VORHERIGER_WERT))
    summe = 0.0
    zahlung = 0
    naechste: date | None = None
    while zahlung < 1200:
        termin = plus_months(erste, zahlung * intervall)
        if ende is not None and termin > ende:
            break
        if termin > heute and naechste is None:
            naechste = termin
        # Monatliche Zahlung: die Rate zaehlt, sobald ihr Monat begonnen hat (egal, an welchem Tag des
        # Monats sie liegt); sonst zaehlt jede Abbuchung am Tag der Zahlung
        if (intervall == 1 and (termin.year, termin.month) > monat_heute) or (
            intervall != 1 and termin > heute
        ):
            break
        if segmente:
            # Preis-/Beitragsaenderungen: der am Zahltag gueltige Betrag (die gemerkte
            # Abschlagsaenderung steckt schon in den Abschnitten, siehe abschlag_segmente)
            monatlich = segment_am(segmente, termin)["abschlag"] or 0.0
        else:
            monatlich = vorher if (ab_datum is not None and vorher and termin < ab_datum) else abschlag
        summe += round(monatlich * intervall, 2)
        zahlung += 1
    if naechste is None:  # die naechste Abbuchung nach heute, falls der Vertrag dann noch laeuft
        termin = plus_months(erste, zahlung * intervall)
        if termin > heute and (ende is None or termin <= ende):
            naechste = termin
    return round(summe, 2), naechste


def abschlag_segmente(
    segmente: list[dict[str, Any]], beginn: date, data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Abschnitte fuer den Abschlag. Beruecksichtigt zusaetzlich eine Abschlags-Aenderung beim
    Bearbeiten des Vertrags (CONF_ABSCHLAG_AB_DATUM, siehe abschlag_bisher_gezahlt), aber nur,
    wenn sie im ersten Abschnitt liegt: dort hat sie den Abschlag ab Vertragsbeginn geaendert."""
    ab_datum = parse_date(data.get(CONF_ABSCHLAG_AB_DATUM))
    vorher = f(data.get(CONF_ABSCHLAG_VORHERIGER_WERT))
    naechster = segmente[1]["von"] if len(segmente) > 1 else None
    if (
        ab_datum is None
        or not vorher
        or ab_datum <= beginn
        or (naechster is not None and ab_datum >= naechster)
    ):
        return segmente
    erster = segmente[0]
    return [
        {**erster, "abschlag": vorher},
        {**erster, "von": ab_datum},
        *segmente[1:],
    ]


def abschlag_zahlungen(
    ab_segmente: list[dict[str, Any]], erste: date, ende: date, heute: date
) -> tuple[float, float, int]:
    """Monatliche Abschlagszahlungen ab `erste` bis `ende`: (bis heute gezahlt, Summe ueber die
    Laufzeit, Anzahl der noch offenen Raten). Eine Rate zaehlt als gezahlt, sobald ihr Monat
    begonnen hat; ihr Betrag ist der am Zahltag gueltige Abschlag."""
    bisher = gesamt = 0.0
    offen = 0
    monat_heute = (heute.year, heute.month)
    for nummer in range(240):
        termin = plus_months(erste, nummer)
        if termin > ende:
            break
        betrag = segment_am(ab_segmente, termin)["abschlag"] or 0.0
        gesamt += betrag
        if (termin.year, termin.month) <= monat_heute:
            bisher += betrag
        else:
            offen += 1
    return round(bisher, 2), round(gesamt, 2), offen
