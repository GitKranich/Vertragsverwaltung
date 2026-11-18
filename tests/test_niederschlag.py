"""Niederschlagswassergebuehr beim Wasservertrag: versiegelte Flaeche x Gebuehr je m² und Jahr,
als fester Monatsbetrag wie ein Grundpreis."""

from __future__ import annotations

import pytest

from .test_coordinator import SENSOR
from .test_panel import GOLDEN

M = 30.44
WASSER = {
    "sparte": "water",
    "anbieter": "Stadtwerke",
    "arbeitspreis": 1.89,
    "grundpreis": 14.68,
    "abschlag": 60.0,
    "beginn": "2026-01-01",
    "ende": "2026-12-31",
    "verbrauch_sensor": SENSOR,
    "erinnerung_monate": "0",
}


async def test_stormwater_fee_counts_like_a_standing_charge(contract):
    _, base = await contract.setup(dict(WASSER), **GOLDEN)
    entry, _ = await contract.setup(
        {**WASSER, "niederschlag_flaeche": 150.0, "niederschlag_gebuehr": 0.80}, **GOLDEN
    )
    d = entry.runtime_data.data
    monat = 150 * 0.80 / 12  # 10 € im Monat
    assert d["niederschlag_jahr"] == 120.0
    assert d["kosten_bisher"] == pytest.approx(base["kosten_bisher"] + monat * 192 / M, abs=0.01)
    assert d["guthaben_bisher"] == pytest.approx(base["guthaben_bisher"] - monat * 192 / M, abs=0.01)
    assert d["prognose_real"] == pytest.approx(base["prognose_real"] - monat * 364 / M, abs=0.01)
    assert d["kosten_jahr"] == pytest.approx(base["kosten_jahr"] + 120, abs=0.01)
    assert d["grundpreis"] == 14.68  # der Grundpreis selbst bleibt


async def test_stormwater_fee_only_with_area_and_fee(contract):
    entry, base = await contract.setup({**WASSER, "niederschlag_flaeche": 150.0}, **GOLDEN)
    assert entry.runtime_data.data["niederschlag_jahr"] is None
