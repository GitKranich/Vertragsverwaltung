"""Abschlaege als gebuchte Monatsraten ab der ersten Abschlagszahlung, und das Werten einer
gemerkten Abschlagsaenderung als Korrektur."""

from __future__ import annotations

import pytest

from .test_coordinator import STROM
from .test_panel import GOLDEN, frontend_env  # noqa: F401  (fixture)


async def _call(client, kind, **extra):
    await client.send_json_auto_id({"type": f"tariffy/{kind}", **extra})
    return await client.receive_json()


async def test_monthly_debits_from_the_first_payment(contract):
    _, base = await contract.setup(STROM, **GOLDEN)
    entry, _ = await contract.setup({**STROM, "erste_zahlung": "2026-02-01"}, **GOLDEN)
    d = entry.runtime_data.data
    assert d["abschlag_bisher_gezahlt"] == 240.0  # Feb bis Jul, je 40 €
    assert d["jahreskosten"] == 440.0  # Feb bis Dez: 11 Raten
    assert d["kosten_bisher"] == base["kosten_bisher"]
    assert d["guthaben_bisher"] == pytest.approx(240.0 - base["kosten_bisher"], abs=0.01)
    geschaetzt = base["jahreskosten"] - base["prognose_real"]
    assert d["prognose_real"] == pytest.approx(440.0 - geschaetzt, abs=0.02)
    assert d["abschlag_anpassung_empfohlen"] == pytest.approx((geschaetzt - 240.0) / 5, abs=0.02)


async def test_debits_use_the_price_changes_and_the_bonus_spreads_over_the_open_rates(contract):
    data = {
        **STROM, "erste_zahlung": "2026-02-01", "bonus": 100.0,
        "_preisaenderungen": [{"datum": "2026-10-01", "abschlag": 60.0}],
    }
    entry, _ = await contract.setup(data, **GOLDEN)
    d = entry.runtime_data.data
    assert d["abschlag_bisher_gezahlt"] == 240.0
    assert d["jahreskosten"] == 8 * 40.0 + 3 * 60.0  # Feb-Sep 40 €, Okt-Dez 60 €


async def test_a_remembered_instalment_change_can_be_treated_as_a_correction(
    frontend_env, contract, hass, hass_ws_client  # noqa: F811
):
    client = await hass_ws_client()
    data = {**STROM, "_abschlag_ab_datum": "2026-07-12", "_abschlag_vorheriger_wert": 41.0}
    entry, before = await contract.setup(data, **GOLDEN)
    assert before["abschlag_aenderung"] == {"datum": "2026-07-12", "vorher": 41.0}
    reply = await _call(client, "abschlag_korrektur", entry_id=entry.entry_id)
    assert reply["success"] is True
    await hass.async_block_till_done()
    assert "_abschlag_ab_datum" not in entry.data and "_abschlag_vorheriger_wert" not in entry.data
    d = entry.runtime_data.data
    assert d["abschlag_aenderung"] is None
    assert d["abschlag_bisher_gezahlt"] < before["abschlag_bisher_gezahlt"]
    reply = await _call(client, "abschlag_korrektur", entry_id=entry.entry_id)
    assert reply["success"] is False and reply["error"]["code"] == "not_found"
