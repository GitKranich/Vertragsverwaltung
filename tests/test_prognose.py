"""Verbrauchsprognose bis Vertragsende: saisonal (Monatswerte des Zaehlers, sonst Standardprofil),
letzte 30 Tage, Durchschnitt."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.tariffy import coordinator as coord

from .test_coordinator import STROM
from .test_panel import GOLDEN

# echte Monatswerte eines Haushalts (kWh), Maerz 2025 bis Oktober 2026
MONATE = {
    (2025, 3): 533, (2025, 4): 441, (2025, 5): 425, (2025, 6): 178, (2025, 7): 181,
    (2025, 8): 243, (2025, 9): 324, (2025, 10): 578, (2025, 11): 571, (2025, 12): 676,
    (2026, 1): 669, (2026, 2): 573, (2026, 3): 432, (2026, 4): 328, (2026, 5): 381,
    (2026, 6): 391, (2026, 7): 250, (2026, 8): 267, (2026, 9): 244, (2026, 10): 536,
}


def test_seasonal_profile_puts_more_into_the_winter():
    profil = coord._monatsprofil_aus_statistik(MONATE)
    assert profil[1] == pytest.approx(669 / 31)
    assert profil[3] == pytest.approx((533 / 31 + 432 / 31) / 2)
    abschnitte = coord._prognose_abschnitte(
        profil, date(2026, 2, 11), date(2026, 11, 17), date(2027, 2, 11), 3560.9
    )
    rest = sum(m for _, _, m in abschnitte)
    linear = 3560.9 / 279 * 86
    assert 1750 < rest < 1900  # Winterverbrauch, nicht ~1100 kWh linear
    assert rest > linear * 1.5
    assert [a[0] for a in abschnitte] == [date(2026, 11, 17), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)]


def test_missing_months_fall_back_and_the_gas_profile_follows_the_heating_season():
    assert coord._prognose_abschnitte({1: 1.0}, date(2026, 1, 1), date(2026, 3, 1), date(2026, 12, 31), 100) is None
    gas = coord._standardprofil("gas")
    assert gas[1] > 3 * gas[7]
    strom = coord._standardprofil("electricity")
    assert strom[12] > strom[6]


def test_rest_cost_by_month_uses_each_months_price():
    data = {**STROM, "_preisaenderungen": [{"datum": "2026-10-01", "arbeitspreis": 0.40}]}
    seg = coord._preis_segmente(data, date(2026, 1, 1))
    abschnitte = [(date(2026, 9, 1), date(2026, 10, 1), 100.0), (date(2026, 10, 1), date(2026, 11, 1), 300.0)]
    assert coord._rest_arbeitspreis_kosten_abschnitte(seg, abschnitte, 1.0) == pytest.approx(
        100 * 0.2977 + 300 * 0.40
    )


async def test_seasonal_forecast_in_the_coordinator(contract):
    _, base = await contract.setup(STROM, **GOLDEN)
    with patch.object(coord.TariffyCoordinator, "_monatswerte", AsyncMock(return_value=MONATE)):
        entry, _ = await contract.setup({**STROM, "prognose": "saisonal"}, **GOLDEN)
    d = entry.runtime_data.data
    assert d["prognose_methode"] == "saisonal"
    profil = coord._monatsprofil_aus_statistik(MONATE)
    rest = sum(m for _, _, m in coord._prognose_abschnitte(
        profil, date(2026, 1, 1), date(2026, 7, 12), date(2026, 12, 31), base["verbrauch_bisher"]
    ))
    assert d["verbrauch_hochgerechnet"] == pytest.approx(base["verbrauch_bisher"] + rest, abs=0.1)
    geschaetzt = d["verbrauch_hochgerechnet"] * 0.2977 + 11.51 * 364 / 30.44
    assert d["prognose_real"] == pytest.approx(base["jahreskosten"] - geschaetzt, abs=0.05)


async def test_seasonal_without_statistics_uses_the_standard_profile(contract):
    with patch.object(coord.TariffyCoordinator, "_monatswerte", AsyncMock(return_value={})):
        entry, _ = await contract.setup({**STROM, "prognose": "saisonal"}, **GOLDEN)
    assert entry.runtime_data.data["prognose_methode"] == "standardprofil"


async def test_last_30_days(contract):
    _, base = await contract.setup(STROM, **GOLDEN)
    with patch.object(
        coord.TariffyCoordinator, "_verbrauch_bisher_fuer_sensor", AsyncMock(return_value=300.0)
    ):
        entry, _ = await contract.setup({**STROM, "prognose": "letzte_30_tage"}, **GOLDEN)
    d = entry.runtime_data.data
    assert d["prognose_methode"] == "letzte_30_tage"
    # 300 kWh in 30 Tagen = 10 kWh/Tag fuer die 172 Resttage (12.07. bis 31.12.)
    assert d["verbrauch_hochgerechnet"] == pytest.approx(d["verbrauch_bisher"] + 10 * 172, abs=0.1)


async def test_contracts_without_a_setting_keep_the_average(contract):
    entry, base = await contract.setup(STROM, **GOLDEN)
    d = entry.runtime_data.data
    assert d["prognose_methode"] == "durchschnitt"
    assert d["prognose"] == "durchschnitt"
    assert d["verbrauch_hochgerechnet"] == pytest.approx(d["verbrauch_bisher"] / 192 * 364, abs=0.1)
