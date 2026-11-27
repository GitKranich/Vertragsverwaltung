"""Sidebar panel: registers the panel, serves its files and builds the contract data it displays.

The commands the panel sends (`tariffy/...`) are in websocket.py. Everything here is best effort: if the panel cannot be
set up (no frontend, http not loaded, ...) a warning is logged and the
integration continues exactly as without a panel.
"""

from __future__ import annotations

from datetime import date, datetime
from functools import partial
import logging
import math
from pathlib import Path
from typing import Any


from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_send,
)
from homeassistant.loader import async_get_integration

from . import dokumente, objekte as objekte_mod
from .const import (
    CONF_OBJEKT,
    CONF_VERBRAUCH_SENSOR,
    DOMAIN,
    NEXT_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "tariffy"
PANEL_ELEMENT = "tariffy-panel"
PANEL_TITLE = "Tariffy"
PANEL_ICON = "mdi:cash-multiple"
STATIC_URL = "/tariffy_static"
PANEL_FILE = "tariffy-panel.js"
SIGNAL_CONTRACTS = f"{DOMAIN}_contracts_updated"

DATA_KEY = f"{DOMAIN}_panel"

# Values shown by the panel. Deliberately a whitelist: the panel is visible to
# every user, so customer number, meter numbers, MaLo-ID, notification target
# and internal flags are not sent.
CONTRACT_FIELDS = (
    "sparte",
    "icon",
    "anbieter",
    "tarif",
    "versicherungsart",
    "hauskostenart",
    "km_start",
    "km_pro_jahr",
    "currency",
    "beginn",
    "ende",
    "restlaufzeit",
    "arbeitspreis",
    "arbeitspreis_ht",
    "arbeitspreis_nt",
    "arbeitspreis_nacht",
    "effektiver_arbeitspreis",
    "arbeitspreis_gesamt_wasser",
    "grundpreis",
    "abschlag",
    "abschlag_bisher_gezahlt",
    "zahlweise",
    "abschlag_jahr",
    "abschlag_je_zahlung",
    "kosten_jahr",
    "erste_zahlung",
    "naechste_zahlung",
    "jahreskosten",
    "verbrauch_bisher",
    "verbrauch_bisher_ht",
    "verbrauch_bisher_nt",
    "verbrauch_hochgerechnet",
    "verbrauch_letzte_laufzeit",
    "verbrauch_letzte_laufzeit_monate",
    "kosten_bisher",
    "kosten_ht_bisher",
    "kosten_nt_bisher",
    "guthaben_bisher",
    "prognose_real",
    "abschlag_anpassung_empfohlen",
    "empfohlener_abschlag",
    "erinnerung_datum",
    "erinnerung_aktiv",
    "erinnerung_bestaetigt",
    "erinnerung_monate",
    "kuendigungsfrist",
    "kuendigung_bis",
    "mindestlaufzeit",
    "mindestlaufzeit_bis",
    "abschlag_warnung_aktiv",
    "abschlag_warnung_zu_bestaetigen",
    "wechsel",
    "archiviert",
    "archiviert_am",
    "archivierbar",
    "verlaengerbar",
    "historie",
    "einmalposten",
    "bonus",
    "sofortbonus",
    "abschlag_aenderung",
    "prognose",
    "prognose_methode",
    "niederschlag_flaeche",
    "niederschlag_gebuehr",
    "niederschlag_jahr",
    "zaehlerstand_eingabe",
    "preisaenderungen",
    "naechste_preisaenderung",
    "preis_basis",
    "preisaenderung_moeglich",
    "aussenzaehler",
    "laufzeit_monate",
    "kredit_summe",
    "kredit_sollzins",
    "kredit_effektivzins",
    "kredit_rate",
    "kredit_rate_berechnet",
    "kredit_rate_abweichung",
    "kredit_rate_passt",
    "kredit_zahlungen_gesamt",
    "kredit_zahlungen_geleistet",
    "kredit_restschuld",
    "kredit_zinsen_bisher",
    "kredit_tilgung_bisher",
    "kredit_gezahlt",
    "kredit_restsumme",
    "kredit_zinsen_gesamt",
    "kredit_gesamtkosten",
    "kredit_schlussrate",
    "kredit_tilgung_prozent",
    "kredit_erste_zahlung",
    "kredit_naechste_zahlung",
    "kredit_naechste_betrag",
    "kredit_zins_anteil",
    "kredit_tilgung_anteil",
    "garten_verbrauch",
    "garten_kosten",
    "abwasserpflichtiger_verbrauch",
    "abwasser_kosten",
    "einmal_summe",
    "kosten_gesamt",
    "kosten_gesamt_vollstaendig",
    "laufzeiten_anzahl",
    "seit",
)
NEXT_FIELDS = ("beginn", "ende", "anbieter", "tarif", "arbeitspreis", "abschlag")


def _json_safe(value: Any) -> Any:
    """Make a coordinator value safe for JSON (dates, NaN, nested containers)."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return value


def contract_payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Data of one contract as shown by the panel."""
    data = entry.runtime_data.data or {}
    payload: dict[str, Any] = {"entry_id": entry.entry_id, "title": entry.title}
    for key in CONTRACT_FIELDS:
        payload[key] = _json_safe(data.get(key))

    nxt = data.get("next") or {}
    payload["next"] = (
        {key: _json_safe(nxt.get(NEXT_PREFIX + key)) for key in NEXT_FIELDS}
        if nxt
        else None
    )

    # Unit of the raw consumption values: the unit of the user's meter sensor.
    sensor_id = entry.data.get(CONF_VERBRAUCH_SENSOR)
    state = hass.states.get(sensor_id) if sensor_id else None
    unit = state.attributes.get("unit_of_measurement") if state else None
    # a meter reporting Wh or MWh is calculated in kWh (see coordinator._einheit_faktor)
    # ohne Verbrauchssensor bietet das Panel "Zaehler anlegen" an
    payload["hat_zaehler"] = bool(sensor_id)
    payload["verbrauch_einheit"] = "kWh" if str(unit or "").strip().lower() in ("wh", "mwh") else unit
    # verknuepfte Paperless-Dokumente: nur Verweise; Vorschau und Download prueft die Paperless-Integration
    payload["dokumente"] = dokumente.payload(entry.data)
    # das Objekt, zu dem der Vertrag gehoert: das Panel zeigt jeweils die Vertraege eines Objekts
    payload["objekt_id"] = entry.data.get(CONF_OBJEKT)
    return payload


@callback
def async_contracts(hass: HomeAssistant) -> list[dict[str, Any]]:
    """All loaded contracts."""
    contracts = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        # Ein Vertrag, der gerade eingerichtet wird, zaehlt mit: er meldet sich aus async_setup_entry beim Panel,
        # wenn sein Zustand noch nicht LOADED ist. Ohne ihn bekam das offene Panel nach jedem Anlegen und
        # Neuladen eine leere Liste. Ein Vertrag, der entladen wird oder fehlgeschlagen ist, hat nichts zu zeigen.
        if entry.state not in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS):
            continue
        if getattr(entry, "runtime_data", None) is None:
            continue
        contracts.append(contract_payload(hass, entry))
    return contracts


