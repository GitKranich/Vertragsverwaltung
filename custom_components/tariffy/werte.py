"""Kleine Hilfsfunktionen fuer Werte aus den Vertragsdaten: Zahlen, Datumsangaben, Monate und Einheiten.

Reine Funktionen ohne Home Assistant: sie bekommen Werte und geben Werte zurueck, damit sie sich ohne
laufendes System testen lassen. Der Coordinator (coordinator.py) ruft sie auf.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def wasser_einheit(hass: HomeAssistant, entity_id: str | None) -> str:
    state = hass.states.get(entity_id) if entity_id else None
    unit = state.attributes.get("unit_of_measurement") if state else None
    return str(unit or "").strip().lower().replace("m3", "m³")


def in_einheit(wert: float | None, von: str, nach: str) -> float | None:
    """Rechnet einen Wasserverbrauch von Liter in m³ (oder zurueck) um; sonst unveraendert."""
    if wert is None:
        return None
    if von == "l" and nach == "m³":
        return wert / 1000
    if von == "m³" and nach == "l":
        return wert * 1000
    return wert


def monate_wert(value: Any) -> int:
    """Monate aus einer optionalen Eingabe (leer, 0 oder ungueltig = 0)."""
    zahl = f(value)
    return int(zahl) if zahl is not None and zahl > 0 else 0


def minus_months(d: date, months: int) -> date:
    if not months:
        return d
    index = (d.month - 1) - months
    year = d.year + index // 12
    month = index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def plus_months(d: date, months: int) -> date:
    return minus_months(d, -months)
