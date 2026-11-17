"""Preisaenderungen im laufenden Strom-/Gasvertrag: neue Preise ab einem Datum, der Vertrag
(Beginn, Ende, Zaehler) laeuft unveraendert weiter."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from custom_components.tariffy import coordinator as coord

from .test_coordinator import STROM
from .test_panel import GOLDEN, frontend_env  # noqa: F401  (fixture)

M = 30.44
BEGINN = date(2026, 1, 1)
STICHTAG = date(2026, 4, 1)
HEUTE = date(2026, 7, 12)
ENDE = date(2026, 12, 31)


async def _call(client, kind, **extra):
    await client.send_json_auto_id({"type": f"tariffy/{kind}", **extra})
    return await client.receive_json()


def _historic(stichtag_wert):
    """LTS: am Vertragsbeginn 1000, am Stichtag `stichtag_wert`."""

    async def _value(_hass, _entity_id, at_date):
        if at_date == STICHTAG:
            return (stichtag_wert, stichtag_wert)
        return (1000.0, 1000.0)

    return _value


# ------------------------------------------------------------------ reine Funktionen
def test_segments_carry_unchanged_values_forward():
    data = {
        **STROM,
        "_preisaenderungen": [
            {"datum": "2026-08-01", "abschlag": 70.0},
            {"datum": "2026-04-01", "arbeitspreis": 0.35},
            {"datum": "2025-12-01", "arbeitspreis": 9.9},  # vor Vertragsbeginn: ignoriert
        ],
    }
    seg = coord._preis_segmente(data, BEGINN)
    assert [s["von"] for s in seg] == [BEGINN, STICHTAG, date(2026, 8, 1)]
    assert seg[1] == {**seg[1], "arbeitspreis": 0.35, "grundpreis": 11.51, "abschlag": 40.0}
    assert seg[2] == {**seg[2], "arbeitspreis": 0.35, "grundpreis": 11.51, "abschlag": 70.0}
    assert coord._segment_am(seg, HEUTE) is seg[1]
    assert coord._segment_am(seg, date(2026, 3, 31)) is seg[0]


def test_monthly_integral_splits_at_the_date():
    seg = coord._preis_segmente({**STROM, "_preisaenderungen": [{"datum": "2026-04-01", "abschlag": 60}]}, BEGINN)
    m_stichtag = 90 / M
    assert coord._integral_monate(seg, "abschlag", BEGINN, 0, 6) == pytest.approx(
        40 * m_stichtag + 60 * (6 - m_stichtag)
    )
    assert coord._integral_monate(seg, "abschlag", BEGINN, 0, 2) == pytest.approx(80)


# ------------------------------------------------------------------ im Coordinator
async def test_costs_balance_and_forecast_use_each_period_with_its_prices(
    frontend_env, contract, hass, hass_ws_client  # noqa: F811
):
    client = await hass_ws_client()
    entry, base = await contract.setup(STROM, **GOLDEN)
    with patch(f"{coord.__name__}._get_historic_value", _historic(1800.0)):
        reply = await _call(
            client, "preis_save", entry_id=entry.entry_id,
            values={"datum": "2026-04-01", "arbeitspreis": 0.35, "grundpreis": 13.0, "abschlag": 60},
        )
        assert reply["success"] is True
        await hass.async_block_till_done()
    d = entry.runtime_data.data

    # heute gueltige Werte
    assert d["arbeitspreis"] == 0.35 and d["grundpreis"] == 13.0 and d["abschlag"] == 60.0
    # Vertragsdaten bleiben die Werte ab Vertragsbeginn
    assert entry.data["arbeitspreis"] == 0.2977 and entry.data["beginn"] == "2026-01-01"
    assert d["verbrauch_bisher"] == base["verbrauch_bisher"]  # Zaehler laeuft einfach weiter

    vorher, nachher = 800.0, 1481.17 - 800.0
    m1, m2 = 90 / M, 102 / M
    kosten = vorher * 0.2977 + nachher * 0.35 + 11.51 * m1 + 13.0 * m2
    gezahlt = 40 * m1 + 60 * m2
    assert d["kosten_bisher"] == pytest.approx(kosten, abs=0.01)
    assert d["abschlag_bisher_gezahlt"] == pytest.approx(gezahlt, abs=0.01)
    assert d["guthaben_bisher"] == pytest.approx(gezahlt - kosten, abs=0.01)

    laufzeit = (ENDE - BEGINN).days / M
    rest_menge = d["verbrauch_hochgerechnet"] - 1481.17
    geschaetzt = kosten + rest_menge * 0.35 + 13.0 * (laufzeit - (m1 + m2))
    ab_gesamt = 40 * m1 + 60 * (laufzeit - m1)
    assert d["jahreskosten"] == pytest.approx(ab_gesamt, abs=0.01)
    assert d["prognose_real"] == pytest.approx(ab_gesamt - geschaetzt, abs=0.01)
    assert d["abschlag_anpassung_empfohlen"] == pytest.approx(
        (geschaetzt - gezahlt) / (laufzeit - m1 - m2), abs=0.01
    )
    assert d["preisaenderungen"][0]["status"] == "aktiv"
    assert d["preis_basis"] == {"arbeitspreis": 0.2977, "grundpreis": 11.51, "abschlag": 40.0}


async def test_without_statistics_on_the_date_the_meter_reading_is_used(
    frontend_env, contract, hass, hass_ws_client  # noqa: F811
):
    client = await hass_ws_client()
    entry, _ = await contract.setup(STROM, **GOLDEN)
    with patch(f"{coord.__name__}._get_historic_value", _historic(None)):
        await _call(
            client, "preis_save", entry_id=entry.entry_id,
            values={"datum": "2026-04-01", "arbeitspreis": 0.35, "zaehlerstand": 1800},
        )
        await hass.async_block_till_done()
    d = entry.runtime_data.data
    kosten = 800 * 0.2977 + 681.17 * 0.35 + 11.51 * (192 / M)
    assert d["kosten_bisher"] == pytest.approx(kosten, abs=0.01)


async def test_a_future_change_only_moves_the_forecast(frontend_env, contract, hass, hass_ws_client):  # noqa: F811
    client = await hass_ws_client()
    entry, base = await contract.setup(STROM, **GOLDEN)
    await _call(client, "preis_save", entry_id=entry.entry_id,
                values={"datum": "2026-10-01", "arbeitspreis": 0.40})
    await hass.async_block_till_done()
    d = entry.runtime_data.data
    assert d["arbeitspreis"] == 0.2977
    assert d["kosten_bisher"] == pytest.approx(base["kosten_bisher"], abs=0.01)
    assert d["guthaben_bisher"] == pytest.approx(base["guthaben_bisher"], abs=0.01)
    assert d["abschlag_bisher_gezahlt"] == pytest.approx(base["abschlag_bisher_gezahlt"], abs=0.01)
    assert d["prognose_real"] < base["prognose_real"]  # teurer ab Oktober
    assert d["naechste_preisaenderung"] == date(2026, 10, 1)
    assert d["preisaenderungen"][0]["status"] == "kuenftig"

    # am Stichtag gilt der neue Preis
    contract.freezer.move_to("2026-10-02 12:00:00")
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.data["arbeitspreis"] == 0.40


async def test_edit_delete_and_validation(frontend_env, contract, hass, hass_ws_client):  # noqa: F811
    client = await hass_ws_client()
    entry, base = await contract.setup(STROM, **GOLDEN)
    eid = entry.entry_id
    assert (await _call(client, "preis_save", entry_id=eid, values={"datum": "2026-05-01", "abschlag": 50}))["success"]
    assert (await _call(client, "preis_save", entry_id=eid, values={"datum": "2026-03-01", "abschlag": 45}))["success"]
    assert [p["datum"] for p in entry.data["_preisaenderungen"]] == ["2026-03-01", "2026-05-01"]  # sortiert

    for values, code in (
        ({"datum": "2026-01-01", "abschlag": 1}, "invalid_preis_datum"),
        ({"datum": "2027-02-01", "abschlag": 1}, "invalid_preis_datum"),
        ({"datum": "kaputt", "abschlag": 1}, "invalid_preis_datum"),
        ({"datum": "2026-06-01"}, "invalid_preis_leer"),
        ({"datum": "2026-05-01", "abschlag": 1}, "duplicate_datum"),
        ({"datum": "2026-06-01", "abschlag": -1}, "invalid_abschlag"),
    ):
        reply = await _call(client, "preis_save", entry_id=eid, values=values)
        assert reply["success"] is False and reply["error"]["code"] == code, values

    assert (await _call(client, "preis_save", entry_id=eid, index=1,
                        values={"datum": "2026-05-01", "abschlag": 55}))["success"]
    assert entry.data["_preisaenderungen"][1] == {"datum": "2026-05-01", "abschlag": 55.0}
    assert (await _call(client, "preis_delete", entry_id=eid, index=0))["success"]
    assert (await _call(client, "preis_delete", entry_id=eid, index=0))["success"]
    assert "_preisaenderungen" not in entry.data
    await hass.async_block_till_done()
    assert entry.runtime_data.data["kosten_bisher"] == base["kosten_bisher"]


async def test_only_for_electricity_and_gas(frontend_env, contract, hass, hass_ws_client):  # noqa: F811
    client = await hass_ws_client()
    entry, _ = await contract.setup({**STROM, "sparte": "internet"}, **GOLDEN)
    reply = await _call(client, "preis_save", entry_id=entry.entry_id,
                        values={"datum": "2026-04-01", "abschlag": 30})
    assert reply["success"] is False and reply["error"]["code"] == "not_available"


async def test_a_tariff_switch_starts_without_the_price_changes(contract, hass):
    options = {"next_beginn": "2026-07-01", "next_ende": "2027-06-30", "next_anbieter": "Neu", "next_arbeitspreis": 0.25}
    data = {**STROM, "_preisaenderungen": [{"datum": "2026-04-01", "arbeitspreis": 0.35}]}
    entry, _ = await contract.setup(data, options=options, **GOLDEN)
    await hass.async_block_till_done()
    assert "_preisaenderungen" not in entry.data
    assert entry.data["_historie"][-1]["arbeitspreis"] == 0.35  # zuletzt gueltiger Preis
