"""Historische Zaehlerstaende in die Langzeitstatistik eines Zaehlersensors importieren.

Fuer Zaehler ohne digitale Anbindung (Template-Sensor mit total_increasing auf einem Zahl-Helfer)
oder einen neu eingerichteten Sensor: abgelesene Staende (Datum; Stand) werden als stuendliche
Statistikzeilen (state = Zaehlerstand, sum = fortlaufender Verbrauch) eingetragen. Home Assistant
zeigt sie danach in Verlauf und Energie-Dashboard, Tariffy nutzt sie fuer Verbrauch, Stichtage und
die saisonale Prognose.

Regeln:
- Importiert wird nur, was vor der aeltesten vorhandenen Statistikzeile des Sensors liegt; die
  vorhandenen Zeilen bleiben unveraendert. Die Summen der importierten Zeilen werden so gesetzt,
  dass der Verbrauch nahtlos in die vorhandene Statistik uebergeht.
- Ein Stand unter dem vorherigen gilt als Zaehlerwechsel (der neue Zaehler beginnt bei 0).
- Ein Datum ohne Uhrzeit gilt als 12:00 Uhr Ortszeit.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

MAX_ZEILEN = 5000

_EINHEITEN_KLASSE = {
    "m³": "volume", "L": "volume", "gal": "volume", "ft³": "volume", "CCF": "volume",
    "Wh": "energy", "kWh": "energy", "MWh": "energy", "GJ": "energy", "MJ": "energy",
}

_DATUM = re.compile(
    r"^\s*(?:(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})\.(\d{1,2})\.(\d{2,4}))"
    r"(?:[ T](\d{1,2}):(\d{2}))?\s*$"
)


class ImportFehler(Exception):
    """Fehler mit Code fuer das Panel (wird als WebSocket-Fehlercode gesendet)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _zahl(text: str) -> float | None:
    text = text.strip().replace(" ", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:  # 1.234,567 -> Punkt = Tausender
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        wert = float(text)
    except ValueError:
        return None
    return wert if wert >= 0 else None


def _zeitpunkt(text: str) -> datetime | None:
    treffer = _DATUM.match(text)
    if not treffer:
        return None
    if treffer.group(1):
        jahr, monat, tag = int(treffer.group(1)), int(treffer.group(2)), int(treffer.group(3))
    else:
        tag, monat, jahr = int(treffer.group(4)), int(treffer.group(5)), int(treffer.group(6))
        if jahr < 100:
            jahr += 2000
    stunde = int(treffer.group(7)) if treffer.group(7) else 12
    minute = int(treffer.group(8)) if treffer.group(8) else 0
    try:
        lokal = datetime.combine(date(jahr, monat, tag), time(stunde, minute))
    except ValueError:
        return None
    return lokal.replace(tzinfo=dt_util.get_default_time_zone())


def zeilen_lesen(text: str) -> tuple[list[tuple[datetime, float]], list[int]]:
    """Liest "Datum; Stand" je Zeile (Trenner ; Tab oder Komma, Datum TT.MM.JJJJ oder
    JJJJ-MM-TT, optional mit Uhrzeit). Gibt die Werte und die Nummern der nicht lesbaren Zeilen
    zurueck (z. B. eine Kopfzeile)."""
    werte: list[tuple[datetime, float]] = []
    fehler: list[int] = []
    for nummer, zeile in enumerate(text.splitlines(), start=1):
        if not zeile.strip():
            continue
        if ";" in zeile or "\t" in zeile:
            teile = re.split(r"[;\t]", zeile)
        else:
            teile = zeile.split(",", 1)  # "01.03.2026,512,3": der Rest ist die Zahl
        teile = [t.strip().strip('"') for t in teile if t.strip()]
        zeitpunkt = _zeitpunkt(teile[0]) if teile else None
        wert = _zahl(teile[1]) if len(teile) >= 2 else None
        if zeitpunkt is None or wert is None:
            fehler.append(nummer)
            continue
        werte.append((zeitpunkt, wert))
    if len(werte) > MAX_ZEILEN:
        raise ImportFehler("zu_viele_zeilen")
    return werte, fehler


def statistik_zeilen(
    werte: list[tuple[datetime, float]],
    anschluss: tuple[float, float] | None,
) -> tuple[list[dict[str, Any]], int]:
    """Stuendliche Statistikzeilen (start, state, sum) aus den Staenden, nach Zeit sortiert, je
    Stunde der letzte Stand. `anschluss` ist (state, sum) der aeltesten vorhandenen Zeile: die
    Summen werden so verschoben, dass der Verbrauch dorthin nahtlos weiterlaeuft. Gibt auch die
    Zahl der erkannten Zaehlerwechsel zurueck."""
    je_stunde: dict[datetime, float] = {}
    for zeitpunkt, wert in sorted(werte, key=lambda paar: paar[0]):
        start = dt_util.as_utc(zeitpunkt).replace(minute=0, second=0, microsecond=0)
        je_stunde[start] = wert
    zeilen: list[dict[str, Any]] = []
    summe = 0.0
    vorher: float | None = None
    wechsel = 0
    for start in sorted(je_stunde):
        stand = je_stunde[start]
        if vorher is not None:
            if stand >= vorher:
                summe += stand - vorher
            else:  # Zaehlerwechsel: der neue Zaehler zaehlt ab 0
                summe += stand
                wechsel += 1
        zeilen.append({"start": start, "state": stand, "sum": summe})
        vorher = stand
    if anschluss is not None and zeilen:
        anschluss_stand, anschluss_summe = anschluss
        letzte = zeilen[-1]
        schritt = anschluss_stand - letzte["state"]
        if schritt < 0:  # Zaehlerwechsel zwischen Import und vorhandener Statistik
            schritt = anschluss_stand
        verschiebung = anschluss_summe - schritt - letzte["sum"]
        for zeile in zeilen:
            zeile["sum"] = round(zeile["sum"] + verschiebung, 6)
    return zeilen, wechsel


async def _aelteste_zeile(hass: HomeAssistant, sensor: str) -> dict[str, Any] | None:
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    start = datetime(2000, 1, 1, tzinfo=dt_util.UTC)
    ende = dt_util.utcnow() + timedelta(hours=1)
    stats = await get_instance(hass).async_add_executor_job(
        lambda: statistics_during_period(
            hass, start, ende, statistic_ids={sensor}, period="hour", units={}, types={"state", "sum"}
        )
    )
    zeilen = stats.get(sensor) or []
    return zeilen[0] if zeilen else None


def _als_datetime(wert: Any) -> datetime:
    if isinstance(wert, (int, float)):
        return datetime.fromtimestamp(float(wert), dt_util.UTC)
    return dt_util.as_utc(wert)


async def zaehlerstaende_importieren(
    hass: HomeAssistant, sensor: str, text: str, *, nur_pruefen: bool
) -> dict[str, Any]:
    """Prueft (nur_pruefen) oder importiert die Staende aus `text` in die Statistik von `sensor`.
    Gibt eine Zusammenfassung fuer das Panel zurueck."""
    zustand = hass.states.get(sensor)
    if zustand is None:
        raise ImportFehler("sensor_fehlt")
    attribute = zustand.attributes
    if attribute.get("state_class") not in ("total_increasing", "total"):
        raise ImportFehler("sensor_ohne_summe")
    einheit = attribute.get("unit_of_measurement")

    werte, fehlerzeilen = zeilen_lesen(text)
    if not werte:
        raise ImportFehler("keine_werte")

    aelteste = await _aelteste_zeile(hass, sensor)
    anschluss: tuple[float, float] | None = None
    gelesen = len(werte)
    if aelteste is not None:
        grenze = _als_datetime(aelteste["start"])
        werte = [(z, w) for z, w in werte if dt_util.as_utc(z) < grenze]
        if not werte:
            raise ImportFehler("nur_ueberschneidung")
        if aelteste.get("state") is not None and aelteste.get("sum") is not None:
            anschluss = (float(aelteste["state"]), float(aelteste["sum"]))

    zeilen, wechsel = statistik_zeilen(werte, anschluss)
    ergebnis = {
        "anzahl": len(zeilen),
        "von": dt_util.as_local(zeilen[0]["start"]).isoformat(),
        "bis": dt_util.as_local(zeilen[-1]["start"]).isoformat(),
        "erster_stand": zeilen[0]["state"],
        "letzter_stand": zeilen[-1]["state"],
        "verbrauch": round(zeilen[-1]["sum"] - zeilen[0]["sum"], 3),
        "zaehlerwechsel": wechsel,
        "nicht_gelesen": fehlerzeilen[:20],
        "nicht_gelesen_anzahl": len(fehlerzeilen),
        # Staende ab der aeltesten vorhandenen Statistikzeile bleiben aussen vor
        "uebersprungen": gelesen - len(werte),
        "einheit": einheit,
        "anschluss": anschluss is not None,
        "importiert": False,
    }
    if nur_pruefen:
        return ergebnis

    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics

    metadata = {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": None,
        "source": "recorder",
        "statistic_id": sensor,
        "unit_class": _EINHEITEN_KLASSE.get(einheit or ""),
        "unit_of_measurement": einheit,
    }
    async_import_statistics(hass, metadata, zeilen)
    ergebnis["importiert"] = True
    return ergebnis
