"""Beitrags-/Preiserhoehung in Pauschalvertraegen (Versicherung, Internet, ...): neuer Betrag
pro Zahlung ab einem Datum, der Vertrag laeuft weiter."""

from __future__ import annotations

import pytest

from .test_panel import GOLDEN, frontend_env  # noqa: F401  (fixture)

M = 30.44
INSURANCE = {
    "sparte": "insurance",
    "anbieter": "Versicherung AG",
    "abschlag": 50.0,
    "beginn": "2026-01-01",
    "ende": "2026-12-31",
    "erinnerung_monate": "0",
}


async def _call(client, kind, **extra):
    await client.send_json_auto_id({"type": f"tariffy/{kind}", **extra})
    return await client.receive_json()


async def test_monthly_premium_increase(frontend_env, contract, hass, hass_ws_client):  # noqa: F811
    client = await hass_ws_client()
    entry, base = await contract.setup(dict(INSURANCE), **GOLDEN)
    assert base["preisaenderung_moeglich"] is True
    reply = await _call(client, "preis_save", entry_id=entry.entry_id,
                        values={"datum": "2026-05-01", "betrag": 60, "arbeitspreis": 0.3})
    assert reply["success"] is True
    await hass.async_block_till_done()
    assert entry.data["_preisaenderungen"] == [{"datum": "2026-05-01", "abschlag": 60.0}]
    d = entry.runtime_data.data
    assert d["abschlag"] == 60.0
    assert d["abschlag_bisher_gezahlt"] == 4 * 50 + 3 * 60  # Jan-Apr 50, Mai-Jul 60
    assert d["kosten_gesamt"] == 380.0
    assert d["jahreskosten"] == pytest.approx(50 * 120 / M + 60 * (364 - 120) / M, abs=0.01)
    assert d["kosten_jahr"] == 720.0
    assert d["preisaenderungen"][0]["status"] == "aktiv"


async def test_quarterly_premium_increase_takes_the_amount_per_payment(
    frontend_env, contract, hass, hass_ws_client  # noqa: F811
):
    client = await hass_ws_client()
    entry, _ = await contract.setup({**INSURANCE, "zahlweise": "quarterly"}, **GOLDEN)
    reply = await _call(client, "preis_save", entry_id=entry.entry_id,
                        values={"datum": "2026-04-01", "betrag": 180})
    assert reply["success"] is True
    await hass.async_block_till_done()
    assert entry.data["_preisaenderungen"] == [{"datum": "2026-04-01", "abschlag": 60.0}]
    d = entry.runtime_data.data
    assert d["abschlag_je_zahlung"] == 180.0
    assert d["abschlag_bisher_gezahlt"] == 150 + 180 + 180  # 01.01., 01.04., 01.07.


async def test_flat_rate_needs_an_amount_and_loans_are_excluded(
    frontend_env, contract, hass, hass_ws_client  # noqa: F811
):
    client = await hass_ws_client()
    entry, _ = await contract.setup(dict(INSURANCE), **GOLDEN)
    reply = await _call(client, "preis_save", entry_id=entry.entry_id,
                        values={"datum": "2026-05-01", "arbeitspreis": 0.3})
    assert reply["success"] is False and reply["error"]["code"] == "invalid_preis_leer"
    entry, _ = await contract.setup({**INSURANCE, "sparte": "loan"}, **GOLDEN)
    reply = await _call(client, "preis_save", entry_id=entry.entry_id,
                        values={"datum": "2026-05-01", "betrag": 60})
    assert reply["success"] is False and reply["error"]["code"] == "not_available"
