"""Zaehler anlegen: Zahl-Helfer (vom Panel angelegt) und Template-Sensor, beide dem Vertrag zugeordnet."""

from __future__ import annotations

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.tariffy import objekte
from .test_coordinator import STROM
from .test_objekte import _add, _call, _statistics  # noqa: F401  (fixture)
from .test_panel import frontend_env  # noqa: F401  (fixture)

OHNE = {k: v for k, v in STROM.items() if k != "verbrauch_sensor"}
GAS = {**OHNE, "sparte": "gas", "gas_einheit": "m³", "brennwert": 11.0, "zustandszahl": 0.95}


async def _eingabe(hass, client, name="Gaszähler Haus Ablesung", initial=1234.5, unit="m³"):
    """Wie das Panel: den Zahl-Helfer ueber input_number/create anlegen."""
    assert await async_setup_component(hass, "input_number", {})
    await client.send_json_auto_id({
        "type": "input_number/create", "name": name, "min": 0, "max": 999999999, "step": 0.001,
        "mode": "box", "unit_of_measurement": unit, "initial": initial, "icon": "mdi:counter",
    })
    reply = await client.receive_json()
    assert reply["success"], reply
    await hass.async_block_till_done()
    return reply["result"]["id"]


class TestZaehlerAnlegen:
    async def test_proposal_names_the_meter_after_kind_and_property(self, frontend_env, hass, hass_ws_client):  # noqa: F811
        hass.config.language = "de"
        client = await hass_ws_client()
        entry = await _add(hass, "Gas", GAS)
        await objekte.cached(hass).async_save_objekt(entry.data["objekt_id"], "Bahnhofstraße", None)
        reply = await _call(client, "zaehler_vorschlag", entry_id=entry.entry_id)
        assert reply["result"] == {
            "name": "Gaszähler Bahnhofstraße", "ablesung": "Ablesung", "unit": "m³", "device_class": "gas", "step": 0.001,
        }

    async def test_creates_the_sensor_and_assigns_both(self, frontend_env, hass, hass_ws_client):  # noqa: F811
        client = await hass_ws_client()
        entry = await _add(hass, "Gas", GAS)
        helfer = await _eingabe(hass, client)
        reply = await _call(client, "zaehler_anlegen", entry_id=entry.entry_id, eingabe=helfer, name="Gaszähler Haus")
        assert reply["success"], reply
        await hass.async_block_till_done()
        sensor, eingabe = reply["result"]["sensor"], reply["result"]["eingabe"]
        assert eingabe == "input_number.gaszahler_haus_ablesung"
        state = hass.states.get(sensor)
        assert float(state.state) == 1234.5
        assert state.attributes["state_class"] == "total_increasing"
        assert state.attributes["device_class"] == "gas"
        assert state.attributes["unit_of_measurement"] == "m³"
        # a real helper of the template integration, not an entity of Tariffy
        assert er.async_get(hass).async_get(sensor).platform == "template"
        entry = hass.config_entries.async_get_entry(entry.entry_id)
        assert entry.data["verbrauch_sensor"] == sensor
        assert entry.data["zaehlerstand_eingabe"] == eingabe
        # a new reading goes through
        await hass.services.async_call("input_number", "set_value", {"entity_id": eingabe, "value": 1240}, blocking=True)
        await hass.async_block_till_done()
        assert float(hass.states.get(sensor).state) == 1240
        # the helpers stay when the contract is deleted
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(sensor) is not None and hass.states.get(eingabe) is not None

    async def test_electricity_in_kwh(self, frontend_env, hass, hass_ws_client):  # noqa: F811
        client = await hass_ws_client()
        entry = await _add(hass, "Strom", OHNE)
        helfer = await _eingabe(hass, client, "Stromzähler Ablesung", 5000, "kWh")
        reply = await _call(client, "zaehler_anlegen", entry_id=entry.entry_id, eingabe=helfer, name="Stromzähler")
        await hass.async_block_till_done()
        state = hass.states.get(reply["result"]["sensor"])
        assert state.attributes["device_class"] == "energy" and state.attributes["unit_of_measurement"] == "kWh"

    @pytest.mark.parametrize(
        ("data", "code"),
        [({**STROM, "verbrauch_sensor": "sensor.zaehler"}, "hat_sensor"), ({**OHNE, "sparte": "internet"}, "sparte")],
    )
    async def test_not_for_every_contract(self, frontend_env, hass, hass_ws_client, data, code):  # noqa: F811
        client = await hass_ws_client()
        entry = await _add(hass, "X", data)
        reply = await _call(client, "zaehler_vorschlag", entry_id=entry.entry_id)
        assert reply["error"]["code"] == code
        reply = await _call(client, "zaehler_anlegen", entry_id=entry.entry_id, eingabe="input_number.x", name="X")
        assert reply["error"]["code"] == code

    async def test_unknown_helper(self, frontend_env, hass, hass_ws_client):  # noqa: F811
        client = await hass_ws_client()
        entry = await _add(hass, "Gas", GAS)
        reply = await _call(client, "zaehler_anlegen", entry_id=entry.entry_id, eingabe="gibt_es_nicht", name="Gaszähler")
        assert reply["error"]["code"] == "eingabe_fehlt"
        assert not hass.config_entries.async_entries("template")

    async def test_only_administrators(self, frontend_env, hass, hass_ws_client, hass_read_only_access_token):  # noqa: F811
        client = await hass_ws_client(hass, hass_read_only_access_token)
        entry = await _add(hass, "Gas", GAS)
        for type_, extra in (("zaehler_vorschlag", {}), ("zaehler_anlegen", {"eingabe": "x", "name": "x"})):
            reply = await _call(client, type_, entry_id=entry.entry_id, **extra)
            assert reply["error"]["code"] == "unauthorized"
