"""Zaehler anlegen: Zahl-Helfer und Template-Sensor fuer einen von Hand abgelesenen Zaehler.

Fuer einen Verbrauchsvertrag ohne Verbrauchssensor legt das Panel auf Knopfdruck einen Zaehler an, wie man ihn
sonst von Hand anlegen wuerde:

* einen Zahl-Helfer (`input_number`) fuer die Ablesung - den legt das Panel ueber die offizielle Schnittstelle der
  Helfer-Verwaltung an (`input_number/create`, wie die Oberflaeche von Home Assistant),
* einen Template-Sensor (Helfer der Template-Integration) mit `total_increasing`, Geraeteklasse und Einheit, der den
  Stand des Zahl-Helfers zeigt - den legt `async_zaehler_anlegen` ueber den Einrichtungsdialog der Template-
  Integration an.

Beide sind echte Helfer von Home Assistant: Sie und ihre Statistik bleiben, auch wenn der Vertrag geloescht oder
Tariffy entfernt wird, und ein Folgevertrag kann denselben Zaehler weiter nutzen. Benannt wird der Zaehler nach
Sparte und Objekt ("Gaszähler Bahnhofstraße"), nicht nach dem Vertrag, weil er Vertraege ueberdauert.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import SOURCE_USER, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from . import objekte
from .const import (
    CONF_GAS_EINHEIT,
    CONF_SPARTE,
    CONF_VERBRAUCH_SENSOR,
    CONF_WASSER_EINHEIT,
    CONF_ZAEHLERSTAND_EINGABE,
    GAS_SPARTE,
    WASSER_SPARTE,
)

_LOGGER = logging.getLogger(__name__)

# Sparten mit einem einzigen Zaehler (Zweizaehler-Tarife HT/NT brauchen zwei Zaehler und bleiben aussen vor)
SPARTEN = ("electricity", GAS_SPARTE, WASSER_SPARTE)
MAX_NAME = 80

_ZAEHLER_WORT = {
    "de": {"electricity": "Stromzähler", GAS_SPARTE: "Gaszähler", WASSER_SPARTE: "Wasserzähler"},
    "en": {"electricity": "Electricity meter", GAS_SPARTE: "Gas meter", WASSER_SPARTE: "Water meter"},
}
_ABLESUNG = {"de": "Ablesung", "en": "reading"}

# Geraeteklasse je Einheit; Einheiten ohne passende Klasse (therm, MBtu) bekommen keine
_GAS_KLASSE = {"m³": "gas", "ft³": "gas", "CCF": "gas", "kWh": "energy"}
_WASSER_KLASSE = {"m³": "water", "L": "water", "gal": "water", "ft³": "water", "CCF": "water"}


class ZaehlerFehler(ValueError):
    """Zaehler nicht anlegbar; `code` steht fuer die Meldung des Panels."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sprache(hass: HomeAssistant) -> str:
    return "de" if str(hass.config.language or "").lower().startswith("de") else "en"


def _einheit(entry: ConfigEntry) -> tuple[str, str | None]:
    """Einheit und Geraeteklasse des Zaehlers: wie im Vertrag eingestellt."""
    sparte = entry.data.get(CONF_SPARTE)
    if sparte == GAS_SPARTE:
        unit = entry.data.get(CONF_GAS_EINHEIT) or "m³"
        return unit, _GAS_KLASSE.get(unit)
    if sparte == WASSER_SPARTE:
        unit = entry.data.get(CONF_WASSER_EINHEIT) or "m³"
        return unit, _WASSER_KLASSE.get(unit)
    return "kWh", "energy"


def pruefen(entry: ConfigEntry) -> None:
    """Laesst sich fuer diesen Vertrag ein Zaehler anlegen?"""
    if entry.data.get(CONF_SPARTE) not in SPARTEN:
        raise ZaehlerFehler("sparte")
    if entry.data.get(CONF_VERBRAUCH_SENSOR):
        raise ZaehlerFehler("hat_sensor")


def vorschlag(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Name, Einheit und Schrittweite fuer den neuen Zaehler (das Panel zeigt sie vor dem Anlegen)."""
    pruefen(entry)
    sprache = _sprache(hass)
    store = objekte.cached(hass)
    ort = store.name(objekte.objekt_von(entry)) if store is not None else None
    name = " ".join(filter(None, [_ZAEHLER_WORT[sprache][entry.data[CONF_SPARTE]], ort]))
    unit, device_class = _einheit(entry)
    return {
        "name": name,
        "ablesung": _ABLESUNG[sprache],
        "unit": unit,
        "device_class": device_class,
        "step": 0.001 if unit in ("m³", "ft³", "CCF") else 0.1 if unit in ("kWh", "L", "gal") else 0.01,
    }


def _eingabe_entity(hass: HomeAssistant, eingabe: str) -> str:
    """Entity-ID des Zahl-Helfers: aus seiner Helfer-ID (Antwort von input_number/create) oder direkt."""
    if eingabe.startswith("input_number."):
        entity_id = eingabe
    else:
        entity_id = er.async_get(hass).async_get_entity_id("input_number", "input_number", eingabe)
    if not entity_id or hass.states.get(entity_id) is None:
        raise ZaehlerFehler("eingabe_fehlt")
    return entity_id


async def async_zaehler_anlegen(hass: HomeAssistant, entry: ConfigEntry, eingabe: str, name: str) -> dict[str, str]:
    """Legt den Template-Sensor zum Zahl-Helfer an und ordnet beide dem Vertrag zu."""
    pruefen(entry)
    name = str(name or "").strip()
    if not name or len(name) > MAX_NAME:
        raise ZaehlerFehler("name")
    eingabe_id = _eingabe_entity(hass, eingabe)
    unit, device_class = _einheit(entry)

    flow = hass.config_entries.flow
    result = await flow.async_init("template", context={"source": SOURCE_USER})
    if result["type"] is FlowResultType.MENU:
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "sensor"})
    werte: dict[str, Any] = {
        "name": name,
        # ohne gueltigen Stand "unbekannt" (nie 0: das zaehlte Home Assistant als Zaehlerwechsel)
        "state": "{{ states('" + eingabe_id + "') | float(none) }}",
        "unit_of_measurement": unit,
        "state_class": "total_increasing",
    }
    if device_class:
        werte["device_class"] = device_class
    try:
        result = await flow.async_configure(result["flow_id"], werte)
    except Exception as err:  # noqa: BLE001 - jede Ablehnung der Template-Integration ist ein Fehler fuers Panel
        _LOGGER.warning("Tariffy: Template-Sensor fuer '%s' nicht angelegt: %s", entry.title, err)
        raise ZaehlerFehler("template") from err
    if result["type"] is not FlowResultType.CREATE_ENTRY:
        if result.get("flow_id") and result["type"] is FlowResultType.FORM:
            flow.async_abort(result["flow_id"])
        _LOGGER.warning("Tariffy: Template-Sensor fuer '%s' nicht angelegt: %s", entry.title, result.get("errors"))
        raise ZaehlerFehler("template")

    template_entry = result["result"]
    sensor_id = next(
        (
            reg_entry.entity_id
            for reg_entry in er.async_entries_for_config_entry(er.async_get(hass), template_entry.entry_id)
            if reg_entry.domain == "sensor"
        ),
        None,
    )
    if sensor_id is None:
        raise ZaehlerFehler("template")

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_VERBRAUCH_SENSOR: sensor_id, CONF_ZAEHLERSTAND_EINGABE: eingabe_id}
    )
    await hass.config_entries.async_reload(entry.entry_id)
    return {"sensor": sensor_id, "eingabe": eingabe_id}
