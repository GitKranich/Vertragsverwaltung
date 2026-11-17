"""Coordinator: Berechnung, Tarifwechsel und Kündigungs-Erinnerung."""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import loan
from .const import (
    BASIS_FELDER,
    CONF_EFFEKTIVZINS,
    CONF_KREDIT_SUMME,
    CONF_LAUFZEIT_MONATE,
    CONF_SOLLZINS,
    SPARTE_LOAN,
    CONF_ABSCHLAG,
    CONF_ABSCHLAG_AB_DATUM,
    CONF_ABSCHLAG_VORHERIGER_WERT,
    CONF_ABSCHLAG_WARNUNG,
    CONF_ABSCHLAG_WARNUNG_BESTAETIGT,
    CONF_ABSCHLAG_WARNUNG_NOTIFY_GESENDET,
    CONF_ABSCHLAG_WARNUNG_SCHWELLE,
    DEFAULT_ABSCHLAG_WARNUNG_SCHWELLE,
    DEFAULT_ZAHLWEISE,
    HISTORIE_MAX,
    METERED_SPARTEN,
    NOTIFY_ID_ABSCHLAG_PREFIX,
    CONF_ANBIETER,
    CONF_ARBEITSPREIS,
    CONF_BEGINN,
    CONF_BONUS,
    CONF_SOFORTBONUS,
    ENERGIE_SPARTEN,
    CONF_BRENNWERT,
    CONF_ENDE,
    CONF_ERINNERUNG_MONATE,
    CONF_KUENDIGUNGSFRIST,
    CONF_MINDESTLAUFZEIT,
    CONF_ERSTE_ZAHLUNG,
    CONF_ARCHIVIERT,
    CONF_GAS_EINHEIT,
    CONF_GRUNDPREIS,
    CONF_AUSSENZAEHLER,
    CONF_EINMALPOSTEN,
    CONF_PREISAENDERUNGEN,
    PREISAENDERUNG_SPARTEN,
    CONF_HISTORIE,
    CONF_JAHRESVERBRAUCH,
    CONF_KUENDIGUNG_BESTAETIGT,
    CONF_KENNZEICHEN,
    CONF_KM_PRO_JAHR,
    CONF_KM_START,
    CONF_KUNDENNUMMER,
    CONF_MARKTLOKATION,
    CONF_NOTIFY_GESENDET,
    CONF_NOTIFY_TARGET,
    CONF_OEKOSTROM,
    CONF_PREISGARANTIE,
    CONF_SPARTE,
    CONF_TARIF,
    CONF_VERBRAUCH_LETZTE_LAUFZEIT,
    CONF_VERBRAUCH_LETZTE_LAUFZEIT_MONATE,
    CONF_VERBRAUCH_SENSOR,
    CONF_VERBRAUCH_START_WERT,
    CONF_VERSICHERUNGSART,
    CONF_ZAEHLERNUMMER,
    CONF_ZAHLWEISE,
    CONF_ZUSTANDSZAHL,
    ABWASSER_TYP_PAUSCHAL,
    ABWASSER_TYP_PROZENT,
    ABWASSER_TYP_PREIS,
    CONF_ABWASSER_TYP,
    CONF_ARBEITSPREIS_ABWASSER,
    CONF_ARBEITSPREIS_NACHT,
    CONF_ARBEITSPREIS_SENSOR,
    CONF_ARBEITSPREIS_AUFSCHLAG,
    CONF_EINSPEISEVERGUETUNG,
    CONF_JAHRESVERBRAUCH_NACHT,
    CONF_JAHRESVERBRAUCH_TAG,
    CONF_TIER_LIMITS,
    CONF_TIER_PRICES,
    CONF_TIERED,
    CONF_WASSER_EINHEIT,
    MAX_TIERS,
    GAS_SPARTE,
    WASSER_SPARTE,
    NEXT_PREFIX,
    NOTIFY_ID_PREFIX,
    SPARTEN_MIGRATION,
    ZAHLWEISE_MONATE,
    SPARTE_ELECTRICITY_HT_NT,
    CONF_ARBEITSPREIS_NT,
    CONF_VERBRAUCH_SENSOR_NT,
    CONF_VERBRAUCH_START_WERT_NT,
    CONF_ZAEHLERNUMMER_NT,
)

if TYPE_CHECKING:
    from . import TariffyConfigEntry

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(hours=6)


def _tiered_cost(verbrauch: float, limits: list, prices: list) -> float | None:
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


