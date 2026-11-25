"""Ablesung korrigieren: Planung der neuen Summen (ohne Recorder)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.tariffy.ablesung import (
    KorrekturFehler,
    ablesungen_aus_zeilen,
    korrektur_planen,
)

T0 = datetime(2026, 11, 1, 0, tzinfo=dt_util.UTC)


def _reihe(*paare):
    """Stundenzeilen ab T0 aus (stand, summe)."""
    return [{"start": T0 + timedelta(hours=i), "state": st, "sum": s} for i, (st, s) in enumerate(paare)]


# Der Fall aus der Praxis: 33 abgelesen, dann 53 statt 35 (Zahlendreher), dann 38. HA hat 53 -> 38
# als Zaehlerwechsel gezaehlt (+38).
ZAHLENDREHER = _reihe((33, 550), (53, 570), (53, 570), (38, 608), (38, 608))


def _summen(zeilen, plan):
    """Die Summen nach Anwenden der Stufen."""
    ergebnis = []
    for z in zeilen:
        ergebnis.append(z["sum"] + sum(b for ab, b in plan["stufen"] if ab <= z["start"]))
    return ergebnis


def test_zahlendreher_wird_ohne_zaehlerwechsel_korrigiert():
    plan = korrektur_planen(ZAHLENDREHER, T0 + timedelta(hours=1), 35)
    assert _summen(ZAHLENDREHER, plan) == [550, 552, 552, 555, 555]
    assert [(z["state"], z["sum"]) for z in plan["zeilen"]] == [(35, 552), (35, 552)]
    assert plan["aenderung"] == -53
    assert plan["alt"] == 53
    assert not plan["bis_ende"]


def test_echter_zaehlerwechsel_bleibt_erhalten():
    # 589 -> 0 ist ein echter Wechsel; korrigiert wird spaeter 23 -> 25
    zeilen = _reihe((589, 517), (0, 517), (23, 540), (33, 550))
    plan = korrektur_planen(zeilen, T0 + timedelta(hours=2), 25)
    assert _summen(zeilen, plan) == [517, 517, 542, 550]


def test_korrektur_einer_wechsel_ablesung_bleibt_wechsel():
    # der erste Stand des neuen Zaehlers war 2 statt 0
    zeilen = _reihe((589, 517), (2, 519), (23, 540))
    plan = korrektur_planen(zeilen, T0 + timedelta(hours=1), 0)
    assert _summen(zeilen, plan) == [517, 517, 540]


def test_kleiner_als_vorher_wird_abgelehnt():
    with pytest.raises(KorrekturFehler) as err:
        korrektur_planen(ZAHLENDREHER, T0 + timedelta(hours=1), 30)
    assert err.value.code == "kleiner_als_vorher"


def test_groesser_als_naechste_wird_abgelehnt():
    zeilen = _reihe((33, 550), (35, 552), (38, 555))
    with pytest.raises(KorrekturFehler) as err:
        korrektur_planen(zeilen, T0 + timedelta(hours=1), 40)
    assert err.value.code == "groesser_als_naechste"


def test_nur_eine_ablesung_kann_gewaehlt_werden():
    with pytest.raises(KorrekturFehler) as err:
        korrektur_planen(ZAHLENDREHER, T0 + timedelta(hours=2), 35)  # Folgezeile von 53
    assert err.value.code == "ablesung_fehlt"
    with pytest.raises(KorrekturFehler) as err:
        korrektur_planen(ZAHLENDREHER, T0 + timedelta(hours=1), 53)
    assert err.value.code == "gleich"


def test_aktuelle_ablesung_nach_dem_setzen_des_helfers():
    # Helfer von 53 auf 35 gesetzt: HA hat einen Wechsel gezaehlt (+35)
    zeilen = _reihe((33, 550), (53, 570), (53, 570), (35, 605))
    plan = korrektur_planen(zeilen, T0 + timedelta(hours=1), 35, nachher_gleich_neu=True)
    assert _summen(zeilen, plan) == [550, 552, 552, 552]
    assert plan["bis_ende"]


def test_erste_zeile_behaelt_den_nullpunkt():
    zeilen = _reihe((53, 0), (60, 7))
    plan = korrektur_planen(zeilen, T0, 50)
    assert _summen(zeilen, plan) == [0, 10]


def test_ablesungen_neueste_zuerst():
    liste = ablesungen_aus_zeilen(ZAHLENDREHER)
    assert [(a["stand"], a["wechsel"]) for a in liste] == [(38, True), (53, False), (33, False)]
    assert liste[0]["start"] == (T0 + timedelta(hours=3)).isoformat()


async def test_nachziehen_rechnet_ab_dem_setzen_des_helfers(hass):
    """Nachdem HA den neuen Helferwert als Zaehlerwechsel erfasst hat, werden die Summen so
    verschoben, dass der Verbrauch durchlaeuft; die Verschiebung fuer den Wechsel gilt ab dem
    5-Minuten-Raster des Setzens."""
    from unittest.mock import AsyncMock, patch

    from custom_components.tariffy import ablesung

    gesetzt = T0 + timedelta(hours=3, minutes=7)
    zeilen = _reihe((33, 550), (53, 570), (53, 570))
    zeilen.append({"start": T0 + timedelta(hours=3, minutes=10), "state": 35.0, "sum": 605.0, "kurzzeit": True})
    geplant = []
    anwenden = AsyncMock()
    with (
        patch.object(ablesung, "async_call_later", lambda _h, _s, aktion: geplant.append(aktion)),
        patch.object(ablesung, "_zeilen", AsyncMock(return_value=zeilen)),
        patch.object(ablesung, "_anwenden", anwenden),
    ):
        ablesung._nachziehen_planen(hass, "sensor.wasser", "m³", T0 + timedelta(hours=1), 53, 35, gesetzt, None)
        await geplant[0](None)
    plan = anwenden.await_args.args[3]
    assert plan["stufen"] == [(T0 + timedelta(hours=1), -18.0), (T0 + timedelta(hours=3, minutes=5), -35.0)]
    assert [z["kurzzeit"] for z in plan["zeilen"]] == [False, False, True]


async def test_nachziehen_wartet_bis_der_wert_erfasst_ist(hass):
    from unittest.mock import AsyncMock, patch

    from custom_components.tariffy import ablesung

    geplant = []
    anwenden = AsyncMock()
    with (
        patch.object(ablesung, "async_call_later", lambda _h, _s, aktion: geplant.append(aktion)),
        patch.object(ablesung, "_zeilen", AsyncMock(return_value=_reihe((33, 550), (53, 570)))),
        patch.object(ablesung, "_anwenden", anwenden),
    ):
        ablesung._nachziehen_planen(hass, "sensor.wasser", "m³", T0 + timedelta(hours=1), 53, 35, T0 + timedelta(hours=2), None)
        await geplant[0](None)
    assert len(geplant) == 2  # erneut geplant
    anwenden.assert_not_awaited()
