"""Objekte: Zuordnung der Vertraege, Default-Objekt, Verwaltung im Panel, Sicherung, Meldungen."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tariffy import objekte
from custom_components.tariffy.const import DOMAIN

from .test_coordinator import STROM
from .test_panel import frontend_env  # noqa: F401  (fixture)
from .test_reconfigure import _form_defaults

COORDINATOR = "custom_components.tariffy.coordinator"
NEXT = {"next_beginn": "2027-01-01", "next_ende": "2027-12-31", "next_anbieter": "Neu", "next_abschlag": 55.0}


@pytest.fixture(autouse=True)
def _statistics():
    """The contracts of these tests need no recorder."""
    async def none(*_args, **_kwargs):
        return (None, None)

    async def nothing(*_args, **_kwargs):
        return None

    with (
        patch(f"{COORDINATOR}._get_latest_sum", none),
        patch(f"{COORDINATOR}._get_historic_value", none),
        patch(f"{COORDINATOR}._get_average_price", nothing),
    ):
        yield


async def _add(hass, title, data=None, options=None):
    entry = MockConfigEntry(domain=DOMAIN, title=title, data=data or STROM, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entries(hass):
    return {entry.title: entry for entry in hass.config_entries.async_entries(DOMAIN)}


async def _call(client, type_, **extra):
    await client.send_json_auto_id({"type": f"tariffy/{type_}", **extra})
    return await client.receive_json()


async def _zweites(hass, name="Ferienwohnung"):
    store = await objekte.async_get(hass)
    return await store.async_save_objekt(None, name, "mdi:beach")


class TestZuordnung:
    async def test_first_contract_creates_the_default_property(self, hass: HomeAssistant, hass_storage):
        entry = await _add(hass, "Strom")
        store = objekte.cached(hass)
        assert [o["name"] for o in store.items] == ["Home"]
        assert store.default == store.items[0]["id"]
        assert entry.data["objekt_id"] == store.default  # fixed, not a silent fallback
        assert hass_storage[objekte.STORE_KEY]["data"]["default"] == store.default

    async def test_german_name_of_the_first_property(self, hass: HomeAssistant):
        hass.config.language = "de"
        await _add(hass, "Strom")
        assert objekte.cached(hass).items[0]["name"] == "Zuhause"

    async def test_contracts_stay_where_they_are_when_the_default_changes(self, hass: HomeAssistant):
        alt = await _add(hass, "Strom")
        haus = objekte.cached(hass).default
        zweit = await _zweites(hass)
        await objekte.cached(hass).async_set_default(zweit)
        await hass.config_entries.async_reload(alt.entry_id)
        await hass.async_block_till_done()
        assert alt.data["objekt_id"] == haus

    async def test_an_unknown_property_falls_back_to_the_default(self, hass: HomeAssistant):
        entry = await _add(hass, "Strom", {**STROM, "objekt_id": "gibt-es-nicht"})
        assert entry.data["objekt_id"] == objekte.cached(hass).default

    async def test_stored_properties_are_loaded(self, hass: HomeAssistant, hass_storage):
        hass_storage[objekte.STORE_KEY] = {
            "version": 1, "key": objekte.STORE_KEY,
            "data": {"objekte": [{"id": "a", "name": "Haus", "icon": "mdi:home"}, {"id": "b", "name": "Laube", "icon": None}],
                     "default": "b"},
        }
        entry = await _add(hass, "Strom")
        assert entry.data["objekt_id"] == "b"
        assert [o["id"] for o in objekte.cached(hass).items] == ["a", "b"]

    async def test_the_next_contract_stays_in_the_property(self, hass: HomeAssistant):
        entry = await _add(hass, "Strom", None, NEXT)
        zweit = await _zweites(hass)
        objekte.zuordnen(hass, entry, zweit)
        entry.runtime_data._switch_now()  # noqa: SLF001
        await hass.async_block_till_done()
        assert entry.data["anbieter"] == "Neu"
        assert entry.data["objekt_id"] == zweit

    async def test_titles_of_notifications_name_the_property_with_several(self, hass: HomeAssistant):
        entry = await _add(hass, "Strom")
        assert entry.runtime_data._titel() == "Strom"  # noqa: SLF001
        await _zweites(hass)
        assert entry.runtime_data._titel() == "Home · Strom"  # noqa: SLF001

    async def test_sensors_carry_the_property(self, hass: HomeAssistant):
        entry = await _add(hass, "Strom")
        state = next(
            s for s in hass.states.async_all("sensor")
            if s.attributes.get("objekt") is not None
        )
        assert state.attributes["objekt"] == "Home"
        store = objekte.cached(hass)
        await store.async_save_objekt(entry.data["objekt_id"], "Haus", None)
        objekte.async_refresh_entities(hass)
        await hass.async_block_till_done()
        assert hass.states.get(state.entity_id).attributes["objekt"] == "Haus"


class TestObjekteVerwalten:
    async def test_add_rename_default_delete(self, hass: HomeAssistant):
        await _add(hass, "Strom")
        store = objekte.cached(hass)
        haus = store.default
        zweit = await _zweites(hass)
        with pytest.raises(objekte.ObjektError) as err:
            await store.async_save_objekt(None, " ferienwohnung ")
        assert err.value.code == "name_exists"
        for name in ("", " ", "x" * 81):
            with pytest.raises(objekte.ObjektError):
                await store.async_save_objekt(None, name)
        with pytest.raises(objekte.ObjektError):
            await store.async_save_objekt(None, "Garage", "kein-icon")
        await store.async_save_objekt(zweit, "Ferienhaus", "mdi:beach")
        assert store.name(zweit) == "Ferienhaus"
        # with contracts only with a target
        with pytest.raises(objekte.ObjektError) as err:
            await store.async_delete(haus)
        assert err.value.code == "has_contracts"
        with pytest.raises(objekte.ObjektError) as err:
            await store.async_delete(haus, haus)
        assert err.value.code == "invalid_target"
        assert await store.async_delete(haus, zweit) == 1
        assert _entries(hass)["Strom"].data["objekt_id"] == zweit
        assert store.default == zweit  # the default moved along
        with pytest.raises(objekte.ObjektError) as err:
            await store.async_delete(zweit)
        assert err.value.code == "last_objekt"

    async def test_panel_commands(self, frontend_env, hass, hass_ws_client):  # noqa: F811
        client = await hass_ws_client()
        await _add(hass, "Strom")
        haus = objekte.cached(hass).default

        reply = await _call(client, "contracts")
        assert reply["result"]["default_objekt"] == haus
        assert [o["name"] for o in reply["result"]["objekte"]] == ["Home"]
        assert reply["result"]["contracts"][0]["objekt_id"] == haus

        reply = await _call(client, "objekt_save", name="Ferienwohnung", icon="mdi:beach")
        zweit = reply["result"]["objekt_id"]
        assert (await _call(client, "objekt_save", name="ferienwohnung"))["error"]["code"] == "name_exists"
        assert (await _call(client, "objekt_save", objekt_id="x", name="Neu"))["error"]["code"] == "not_found"

        assert (await _call(client, "objekt_default", objekt_id=zweit))["success"] is True
        assert objekte.cached(hass).default == zweit

        # the panel's choice is the preselection for a new contract
        assert (await _call(client, "objekt_aktiv", objekt_id=haus))["success"] is True
        assert objekte.cached(hass).vorauswahl == haus
        assert (await _call(client, "objekt_aktiv", objekt_id="x"))["error"]["code"] == "not_found"

        assert (await _call(client, "objekt_delete", objekt_id=haus))["error"]["code"] == "has_contracts"
        reply = await _call(client, "objekt_delete", objekt_id=haus, ziel=zweit)
        assert reply["result"] == {"moved": 1}
        assert _entries(hass)["Strom"].data["objekt_id"] == zweit

    async def test_subscribers_hear_about_changes(self, frontend_env, hass, hass_ws_client):  # noqa: F811
        client = await hass_ws_client()
        await _add(hass, "Strom")
        await client.send_json_auto_id({"type": "tariffy/subscribe"})
        assert (await client.receive_json())["success"] is True
        first = await client.receive_json()
        assert len(first["event"]["objekte"]) == 1
        await _call_no_reply(client, "objekt_save", name="Garage")
        events = [await client.receive_json() for _ in range(2)]
        pushed = next(e for e in events if e.get("type") == "event")
        assert [o["name"] for o in pushed["event"]["objekte"]] == ["Home", "Garage"]

    async def test_only_administrators_manage(
        self, frontend_env, hass, hass_ws_client, hass_read_only_access_token  # noqa: F811
    ):
        client = await hass_ws_client(hass, hass_read_only_access_token)
        await _add(hass, "Strom")
        haus = objekte.cached(hass).default
        for type_, extra in (
            ("objekt_save", {"name": "Neu"}),
            ("objekt_default", {"objekt_id": haus}),
            ("objekt_delete", {"objekt_id": haus}),
        ):
            reply = await _call(client, type_, **extra)
            assert reply["error"]["code"] == "unauthorized", type_
        assert len(objekte.cached(hass).items) == 1
        # switching what one sees is for everybody
        assert (await _call(client, "objekt_aktiv", objekt_id=haus))["success"] is True


async def _call_no_reply(client, type_, **extra):
    await client.send_json_auto_id({"type": f"tariffy/{type_}", **extra})


class TestConfigFlow:
    async def _first_form(self, hass):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.FORM
        return result

    async def test_one_property_needs_no_choice(self, hass: HomeAssistant):
        await _add(hass, "Strom")
        result = await self._first_form(hass)
        assert "objekt_id" not in {str(k) for k in result["data_schema"].schema}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**_form_defaults(result), "name": "Internet", "sparte": "internet", "anbieter": "Telekom"}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _form_defaults(result))
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["objekt_id"] == objekte.cached(hass).default

    async def test_the_property_chosen_in_the_panel_is_preselected(self, hass: HomeAssistant):
        await _add(hass, "Strom")
        zweit = await _zweites(hass)
        objekte.cached(hass).aktiv = zweit
        result = await self._first_form(hass)
        defaults = _form_defaults(result)
        assert defaults["objekt_id"] == zweit
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**defaults, "name": "Internet", "sparte": "internet", "anbieter": "Telekom"}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _form_defaults(result))
        assert result["data"]["objekt_id"] == zweit

    async def test_a_contract_moves_by_editing_it(self, hass: HomeAssistant):
        entry = await _add(hass, "Strom")
        zweit = await _zweites(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        assert _form_defaults(result)["objekt_id"] == entry.data["objekt_id"]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**_form_defaults(result), "objekt_id": zweit}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _form_defaults(result))
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert _entries(hass)["Strom"].data["objekt_id"] == zweit

    async def test_editing_without_a_choice_keeps_the_property(self, hass: HomeAssistant):
        entry = await _add(hass, "Strom")
        haus = entry.data["objekt_id"]
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _form_defaults(result))
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _form_defaults(result))
        await hass.async_block_till_done()
        assert _entries(hass)["Strom"].data["objekt_id"] == haus


class TestSicherung:
    async def test_properties_travel_with_the_backup(self, frontend_env, hass, hass_ws_client, hass_storage):  # noqa: F811
        client = await hass_ws_client()
        await _add(hass, "Strom")
        zweit = await _zweites(hass)
        await _add(hass, "Strom ", {**STROM, "objekt_id": zweit})  # same kind, other property
        hass.config_entries.async_update_entry(_entries(hass)["Strom "], title="Strom")
        saved = (await _call(client, "backup_export"))["result"]["backup"]
        assert {o["name"] for o in saved["objekte"]} == {"Home", "Ferienwohnung"}
        assert saved["default_objekt"] == objekte.cached(hass).default

        # the same contract name in two properties: both are there, nothing to create
        reply = await _call(client, "backup_preview", backup=saved)
        assert [step["action"] for step in reply["result"]["contracts"]] == ["skip", "skip"]
        assert {step["objekt"] for step in reply["result"]["contracts"]} == {"Home", "Ferienwohnung"}

        # the second property and its contract are lost: restoring brings both back, the contract into its property
        lost = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.data["objekt_id"] == zweit)
        await hass.config_entries.async_remove(lost.entry_id)
        await hass.async_block_till_done()
        await objekte.cached(hass).async_delete(zweit)
        reply = await _call(client, "backup_restore", backup=saved)
        assert reply["result"] == {"created": ["Strom"], "skipped": ["Strom"]}
        await hass.async_block_till_done()
        assert objekte.cached(hass).name(zweit) == "Ferienwohnung"
        assert sorted(e.data["objekt_id"] == zweit for e in hass.config_entries.async_entries(DOMAIN)) == [False, True]

    async def test_an_old_backup_goes_into_the_default_property(self, frontend_env, hass, hass_ws_client):  # noqa: F811
        client = await hass_ws_client()
        await _add(hass, "Wasser", {**STROM, "sparte": "water"})
        await _zweites(hass)
        old = {"tariffy_backup": 1, "contracts": [{"title": "Strom", "data": STROM, "options": {}}]}
        reply = await _call(client, "backup_restore", backup=old)
        assert reply["result"]["created"] == ["Strom"]
        await hass.async_block_till_done()
        assert _entries(hass)["Strom"].data["objekt_id"] == objekte.cached(hass).default
