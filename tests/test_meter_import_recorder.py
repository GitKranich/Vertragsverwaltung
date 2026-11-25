"""Import historischer Zaehlerstaende: Schreiben in die echte Recorder-Statistik."""

from __future__ import annotations

from datetime import datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.tariffy import meter_import as mi

from .test_meter_import import TEXT


# der Recorder muss vor hass eingerichtet werden: die autouse-Fixtures aus conftest hier ersetzen
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Recorder zuerst, dann die eigene Integration."""


@pytest.fixture(autouse=True)
async def _notifications(recorder_mock, hass: HomeAssistant):
    assert await async_setup_component(hass, "persistent_notification", {})


async def test_import_writes_the_long_term_statistics(hass):
    from homeassistant.components.recorder.statistics import statistics_during_period
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )
    from homeassistant.components.recorder import get_instance

    hass.states.async_set(
        "sensor.wasser", "unavailable", {"state_class": "total_increasing", "unit_of_measurement": "m³"}
    )
    bericht = await mi.zaehlerstaende_importieren(hass, "sensor.wasser", TEXT, nur_pruefen=False)
    assert bericht["importiert"] is True
    await async_wait_recording_done(hass)
    start = datetime(2025, 12, 1, tzinfo=dt_util.UTC)
    stats = await get_instance(hass).async_add_executor_job(
        lambda: statistics_during_period(
            hass, start, None, {"sensor.wasser"}, "hour", None, {"state", "sum"}
        )
    )
    rows = stats["sensor.wasser"]
    assert [r["state"] for r in rows] == [72.0, 80.5, 89.25, 95.0]
    assert [r["sum"] for r in rows] == [0.0, 8.5, 17.25, 23.0]


async def test_latest_sum_falls_back_to_the_last_row_when_older_than_three_days(hass):
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    from custom_components.tariffy import coordinator as coord

    hass.states.async_set(
        "sensor.wasser", "unavailable", {"state_class": "total_increasing", "unit_of_measurement": "m³"}
    )
    await mi.zaehlerstaende_importieren(hass, "sensor.wasser", TEXT, nur_pruefen=False)
    await async_wait_recording_done(hass)
    assert await coord._get_latest_sum(hass, "sensor.wasser") == (23.0, 95.0)


async def test_sum_restart_is_bridged_row_by_row(hass):
    """Die Summenreihe faengt mitten im Vertrag neu bei 0 an (Sensor neu angelegt): Der Verbrauch
    wird Zeile fuer Zeile zusammengesetzt statt negativ zu werden."""
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    from custom_components.tariffy import coordinator as coord

    hass.states.async_set(
        "sensor.wasser", "53", {"state_class": "total_increasing", "unit_of_measurement": "m³"}
    )
    metadata = {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": None,
        "source": "recorder",
        "statistic_id": "sensor.wasser",
        "unit_class": "volume",
        "unit_of_measurement": "m³",
    }
    zeilen = [
        (datetime(2026, 1, 1, 0), 442.0, 514.0),
        (datetime(2026, 7, 15, 0), 517.0, 589.0),
        (datetime(2026, 7, 16, 0), 517.0, 0.0),
        (datetime(2026, 11, 7, 0), 550.0, 33.0),
        (datetime(2026, 11, 18, 12), 0.0, 53.0),
        (datetime(2026, 11, 18, 13), 0.0, 53.0),
    ]
    async_import_statistics(
        hass,
        metadata,
        [
            {"start": t.replace(tzinfo=dt_util.get_default_time_zone()), "sum": s, "state": st}
            for t, s, st in zeilen
        ],
    )
    await async_wait_recording_done(hass)
    from datetime import date

    assert await coord._get_verbrauch_gestueckelt(hass, "sensor.wasser", date(2026, 1, 1), 53.0) == (128.0, 1)


async def _reihe_importieren(hass, paare, start):
    from datetime import timedelta

    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": "sensor.wasser",
            "unit_class": "volume",
            "unit_of_measurement": "m³",
        },
        [{"start": start + timedelta(hours=i), "state": st, "sum": s} for i, (st, s) in enumerate(paare)],
    )
    await async_wait_recording_done(hass)


async def _stundenzeilen(hass):
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    stats = await get_instance(hass).async_add_executor_job(
        lambda: statistics_during_period(
            hass, datetime(2026, 1, 1, tzinfo=dt_util.UTC), None, {"sensor.wasser"}, "hour", None, {"state", "sum"}
        )
    )
    return [(r["state"], r["sum"]) for r in stats["sensor.wasser"]]


async def test_ablesung_korrigieren_schreibt_staende_und_summen(hass):
    """Zahlendreher 53 statt 35: danach stehen 35 und eine durchgehende Summe in der Statistik."""
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    from custom_components.tariffy import ablesung

    hass.states.async_set(
        "sensor.wasser", "38", {"state_class": "total_increasing", "unit_of_measurement": "m³"}
    )
    hass.states.async_set("input_number.stand", "38", {"max": 99999})
    start = datetime(2026, 11, 1, 0, tzinfo=dt_util.UTC)
    await _reihe_importieren(hass, [(33, 550), (53, 570), (53, 570), (38, 608), (38, 608)], start)

    liste = await ablesung.ablesungen(hass, "sensor.wasser")
    falsch = next(a for a in liste["ablesungen"] if a["stand"] == 53)

    vorschau = await ablesung.ablesung_korrigieren(
        hass, "sensor.wasser", "input_number.stand", falsch["start"], 35, nur_pruefen=True
    )
    assert vorschau["aenderung"] == -53
    assert vorschau["aktuell"] is False
    assert await _stundenzeilen(hass) == [(33, 550), (53, 570), (53, 570), (38, 608), (38, 608)]

    gerufen = []

    async def fertig():
        gerufen.append(True)

    ergebnis = await ablesung.ablesung_korrigieren(
        hass, "sensor.wasser", "input_number.stand", falsch["start"], 35, nur_pruefen=False, fertig=fertig
    )
    assert ergebnis["korrigiert"] is True
    await async_wait_recording_done(hass)
    assert await _stundenzeilen(hass) == [(33, 550), (35, 552), (35, 552), (38, 555), (38, 555)]
    assert gerufen == [True]


async def test_aktuelle_ablesung_setzt_den_helfer_und_zieht_nach(hass):
    """Ist die falsche Ablesung die aktuelle, wird der Helfer gesetzt und das Nachrechnen geplant."""
    from unittest.mock import patch

    from homeassistant.setup import async_setup_component

    from custom_components.tariffy import ablesung

    assert await async_setup_component(
        hass, "input_number", {"input_number": {"stand": {"min": 0, "max": 99999, "step": 0.001, "mode": "box"}}}
    )
    await hass.services.async_call("input_number", "set_value", {"entity_id": "input_number.stand", "value": 53}, blocking=True)
    hass.states.async_set(
        "sensor.wasser", "53", {"state_class": "total_increasing", "unit_of_measurement": "m³"}
    )
    start = datetime(2026, 11, 1, 0, tzinfo=dt_util.UTC)
    await _reihe_importieren(hass, [(33, 550), (53, 570), (53, 570)], start)
    falsch = (start.replace(hour=1)).isoformat()

    geplant = []
    with patch.object(ablesung, "async_call_later", lambda _h, sek, aktion: geplant.append((sek, aktion))):
        ergebnis = await ablesung.ablesung_korrigieren(
            hass, "sensor.wasser", "input_number.stand", falsch, 35, nur_pruefen=False
        )
    assert ergebnis["aktuell"] is True
    assert ergebnis["nachziehen"] is True
    assert hass.states.get("input_number.stand").state == "35.0"
    assert len(geplant) == 1


async def test_digitaler_zaehler_aktueller_stand_nicht_korrigierbar(hass):
    from custom_components.tariffy import ablesung

    hass.states.async_set(
        "sensor.wasser", "53", {"state_class": "total_increasing", "unit_of_measurement": "m³"}
    )
    start = datetime(2026, 11, 1, 0, tzinfo=dt_util.UTC)
    await _reihe_importieren(hass, [(33, 550), (53, 570)], start)
    with pytest.raises(ablesung.KorrekturFehler) as err:
        await ablesung.ablesung_korrigieren(
            hass, "sensor.wasser", None, start.replace(hour=1).isoformat(), 35, nur_pruefen=True
        )
    assert err.value.code == "aktuell_ohne_helfer"
