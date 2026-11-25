"""Eine falsch erfasste Ablesung im Nachhinein korrigieren, ohne dass ein Zaehlerwechsel entsteht.

Hintergrund: Der Verbrauchssensor eines von Hand abgelesenen Zaehlers ist ein Template-Sensor mit
total_increasing auf einem Zahl-Helfer. Home Assistant wertet jeden kleineren Stand als neuen
Zaehler und zaehlt ihn komplett als Verbrauch. Ein Zahlendreher (53 statt 35) laesst sich daher
nicht einfach durch Ueberschreiben des Helfers beheben.

Ablauf:
- Die Ablesungen sind die Stundenzeilen der Langzeitstatistik, an denen sich der Stand aendert.
- Fuer die gewaehlte Ablesung wird der Stand in allen Zeilen bis zur naechsten Ablesung ersetzt und
  die Summe neu gerechnet: Uebergaenge an den Raendern zaehlen neu, alle spaeteren Zeilen behalten
  ihre bisherigen Zuwaechse und werden nur verschoben. Echte Zaehlerwechsel bleiben erhalten.
- Die Summen werden mit "Summe anpassen" des Recorders verschoben (Stunden- und 5-Minuten-Statistik),
  die Staende der betroffenen Stundenzeilen per Import ueberschrieben.
- Ist die falsche Ablesung die aktuelle, wird zusaetzlich der Helfer gesetzt. Home Assistant zaehlt
  den kleineren Wert dann erst einmal als Zaehlerwechsel; sobald die naechste 5-Minuten-Statistik
  ihn erfasst hat, rechnet Tariffy die Summen nach (wenige Minuten).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .meter_import import _EINHEITEN_KLASSE, _als_datetime

_LOGGER = logging.getLogger(__name__)

MAX_ABLESUNGEN = 100
# Nachziehen bei der aktuellen Ablesung: alle 30 s pruefen, hoechstens 15 Minuten
_NACHZIEHEN_TAKT = 30
_NACHZIEHEN_VERSUCHE = 30
_EPS = 1e-9


class KorrekturFehler(Exception):
    """Fehler mit Code fuer das Panel (wird als WebSocket-Fehlercode gesendet)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _zeile(eintrag: dict[str, Any]) -> dict[str, Any] | None:
    if eintrag.get("state") is None or eintrag.get("sum") is None or eintrag.get("start") is None:
        return None
    return {
        "start": _als_datetime(eintrag["start"]),
        "state": float(eintrag["state"]),
        "sum": float(eintrag["sum"]),
    }