@callback
def async_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    """Alles, was das Panel braucht: die Vertraege, die Objekte und das Default-Objekt."""
    objekte = objekte_mod.cached(hass)
    return {
        "contracts": async_contracts(hass),
        **(objekte.payload() if objekte is not None else {"objekte": [], "default_objekt": None}),
    }


@callback
def async_notify(hass: HomeAssistant) -> None:
    """Tell open panels that the contract data changed."""
    async_dispatcher_send(hass, SIGNAL_CONTRACTS)


@callback
def async_attach_entry(hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
    """Push a new snapshot to open panels whenever the coordinator updates."""
    entry.async_on_unload(coordinator.async_add_listener(partial(async_notify, hass)))


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the panel once per Home Assistant. Never raises."""
    state = hass.data.setdefault(
        DATA_KEY, {"commands": False, "static": False, "panel": False}
    )
    try:
        if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
            _LOGGER.debug("Tariffy: no frontend available, panel not registered")
            return

        if not state["commands"]:
            from . import websocket  # noqa: PLC0415 - websocket.py imports this module

            websocket.async_register_commands(hass)
            state["commands"] = True

        if not state["static"]:
            await hass.http.async_register_static_paths(
                [
                    StaticPathConfig(
                        STATIC_URL, str(Path(__file__).parent / "frontend"), True
                    )
                ]
            )
            state["static"] = True

        if not state["panel"]:
            version = (await async_get_integration(hass, DOMAIN)).version
            await panel_custom.async_register_panel(
                hass,
                frontend_url_path=PANEL_URL_PATH,
                webcomponent_name=PANEL_ELEMENT,
                sidebar_title=PANEL_TITLE,
                sidebar_icon=PANEL_ICON,
                module_url=f"{STATIC_URL}/{PANEL_FILE}?v={version}",
                embed_iframe=False,
                require_admin=False,
            )
            state["panel"] = True
    except Exception as err:  # noqa: BLE001 - the panel must never break the integration
        _LOGGER.warning("Tariffy: panel could not be set up (%s)", err)


async def async_unload_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the panel when the last contract is unloaded. Never raises."""
    state = hass.data.get(DATA_KEY)
    if not state or not state["panel"]:
        return
    try:
        others_loaded = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED
        ]
        if not others_loaded:
            frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
            state["panel"] = False
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Tariffy: panel could not be removed (%s)", err)


__all__ = [
    "async_attach_entry",
    "async_contracts",
    "async_notify",
    "async_setup_panel",
    "async_snapshot",
    "async_unload_panel",
    "contract_payload",
]
