"""Import historischer Zaehlerstaende in die Langzeitstatistik."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.tariffy import meter_import as mi

TEXT = """Datum;Zählerstand
01.01.2026;72
01.02.2026;80,5
2026-03-01;89.250
01.04.2026 07:30;95
kaputt;1
"""


def test_reads_german_and_iso_dates_and_numbers():
    werte, fehler = mi.zeilen_lesen(TEXT)
    assert [w for _, w in werte] == [72.0, 80.5, 89.25, 95.0]
    assert fehler == [1, 6]  # Kopfzeile und kaputte Zeile
    zeit, _ = werte[0]
    assert (zeit.hour, zeit.minute) == (12, 0)  # ohne Uhrzeit: Mittag
    assert werte[3][0].hour == 7
    assert mi._zahl("1.234,5") == 1234.5
    assert mi.zeilen_lesen("01.03.2026,512,3")[0][0][1] == 512.3


def test_rows_accumulate_and_handle_a_meter_change():
    tz = dt_util.get_default_time_zone()
    werte = [
        (datetime(2026, 1, 1, 12, tzinfo=tz), 100.0),
        (datetime(2026, 2, 1, 12, tzinfo=tz), 110.0),
        (datetime(2026, 3, 1, 12, tzinfo=tz), 3.0),  # neuer Zaehler
        (datetime(2026, 4, 1, 12, tzinfo=tz), 9.0),
    ]
    zeilen, wechsel = mi.statistik_zeilen(werte, None)
    assert [z["sum"] for z in zeilen] == [0.0, 10.0, 13.0, 19.0]
    assert wechsel == 1
    assert zeilen[0]["start"].minute == 0


def test_sums_are_shifted_to_join_the_existing_statistics():
    tz = dt_util.get_default_time_zone()
    werte = [(datetime(2026, 1, 1, 12, tzinfo=tz), 100.0), (datetime(2026, 2, 1, 12, tzinfo=tz), 110.0)]
    # vorhandene Statistik beginnt mit Stand 115 und Summe 0
    zeilen, _ = mi.statistik_zeilen(werte, (115.0, 0.0))
    assert [z["sum"] for z in zeilen] == [-15.0, -5.0]  # 110 -> 115 = +5 bis zur Summe 0


async def test_import_checks_the_sensor_and_reports(hass):
    hass.states.async_set("sensor.wasser", "unavailable", {"state_class": "measurement"})
    with pytest.raises(mi.ImportFehler) as err:
        await mi.zaehlerstaende_importieren(hass, "sensor.wasser", TEXT, nur_pruefen=True)
    assert err.value.code == "sensor_ohne_summe"

    hass.states.async_set(
        "sensor.wasser", "unavailable", {"state_class": "total_increasing", "unit_of_measurement": "m³"}
    )

    async def _keine(_hass, _sensor):
        return None

    mi_old = mi._aelteste_zeile
    mi._aelteste_zeile = _keine
    try:
        bericht = await mi.zaehlerstaende_importieren(hass, "sensor.wasser", TEXT, nur_pruefen=True)
    finally:
        mi._aelteste_zeile = mi_old
    assert bericht["anzahl"] == 4
    assert bericht["verbrauch"] == 23.0
    assert bericht["nicht_gelesen_anzahl"] == 2
    assert bericht["importiert"] is False


async def test_only_rows_before_the_existing_statistics_are_imported(hass):
    hass.states.async_set(
        "sensor.wasser", "100", {"state_class": "total_increasing", "unit_of_measurement": "m³"}
    )
    tz = dt_util.get_default_time_zone()
    grenze = datetime(2026, 3, 15, tzinfo=tz)

    async def _vorhanden(_hass, _sensor):
        return {"start": grenze.timestamp(), "state": 92.0, "sum": 0.0}

    mi_old = mi._aelteste_zeile
    mi._aelteste_zeile = _vorhanden
    try:
        bericht = await mi.zaehlerstaende_importieren(hass, "sensor.wasser", TEXT, nur_pruefen=True)
        assert bericht["anzahl"] == 3 and bericht["uebersprungen"] == 1  # der 01.04. liegt danach
        assert bericht["anschluss"] is True
        with pytest.raises(mi.ImportFehler) as err:
            await mi.zaehlerstaende_importieren(hass, "sensor.wasser", "01.04.2026;95", nur_pruefen=True)
        assert err.value.code == "nur_ueberschneidung"
    finally:
        mi._aelteste_zeile = mi_old
    assert grenze - timedelta(days=1) < grenze