def _extract_tiers(data: dict) -> tuple[list, list]:
    """Extrahiert Tier-Listen aus entry.data (tier_limit_0..N, tier_price_0..N)."""
    limits = []
    prices = []
    # Zuerst prüfen ob alte Listen-Form vorhanden
    if data.get(CONF_TIER_LIMITS):
        return list(data[CONF_TIER_LIMITS]), list(data[CONF_TIER_PRICES] or [])
    # Neue Form: einzelne Felder
    for i in range(MAX_TIERS):
        lim = _f(data.get(f"tier_limit_{i}"))
        prc = _f(data.get(f"tier_price_{i}"))
        if prc is None:
            break
        limits.append(lim)  # None = unbegrenzt
        prices.append(prc)
    return limits, prices


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _abschlag_bisher_gezahlt(
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
    ab_datum = _parse_date(data.get(CONF_ABSCHLAG_AB_DATUM))
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


def _f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _wasser_einheit(hass: HomeAssistant, entity_id: str | None) -> str:
    state = hass.states.get(entity_id) if entity_id else None
    unit = state.attributes.get("unit_of_measurement") if state else None
    return str(unit or "").strip().lower().replace("m3", "m³")


def _in_einheit(wert: float | None, von: str, nach: str) -> float | None:
    """Rechnet einen Wasserverbrauch von Liter in m³ (oder zurueck) um; sonst unveraendert."""
    if wert is None:
        return None
    if von == "l" and nach == "m³":
        return wert / 1000
    if von == "m³" and nach == "l":
        return wert * 1000
    return wert


def _monate_wert(value: Any) -> int:
    """Monate aus einer optionalen Eingabe (leer, 0 oder ungueltig = 0)."""
    zahl = _f(value)
    return int(zahl) if zahl is not None and zahl > 0 else 0


def _minus_months(d: date, months: int) -> date:
    if not months:
        return d
    index = (d.month - 1) - months
    year = d.year + index // 12
    month = index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _plus_months(d: date, months: int) -> date:
    return _minus_months(d, -months)


def _einmal_summen(
    data: dict[str, Any], beginn: date | None, heute: date
) -> tuple[list[dict[str, Any]], float, float]:
    """Einmalposten des Vertrags: (Liste, Summe bis heute ueber alle Laufzeiten, Summe bis
    heute in der laufenden Laufzeit). Positiv = Gutschrift, negativ = Rechnung. Ein Posten
    mit Datum in der Zukunft zaehlt erst ab diesem Tag; einer ohne Datum zaehlt sofort."""
    posten: list[dict[str, Any]] = []
    gesamt = 0.0
    laufend = 0.0
    for item in data.get(CONF_EINMALPOSTEN) or []:
        betrag = _f(item.get("betrag")) if isinstance(item, dict) else None
        if betrag is None:
            continue
        tag = _parse_date(item.get("datum"))
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



def _abschlag_nach_zahlungen(
    abschlag: float,
    intervall: int,
    beginn: date,
    ende: date | None,
    heute: date,
    data: dict[str, Any],
) -> tuple[float, date | None]:
    """Bis heute tatsaechlich abgebuchte Betraege bei nicht monatlicher Zahlweise (zum Beispiel eine
    Versicherung, die jaehrlich im Voraus bezahlt wird): gezaehlt wird jede Zahlung ab der ersten
    Abbuchung (leer = Vertragsbeginn), nicht ein monatlicher Anteil. Bei monatlicher Zahlung zaehlt
    eine Rate, sobald ihr Monat begonnen hat, auch wenn der Tag im Monat noch aussteht. Gibt auch die naechste Zahlung
    zurueck. Eine Preisaenderung gilt fuer Zahlungen ab ihrem Datum."""
    erste = _parse_date(data.get(CONF_ERSTE_ZAHLUNG)) or beginn
    monat_heute = (heute.year, heute.month)
    ab_datum = _parse_date(data.get(CONF_ABSCHLAG_AB_DATUM))
    vorher = _f(data.get(CONF_ABSCHLAG_VORHERIGER_WERT))
    summe = 0.0
    zahlung = 0
    naechste: date | None = None
    while zahlung < 1200:
        termin = _plus_months(erste, zahlung * intervall)
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
        monatlich = vorher if (ab_datum is not None and vorher and termin < ab_datum) else abschlag
        summe += round(monatlich * intervall, 2)
        zahlung += 1
    if naechste is None:  # die naechste Abbuchung nach heute, falls der Vertrag dann noch laeuft
        termin = _plus_months(erste, zahlung * intervall)
        if termin > heute and (ende is None or termin <= ende):
            naechste = termin
    return round(summe, 2), naechste


# ------------------------------------------------- Preisaenderungen im Vertrag
_MONAT_TAGE = 30.44
_PREIS_FELDER = ("arbeitspreis", "grundpreis", "abschlag")


def _preis_segmente(data: dict[str, Any], beginn: date) -> list[dict[str, Any]]:
    """Zeitabschnitte des Vertrags mit den jeweils gueltigen Preisen.

    Der erste Abschnitt beginnt am Vertragsbeginn mit den Werten aus den Vertragsdaten; jede
    Preisaenderung (CONF_PREISAENDERUNGEN) beginnt einen neuen Abschnitt. Ein leerer Wert in
    einer Preisaenderung uebernimmt den Wert des vorherigen Abschnitts. Aenderungen am oder vor
    dem Vertragsbeginn werden ignoriert (sie gehoeren zu keinem Abschnitt dieses Vertrags)."""
    basis = {
        "von": beginn,
        "arbeitspreis": _f(data.get(CONF_ARBEITSPREIS)),
        "grundpreis": _f(data.get(CONF_GRUNDPREIS)),
        "abschlag": _f(data.get(CONF_ABSCHLAG)),
        "zaehlerstand": None,
    }
    aenderungen = []
    for item in data.get(CONF_PREISAENDERUNGEN) or []:
        if not isinstance(item, dict):
            continue
        tag = _parse_date(item.get("datum"))
        if tag is None or tag <= beginn:
            continue
        aenderungen.append((tag, item))
    aenderungen.sort(key=lambda paar: paar[0])
    segmente = [basis]
    for tag, item in aenderungen:
        vorher = segmente[-1]
        segment = {"von": tag, "zaehlerstand": _f(item.get("zaehlerstand"))}
        for feld in _PREIS_FELDER:
            wert = _f(item.get(feld))
            segment[feld] = wert if wert is not None else vorher[feld]
        if vorher["von"] == tag:  # zwei Aenderungen am selben Tag: die spaetere zaehlt
            segmente[-1] = segment
        else:
            segmente.append(segment)
    return segmente


def _segment_am(segmente: list[dict[str, Any]], tag: date) -> dict[str, Any]:
    """Der am `tag` gueltige Abschnitt (vor dem ersten Abschnitt: der erste)."""
    aktiv = segmente[0]
    for segment in segmente:
        if segment["von"] <= tag:
            aktiv = segment
    return aktiv


def _abschlag_segmente(
    segmente: list[dict[str, Any]], beginn: date, data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Abschnitte fuer den Abschlag. Beruecksichtigt zusaetzlich eine Abschlags-Aenderung beim
    Bearbeiten des Vertrags (CONF_ABSCHLAG_AB_DATUM, siehe _abschlag_bisher_gezahlt), aber nur,
    wenn sie im ersten Abschnitt liegt: dort hat sie den Abschlag ab Vertragsbeginn geaendert."""
    ab_datum = _parse_date(data.get(CONF_ABSCHLAG_AB_DATUM))
    vorher = _f(data.get(CONF_ABSCHLAG_VORHERIGER_WERT))
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


def _abschlag_zahlungen(
    ab_segmente: list[dict[str, Any]], erste: date, ende: date, heute: date
) -> tuple[float, float, int]:
    """Monatliche Abschlagszahlungen ab `erste` bis `ende`: (bis heute gezahlt, Summe ueber die
    Laufzeit, Anzahl der noch offenen Raten). Eine Rate zaehlt als gezahlt, sobald ihr Monat
    begonnen hat; ihr Betrag ist der am Zahltag gueltige Abschlag."""
    bisher = gesamt = 0.0
    offen = 0
    monat_heute = (heute.year, heute.month)
    for nummer in range(240):
        termin = _plus_months(erste, nummer)
        if termin > ende:
            break
        betrag = _segment_am(ab_segmente, termin)["abschlag"] or 0.0
        gesamt += betrag
        if (termin.year, termin.month) <= monat_heute:
            bisher += betrag
        else:
            offen += 1
    return round(bisher, 2), round(gesamt, 2), offen


def _monat_von(beginn: date, tag: date) -> float:
    return (tag - beginn).days / _MONAT_TAGE


def _integral_monate(
    segmente: list[dict[str, Any]], feld: str, beginn: date, von_m: float, bis_m: float
) -> float | None:
    """Summe eines Monatsbetrags (Grundpreis, Abschlag) ueber den Zeitraum [von_m, bis_m] in
    Monaten ab Vertragsbeginn, je Abschnitt mit dessen Wert. None, wenn ein Abschnitt im
    Zeitraum keinen Wert hat."""
    if bis_m <= von_m:
        return 0.0
    summe = 0.0
    for index, segment in enumerate(segmente):
        start = 0.0 if index == 0 else _monat_von(beginn, segment["von"])
        ende = (
            _monat_von(beginn, segmente[index + 1]["von"])
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


def _arbeitspreis_kosten(
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


def _rest_arbeitspreis_kosten(
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


def _preisaenderungen_anzeige(
    data: dict[str, Any], beginn: date | None, heute: date
) -> list[dict[str, Any]]:
    """Die gespeicherten Preisaenderungen fuer Panel und Attribute, in gespeicherter Reihenfolge
    (der Index ist der zum Bearbeiten), mit den ab dem Datum gueltigen Werten und dem Status
    (aktiv, kuenftig, vergangen, ungueltig = am oder vor dem Vertragsbeginn)."""
    liste = [item for item in data.get(CONF_PREISAENDERUNGEN) or [] if isinstance(item, dict)]
    if not liste:
        return []
    segmente = _preis_segmente(data, beginn) if beginn is not None else []
    aktiv = _segment_am(segmente, heute) if segmente else None
    ergebnis = []
    for index, item in enumerate(liste):
        tag = _parse_date(item.get("datum"))
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
                **{feld: _f(item.get(feld)) for feld in (*_PREIS_FELDER, "zaehlerstand")},
                "gilt": {feld: segment[feld] for feld in _PREIS_FELDER} if segment else None,
                "status": status,
            }
        )
    return ergebnis


def _entry_sum_state(entry: dict) -> tuple[float | None, float | None]:
    s = entry.get("sum")
    st = entry.get("state")
    return (float(s) if s is not None else None, float(st) if st is not None else None)


async def _get_historic_value(
    hass: HomeAssistant, entity_id: str, at_date: date
) -> tuple[float | None, float | None]:
    """Liest 'sum' UND rohen 'state' zum Vertragsbeginn aus den Long-Term
    Statistics, als (sum, state)-Tupel.

    'sum' ist reset-sicher (wichtig fuer Sensoren, die bei
    state_class: total_increasing erlaubterweise periodisch zurueckspringen,
    z.B. manche Gaszaehler) und daher normalerweise die bevorzugte Basis. Der
    rohe 'state' wird zusaetzlich mitgeliefert, damit der Aufrufer bei einer
    Anomalie in der 'sum'-Statistik selbst (z.B. ein Baseline-Sprung durch
    einen Recorder-Neustart, siehe CHANGELOG) auf die einfache State-Differenz
    ausweichen kann, wenn kein echter Reset vorliegt (siehe
    _pick_verbrauch_delta()).
    """
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        tz = dt_util.get_default_time_zone()
        # Suche ab dem Vortag bis 3 Tage danach um den nächsten verfügbaren Wert zu finden
        start_dt = datetime.combine(at_date - timedelta(days=1), datetime.min.time()).replace(tzinfo=tz)
        end_dt = datetime.combine(at_date + timedelta(days=3), datetime.min.time()).replace(tzinfo=tz)

        instance = get_instance(hass)
        stats = await instance.async_add_executor_job(
            lambda: statistics_during_period(
                hass,
                start_dt,
                end_dt,
                statistic_ids={entity_id},
                period="hour",
                units={},
                types={"sum", "state"},
            )
        )

        entries = stats.get(entity_id, [])
        if entries:
            # Ersten Eintrag am oder nach dem Vertragsbeginn nehmen
            # In HA 2026+ liefert statistics_during_period den start-Wert als
            # Unix-Timestamp (float), nicht als datetime — beide Fälle abdecken
            at_ts = datetime.combine(at_date, datetime.min.time()).replace(tzinfo=tz).timestamp()
            for entry in entries:
                entry_start = entry.get("start")
                if entry_start is None:
                    continue
                # Normalisieren: float (Unix-ts) oder datetime → float
                if isinstance(entry_start, (int, float)):
                    entry_ts = float(entry_start)
                else:
                    entry_ts = entry_start.timestamp()
                if entry_ts >= at_ts:
                    sum_val, state_val = _entry_sum_state(entry)
                    if sum_val is not None:
                        _LOGGER.debug(
                            "Tariffy: LTS-Offset (sum) für %s am %s = %.3f (state=%s)",
                            entity_id, at_date, sum_val, state_val,
                        )
                        return sum_val, state_val
            # Fallback: letzten Eintrag vor Vertragsbeginn nehmen
            for entry in reversed(entries):
                sum_val, state_val = _entry_sum_state(entry)
                if sum_val is not None:
                    _LOGGER.debug(
                        "Tariffy: LTS-Offset (sum, vor Beginn) für %s = %.3f (state=%s)",
                        entity_id, sum_val, state_val,
                    )
                    return sum_val, state_val

        # Kein Eintrag im Suchfenster — frühesten verfügbaren LTS-Wert suchen
        # 730 Tage = 2 Jahre, deckt Sensoren ab die erst lange nach Vertragsbeginn
        # existierten (z.B. neu eingerichtete Zähler)
        stats_all = await instance.async_add_executor_job(
            lambda: statistics_during_period(
                hass,
                end_dt,
                end_dt + timedelta(days=730),
                statistic_ids={entity_id},
                period="hour",
                units={},
                types={"sum", "state"},
            )
        )
        early_entries = stats_all.get(entity_id, [])
        for entry in early_entries:
            sum_val, state_val = _entry_sum_state(entry)
            if sum_val is not None:
                _LOGGER.debug(
                    "Tariffy: Frühester LTS-Offset (sum) für %s = %.3f (Sensor existierte "
                    "noch nicht am Vertragsbeginn, state=%s)",
                    entity_id, sum_val, state_val,
                )
                return sum_val, state_val

    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Tariffy: LTS-Abfrage für %s fehlgeschlagen: %s", entity_id, err)
    return None, None


async def _get_latest_sum(hass: HomeAssistant, entity_id: str) -> tuple[float | None, float | None]:
    """Liest den neuesten verfügbaren kumulierten 'sum'-Wert UND den dazu
    gehörigen rohen 'state' aus den Long-Term Statistics, als (sum, state)-
    Tupel — siehe _get_historic_value() für den Grund, warum beide gebraucht
    werden.
    """
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        instance = get_instance(hass)
        end_dt = dt_util.now()
        start_dt = end_dt - timedelta(days=3)
        stats = await instance.async_add_executor_job(
            lambda: statistics_during_period(
                hass,
                start_dt,
                end_dt,
                statistic_ids={entity_id},
                period="hour",
                units={},
                types={"sum", "state"},
            )
        )
        entries = stats.get(entity_id, [])
        for entry in reversed(entries):
            sum_val, state_val = _entry_sum_state(entry)
            if sum_val is not None:
                return sum_val, state_val
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Tariffy: aktueller Sum-Wert für %s fehlgeschlagen: %s", entity_id, err)
    return None, None


# Zaehler, die in Wh oder MWh statt kWh melden (z. B. Tasmota-Lesekopf): der Verbrauch wird in kWh
# gerechnet. Andere Einheiten bleiben unveraendert (Faktor 1).
_ENERGIE_FAKTOR = {"wh": 0.001, "mwh": 1000.0}


async def _einheit_faktor(hass: HomeAssistant, entity_id: str | None) -> float:
    """Faktor, der den Verbrauch eines Zaehlers in kWh umrechnet: 0.001 bei Wh, 1000 bei MWh, sonst 1.
    Die Einheit steht am Live-Zustand; fehlt er (z. B. kurz nach dem Start), gilt die Einheit der
    Langzeitstatistik. Wirft nie."""
    if not entity_id:
        return 1.0
    unit = None
    state = hass.states.get(entity_id)
    if state is not None:
        unit = state.attributes.get("unit_of_measurement")
    if not unit:
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import get_metadata

            instance = get_instance(hass)
            meta = await instance.async_add_executor_job(
                lambda: get_metadata(hass, statistic_ids={entity_id})
            )
            if entity_id in meta:
                unit = meta[entity_id][1].get("statistics_unit_of_measurement")
        except Exception:  # noqa: BLE001 - ohne Einheit bleibt es bei kWh
            unit = None
    return _ENERGIE_FAKTOR.get(str(unit or "").strip().lower(), 1.0)


def _pick_verbrauch_delta(
    aktuell_sum: float | None,
    offset_sum: float | None,
    aktuell_state: float | None,
    offset_state: float | None,
    faktor: float = 1.0,
) -> float | None:
    """Waehlt zwischen dem reset-sicheren 'sum'-Delta und dem einfachen
    rohen State-Delta. Bevorzugt bewusst das State-Delta, sobald der
    aktuelle Zustand nicht unter den Zustand bei Vertragsbeginn gefallen
    ist -- das ist der Normalfall (kein Reset), und macht die Berechnung
    robust gegenueber Anomalien in der Recorder-'sum'-Statistik selbst
    (z.B. ein Baseline-Sprung nach einem HA-Neustart, bei dem der rohe
    Zaehlerstand durchgehend korrekt blieb, die 'sum'-Statistik aber ploetzlich
    einen unplausiblen Sprung machte). Ist der Zustand dagegen gefallen (ein
    echter Reset, z.B. bei manchen Gaszaehlern ueblich), ist das 'sum'-Delta
    weiterhin die einzig korrekte Wahl, da es Resets bereits aufsummiert."""
    # `faktor` rechnet die Einheit des Zaehlers in kWh um (Wh, MWh), vor dem Runden
    if aktuell_state is not None and offset_state is not None and aktuell_state >= offset_state:
        return round((aktuell_state - offset_state) * faktor, 2)
    if aktuell_sum is not None and offset_sum is not None:
        return round((aktuell_sum - offset_sum) * faktor, 2)
    return None


async def _get_average_price(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> float | None:
    """Liest den mittleren Sensorwert (z.B. Boersen-/Spotpreis eines
    dynamischen Stromtarifs) ueber den angegebenen Zeitraum aus den
    Long-Term Statistics ('mean'). Gibt None zurueck, wenn (noch) keine
    Statistik vorliegt — der Aufrufer faellt dann auf den manuell
    eingegebenen Arbeitspreis zurueck."""
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        instance = get_instance(hass)
        stats = await instance.async_add_executor_job(
            lambda: statistics_during_period(
                hass, start, end,
                statistic_ids={entity_id},
                period="hour",
                units={},
                types={"mean"},
            )
        )
        means = [
            entry["mean"] for entry in stats.get(entity_id, [])
            if entry.get("mean") is not None
        ]
        if means:
            return sum(means) / len(means)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Tariffy: Durchschnittspreis-Abfrage für %s fehlgeschlagen: %s",
            entity_id, err,
        )
    return None


class TariffyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Hält Vertragsdaten aktuell, schaltet Tarife und erinnert an Kündigungen."""

    def __init__(self, hass: HomeAssistant, entry: "TariffyConfigEntry") -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=SCAN_INTERVAL,
        )
        self.entry = entry
        self._verbrauch_offset: float | None = None
        self._verbrauch_offset_state: float | None = None
        self._verbrauch_offset_date: date | None = None
        # Niedertarif-Register beim Zweizaehlertarif (SPARTE_ELECTRICITY_HT_NT)
        # -- eigener Cache, unabhaengig vom Hochtarif-Register oben.
        self._verbrauch_offset_nt: float | None = None
        self._verbrauch_offset_state_nt: float | None = None
        self._verbrauch_offset_date_nt: date | None = None
        # Aussenzaehler eines Wasservertrags: Offset je Sensor (Datum, sum, state)
        self._aussen_offsets: dict[str, tuple[date, float, float | None]] = {}
        # Preisaenderungen: Zaehler-Offset (sum, state) je Stichtag aus der Langzeitstatistik
        self._preis_offsets: dict[date, tuple[float, float | None]] = {}
        # Fuer den Diagnose-Download und die Warnung im Log: wie der
        # Verbrauch beim letzten Update ermittelt wurde (siehe diagnostics.py).
        self.verbrauch_diag: dict[str, Any] = {}
        self._verbrauch_war_unbekannt = False

        async def _startup_refresh(_event: Any = None) -> None:
            """Refresh nach HA-Start, damit LTS-Daten verfügbar sind."""
            if entry.data.get(CONF_VERBRAUCH_SENSOR):
                await self.async_refresh()

        if hass.is_running:
            # Setup/Reload passiert nach dem eigentlichen HA-Start (z.B. Optionen
            # geaendert, Integration neu geladen) -- EVENT_HOMEASSISTANT_STARTED
            # feuert dann nicht erneut, also direkt refreshen statt zu warten.
            hass.async_create_task(_startup_refresh())
        else:
            # Bewusst NICHT ueber entry.async_on_unload() nachverfolgt: ein
            # "listen_once"-Listener entfernt sich nach dem einmaligen Feuern
            # automatisch selbst. Wuerde man ihn trotzdem in async_on_unload
            # registrieren, versucht ein spaeteres Neuladen/Entladen -- nachdem
            # das Event laengst gefeuert und der Listener sich schon selbst
            # entfernt hat -- ihn ein zweites Mal zu entfernen und wirft
            # "Unable to remove unknown job listener".
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _startup_refresh)

        async def _midnight_refresh(_now: Any) -> None:
            """Prueft den Tarifwechsel exakt bei Datumswechsel, nicht erst
            beim naechsten 6h-Poll."""
            await self.async_refresh()

        entry.async_on_unload(
            async_track_time_change(hass, _midnight_refresh, hour=0, minute=0, second=1)
        )

    @property
    def _notification_id(self) -> str:
        return f"{NOTIFY_ID_PREFIX}{self.entry.entry_id}"

    @property
    def _abschlag_notification_id(self) -> str:
        return f"{NOTIFY_ID_ABSCHLAG_PREFIX}{self.entry.entry_id}"

    # ------------------------------------------------------------- Historie
    def _historie_eintrag(self, heute: date) -> dict[str, Any]:
        """Die auslaufende Laufzeit als Eintrag der Vertragshistorie.

        Nur Werte, die das Panel ohnehin zeigt (keine Kunden- oder
        Zaehlernummern). Die Summen stammen aus der letzten Berechnung."""
        alt = self.entry.data
        d = self.data or {}
        kredit = alt.get(CONF_SPARTE) == SPARTE_LOAN

        def _zahl(wert: Any) -> float | None:
            zahl = _f(wert)
            return round(zahl, 2) if zahl is not None else None

        alt_beginn = _parse_date(alt.get(CONF_BEGINN))
        if alt.get(CONF_PREISAENDERUNGEN) and alt_beginn is not None:
            preise = _segment_am(_preis_segmente(alt, alt_beginn), heute)
        else:
            preise = {feld: _f(alt.get(key)) for feld, key in zip(
                _PREIS_FELDER, (CONF_ARBEITSPREIS, CONF_GRUNDPREIS, CONF_ABSCHLAG)
            )}

        return {
            "sparte": alt.get(CONF_SPARTE),
            "anbieter": alt.get(CONF_ANBIETER),
            "tarif": alt.get(CONF_TARIF),
            "beginn": alt.get(CONF_BEGINN),
            "ende": alt.get(CONF_ENDE) or heute.isoformat(),
            # bei Preisaenderungen im Vertrag die zuletzt gueltigen Werte
            "arbeitspreis": preise["arbeitspreis"],
            "grundpreis": preise["grundpreis"],
            "abschlag": preise["abschlag"],
            # die Boni gehoeren zu dieser Laufzeit: Sofortbonus und Neukundenbonus senken ihre
            # Kosten, der Neukundenbonus ist im Saldo der Jahresrechnung enthalten
            "kosten": _zahl(
                d.get("kredit_gezahlt")
                if kredit
                else (
                    d["kosten_bisher"] - (_f(alt.get(CONF_BONUS)) or 0) - (_f(alt.get(CONF_SOFORTBONUS)) or 0)
                    if d.get("kosten_bisher") is not None
                    else None
                )
            ),
            "abschlag_gezahlt": _zahl(d.get("abschlag_bisher_gezahlt")),
            "saldo": _zahl(
                d["guthaben_bisher"] + (0 if kredit else (_f(alt.get(CONF_BONUS)) or 0))
                if d.get("guthaben_bisher") is not None
                else None
            ),
            "verbrauch": _zahl(d.get("verbrauch_bisher")),
            "abgeschlossen": heute.isoformat(),
            **(
                {
                    "kredit_summe": _zahl(alt.get(CONF_KREDIT_SUMME)),
                    "sollzins": _f(alt.get(CONF_SOLLZINS)),
                    "effektivzins": _f(alt.get(CONF_EFFEKTIVZINS)),
                    "restschuld": _zahl(d.get("kredit_restschuld")),
                    "zinsen": _zahl(d.get("kredit_zinsen_bisher")),
                }
                if kredit
                else {}
            ),
        }

    def _gesamtkosten(
        self,
        data: dict[str, Any],
        beginn: date | None,
        kosten_bisher: float | None,
        abschlag_bisher: float | None,
        archiviert: bool,
        einmal: float = 0.0,
    ) -> dict[str, Any]:
        """Kosten ueber alle Laufzeiten des Vertrags: die frueheren aus der Historie plus die
        laufende. Bei einem archivierten Vertrag steht die letzte Laufzeit schon in der Historie
        (das Archivieren legt sie dort ab), sie wird nicht doppelt gezaehlt."""
        historie = list(data.get(CONF_HISTORIE) or [])
        summe = 0.0
        vollstaendig = True
        beginne: list[date] = []
        for term in historie:
            kosten = _f(term.get("kosten"))
            if kosten is None:
                kosten = _f(term.get("abschlag_gezahlt"))
            if kosten is None:
                vollstaendig = False
            else:
                summe += kosten
            fruehester = _parse_date(term.get(CONF_BEGINN))
            if fruehester is not None:
                beginne.append(fruehester)
        laufzeiten = len(historie)
        if not archiviert:
            laufzeiten += 1
            laufende = kosten_bisher if kosten_bisher is not None else abschlag_bisher
            if laufende is None:
                vollstaendig = False
            else:
                summe += laufende
            if beginn is not None:
                beginne.append(beginn)
        summe -= einmal  # Bonus (positiv) senkt, Rechnung (negativ) erhoeht die Kosten
        return {
            "kosten_gesamt": round(summe, 2) if (summe or vollstaendig) else None,
            "kosten_gesamt_vollstaendig": vollstaendig,
            "laufzeiten_anzahl": laufzeiten,
            "seit": min(beginne) if beginne else None,
        }

    async def async_archive(self) -> None:
        """Archiviert den abgelaufenen Vertrag: ein Merker plus die letzte Laufzeit
        als Historien-Eintrag. Nichts wird geloescht, alle Entitaeten bleiben."""
        await self._dismiss_notifications()
        heute = dt_util.now().date()
        historie = list(self.entry.data.get(CONF_HISTORIE) or [])
        historie.append({**self._historie_eintrag(heute), "archiviert": True})
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_ARCHIVIERT: heute.isoformat(),
                CONF_HISTORIE: historie[-HISTORIE_MAX:],
            },
        )
        # sofort neu berechnen (async_request_refresh waere entprellt)
        await self.async_refresh()

    async def async_restore(self) -> None:
        """Holt einen archivierten Vertrag zurueck."""
        data = dict(self.entry.data)
        data.pop(CONF_ARCHIVIERT, None)
        historie = list(data.get(CONF_HISTORIE) or [])
        for position in range(len(historie) - 1, -1, -1):
            if historie[position].get("archiviert"):
                del historie[position]  # der Eintrag, den das Archivieren angelegt hat
                break
        if historie:
            data[CONF_HISTORIE] = historie
        else:
            data.pop(CONF_HISTORIE, None)
        self.hass.config_entries.async_update_entry(self.entry, data=data)
        await self.async_refresh()

    async def _dismiss_notifications(self) -> None:
        for notification_id in (self._notification_id, self._abschlag_notification_id):
            await self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": notification_id},
                blocking=True,
            )

    # -------------------------------------------------------------- Wechsel
    def _switch_now(self, verbrauch_letzte: float | None = None) -> None:
        nxt = dict(self.entry.options)
        if not nxt:
            return
        historie = list(self.entry.data.get(CONF_HISTORIE) or [])
        if not self.entry.data.get(CONF_ARCHIVIERT):  # sonst steht die Laufzeit schon drin
            historie.append(self._historie_eintrag(dt_util.now().date()))
        new_data: dict[str, Any] = {
            CONF_HISTORIE: historie[-HISTORIE_MAX:],
            CONF_SPARTE: self.entry.data.get(CONF_SPARTE),
            CONF_ERINNERUNG_MONATE: self.entry.data.get(CONF_ERINNERUNG_MONATE, "0"),
            # Einmalposten gehoeren zum Vertrag (auch in die Gesamtkosten ueber alle Laufzeiten)
            **(
                {CONF_EINMALPOSTEN: self.entry.data[CONF_EINMALPOSTEN]}
                if self.entry.data.get(CONF_EINMALPOSTEN)
                else {}
            ),
            # die Aussenzaehler bleiben angeschlossen; ihr Startwert gehoerte zum alten Vertrag
            **(
                {
                    CONF_AUSSENZAEHLER: [
                        {key: value for key, value in zaehler.items() if key != "start_wert"}
                        for zaehler in self.entry.data[CONF_AUSSENZAEHLER]
                    ]
                }
                if self.entry.data.get(CONF_AUSSENZAEHLER)
                else {}
            ),
            # die Erinnerung haengt an der Kuendigungsfrist: sie bleibt beim Wechsel erhalten
            **(
                {CONF_KUENDIGUNGSFRIST: self.entry.data[CONF_KUENDIGUNGSFRIST]}
                if self.entry.data.get(CONF_KUENDIGUNGSFRIST) not in (None, "")
                else {}
            ),
            CONF_NOTIFY_TARGET: self.entry.data.get(CONF_NOTIFY_TARGET),
        }
        for feld in BASIS_FELDER:
            wert = nxt.get(NEXT_PREFIX + feld)
            if feld == CONF_BONUS:
                # ein Bonus gehoert zu genau einer Laufzeit und geht nicht auf den Folgevertrag ueber
                if wert not in (None, ""):
                    new_data[feld] = wert
                continue
            new_data[feld] = wert if wert not in (None, "") else self.entry.data.get(feld)
        # Verbrauch der letzten Laufzeit einfrieren — inkl. ihrer tatsächlichen
        # Dauer in Monaten, damit empfohlener_abschlag nicht von einem festen
        # 12-Monats-Jahr ausgehen muss.
        alte_laufzeit_monate: float | None = None
        alte_beginn = _parse_date(self.entry.data.get(CONF_BEGINN))
        if alte_beginn is not None:
            heute = dt_util.now().date()
            alte_laufzeit_monate = round((heute - alte_beginn).days / 30.44, 2)
        if verbrauch_letzte is not None:
            new_data[CONF_VERBRAUCH_LETZTE_LAUFZEIT] = verbrauch_letzte
            if alte_laufzeit_monate is not None:
                new_data[CONF_VERBRAUCH_LETZTE_LAUFZEIT_MONATE] = alte_laufzeit_monate
        elif self.entry.data.get(CONF_VERBRAUCH_LETZTE_LAUFZEIT) is not None:
            new_data[CONF_VERBRAUCH_LETZTE_LAUFZEIT] = self.entry.data[CONF_VERBRAUCH_LETZTE_LAUFZEIT]
            if self.entry.data.get(CONF_VERBRAUCH_LETZTE_LAUFZEIT_MONATE) is not None:
                new_data[CONF_VERBRAUCH_LETZTE_LAUFZEIT_MONATE] = self.entry.data[CONF_VERBRAUCH_LETZTE_LAUFZEIT_MONATE]
        _LOGGER.info(
            "Tariffy '%s': automatischer Wechsel zu '%s' (%s) — Verbrauch letzte Laufzeit: %s",
            self.entry.title,
            new_data.get(CONF_ANBIETER),
            new_data.get(CONF_TARIF),
            verbrauch_letzte,
        )
        # Offset zurücksetzen — neuer Vertrag, neuer Startpunkt
        self._verbrauch_offset = None
        self._verbrauch_offset_state = None
        self._verbrauch_offset_date = None
        self._verbrauch_offset_nt = None
        self._verbrauch_offset_state_nt = None
        self._verbrauch_offset_date_nt = None
        self._aussen_offsets = {}
        self._preis_offsets = {}
        self.hass.config_entries.async_update_entry(
            self.entry, data=new_data, options={}
        )
        # Which sensors exist (night rate, tiered, ...) is decided once when the
        # entry is set up, so the new contract may need a different set. Reload
        # like the removed update listener did on every entry change.
        self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    async def async_force_switch(self) -> None:
        verbrauch_letzte = await self._verbrauch_letzte_laufzeit_jetzt()
        self._switch_now(verbrauch_letzte)

    # ------------------------------------------------------------ Kündigung
    async def async_confirm_kuendigung(self) -> None:
        await self.hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": self._notification_id},
            blocking=True,
        )
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_KUENDIGUNG_BESTAETIGT: True},
        )
        await self.async_request_refresh()

    async def _send_notify(self, title: str, message: str) -> None:
        target = self.entry.data.get(CONF_NOTIFY_TARGET)
        if not target:
            return
        try:
            await self.hass.services.async_call(
                "notify", target, {"title": title, "message": message}
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("notify.%s nicht erreichbar (%s)", target, err)

    async def _handle_reminder(
        self, aktiv: bool, ende: date | None, data: dict[str, Any]
    ) -> None:
        if not aktiv:
            return
        frist = _monate_wert(data.get(CONF_KUENDIGUNGSFRIST))
        kuendigung_bis = _minus_months(ende, frist) if (ende is not None and frist) else None
        title = f"Kündigung: {self.entry.title}"
        ende_str = ende.isoformat() if ende else "?"
        message = (
            f"Vertrag **{data.get(CONF_ANBIETER) or ''}** "
            f"({data.get(CONF_TARIF) or ''}) läuft am **{ende_str}** aus.\n\n"
            + (
                f"Kündigungsfrist {frist} Monat(e): Die Kündigung muss **bis {kuendigung_bis.isoformat()}** "
                f"vorliegen. Erinnerung {data.get(CONF_ERINNERUNG_MONATE)} Monat(e) vorher. "
                if frist and kuendigung_bis
                else f"Erinnerung {data.get(CONF_ERINNERUNG_MONATE)} Monat(e) vorher. "
            )
            + "Bitte rechtzeitig kündigen oder den Folgevertrag eintragen.\n\n"
            "_Diese Meldung bleibt bestehen, bis du sie über den Button "
            "'Kündigung bestätigen' quittierst._"
        )
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "notification_id": self._notification_id,
                "title": title,
                "message": message,
            },
        )
        if not data.get(CONF_NOTIFY_GESENDET):
            await self._send_notify(title, message)
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_NOTIFY_GESENDET: True}
            )

    # ------------------------------------------------------ Abschlag-Warnung
    async def async_confirm_abschlag_warnung(self) -> None:
        await self.hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": self._abschlag_notification_id},
            blocking=True,
        )
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_ABSCHLAG_WARNUNG_BESTAETIGT: True},
        )
        await self.async_request_refresh()

    async def _handle_abschlag_warnung(
        self,
        aktiv: bool,
        prognose_real: float | None,
        abschlag_empfehlung: float | None,
        data: dict[str, Any],
    ) -> None:
        if not aktiv:
            # Bestaetigt-/Gesendet-Flags zuruecksetzen, damit eine kuenftige
            # neue Nachzahlungs-Warnung (z.B. nach Tarifwechsel oder
            # Abschlagserhoehung und erneuter Verschlechterung) wieder frisch
            # ausgeloest wird, statt dauerhaft unterdrueckt zu bleiben.
            if data.get(CONF_ABSCHLAG_WARNUNG_BESTAETIGT) or data.get(
                CONF_ABSCHLAG_WARNUNG_NOTIFY_GESENDET
            ):
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    data={
                        **self.entry.data,
                        CONF_ABSCHLAG_WARNUNG_BESTAETIGT: False,
                        CONF_ABSCHLAG_WARNUNG_NOTIFY_GESENDET: False,
                    },
                )
            return
        if data.get(CONF_ABSCHLAG_WARNUNG_BESTAETIGT):
            return
        title = f"Abschlag zu niedrig: {self.entry.title}"
        empfehlung_zeile = (
            f"Empfehlung: Abschlag für die restliche Laufzeit auf ca. "
            f"**{abschlag_empfehlung:.2f} €/Monat** anpassen.\n\n"
            if abschlag_empfehlung is not None
            else ""
        )
        message = (
            f"Beim aktuellen Verbrauch ist am Vertragsende eine Nachzahlung "
            f"von ca. **{abs(prognose_real):.2f} €** zu erwarten.\n\n"
            f"{empfehlung_zeile}"
            "_Diese Meldung bleibt bestehen, bis du sie über den Button "
            "'Abschlag-Warnung bestätigen' quittierst._"
        )
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "notification_id": self._abschlag_notification_id,
                "title": title,
                "message": message,
            },
        )
        if not data.get(CONF_ABSCHLAG_WARNUNG_NOTIFY_GESENDET):
            await self._send_notify(title, message)
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_ABSCHLAG_WARNUNG_NOTIFY_GESENDET: True},
            )

    # ------------------------------------------------- Verbrauch Hochrechnung
    async def _get_verbrauch_offset(
        self, entity_id: str, beginn: date
    ) -> tuple[float | None, float | None]:
        """Holt (sum, state) zum Vertragsbeginn (einmalig, dann gecacht)."""
        if (
            self._verbrauch_offset is not None
            and self._verbrauch_offset_date == beginn
        ):
            return self._verbrauch_offset, self._verbrauch_offset_state

        sum_val, state_val = await _get_historic_value(self.hass, entity_id, beginn)
        if sum_val is not None:
            self._verbrauch_offset = sum_val
            self._verbrauch_offset_state = state_val
            self._verbrauch_offset_date = beginn
            _LOGGER.debug(
                "Tariffy '%s': Verbrauch-Offset zum %s = %.2f (state=%s)",
                self.entry.title, beginn, sum_val, state_val,
            )
        return sum_val, state_val

    async def _get_verbrauch_offset_nt(
        self, entity_id: str, beginn: date
    ) -> tuple[float | None, float | None]:
        """Wie _get_verbrauch_offset(), aber fuer das Niedertarif-Register
        beim Zweizaehlertarif — eigener Cache, da Hoch- und Niedertarif
        unabhaengige Sensoren mit potenziell unterschiedlicher LTS-Historie
        sind."""
        if (
            self._verbrauch_offset_nt is not None
            and self._verbrauch_offset_date_nt == beginn
        ):
            return self._verbrauch_offset_nt, self._verbrauch_offset_state_nt

        sum_val, state_val = await _get_historic_value(self.hass, entity_id, beginn)
        if sum_val is not None:
            self._verbrauch_offset_nt = sum_val
            self._verbrauch_offset_state_nt = state_val
            self._verbrauch_offset_date_nt = beginn
            _LOGGER.debug(
                "Tariffy '%s': Verbrauch-Offset (NT) zum %s = %.2f (state=%s)",
                self.entry.title, beginn, sum_val, state_val,
            )
        return sum_val, state_val

    async def _aktueller_zaehlerstand(self, sensor_id: str) -> float | None:
        """Aktueller roher Zaehlerstand fuer die Berechnung mit manuellem
        Startwert. Bevorzugt den Live-Zustand; ist der Sensor gerade
        unavailable/unknown (z.B. Funk-Zaehler kurz offline), wird der
        zuletzt aufgezeichnete Statistik-'state' verwendet, statt alle
        abgeleiteten Sensoren auf 'unbekannt' fallen zu lassen."""
        state = self.hass.states.get(sensor_id)
        live = _f(state.state) if state else None
        if live is not None:
            return live
        _sum, last_state = await _get_latest_sum(self.hass, sensor_id)
        return last_state

    async def _get_aussen_offset(
        self, entity_id: str, beginn: date
    ) -> tuple[float | None, float | None]:
        """(sum, state) eines Aussenzaehlers zum Vertragsbeginn (je Sensor einmal, dann gecacht)."""
        cached = self._aussen_offsets.get(entity_id)
        if cached is not None and cached[0] == beginn:
            return cached[1], cached[2]
        sum_val, state_val = await _get_historic_value(self.hass, entity_id, beginn)
        if sum_val is not None:
            self._aussen_offsets[entity_id] = (beginn, sum_val, state_val)
        return sum_val, state_val

    async def _aussenzaehler(
        self,
        zaehler_liste: list[dict[str, Any]],
        beginn: date | None,
        verbrauch_bisher: float | None,
        hauptsensor: str | None,
        frischwasserpreis: float | None,
        gesamtpreis: float | None,
    ) -> dict[str, Any]:
        """Aussenzaehler (Garten, Pool, ...) eines Wasservertrags: ihr Verbrauch steckt im Hauptzaehler,
        ist aber nicht abwasserpflichtig. Rechnet je Zaehler Verbrauch und Kosten und gibt zurueck, welcher
        durchschnittliche Preis je Einheit des Hauptzaehlers daraus folgt (`preis`: None = unveraendert)."""
        ergebnis: dict[str, Any] = {
            "liste": [],
            "garten_verbrauch": None,
            "garten_kosten": None,
            "abwasserpflichtig": None,
            "abwasser_kosten": None,
            "preis": None,
        }
        haupt_einheit = _wasser_einheit(self.hass, hauptsensor)
        verbrauch_summe = 0.0
        for zaehler in zaehler_liste:
            sensor = zaehler.get("sensor")
            start = _f(zaehler.get("start_wert"))
            verbrauch = await self._verbrauch_bisher_fuer_sensor(
                sensor, start, beginn, self._get_aussen_offset
            )
            verbrauch = _in_einheit(verbrauch, _wasser_einheit(self.hass, sensor), haupt_einheit)
            if verbrauch is not None and verbrauch < 0:
                verbrauch = None
            ergebnis["liste"].append(
                {
                    "name": zaehler.get("name"),
                    "sensor": sensor,
                    "start_wert": start,
                    "preis": _f(zaehler.get("preis")),
                    "verbrauch": verbrauch,
                    "kosten": None,
                }
            )
            if verbrauch is not None:
                verbrauch_summe += verbrauch
        if verbrauch_bisher is None or verbrauch_bisher <= 0 or frischwasserpreis is None:
            return ergebnis
        # mehr als der Hauptzaehler gezaehlt hat kann nicht ausserhalb des Abwassers liegen
        anteil = min(1.0, verbrauch_bisher / verbrauch_summe) if verbrauch_summe > 0 else 0.0
        garten_kosten = 0.0
        for eintrag in ergebnis["liste"]:
            if eintrag["verbrauch"] is None:
                continue
            preis = eintrag["preis"] if eintrag["preis"] is not None else frischwasserpreis
            eintrag["kosten"] = round(eintrag["verbrauch"] * anteil * preis, 2)
            garten_kosten += eintrag["verbrauch"] * anteil * preis
        garten_verbrauch = verbrauch_summe * anteil
        abwasserpflichtig = verbrauch_bisher - garten_verbrauch
        voll = gesamtpreis if gesamtpreis is not None else frischwasserpreis
        ergebnis.update(
            {
                "garten_verbrauch": round(garten_verbrauch, 2),
                "garten_kosten": round(garten_kosten, 2),
                "abwasserpflichtig": round(abwasserpflichtig, 2),
                "abwasser_kosten": round(abwasserpflichtig * (voll - frischwasserpreis), 2),
                # Hauptzaehler-Verbrauch: der abwasserpflichtige Teil zum vollen Preis, der Rest zum Gartenpreis
                "preis": round((abwasserpflichtig * voll + garten_kosten) / verbrauch_bisher, 6)
                if verbrauch_summe > 0
                else None,
            }
        )
        return ergebnis

    async def _verbrauch_bisher_fuer_sensor(
        self,
        sensor_id: str | None,
        manueller_start_wert: float | None,
        beginn: date | None,
        offset_getter: Callable[[str, date], Any],
    ) -> float | None:
        """Verbrauch seit Vertragsbeginn fuer EIN Register (Hoch- oder
        Niedertarif-Sensor beim Zweizaehlertarif) — dieselbe Logik wie der
        Hochtarif/Einzelzaehler-Block in _async_update_data() (manueller
        Override hat Vorrang, sonst reset-sichere 'sum'-Statistik mit
        State-Delta-Absicherung, siehe _pick_verbrauch_delta()), nur als
        wiederverwendbare Methode fuer das zweite Register. `offset_getter`
        ist _get_verbrauch_offset oder _get_verbrauch_offset_nt, je nach
        Register."""
        if not sensor_id or beginn is None:
            return None
        if manueller_start_wert is not None:
            aktuell = await self._aktueller_zaehlerstand(sensor_id)
            if aktuell is None:
                return None
            return round((aktuell - manueller_start_wert) * await _einheit_faktor(self.hass, sensor_id), 2)

        aktuell_sum, aktuell_state = await _get_latest_sum(self.hass, sensor_id)
        live_state = self.hass.states.get(sensor_id)
        live_val = _f(live_state.state) if live_state else None
        if aktuell_sum is None:
            aktuell_sum = live_val
        if live_val is not None:
            aktuell_state = live_val
        if aktuell_sum is None:
            return None
        offset_sum, offset_state = await offset_getter(sensor_id, beginn)
        return _pick_verbrauch_delta(
            aktuell_sum, offset_sum, aktuell_state, offset_state, await _einheit_faktor(self.hass, sensor_id)
        )

    async def _get_preis_offset(
        self, entity_id: str, stichtag: date
    ) -> tuple[float | None, float | None]:
        """(sum, state) des Zaehlers am Stichtag einer Preisaenderung (gecacht)."""
        if stichtag in self._preis_offsets:
            return self._preis_offsets[stichtag]
        sum_val, state_val = await _get_historic_value(self.hass, entity_id, stichtag)
        if sum_val is not None:
            self._preis_offsets[stichtag] = (sum_val, state_val)
        return sum_val, state_val

    async def _verbrauch_grenzen(
        self,
        segmente: list[dict[str, Any]],
        beginn: date,
        heute: date,
        verbrauch_bisher: float,
        sensor_id: str | None,
    ) -> list[float]:
        """Kumulierter Verbrauch seit Vertragsbeginn zu Beginn jedes bisherigen Abschnitts, plus
        der Verbrauch bis heute als letzter Wert. Der Verbrauch ab einem Stichtag kommt aus der
        Langzeitstatistik (oder aus dem eingetragenen Zaehlerstand am Stichtag); fehlt beides,
        wird der Verbrauch zeitanteilig aufgeteilt."""
        grenzen = [0.0]
        tage_gesamt = max(1, (heute - beginn).days)
        for segment in segmente[1:]:
            stichtag = segment["von"]
            if stichtag > heute:
                break
            seit: float | None = None
            if sensor_id:
                seit = await self._verbrauch_bisher_fuer_sensor(
                    sensor_id, segment.get("zaehlerstand"), stichtag, self._get_preis_offset
                )
            if seit is not None:
                bis_stichtag = verbrauch_bisher - seit
            else:
                bis_stichtag = verbrauch_bisher * (stichtag - beginn).days / tage_gesamt
            grenzen.append(min(max(bis_stichtag, grenzen[-1]), verbrauch_bisher))
        grenzen.append(verbrauch_bisher)
        return grenzen

    def _log_verbrauch_status(
        self, sensor_id: str, verbrauch_bisher: float | None, diag: dict[str, Any]
    ) -> None:
        """Merkt sich, wie der Verbrauch ermittelt wurde, und schreibt genau
        eine Zeile ins Log, wenn er unbekannt wird (und eine, wenn er wieder
        da ist) -- ohne dass der Nutzer die Debug-Protokollierung einschalten
        muss. Kein Log bei jedem Update, damit das Protokoll nicht volllaeuft."""
        self.verbrauch_diag = {
            **diag,
            "sensor": sensor_id,
            "zeitpunkt": dt_util.now().isoformat(),
        }
        unbekannt = verbrauch_bisher is None
        if unbekannt and not self._verbrauch_war_unbekannt:
            _LOGGER.warning(
                "Tariffy '%s': Verbrauch bisher ist unbekannt (Sensor %s). Details: %s",
                self.entry.title, sensor_id, diag,
            )
        elif not unbekannt and self._verbrauch_war_unbekannt:
            _LOGGER.info(
                "Tariffy '%s': Verbrauch bisher ist wieder bekannt (%s).",
                self.entry.title, verbrauch_bisher,
            )
        self._verbrauch_war_unbekannt = unbekannt

    # --------------------------------------------------------------- Update
    async def _verbrauch_letzte_laufzeit_jetzt(self) -> float | None:
        """Verbrauch der (noch) laufenden Laufzeit bis jetzt, zum Einfrieren
        beim Wechsel — automatisch wie manuell identisch berechnet. Beim
        Zweizaehlertarif wird Hoch- + Niedertarif-Register kombiniert."""
        sensor_id = self.entry.data.get(CONF_VERBRAUCH_SENSOR)
        if not sensor_id:
            return None
        beginn = _parse_date(self.entry.data.get(CONF_BEGINN))
        if self._verbrauch_offset is None:
            # Cold start: the switch can fall into the very first refresh after
            # a Home Assistant start, before the contract-start meter value was
            # cached. Read it now, as the regular update does. A manually
            # entered start value keeps the previous behaviour (nothing frozen).
            if (
                beginn is None
                or _f(self.entry.data.get(CONF_VERBRAUCH_START_WERT)) is not None
            ):
                return None
            await self._get_verbrauch_offset(sensor_id, beginn)
            if self._verbrauch_offset is None:
                return None
        aktuell_sum, aktuell_state = await _get_latest_sum(self.hass, sensor_id)
        if aktuell_sum is None:
            state = self.hass.states.get(sensor_id)
            aktuell_sum = _f(state.state) if state else None
            aktuell_state = aktuell_sum
        if aktuell_sum is None:
            return None
        verbrauch_ht = _pick_verbrauch_delta(
            aktuell_sum, self._verbrauch_offset, aktuell_state, self._verbrauch_offset_state,
            await _einheit_faktor(self.hass, sensor_id),
        )
        if self.entry.data.get(CONF_SPARTE) != SPARTE_ELECTRICITY_HT_NT:
            return verbrauch_ht

        sensor_id_nt = self.entry.data.get(CONF_VERBRAUCH_SENSOR_NT)
        if (
            sensor_id_nt
            and self._verbrauch_offset_nt is None
            and beginn is not None
            and _f(self.entry.data.get(CONF_VERBRAUCH_START_WERT_NT)) is None
        ):
            await self._get_verbrauch_offset_nt(sensor_id_nt, beginn)
        if not sensor_id_nt or self._verbrauch_offset_nt is None:
            return verbrauch_ht
        aktuell_sum_nt, aktuell_state_nt = await _get_latest_sum(self.hass, sensor_id_nt)
        if aktuell_sum_nt is None:
            state_nt = self.hass.states.get(sensor_id_nt)
            aktuell_sum_nt = _f(state_nt.state) if state_nt else None
            aktuell_state_nt = aktuell_sum_nt
        if aktuell_sum_nt is None:
            return verbrauch_ht
        verbrauch_nt = _pick_verbrauch_delta(
            aktuell_sum_nt, self._verbrauch_offset_nt, aktuell_state_nt, self._verbrauch_offset_state_nt,
            await _einheit_faktor(self.hass, sensor_id_nt),
        )
        if verbrauch_ht is None and verbrauch_nt is None:
            return None
        return round((verbrauch_ht or 0.0) + (verbrauch_nt or 0.0), 2)

    async def _async_update_data(self) -> dict[str, Any]:
        heute = dt_util.now().date()

        nxt = dict(self.entry.options)
        wechsel = _parse_date(nxt.get(NEXT_PREFIX + CONF_BEGINN)) if nxt else None
        if wechsel is not None and wechsel <= heute:
            # Verbrauch der letzten Laufzeit vor dem Wechsel einfrieren
            _vll = await self._verbrauch_letzte_laufzeit_jetzt()
            self._switch_now(_vll)
            nxt = {}

        data = dict(self.entry.data)

        # Rueckwaertskompatibilitaet: alte Sparten-Werte migrieren
        if data.get(CONF_SPARTE) in SPARTEN_MIGRATION:
            migrated_sparte = SPARTEN_MIGRATION[data[CONF_SPARTE]]
            _LOGGER.debug(
                "Tariffy '%s': Sparte migriert von '%s' zu '%s'",
                self.entry.title, data[CONF_SPARTE], migrated_sparte,
            )
            data = {**data, CONF_SPARTE: migrated_sparte}
            # Entry dauerhaft aktualisieren
            self.hass.config_entries.async_update_entry(
                self.entry, data=data
            )

        ende = _parse_date(data.get(CONF_ENDE))
        beginn = _parse_date(data.get(CONF_BEGINN))
        # Kredit / Finanzierung: Tilgungsplan; das Ende ergibt sich aus Beginn und Laufzeit
        ist_kredit = data.get(CONF_SPARTE) == SPARTE_LOAN
        kredit: dict[str, Any] = loan.berechne(data, heute) if ist_kredit else {}
        if ist_kredit and kredit.get("ende") is not None:
            ende = kredit["ende"]
        restlaufzeit = (ende - heute).days if ende is not None else None
        laufzeit_monate = (ende - beginn).days / 30.44 if (beginn and ende) else 12.0

        arbeitspreis = _f(data.get(CONF_ARBEITSPREIS))

        # ---- Dynamischer Tarif (Boersen-/Spotpreis, z.B. Tibber/aWATTar) ----
        # Der Arbeitspreis wird hier durch den Statistik-Mittelwert des
        # referenzierten Preissensors (+ fester Aufschlag) ueber den
        # bisherigen Vertragszeitraum ERSETZT — alle nachfolgenden Formeln
        # (kosten_bisher, geschaetzte_kosten_real, abschlag_anpassung_empfohlen,
        # jahreskosten, ...) bleiben dadurch unveraendert und rechnen einfach
        # mit dem effektiven Durchschnittspreis statt einem fixen Wert.
        # Liegt (noch) keine Statistik vor, bleibt der manuell eingegebene
        # arbeitspreis als Fallback erhalten.
        arbeitspreis_sensor = data.get(CONF_ARBEITSPREIS_SENSOR)
        arbeitspreis_aufschlag = _f(data.get(CONF_ARBEITSPREIS_AUFSCHLAG)) or 0.0
        arbeitspreis_aktuell: float | None = None
        if arbeitspreis_sensor:
            state = self.hass.states.get(arbeitspreis_sensor)
            live = _f(state.state) if state else None
            if live is not None:
                arbeitspreis_aktuell = round(live + arbeitspreis_aufschlag, 6)
            if beginn is not None:
                tz = dt_util.get_default_time_zone()
                start_dt = datetime.combine(beginn, datetime.min.time()).replace(tzinfo=tz)
                avg = await _get_average_price(
                    self.hass, arbeitspreis_sensor, start_dt, dt_util.now()
                )
                if avg is not None:
                    arbeitspreis = round(avg + arbeitspreis_aufschlag, 6)

        grundpreis = _f(data.get(CONF_GRUNDPREIS))
        abschlag = _f(data.get(CONF_ABSCHLAG))

        # ---- Preisaenderungen im laufenden Vertrag (nur Strom/Gas, fester Arbeitspreis) ----
        # Ab hier sind arbeitspreis/grundpreis/abschlag die heute gueltigen Werte. Die Summen
        # ueber die Laufzeit (Abschlaege, Kosten, Prognose) rechnet weiter unten der Block
        # "Preisaenderungen: Summen je Abschnitt" je Abschnitt mit dessen Preisen.
        preis_segmente: list[dict[str, Any]] = []
        if (
            data.get(CONF_SPARTE) in PREISAENDERUNG_SPARTEN
            and data.get(CONF_PREISAENDERUNGEN)
            and beginn is not None
            and not arbeitspreis_sensor
        ):
            preis_segmente = _preis_segmente(data, beginn)
            if len(preis_segmente) > 1:
                gueltig = _segment_am(preis_segmente, heute)
                arbeitspreis = gueltig["arbeitspreis"]
                grundpreis = gueltig["grundpreis"]
                abschlag = gueltig["abschlag"]
            else:
                preis_segmente = []
        naechste_preisaenderung = next(
            (s["von"] for s in preis_segmente if s["von"] > heute), None
        )

        verbrauch = _f(data.get(CONF_JAHRESVERBRAUCH))
        brennwert = _f(data.get(CONF_BRENNWERT))
        zustandszahl = _f(data.get(CONF_ZUSTANDSZAHL))
        sparte = data.get(CONF_SPARTE)

        # Gasverbrauch -> kWh (einheitenabhängig)
        gas_einheit = data.get(CONF_GAS_EINHEIT, "m³")
        # Umrechnungsfaktoren zu kWh (ohne Brennwert)
        # Therm und MBtu haben festen kWh-Wert, m³/CCF/ft³ brauchen Brennwert
        GAS_KWH_FAKTOR = {
            "therm": 29.3071,   # 1 therm = 29.3071 kWh
            "MBtu": 293.071,    # 1 MBtu = 293.071 kWh
            "kWh": 1.0,
        }
        # Wasser direkt in Einheit (keine kWh-Umrechnung nötig)
        if sparte == WASSER_SPARTE:
            verbrauch_kwh = verbrauch  # bleibt in Wasser-Einheit (m³, L, gal, …)
        elif sparte == GAS_SPARTE:
            if gas_einheit in GAS_KWH_FAKTOR:
                verbrauch_kwh = (
                    round(verbrauch * GAS_KWH_FAKTOR[gas_einheit], 1)
                    if verbrauch else None
                )
            else:
                # m³, CCF, ft³ → brauchen Brennwert × Zustandszahl
                verbrauch_kwh = (
                    round(verbrauch * brennwert * (zustandszahl or 1.0), 1)
                    if (verbrauch and brennwert)
                    else None
                )
        else:
            verbrauch_kwh = verbrauch

        # Währung aus HA-Konfiguration (ISO-4217-Code, z.B. "EUR")
        currency = self.hass.config.currency or "EUR"

        # Wasser: Abwasser-Berechnung je nach Typ
        arbeitspreis_abwasser_raw = _f(data.get(CONF_ARBEITSPREIS_ABWASSER))
        abwasser_typ = data.get(CONF_ABWASSER_TYP, ABWASSER_TYP_PREIS)
        wasser_einheit = data.get(CONF_WASSER_EINHEIT, "m³")
        arbeitspreis_abwasser = arbeitspreis_abwasser_raw
        arbeitspreis_gesamt_wasser: float | None = None
        abwasser_pauschal_monat: float | None = None

        if sparte == WASSER_SPARTE and arbeitspreis is not None:
            if abwasser_typ == ABWASSER_TYP_PREIS and arbeitspreis_abwasser_raw is not None:
                # Fixer Preis/Einheit (DE/AT/CH): einfach addieren
                arbeitspreis_gesamt_wasser = round(arbeitspreis + arbeitspreis_abwasser_raw, 6)

            elif abwasser_typ == ABWASSER_TYP_PROZENT and arbeitspreis_abwasser_raw is not None:
                # % des Frischwasserpreises (US/UK/AU)
                # arbeitspreis_abwasser_raw = Prozentsatz (z.B. 80 = 80%)
                abwasser_preis_berechnet = round(arbeitspreis * arbeitspreis_abwasser_raw / 100, 6)
                arbeitspreis_gesamt_wasser = round(arbeitspreis + abwasser_preis_berechnet, 6)
                arbeitspreis_abwasser = abwasser_preis_berechnet  # für Sensor-Anzeige

            elif abwasser_typ == ABWASSER_TYP_PAUSCHAL and arbeitspreis_abwasser_raw is not None:
                # Pauschal pro Monat (FR/BE)
                abwasser_pauschal_monat = arbeitspreis_abwasser_raw
                arbeitspreis_gesamt_wasser = arbeitspreis  # Arbeitspreis bleibt gleich

        abschlagssumme = round(abschlag * laufzeit_monate, 2) if abschlag is not None else None

        # Jahreswerte und Zahlweise (nur Anzeige im Panel, keine neuen Sensoren): `abschlag` ist
        # immer der Betrag pro Monat, die Zahlweise sagt nur, wie er bezahlt wird.
        zahlweise = data.get(CONF_ZAHLWEISE)
        if zahlweise not in ZAHLWEISE_MONATE:
            zahlweise = DEFAULT_ZAHLWEISE
        abschlag_jahr = round(abschlag * 12, 2) if abschlag is not None else None
        abschlag_je_zahlung = (
            round(abschlag * ZAHLWEISE_MONATE[zahlweise], 2) if abschlag is not None else None
        )
        kosten_jahr = (
            round((abschlag + (grundpreis or 0)) * 12, 2) if abschlag is not None else None
        )

        # Bis heute faellige Abschlaege. Bei Pauschalvertraegen (Internet, Mobilfunk, ...)
        # sind das die Kosten bisher. Bewusst nur ein Datenwert, kein Sensor: bestehende
        # Installationen bekommen dadurch keine neuen Entitaeten.
        abschlag_bisher_gezahlt_wert: float | None = None
        naechste_zahlung: date | None = None
        if abschlag is not None and beginn is not None:
            if sparte not in METERED_SPARTEN:
                # Pauschalvertrag: gezaehlt werden die Abbuchungen (jede Rate am Tag der Zahlung, bei
                # monatlicher Zahlung also am Monatstag des Beginns), nicht ein tagesgenauer Anteil
                abschlag_bisher_gezahlt_wert, naechste_zahlung = _abschlag_nach_zahlungen(
                    abschlag, ZAHLWEISE_MONATE[zahlweise], beginn, ende, heute, data
                )
                if zahlweise == DEFAULT_ZAHLWEISE:
                    naechste_zahlung = None  # bei monatlicher Zahlung gibt es dazu keine Anzeige
            else:
                monate_bisher = max(0, (heute - beginn).days) / 30.44
                if ende is not None:
                    monate_bisher = min(monate_bisher, laufzeit_monate)
                abschlag_bisher_gezahlt_wert = round(
                    _abschlag_bisher_gezahlt(abschlag, monate_bisher, beginn, data), 2
                )

        # ---- Echte Verbrauchsmessung via Sensor ----
        verbrauch_sensor = data.get(CONF_VERBRAUCH_SENSOR)
        verbrauch_bisher: float | None = None
        verbrauch_hochgerechnet: float | None = None
        prognose_real: float | None = None
        # Kosten bis Vertragsende (Hochrechnung), fuer die Abschlagszahlungen weiter unten
        geschaetzt_gesamt: float | None = None
        abschlag_anpassung_empfohlen: float | None = None

        diag: dict[str, Any] = {}
        if verbrauch_sensor and beginn:
            manueller_start_wert = _f(data.get(CONF_VERBRAUCH_START_WERT))
            if manueller_start_wert is not None:
                # Manueller Override hat Vorrang vor der automatischen
                # LTS-Erkennung -- deckt den Fall ab, dass die Recorder-
                # Statistik des Sensors nicht bis zum Vertragsbeginn
                # zurueckreicht (z.B. nach einem Rename/Neuanlegen des
                # Sensors). WICHTIG: der Nutzer traegt hier den ECHTEN,
                # rohen Zaehlerstand ein -- das muss gegen den ECHTEN
                # aktuellen Zaehlerstand gerechnet werden, nicht gegen die
                # reset-sichere 'sum'-Statistik, deren Baseline ein
                # beliebiger, unbekannter Punkt in der Vergangenheit ist
                # (sum und roher Zustand unterscheiden sich sonst um genau
                # diese Baseline, was das Ergebnis unbrauchbar macht).
                _live = self.hass.states.get(verbrauch_sensor)
                aktuell = await self._aktueller_zaehlerstand(verbrauch_sensor)
                diag = {
                    "pfad": "manueller_startwert",
                    "startwert": manueller_start_wert,
                    "live_zustand": _live.state if _live else "fehlt",
                    "aktueller_zaehlerstand": aktuell,
                }
                if aktuell is not None:
                    verbrauch_bisher = round(
                        (aktuell - manueller_start_wert) * await _einheit_faktor(self.hass, verbrauch_sensor), 2
                    )
            else:
                # Aktuellen kumulierten Wert lesen (reset-sicher über 'sum', s.o.),
                # PLUS den dazugehoerigen rohen state -- als Absicherung gegen
                # Anomalien in der 'sum'-Statistik selbst (siehe
                # _pick_verbrauch_delta()). Fallback auf den rohen Live-Zustand,
                # falls die Statistik (noch) keinen Sum-Wert liefert (z.B.
                # brandneuer Sensor).
                aktuell_sum, aktuell_state = await _get_latest_sum(self.hass, verbrauch_sensor)
                live_state = self.hass.states.get(verbrauch_sensor)
                live_val = _f(live_state.state) if live_state else None
                if aktuell_sum is None:
                    aktuell_sum = live_val
                # Der aktuelle Live-Zustand ist praeziser/aktueller als der
                # ggf. etwas verzoegerte Statistik-state-Wert.
                if live_val is not None:
                    aktuell_state = live_val

                diag = {
                    "pfad": "statistik",
                    "live_zustand": live_state.state if live_state else "fehlt",
                    "aktuell_sum": aktuell_sum,
                    "aktuell_state": aktuell_state,
                }
                if aktuell_sum is not None:
                    offset_sum, offset_state = await self._get_verbrauch_offset(verbrauch_sensor, beginn)
                    diag["offset_sum"] = offset_sum
                    diag["offset_state"] = offset_state
                    verbrauch_bisher = _pick_verbrauch_delta(
                        aktuell_sum, offset_sum, aktuell_state, offset_state,
                        await _einheit_faktor(self.hass, verbrauch_sensor),
                    )

                if verbrauch_bisher is None:
                    # LTS für Vertragsbeginn nicht verfügbar — Sensor bleibt unbekannt.
                    # Kein Fallback-Offset setzen: beim nächsten Refresh erneut abfragen.
                    _LOGGER.debug(
                        "Tariffy '%s': Kein LTS-Offset für %s am %s — "
                        "verbrauch_bisher unbekannt.",
                        self.entry.title, verbrauch_sensor, beginn,
                    )

        if verbrauch_sensor and beginn:
            diag["ergebnis"] = verbrauch_bisher
            self._log_verbrauch_status(verbrauch_sensor, verbrauch_bisher, diag)

        # ---- Zweizaehlertarif: Niedertarif-Register dazu addieren ----
        # verbrauch_bisher (oben) ist an dieser Stelle das Hochtarif-Register
        # (verbrauch_sensor wird beim Zweizaehlertarif implizit als HT
        # verwendet, siehe const.py). Wird zu einem kombinierten Wert
        # aufaddiert; arbeitspreis wird auf den gewichteten Durchschnitt
        # beider Register umgestellt, damit Hochrechnung/kosten_bisher/
        # Prognose/Abschlag-Empfehlung unten UNVERAENDERT weiterrechnen
        # koennen, einfach mit diesem effektiven Preis statt einem festen.
        verbrauch_bisher_ht: float | None = None
        verbrauch_bisher_nt: float | None = None
        arbeitspreis_nt = _f(data.get(CONF_ARBEITSPREIS_NT))
        arbeitspreis_ht = arbeitspreis  # unvermischter HT-Preis, fuer kosten_ht_bisher unten
        kosten_ht_bisher: float | None = None
        kosten_nt_bisher: float | None = None
        if sparte == SPARTE_ELECTRICITY_HT_NT:
            verbrauch_bisher_ht = verbrauch_bisher
            verbrauch_sensor_nt = data.get(CONF_VERBRAUCH_SENSOR_NT)
            manueller_start_wert_nt = _f(data.get(CONF_VERBRAUCH_START_WERT_NT))
            verbrauch_bisher_nt = await self._verbrauch_bisher_fuer_sensor(
                verbrauch_sensor_nt, manueller_start_wert_nt, beginn, self._get_verbrauch_offset_nt
            )
            if verbrauch_bisher_ht is not None and arbeitspreis_ht is not None:
                kosten_ht_bisher = round(verbrauch_bisher_ht * arbeitspreis_ht, 2)
            if verbrauch_bisher_nt is not None and arbeitspreis_nt is not None:
                kosten_nt_bisher = round(verbrauch_bisher_nt * arbeitspreis_nt, 2)
            if verbrauch_bisher_ht is not None or verbrauch_bisher_nt is not None:
                verbrauch_bisher = round((verbrauch_bisher_ht or 0.0) + (verbrauch_bisher_nt or 0.0), 2)
                if (
                    arbeitspreis is not None
                    and arbeitspreis_nt is not None
                    and verbrauch_bisher > 0
                ):
                    arbeitspreis = round(
                        (
                            (verbrauch_bisher_ht or 0.0) * arbeitspreis
                            + (verbrauch_bisher_nt or 0.0) * arbeitspreis_nt
                        )
                        / verbrauch_bisher,
                        6,
                    )

        aussen: dict[str, Any] = {}
        if sparte == WASSER_SPARTE and data.get(CONF_AUSSENZAEHLER):
            aussen = await self._aussenzaehler(
                list(data[CONF_AUSSENZAEHLER]), beginn, verbrauch_bisher, verbrauch_sensor,
                arbeitspreis, arbeitspreis_gesamt_wasser,
            )
            if aussen["preis"] is not None:
                # wie beim Zweizaehlertarif: ab hier rechnet alles mit dem gewichteten Preis weiter
                arbeitspreis_gesamt_wasser = aussen["preis"]

        if verbrauch_bisher is not None and verbrauch_bisher > 0:
            vergangene_tage = (heute - beginn).days
            if vergangene_tage > 0:
                # Hochrechnung auf die tatsächliche Vertragslaufzeit,
                # nicht auf ein festes Kalenderjahr — ein Vertrag kann
                # auch mitten im Jahr beginnen/enden.
                vertrag_gesamttage = (ende - beginn).days if ende is not None else 365
                verbrauch_hochgerechnet = round(
                    verbrauch_bisher / vergangene_tage * vertrag_gesamttage, 1
                )
                # Bei Gas: m³ hochgerechnet → kWh
                if sparte == GAS_SPARTE and brennwert and zustandszahl:
                    verbrauch_kwh_real = round(
                        verbrauch_hochgerechnet * brennwert * zustandszahl, 1
                    )
                else:
                    verbrauch_kwh_real = verbrauch_hochgerechnet

                ap_real = (
                    arbeitspreis_gesamt_wasser
                    if sparte == WASSER_SPARTE and arbeitspreis_gesamt_wasser is not None
                    else arbeitspreis
                )
                if ap_real is not None:
                    # verbrauch_kwh_real ist bereits auf die gesamte
                    # Vertragslaufzeit hochgerechnet (s.o.), braucht
                    # hier keinen weiteren laufzeit_faktor mehr.
                    geschaetzte_kosten_real = round(
                        verbrauch_kwh_real * ap_real
                        + (grundpreis or 0) * laufzeit_monate,
                        2,
                    )
                    geschaetzt_gesamt = geschaetzte_kosten_real
                    if abschlag is not None:
                        # Abschlaege ueber die gesamte Laufzeit: das, was bis
                        # heute wirklich gezahlt wurde (eine Abschlags-Aenderung
                        # zaehlt erst ab ihrem Datum, siehe
                        # _abschlag_bisher_gezahlt) plus der aktuelle Abschlag
                        # fuer die Restlaufzeit. Ohne Aenderung ergibt das genau
                        # abschlag x Laufzeit, wie bisher.
                        _monate_bisher = vergangene_tage / 30.44
                        abschlag_gesamt = round(
                            _abschlag_bisher_gezahlt(
                                abschlag, _monate_bisher, beginn, data
                            )
                            + abschlag * (laufzeit_monate - _monate_bisher),
                            2,
                        )
                        prognose_real = round(
                            abschlag_gesamt - geschaetzte_kosten_real, 2
                        )
                    # Abschlags-Anpassung fuer den REST des laufenden
                    # Vertrags: was muesste der Abschlag ab jetzt sein,
                    # um unter Beruecksichtigung des bereits gezahlten
                    # Abschlags bis Vertragsende exakt auszugleichen?
                    if abschlag is not None:
                        vergangene_monate_hier = vergangene_tage / 30.44
                        restlaufzeit_monate = laufzeit_monate - vergangene_monate_hier
                        if restlaufzeit_monate > 0:
                            abschlag_bisher_gezahlt = _abschlag_bisher_gezahlt(
                                abschlag, vergangene_monate_hier, beginn, data
                            )
                            abschlag_anpassung_empfohlen = round(
                                (geschaetzte_kosten_real - abschlag_bisher_gezahlt)
                                / restlaufzeit_monate,
                                2,
                            )

        einmal_liste, einmal_gesamt, einmal_laufend = _einmal_summen(data, beginn, heute)

        # Tatsächliche Kosten bisher (auf Basis realer Messung)
        kosten_bisher: float | None = None
        guthaben_bisher: float | None = None
        if verbrauch_bisher is not None and beginn is not None:
            _vergangene_tage_kb = (heute - beginn).days
            if _vergangene_tage_kb >= 0:
                _vergangene_monate_kb = _vergangene_tage_kb / 30.44
                if sparte == GAS_SPARTE and brennwert and zustandszahl:
                    _vb_abr = verbrauch_bisher * brennwert * zustandszahl
                else:
                    _vb_abr = verbrauch_bisher
                _ap_kb = (
                    arbeitspreis_gesamt_wasser
                    if sparte == WASSER_SPARTE and arbeitspreis_gesamt_wasser is not None
                    else arbeitspreis
                )
                if _ap_kb is not None:
                    kosten_bisher = round(
                        _vb_abr * _ap_kb + (grundpreis or 0) * _vergangene_monate_kb,
                        2,
                    )
                    # Guthaben/Nachzahlung bisher: Abschlag, der bis heute
                    # tatsaechlich faellig war, gegen die echten Kosten bisher —
                    # anders als prognose_real KEINE Hochrechnung auf die
                    # gesamte Vertragslaufzeit, sondern der Stand von heute.
                    if abschlag is not None:
                        guthaben_bisher = round(
                            _abschlag_bisher_gezahlt(
                                abschlag, _vergangene_monate_kb, beginn, data
                            )
                            - kosten_bisher,
                            2,
                        )

        # ---- Preisaenderungen: Summen je Abschnitt ----
        # Mit Preisaenderungen im Vertrag rechnen Abschlaege, Grundpreis und Arbeitspreis je
        # Abschnitt mit dessen Werten; die einfachen Formeln oben (ein Preis fuer die ganze
        # Laufzeit) werden hier ersetzt. Ohne Preisaenderung bleibt alles wie oben.
        if preis_segmente:
            ab_segmente = _abschlag_segmente(preis_segmente, beginn, data)
            m_heute = max(0, (heute - beginn).days) / _MONAT_TAGE
            if abschlag is not None:
                abschlagssumme_neu = _integral_monate(ab_segmente, "abschlag", beginn, 0, laufzeit_monate)
                abschlagssumme = round(abschlagssumme_neu, 2) if abschlagssumme_neu is not None else None
                gezahlt = _integral_monate(
                    ab_segmente, "abschlag", beginn, 0, min(m_heute, laufzeit_monate)
                )
                abschlag_bisher_gezahlt_wert = round(gezahlt, 2) if gezahlt is not None else None
            gas_faktor = (
                brennwert * zustandszahl
                if sparte == GAS_SPARTE and brennwert and zustandszahl
                else 1.0
            )
            kosten_bisher = None
            guthaben_bisher = None
            prognose_real = None
            abschlag_anpassung_empfohlen = None
            geschaetzt_gesamt = None
            if verbrauch_bisher is not None and heute >= beginn:
                grenzen = await self._verbrauch_grenzen(
                    preis_segmente, beginn, heute, verbrauch_bisher, verbrauch_sensor
                )
                ap_kosten = _arbeitspreis_kosten(preis_segmente, grenzen, gas_faktor)
                gp_bisher = _integral_monate(preis_segmente, "grundpreis", beginn, 0, m_heute)
                if ap_kosten is not None:
                    kosten_bisher = round(ap_kosten + (gp_bisher or 0.0), 2)
                    ab_bisher = _integral_monate(ab_segmente, "abschlag", beginn, 0, m_heute)
                    if abschlag is not None and ab_bisher is not None:
                        guthaben_bisher = round(ab_bisher - kosten_bisher, 2)
                    # Prognose zum Vertragsende: bisherige Kosten plus der hochgerechnete
                    # Restverbrauch, je Abschnitt mit dessen Arbeitspreis
                    if verbrauch_hochgerechnet is not None and (heute - beginn).days > 0:
                        vertrag_ende = ende if ende is not None else beginn + timedelta(days=365)
                        rest_menge = max(0.0, verbrauch_hochgerechnet - verbrauch_bisher) * gas_faktor
                        rest_ap = _rest_arbeitspreis_kosten(preis_segmente, heute, vertrag_ende, rest_menge)
                        rest_gp = _integral_monate(
                            preis_segmente, "grundpreis", beginn, m_heute, laufzeit_monate
                        )
                        if rest_ap is not None:
                            geschaetzt = ap_kosten + (gp_bisher or 0.0) + rest_ap + (rest_gp or 0.0)
                            geschaetzt_gesamt = geschaetzt
                            ab_gesamt = _integral_monate(ab_segmente, "abschlag", beginn, 0, laufzeit_monate)
                            if abschlag is not None and ab_gesamt is not None:
                                prognose_real = round(ab_gesamt - geschaetzt, 2)
                            rest_monate = laufzeit_monate - m_heute
                            if abschlag is not None and ab_bisher is not None and rest_monate > 0:
                                abschlag_anpassung_empfohlen = round(
                                    (geschaetzt - ab_bisher) / rest_monate, 2
                                )

        # ---- Abschlaege als gebuchte Monatsraten (Strom/Gas mit "Erste Abschlagszahlung") ----
        # Statt tagesgenau ab Vertragsbeginn zaehlen die monatlichen Abbuchungen ab dem ersten
        # Abschlag: eine Rate zaehlt ab Beginn ihres Monats, mit dem dann gueltigen Abschlag
        # (Preisaenderungen und eine Abschlagsaenderung beim Bearbeiten eingeschlossen).
        erste_abschlag = _parse_date(data.get(CONF_ERSTE_ZAHLUNG))
        if (
            erste_abschlag is not None
            and sparte in ENERGIE_SPARTEN
            and beginn is not None
            and abschlag is not None
        ):
            basis_segmente = preis_segmente or [
                {"von": beginn, "arbeitspreis": None, "grundpreis": None,
                 "abschlag": _f(data.get(CONF_ABSCHLAG)), "zaehlerstand": None}
            ]
            vertrag_ende_z = ende if ende is not None else beginn + timedelta(days=365)
            bisher_z, gesamt_z, rest_z = _abschlag_zahlungen(
                _abschlag_segmente(basis_segmente, beginn, data), erste_abschlag, vertrag_ende_z, heute
            )
            abschlag_bisher_gezahlt_wert = bisher_z
            abschlagssumme = gesamt_z
            guthaben_bisher = round(bisher_z - kosten_bisher, 2) if kosten_bisher is not None else None
            if geschaetzt_gesamt is not None:
                prognose_real = round(gesamt_z - geschaetzt_gesamt, 2)
                abschlag_anpassung_empfohlen = (
                    round((geschaetzt_gesamt - bisher_z) / rest_z, 2) if rest_z > 0 else None
                )
            rest_zahlungen_bonus: int | None = rest_z
        else:
            rest_zahlungen_bonus = None

        # ---- Neukundenbonus: wird mit der Jahresrechnung verrechnet ----
        # Er verbessert das Ergebnis zum Vertragsende (Prognose) und senkt den Abschlag, der fuer
        # die Restlaufzeit noetig ist. Den Saldo heute aendert er nicht: gutgeschrieben wird er
        # erst mit der Rechnung. Der Sofortbonus (schon ausgezahlt) zaehlt nur in den Gesamtkosten.
        neukundenbonus = _f(data.get(CONF_BONUS)) or 0.0
        sofortbonus = _f(data.get(CONF_SOFORTBONUS)) or 0.0
        if neukundenbonus and beginn is not None:
            if prognose_real is not None:
                prognose_real = round(prognose_real + neukundenbonus, 2)
            rest_monate_bonus = (
                rest_zahlungen_bonus
                if rest_zahlungen_bonus is not None
                else laufzeit_monate - max(0, (heute - beginn).days) / _MONAT_TAGE
            )
            if abschlag_anpassung_empfohlen is not None and rest_monate_bonus > 0:
                abschlag_anpassung_empfohlen = round(
                    abschlag_anpassung_empfohlen - neukundenbonus / rest_monate_bonus, 2
                )

        # Tag/Nacht-Tarif (Economy 7 / TOU)
        arbeitspreis_nacht = _f(data.get(CONF_ARBEITSPREIS_NACHT))
        verbrauch_tag = _f(data.get(CONF_JAHRESVERBRAUCH_TAG))
        verbrauch_nacht = _f(data.get(CONF_JAHRESVERBRAUCH_NACHT))
        hat_tag_nacht = (
            arbeitspreis_nacht is not None
            and arbeitspreis is not None
            and verbrauch_tag is not None
            and verbrauch_nacht is not None
        )
        if hat_tag_nacht:
            tou_jahreskosten: float | None = round(
                verbrauch_tag * arbeitspreis + verbrauch_nacht * arbeitspreis_nacht, 2
            )
            # Effektiver Durchschnittspreis
            gesamt_verbrauch = (verbrauch_tag or 0) + (verbrauch_nacht or 0)
            if gesamt_verbrauch > 0:
                verbrauch_kwh = gesamt_verbrauch
        else:
            tou_jahreskosten = None

        # Tiered / Staffelpreise
        ist_tiered = bool(data.get(CONF_TIERED))
        tier_limits, tier_prices = _extract_tiers(data)
        tiered_jahreskosten: float | None = None
        effektiver_arbeitspreis: float | None = None  # Durchschnittspreis für Anzeige

        if ist_tiered and tier_prices and verbrauch_kwh:
            tiered_jahreskosten = _tiered_cost(verbrauch_kwh, tier_limits, tier_prices)
            if tiered_jahreskosten is not None and verbrauch_kwh > 0:
                effektiver_arbeitspreis = round(tiered_jahreskosten / verbrauch_kwh, 6)

        # Einspeiseverguetung — nur als Eingabewert gespeichert, keine Berechnung
        einspeiseverguetung = _f(data.get(CONF_EINSPEISEVERGUETUNG))

        # Verbrauch letzte Laufzeit (eingefroren beim Tarifwechsel)
        verbrauch_letzte_laufzeit = _f(data.get(CONF_VERBRAUCH_LETZTE_LAUFZEIT))
        verbrauch_letzte_laufzeit_monate = _f(data.get(CONF_VERBRAUCH_LETZTE_LAUFZEIT_MONATE))

        # Empfohlener Abschlag auf Basis der letzten Vertragslaufzeit
        empfohlener_abschlag: float | None = None
        if verbrauch_letzte_laufzeit is not None and verbrauch_letzte_laufzeit > 0:
            # Gas: m³ → kWh
            if sparte == GAS_SPARTE and brennwert and zustandszahl:
                _vll_kwh = verbrauch_letzte_laufzeit * brennwert * zustandszahl
            else:
                _vll_kwh = verbrauch_letzte_laufzeit
            _ap = (
                arbeitspreis_gesamt_wasser
                if sparte == WASSER_SPARTE and arbeitspreis_gesamt_wasser is not None
                else arbeitspreis
            )
            # Tatsächliche Dauer der letzten Laufzeit verwenden, nicht fest 12
            # Monate — nur als Fallback für vor diesem Fix eingefrorene Werte.
            _monate_alt = (
                verbrauch_letzte_laufzeit_monate
                if verbrauch_letzte_laufzeit_monate and verbrauch_letzte_laufzeit_monate > 0
                else 12.0
            )
            if _ap is not None:
                empfohlener_abschlag = round(
                    (_vll_kwh * _ap + (grundpreis or 0) * _monate_alt) / _monate_alt, 2
                )

        monate = int(data.get(CONF_ERINNERUNG_MONATE) or 0)
        bestaetigt = bool(data.get(CONF_KUENDIGUNG_BESTAETIGT))
        frist = _monate_wert(data.get(CONF_KUENDIGUNGSFRIST))
        mindest = _monate_wert(data.get(CONF_MINDESTLAUFZEIT))
        # letzter Tag, an dem noch gekuendigt werden kann; die Erinnerung kommt `monate` davor
        kuendigung_bis = _minus_months(ende, frist) if (ende is not None and frist) else None
        erinnerung_datum = (
            _minus_months(ende, monate + frist) if (ende is not None and monate) else None
        )
        mindestlaufzeit_bis = _plus_months(beginn, mindest) if (beginn is not None and mindest) else None
        archiviert_am = _parse_date(data.get(CONF_ARCHIVIERT))
        archiviert = archiviert_am is not None
        aktiv = bool(
            erinnerung_datum is not None
            and ende is not None
            and erinnerung_datum <= heute <= ende
            and not bestaetigt
            and not archiviert
        )
        await self._handle_reminder(aktiv, ende, data)

        warnung_schwelle = float(
            data.get(CONF_ABSCHLAG_WARNUNG_SCHWELLE) or DEFAULT_ABSCHLAG_WARNUNG_SCHWELLE
        )
        warnung_aktiv = bool(
            data.get(CONF_ABSCHLAG_WARNUNG)
            and prognose_real is not None
            and prognose_real < -warnung_schwelle
            and not archiviert
        )
        await self._handle_abschlag_warnung(
            warnung_aktiv, prognose_real, abschlag_anpassung_empfohlen, data
        )
        # warnung_aktiv bleibt bewusst unabhaengig von "bestaetigt" (siehe
        # _handle_abschlag_warnung), damit sich eine erneut verschlechterte
        # Prognose spaeter wieder frisch meldet. Fuer die Button-Sichtbarkeit
        # (soll nach Bestaetigen verschwinden) daher ein separates Flag.
        warnung_zu_bestaetigen = bool(
            warnung_aktiv and not data.get(CONF_ABSCHLAG_WARNUNG_BESTAETIGT)
        )

        return {
            "currency": currency,
            "gas_einheit": gas_einheit,
            "wasser_einheit": wasser_einheit,
            CONF_ARBEITSPREIS_ABWASSER: arbeitspreis_abwasser,
            CONF_ABWASSER_TYP: abwasser_typ,
            "arbeitspreis_gesamt_wasser": arbeitspreis_gesamt_wasser,
            "abwasser_pauschal_monat": abwasser_pauschal_monat,
            "aussenzaehler": aussen.get("liste") or [],
            "garten_verbrauch": aussen.get("garten_verbrauch"),
            "garten_kosten": aussen.get("garten_kosten"),
            "abwasserpflichtiger_verbrauch": aussen.get("abwasserpflichtig"),
            "abwasser_kosten": aussen.get("abwasser_kosten"),
            "hat_tag_nacht": hat_tag_nacht,
            CONF_EINSPEISEVERGUETUNG: einspeiseverguetung,
            CONF_ARBEITSPREIS_NACHT: arbeitspreis_nacht,
            CONF_JAHRESVERBRAUCH_TAG: verbrauch_tag,
            CONF_JAHRESVERBRAUCH_NACHT: verbrauch_nacht,
            "tou_jahreskosten": tou_jahreskosten,
            "ist_tiered": ist_tiered,
            "tier_limits": tier_limits,
            "tier_prices": tier_prices,
            "tiered_jahreskosten": tiered_jahreskosten,
            "effektiver_arbeitspreis": effektiver_arbeitspreis,
            CONF_SPARTE: sparte,
            CONF_ANBIETER: data.get(CONF_ANBIETER),
            CONF_KUNDENNUMMER: data.get(CONF_KUNDENNUMMER),
            CONF_TARIF: data.get(CONF_TARIF),
            CONF_ARBEITSPREIS: arbeitspreis,
            CONF_ARBEITSPREIS_SENSOR: arbeitspreis_sensor,
            CONF_ARBEITSPREIS_AUFSCHLAG: arbeitspreis_aufschlag,
            "arbeitspreis_aktuell": arbeitspreis_aktuell,
            CONF_GRUNDPREIS: grundpreis,
            CONF_ABSCHLAG: abschlag,
            CONF_JAHRESVERBRAUCH: verbrauch,
            CONF_BRENNWERT: brennwert,
            CONF_ZUSTANDSZAHL: zustandszahl,
            "verbrauch_kwh": verbrauch_kwh,
            CONF_ZAEHLERNUMMER: data.get(CONF_ZAEHLERNUMMER),
            CONF_VERSICHERUNGSART: data.get(CONF_VERSICHERUNGSART),
            CONF_KENNZEICHEN: data.get(CONF_KENNZEICHEN),
            CONF_KM_START: _f(data.get(CONF_KM_START)),
            CONF_KM_PRO_JAHR: _f(data.get(CONF_KM_PRO_JAHR)),
            CONF_MARKTLOKATION: data.get(CONF_MARKTLOKATION),
            CONF_PREISGARANTIE: _parse_date(data.get(CONF_PREISGARANTIE)),
            CONF_BONUS: _f(data.get(CONF_BONUS)),
            CONF_SOFORTBONUS: _f(data.get(CONF_SOFORTBONUS)),
            # eine beim Bearbeiten gemerkte Abschlagsaenderung (vorheriger Wert bis zum Datum)
            "abschlag_aenderung": (
                {
                    "datum": data.get(CONF_ABSCHLAG_AB_DATUM),
                    "vorher": _f(data.get(CONF_ABSCHLAG_VORHERIGER_WERT)),
                }
                if data.get(CONF_ABSCHLAG_AB_DATUM) and _f(data.get(CONF_ABSCHLAG_VORHERIGER_WERT))
                else None
            ),
            CONF_OEKOSTROM: bool(data.get(CONF_OEKOSTROM)),
            CONF_BEGINN: beginn,
            CONF_ENDE: ende,
            "restlaufzeit": restlaufzeit,
            "jahreskosten": abschlagssumme,
            "zahlweise": zahlweise,
            "abschlag_jahr": abschlag_jahr,
            "abschlag_je_zahlung": abschlag_je_zahlung,
            "kosten_jahr": kosten_jahr,
            "erste_zahlung": _parse_date(data.get(CONF_ERSTE_ZAHLUNG)),
            "naechste_zahlung": naechste_zahlung,
            "abschlag_bisher_gezahlt": abschlag_bisher_gezahlt_wert,
            "preisaenderungen": _preisaenderungen_anzeige(data, beginn, heute),
            "naechste_preisaenderung": naechste_preisaenderung,
            "preisaenderung_moeglich": bool(
                sparte in PREISAENDERUNG_SPARTEN
                and not arbeitspreis_sensor
                and beginn is not None
                and not archiviert
            ),
            "preis_basis": (
                {
                    "arbeitspreis": _f(data.get(CONF_ARBEITSPREIS)),
                    "grundpreis": _f(data.get(CONF_GRUNDPREIS)),
                    "abschlag": _f(data.get(CONF_ABSCHLAG)),
                }
                if data.get(CONF_PREISAENDERUNGEN)
                else None
            ),
            "verbrauch_bisher": verbrauch_bisher,
            "verbrauch_hochgerechnet": verbrauch_hochgerechnet,
            "prognose_real": prognose_real,
            "abschlag_anpassung_empfohlen": abschlag_anpassung_empfohlen,
            "kosten_bisher": kosten_bisher,
            "guthaben_bisher": (
                round(guthaben_bisher + einmal_laufend, 2) if guthaben_bisher is not None else None
            ),
            "einmalposten": einmal_liste,
            "einmal_summe": einmal_gesamt,
            "verbrauch_letzte_laufzeit": verbrauch_letzte_laufzeit,
            "verbrauch_letzte_laufzeit_monate": verbrauch_letzte_laufzeit_monate,
            "empfohlener_abschlag": empfohlener_abschlag,
            "erinnerung_datum": erinnerung_datum,
            "erinnerung_aktiv": aktiv,
            "erinnerung_bestaetigt": bestaetigt,
            "erinnerung_monate": monate,
            "kuendigungsfrist": frist or None,
            "kuendigung_bis": kuendigung_bis,
            "mindestlaufzeit": mindest or None,
            "mindestlaufzeit_bis": mindestlaufzeit_bis,
            "abschlag_warnung_aktiv": warnung_aktiv,
            "abschlag_warnung_zu_bestaetigen": warnung_zu_bestaetigen,
            "wechsel": _parse_date(nxt.get(NEXT_PREFIX + CONF_BEGINN)) if nxt else None,
            "next": nxt,
            "archiviert": archiviert,
            "archiviert_am": archiviert_am,
            # abgelaufen, kein Folgevertrag geplant, noch nicht archiviert
            "archivierbar": bool(
                ende is not None and ende < heute and not nxt and not archiviert
            ),
            "historie": list(data.get(CONF_HISTORIE) or []),
            **self._gesamtkosten(
                data,
                beginn,
                kredit.get("kredit_gezahlt") if ist_kredit else kosten_bisher,
                abschlag_bisher_gezahlt_wert,
                archiviert,
                # der Sofortbonus der laufenden Laufzeit (bei einer archivierten steht er schon
                # in ihrem Historien-Eintrag)
                einmal_gesamt + (0.0 if archiviert else sofortbonus),
            ),
            **{key: value for key, value in kredit.items() if key != "ende"},
            **(
                {
                    "naechste_zahlung": kredit.get("kredit_naechste_zahlung"),
                    "erste_zahlung": kredit.get("kredit_erste_zahlung"),
                    "laufzeit_monate": _f(data.get(CONF_LAUFZEIT_MONATE)),
                }
                if ist_kredit
                else {}
            ),
            # Pauschalvertrag mit Ende, nicht archiviert: laesst sich verlaengern (neuer Betrag, neues Ende)
            "verlaengerbar": bool(
                sparte not in METERED_SPARTEN and ende is not None and not archiviert
            ),
            CONF_ARBEITSPREIS_NT: arbeitspreis_nt,
            "arbeitspreis_ht": arbeitspreis_ht,
            CONF_ZAEHLERNUMMER_NT: data.get(CONF_ZAEHLERNUMMER_NT),
            "verbrauch_bisher_ht": verbrauch_bisher_ht,
            "verbrauch_bisher_nt": verbrauch_bisher_nt,
            "kosten_ht_bisher": kosten_ht_bisher,
            "kosten_nt_bisher": kosten_nt_bisher,
        }
