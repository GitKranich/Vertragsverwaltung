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