def ablesungen_aus_zeilen(zeilen: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Die Ablesungen (erste Zeile und jede Zeile mit neuem Stand), neueste zuerst. `wechsel`
    heisst: der Stand ist kleiner als der vorherige, Home Assistant hat einen Zaehlerwechsel
    gezaehlt."""
    ergebnis: list[dict[str, Any]] = []
    vorher: float | None = None
    for zeile in zeilen:
        if vorher is None or abs(zeile["state"] - vorher) > _EPS:
            ergebnis.append({
                "start": zeile["start"].isoformat(),
                "zeit": dt_util.as_local(zeile["start"]).isoformat(),
                "stand": zeile["state"],
                "wechsel": vorher is not None and zeile["state"] < vorher,
            })
        vorher = zeile["state"]
    ergebnis.reverse()
    return ergebnis[:MAX_ABLESUNGEN]


def korrektur_planen(
    zeilen: list[dict[str, Any]], start: datetime, neu: float, *, nachher_gleich_neu: bool = False
) -> dict[str, Any]:
    """Plant die Korrektur der Ablesung, die in der Zeile `start` beginnt, auf den Stand `neu`.

    `zeilen` sind die Statistikzeilen nach Zeit (start, state, sum). Mit `nachher_gleich_neu`
    gehoeren auch direkt folgende Zeilen mit dem Stand `neu` zum Abschnitt (die aktuelle Ablesung,
    nachdem der Helfer schon gesetzt ist und Home Assistant den Wert erfasst hat).

    Gibt zurueck: die Summenverschiebungen als Stufen [(ab, betrag)] (jeweils zusaetzlich ab
    diesem Zeitpunkt), die Stundenzeilen mit neuem Stand und neuer Summe, und Kennzahlen fuer die
    Vorschau."""
    if neu < 0:
        raise KorrekturFehler("ungueltig")
    index = next((i for i, z in enumerate(zeilen) if z["start"] == start), None)
    if index is None:
        raise KorrekturFehler("ablesung_fehlt")
    if index > 0 and abs(zeilen[index - 1]["state"] - zeilen[index]["state"]) <= _EPS:
        raise KorrekturFehler("ablesung_fehlt")  # keine Ablesung, nur eine Folgezeile
    alt = zeilen[index]["state"]
    if abs(alt - neu) <= _EPS and not nachher_gleich_neu:
        raise KorrekturFehler("gleich")

    ende = index
    while ende + 1 < len(zeilen) and abs(zeilen[ende + 1]["state"] - alt) <= _EPS:
        ende += 1
    if nachher_gleich_neu:
        while ende + 1 < len(zeilen) and abs(zeilen[ende + 1]["state"] - neu) <= _EPS:
            ende += 1

    # Uebergang in den Abschnitt
    if index > 0:
        vor = zeilen[index - 1]
        war_wechsel = alt < vor["state"]
        if neu >= vor["state"]:
            zuwachs = neu - vor["state"]
        elif war_wechsel:
            zuwachs = neu  # der Zaehler wurde wirklich gewechselt: der neue zaehlt ab 0
        else:
            raise KorrekturFehler("kleiner_als_vorher")
        summe = vor["sum"] + zuwachs
    else:
        summe = zeilen[index]["sum"]  # erste Zeile ueberhaupt: Summe bleibt der Nullpunkt

    soll: list[float] = [z["sum"] for z in zeilen]
    for i in range(index, ende + 1):
        soll[i] = summe
    # Uebergang aus dem Abschnitt, danach bleiben alle Zuwaechse und werden nur verschoben
    if ende + 1 < len(zeilen):
        nach = zeilen[ende + 1]
        war_wechsel = nach["state"] < zeilen[ende]["state"]
        if nach["state"] >= neu:
            zuwachs = nach["state"] - neu
        elif war_wechsel:
            zuwachs = nach["state"]
        else:
            raise KorrekturFehler("groesser_als_naechste")
        verschiebung = summe + zuwachs - nach["sum"]
        for i in range(ende + 1, len(zeilen)):
            soll[i] = zeilen[i]["sum"] + verschiebung

    stufen: list[tuple[datetime, float]] = []
    bisher = 0.0
    for i in range(index, len(zeilen)):
        fehler = soll[i] - zeilen[i]["sum"]
        if abs(fehler - bisher) > _EPS:
            stufen.append((zeilen[i]["start"], round(fehler - bisher, 6)))
            bisher = fehler
    neue_zeilen = [
        {"start": zeilen[i]["start"], "state": neu, "sum": round(soll[i], 6),
         "kurzzeit": bool(zeilen[i].get("kurzzeit"))}
        for i in range(index, ende + 1)
    ]
    return {
        "alt": alt,
        "neu": neu,
        "von": zeilen[index]["start"],
        "bis": zeilen[ende]["start"],
        "bis_ende": ende == len(zeilen) - 1,
        "stufen": stufen,
        "zeilen": neue_zeilen,
        # So aendert sich der Verbrauch bis heute
        "aenderung": round(bisher, 6),
    }


async def _zeilen(hass: HomeAssistant, sensor: str, *, mit_kurzzeit: bool = False) -> list[dict[str, Any]]:
    """Alle Stundenzeilen des Sensors; mit `mit_kurzzeit` zusaetzlich die neueste 5-Minuten-Zeile,
    wenn sie nach der letzten Stundenzeile liegt (die laufende Stunde)."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        get_last_short_term_statistics,
        statistics_during_period,
    )

    instance = get_instance(hass)
    anfang = datetime(2000, 1, 1, tzinfo=dt_util.UTC)
    stats = await instance.async_add_executor_job(
        lambda: statistics_during_period(
            hass, anfang, None, statistic_ids={sensor}, period="hour", units={}, types={"state", "sum"}
        )
    )
    zeilen = [z for z in (_zeile(e) for e in stats.get(sensor) or []) if z is not None]
    if mit_kurzzeit:
        kurz = await instance.async_add_executor_job(
            lambda: get_last_short_term_statistics(hass, 1, sensor, False, {"state", "sum"})
        )
        for eintrag in kurz.get(sensor) or []:
            zeile = _zeile(eintrag)
            if zeile is not None and (not zeilen or zeile["start"] >= zeilen[-1]["start"] + timedelta(hours=1)):
                zeile["kurzzeit"] = True
                zeilen.append(zeile)
    return zeilen


