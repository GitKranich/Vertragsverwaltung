"""Neukundenbonus (mit der Jahresrechnung verrechnet) und Sofortbonus (schon ausgezahlt)."""

from __future__ import annotations

import pytest

from .test_coordinator import STROM
from .test_panel import GOLDEN

M = 30.44


async def test_signup_bonus_counts_in_forecast_and_recommendation_not_in_balance(contract):
    _, base = await contract.setup(STROM, **GOLDEN)
    entry, _ = await contract.setup({**STROM, "bonus": 120.0}, **GOLDEN)
    d = entry.runtime_data.data
    rest = (364 - 192) / M  # Laufzeit 01.01.-31.12.2026, heute Tag 192
    assert d["prognose_real"] == pytest.approx(base["prognose_real"] + 120, abs=0.01)
    assert d["abschlag_anpassung_empfohlen"] == pytest.approx(
        base["abschlag_anpassung_empfohlen"] - 120 / rest, abs=0.02
    )
    assert d["guthaben_bisher"] == base["guthaben_bisher"]
    assert d["kosten_bisher"] == base["kosten_bisher"]
    assert d["kosten_gesamt"] == base["kosten_gesamt"]


async def test_instant_bonus_lowers_only_the_total_cost(contract):
    _, base = await contract.setup(STROM, **GOLDEN)
    entry, _ = await contract.setup({**STROM, "sofortbonus": 75.0}, **GOLDEN)
    d = entry.runtime_data.data
    assert d["kosten_gesamt"] == pytest.approx(base["kosten_gesamt"] - 75, abs=0.01)
    assert d["prognose_real"] == base["prognose_real"]
    assert d["guthaben_bisher"] == base["guthaben_bisher"]
    assert d["abschlag_anpassung_empfohlen"] == base["abschlag_anpassung_empfohlen"]


async def test_the_history_entry_carries_both_bonuses(contract):
    from datetime import date

    entry, _ = await contract.setup(STROM, **GOLDEN)
    plain = entry.runtime_data._historie_eintrag(date(2026, 7, 12))
    entry, _ = await contract.setup({**STROM, "bonus": 100.0, "sofortbonus": 50.0}, **GOLDEN)
    term = entry.runtime_data._historie_eintrag(date(2026, 7, 12))
    assert term["kosten"] == pytest.approx(plain["kosten"] - 150, abs=0.01)
    assert term["saldo"] == pytest.approx(plain["saldo"] + 100, abs=0.01)


async def test_the_bonus_does_not_carry_over_to_the_next_contract(contract, hass):
    options = {"next_beginn": "2026-07-01", "next_ende": "2027-06-30", "next_anbieter": "Neu", "next_arbeitspreis": 0.25}
    entry, _ = await contract.setup({**STROM, "bonus": 100.0, "sofortbonus": 50.0}, options=options, **GOLDEN)
    await hass.async_block_till_done()
    assert entry.data["anbieter"] == "Neu"
    assert "bonus" not in entry.data and "sofortbonus" not in entry.data
