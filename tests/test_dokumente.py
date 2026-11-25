"""Verknuepfte Paperless-Dokumente eines Vertrags (nur Verweise, ueber Paperless-ngx Insights)."""

from __future__ import annotations

import json

import pytest

from custom_components.tariffy.diagnostics import async_get_config_entry_diagnostics

from .test_coordinator import STROM
from .test_panel import GOLDEN, frontend_env  # noqa: F401  (fixture)
from .test_reconfigure import _reconfigure

PL = "01PAPERLESS"
VERTRAG = {"paperless_entry": PL, "id": 1188, "titel": "EnBW Gasliefervertrag", "erstellt": "2026-10-13"}


async def _call(client, kind, **extra):
    await client.send_json_auto_id({"type": f"tariffy/{kind}", **extra})
    return await client.receive_json()


async def _contracts(client):
    await client.send_json_auto_id({"type": "tariffy/contracts"})
    return (await client.receive_json())["result"]["contracts"]


async def test_link_relabel_and_remove(frontend_env, contract, hass, hass_ws_client):  # noqa: F811
    client = await hass_ws_client()
    entry, _ = await contract.setup(STROM, **GOLDEN)
    values = {**VERTRAG, "bezeichnung": "  Vertrag  ", "web_url": "http://diskstation423p:8777/"}
    reply = await _call(client, "dokument_save", entry_id=entry.entry_id, values=values)
    assert reply["success"] is True
    assert entry.data["_dokumente"] == [
        {**VERTRAG, "bezeichnung": "Vertrag", "web_url": "http://diskstation423p:8777"}
    ]
    shown = (await _contracts(client))[0]["dokumente"]
    assert shown[0]["index"] == 0 and shown[0]["titel"] == "EnBW Gasliefervertrag"

    reply = await _call(client, "dokument_save", entry_id=entry.entry_id, index=0, values={**VERTRAG, "bezeichnung": ""})
    assert reply["success"] is True
    assert entry.data["_dokumente"] == [VERTRAG]  # empty label and no address: left out

    assert (await _call(client, "dokument_delete", entry_id=entry.entry_id, index=3))["error"]["code"] == "not_found"
    assert (await _call(client, "dokument_delete", entry_id=entry.entry_id, index=0))["success"] is True
    assert "_dokumente" not in entry.data
    assert (await _contracts(client))[0]["dokumente"] == []


async def test_newest_first_with_their_position(frontend_env, contract, hass, hass_ws_client):  # noqa: F811
    client = await hass_ws_client()
    alt = {**VERTRAG, "id": 5, "erstellt": "2025-01-02"}
    neu = {**VERTRAG, "id": 7, "erstellt": "2026-11-16"}
    entry, _ = await contract.setup({**STROM, "_dokumente": [alt, neu]}, **GOLDEN)
    shown = (await _contracts(client))[0]["dokumente"]
    assert [(d["id"], d["index"]) for d in shown] == [(7, 1), (5, 0)]


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({**VERTRAG, "paperless_entry": ""}, "invalid_entry"),
        ({**VERTRAG, "id": 0}, "invalid_id"),
        ({**VERTRAG, "id": "12"}, "invalid_id"),
        ({**VERTRAG, "titel": "   "}, "invalid_titel"),
        ({**VERTRAG, "titel": "x" * 301}, "invalid_titel"),
        ({**VERTRAG, "erstellt": "gestern"}, "invalid_erstellt"),
        ({**VERTRAG, "bezeichnung": "x" * 101}, "invalid_bezeichnung"),
        ({**VERTRAG, "web_url": "javascript" + ":alert(1)"}, "invalid_web_url"),
        ({**VERTRAG, "web_url": "ftp://nas/docs"}, "invalid_web_url"),
    ],
)
async def test_bad_input_is_refused(frontend_env, contract, hass, hass_ws_client, values, code):  # noqa: F811
    client = await hass_ws_client()
    entry, _ = await contract.setup(STROM, **GOLDEN)
    reply = await _call(client, "dokument_save", entry_id=entry.entry_id, values=values)
    assert reply["success"] is False and reply["error"]["code"] == code
    assert "_dokumente" not in entry.data


async def test_a_document_is_linked_only_once(frontend_env, contract, hass, hass_ws_client):  # noqa: F811
    client = await hass_ws_client()
    entry, _ = await contract.setup({**STROM, "_dokumente": [VERTRAG]}, **GOLDEN)
    reply = await _call(client, "dokument_save", entry_id=entry.entry_id, values={**VERTRAG, "bezeichnung": "Kopie"})
    assert reply["error"]["code"] == "duplicate"
    # the same document from another Paperless connection is a different document
    reply = await _call(client, "dokument_save", entry_id=entry.entry_id, values={**VERTRAG, "paperless_entry": "01OTHER"})
    assert reply["success"] is True


async def test_at_most_thirty(frontend_env, contract, hass, hass_ws_client):  # noqa: F811
    client = await hass_ws_client()
    viele = [{**VERTRAG, "id": n} for n in range(1, 31)]
    entry, _ = await contract.setup({**STROM, "_dokumente": viele}, **GOLDEN)
    reply = await _call(client, "dokument_save", entry_id=entry.entry_id, values={**VERTRAG, "id": 99})
    assert reply["error"]["code"] == "too_many"


async def test_non_admin_cannot_link(
    frontend_env, contract, hass, hass_ws_client, hass_read_only_access_token  # noqa: F811
):
    client = await hass_ws_client(hass, hass_read_only_access_token)
    entry, _ = await contract.setup(STROM, **GOLDEN)
    reply = await _call(client, "dokument_save", entry_id=entry.entry_id, values=VERTRAG)
    assert reply["error"]["code"] == "unauthorized"
    assert "_dokumente" not in entry.data


async def test_the_links_survive_editing_the_contract(frontend_env, contract, hass):  # noqa: F811
    entry, _ = await contract.setup({**STROM, "ende": "2026-12-31", "_dokumente": [VERTRAG]}, **GOLDEN)
    data = await _reconfigure(hass, entry, first={"tarif": "Neu"})
    assert data["tarif"] == "Neu"
    assert data["_dokumente"] == [VERTRAG]


async def test_the_links_survive_a_tariff_switch(contract, hass):
    entry, _ = await contract.setup({**STROM, "_dokumente": [VERTRAG]}, **GOLDEN)
    options = {"next_beginn": "2026-07-01", "next_ende": "2027-06-30", "next_anbieter": "Neu", "next_arbeitspreis": 0.25}
    hass.config_entries.async_update_entry(entry, options=options)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.data["anbieter"] == "Neu"
    assert entry.data["_dokumente"] == [VERTRAG]


def test_the_links_survive_a_backup():
    from custom_components.tariffy import backup

    raw = {"title": "Strom", "data": {**STROM, "_dokumente": [{**VERTRAG, "bezeichnung": "Vertrag"}]}}
    assert backup.sanitize_contract(raw)["data"]["_dokumente"] == [{**VERTRAG, "bezeichnung": "Vertrag"}]


async def test_diagnostics_do_not_show_the_titles(contract, hass):
    entry, _ = await contract.setup({**STROM, "_dokumente": [VERTRAG]}, **GOLDEN)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert "Gasliefervertrag" not in json.dumps(result)