def _sensor_pruefen(hass: HomeAssistant, sensor: str) -> str | None:
    zustand = hass.states.get(sensor)
    if zustand is None:
        raise KorrekturFehler("sensor_fehlt")
    if zustand.attributes.get("state_class") not in ("total_increasing", "total"):
        raise KorrekturFehler("sensor_ohne_summe")
    return zustand.attributes.get("unit_of_measurement")


async def ablesungen(hass: HomeAssistant, sensor: str) -> dict[str, Any]:
    """Die Ablesungen aus der Statistik des Sensors, neueste zuerst, fuer das Panel."""
    einheit = _sensor_pruefen(hass, sensor)
    return {"einheit": einheit, "ablesungen": ablesungen_aus_zeilen(await _zeilen(hass, sensor))}


async def _anwenden(hass: HomeAssistant, sensor: str, einheit: str | None, plan: dict[str, Any]) -> None:
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics, get_metadata

    instance = get_instance(hass)
    meta = await instance.async_add_executor_job(lambda: get_metadata(hass, statistic_ids={sensor}))
    stat_einheit = meta[sensor][1].get("unit_of_measurement") if sensor in meta else einheit
    for ab, betrag in plan["stufen"]:
        instance.async_adjust_statistics(sensor, ab, betrag, stat_einheit)
    # Staende der Stundenzeilen (die laufende Stunde gibt es dort noch nicht)
    stunden = [z for z in plan["zeilen"] if not z.get("kurzzeit")]
    if stunden:
        async_import_statistics(
            hass,
            {
                "mean_type": StatisticMeanType.NONE,
                "has_sum": True,
                "name": None,
                "source": "recorder",
                "statistic_id": sensor,
                "unit_class": _EINHEITEN_KLASSE.get(stat_einheit or ""),
                "unit_of_measurement": stat_einheit,
            },
            [{"start": z["start"], "state": z["state"], "sum": z["sum"]} for z in stunden],
        )


def _vorschau(plan: dict[str, Any], einheit: str | None, aktuell: bool) -> dict[str, Any]:
    return {
        "alt": plan["alt"],
        "neu": plan["neu"],
        "von": dt_util.as_local(plan["von"]).isoformat(),
        "bis": dt_util.as_local(plan["bis"]).isoformat(),
        "aenderung": plan["aenderung"],
        "zeilen": len(plan["zeilen"]),
        "aktuell": aktuell,
        "einheit": einheit,
        "korrigiert": False,
        "nachziehen": False,
    }


async def ablesung_korrigieren(
    hass: HomeAssistant,
    sensor: str,
    helfer: str | None,
    start: str,
    neu: float,
    *,
    nur_pruefen: bool,
    fertig: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Prueft (nur_pruefen) oder korrigiert die Ablesung, deren Stundenzeile bei `start` (ISO)
    beginnt. `fertig` wird nach dem Schreiben aufgerufen (Tariffy neu rechnen lassen)."""
    einheit = _sensor_pruefen(hass, sensor)
    try:
        start_dt = dt_util.as_utc(datetime.fromisoformat(start))
    except ValueError as err:
        raise KorrekturFehler("ablesung_fehlt") from err
    zeilen = await _zeilen(hass, sensor)
    plan = korrektur_planen(zeilen, start_dt, neu)

    # Die aktuelle Ablesung: der Abschnitt reicht bis zur letzten Zeile und der Helfer steht noch
    # auf dem alten Wert. Dann muss auch der Helfer korrigiert werden.
    aktuell = False
    if plan["bis_ende"]:
        zustand = hass.states.get(helfer) if helfer else None
        try:
            helfer_wert = float(zustand.state) if zustand else None
        except ValueError:
            helfer_wert = None
        if helfer_wert is not None and abs(helfer_wert - plan["alt"]) <= _EPS:
            aktuell = True
            maximum = zustand.attributes.get("max")
            if maximum is not None and neu > float(maximum):
                raise KorrekturFehler("helfer_max")
        elif helfer_wert is None and not helfer:
            # Digitaler Zaehler: der aktuelle Stand kommt vom Geraet, nicht von Hand
            sensor_zustand = hass.states.get(sensor)
            try:
                if sensor_zustand and abs(float(sensor_zustand.state) - plan["alt"]) <= _EPS:
                    raise KorrekturFehler("aktuell_ohne_helfer")
            except ValueError:
                pass

    ergebnis = _vorschau(plan, einheit, aktuell)
    if nur_pruefen:
        return ergebnis

    if not aktuell:
        await _anwenden(hass, sensor, einheit, plan)
        ergebnis["korrigiert"] = True
        if fertig is not None:
            await _nach_recorder(hass, fertig)
        return ergebnis

    gesetzt = dt_util.utcnow()
    await hass.services.async_call(
        "input_number", "set_value", {"entity_id": helfer, "value": neu}, blocking=True
    )
    _nachziehen_planen(hass, sensor, einheit, start_dt, plan["alt"], neu, gesetzt, fertig)
    ergebnis["nachziehen"] = True
    return ergebnis


async def _nach_recorder(hass: HomeAssistant, fertig: Callable[[], Awaitable[None]]) -> None:
    """Wartet, bis der Recorder die Auftraege geschrieben hat, und ruft dann `fertig`."""
    from homeassistant.components.recorder import get_instance

    await get_instance(hass).async_block_till_done()
    await fertig()


def _nachziehen_planen(
    hass: HomeAssistant,
    sensor: str,
    einheit: str | None,
    start: datetime,
    alt: float,
    neu: float,
    gesetzt: datetime,
    fertig: Callable[[], Awaitable[None]] | None,
    versuch: int = 0,
) -> None:
    """Sobald Home Assistant den neuen Helferwert in der 5-Minuten-Statistik erfasst hat (und ihn
    dabei als Zaehlerwechsel gezaehlt hat), die Summen nachrechnen."""

    async def _pruefen(_now: Any) -> None:
        try:
            zeilen = await _zeilen(hass, sensor, mit_kurzzeit=True)
            letzte = zeilen[-1] if zeilen else None
            erfasst = (
                letzte is not None
                and abs(letzte["state"] - neu) <= _EPS
                and letzte["start"] + timedelta(minutes=5) > gesetzt
            )
            if not erfasst:
                if versuch + 1 >= _NACHZIEHEN_VERSUCHE:
                    raise KorrekturFehler("zeitueberschreitung")
                _nachziehen_planen(hass, sensor, einheit, start, alt, neu, gesetzt, fertig, versuch + 1)
                return
            if letzte.get("kurzzeit"):
                # Die Verschiebung fuer den neuen Wert gilt ab dem Setzen des Helfers (5-Minuten-Raster),
                # nicht erst ab der neuesten 5-Minuten-Zeile
                stunde = letzte["start"].replace(minute=0, second=0, microsecond=0)
                raster = gesetzt.replace(minute=gesetzt.minute - gesetzt.minute % 5, second=0, microsecond=0)
                letzte["start"] = min(letzte["start"], max(stunde, raster))
            # Die Zeilen der Ablesung haben noch den alten Stand, danach folgen Zeilen mit dem neuen
            plan = korrektur_planen(zeilen, start, neu, nachher_gleich_neu=True)
            await _anwenden(hass, sensor, einheit, plan)
            if fertig is not None:
                await _nach_recorder(hass, fertig)
            _LOGGER.info("Tariffy: Ablesung von %s korrigiert (%s -> %s)", sensor, alt, neu)
        except KorrekturFehler as err:
            _melden(hass, sensor, alt, neu, err.code)
        except Exception as err:  # noqa: BLE001 - nie die Integration stoeren
            _LOGGER.warning("Tariffy: Korrektur der Ablesung von %s fehlgeschlagen: %s", sensor, err)
            _melden(hass, sensor, alt, neu, str(err))

    async_call_later(hass, _NACHZIEHEN_TAKT, _pruefen)


@callback
def _melden(hass: HomeAssistant, sensor: str, alt: float, neu: float, grund: str) -> None:
    from homeassistant.components import persistent_notification

    persistent_notification.async_create(
        hass,
        f"Die Korrektur der Ablesung von {sensor} ({alt:g} → {neu:g}) ist nicht fertig geworden "
        f"({grund}). Der Helfer steht schon auf {neu:g}; Home Assistant hat den kleineren Wert als "
        "Zählerwechsel gezählt. Bitte unter Entwicklerwerkzeuge → Statistik die Summe ab der "
        f"Ablesung um {neu - alt:g} und ab dem Setzen des Helfers um {-neu:g} anpassen.",
        title="Tariffy: Ablesung korrigieren",
        notification_id=f"tariffy_ablesung_{sensor}",
    )
