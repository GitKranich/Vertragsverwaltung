"""Objekte: die uebergeordnete Zuordnung der Vertraege (Haus, Wohnung, Ferienhaus, ...).

Jeder Vertrag gehoert zu genau einem Objekt (`objekt_id` in den Vertragsdaten). Die Objekte selbst stehen in einem
eigenen Speicher (`.storage/tariffy.objekte`) zusammen mit dem Default-Objekt, das das Panel beim Oeffnen zeigt.

* Gibt es noch kein Objekt, legt der erste Zugriff eines an ("Zuhause"/"Home") und macht es zum Default.
* Ein Vertrag ohne (oder mit unbekanntem) Objekt wird beim Einrichten fest dem Default-Objekt zugeordnet. Bewusst
  kein stiller Rueckfall: sonst wanderten diese Vertraege mit, wenn spaeter ein anderes Objekt Default wird.
* Ein Objekt laesst sich nur loeschen, wenn keine Vertraege mehr dazugehoeren oder ein Ziel fuer sie genannt ist;
  das letzte Objekt bleibt immer bestehen.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import CONF_OBJEKT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}.objekte"
DATA_KEY = f"{DOMAIN}_objekte"
LOCK_KEY = f"{DOMAIN}_objekte_lock"
MAX_OBJEKTE = 50
MAX_NAME = 80
DEFAULT_ICON = "mdi:home"


class ObjektError(ValueError):
    """Objekt-Aenderung nicht moeglich; `code` steht fuer die Meldung des Panels."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _default_name(hass: HomeAssistant) -> str:
    return "Zuhause" if str(hass.config.language or "").lower().startswith("de") else "Home"


def _clean_name(name: Any) -> str:
    text = str(name or "").strip()
    if not text or len(text) > MAX_NAME:
        raise ObjektError("invalid_name")
    return text


def _clean_icon(icon: Any) -> str | None:
    text = str(icon or "").strip()
    if not text:
        return None
    if len(text) > 64 or ":" not in text:
        raise ObjektError("invalid_icon")
    return text


class Objekte:
    """Die Objekte und das Default-Objekt (im Speicher gehalten, bei jeder Aenderung gesichert)."""

    def __init__(self, hass: HomeAssistant, store: Store, items: list[dict[str, Any]], default: str) -> None:
        self.hass = hass
        self._store = store
        self.items = items
        self.default = default
        # Objekt, das im Panel zuletzt gewaehlt war: Vorauswahl beim Anlegen eines Vertrags (nur im Speicher)
        self.aktiv: str | None = None

    # ------------------------------------------------------------- lesen
    def get(self, objekt_id: Any) -> dict[str, Any] | None:
        return next((item for item in self.items if item["id"] == objekt_id), None)

    def gueltig(self, objekt_id: Any) -> str:
        """Die Id selbst, wenn es das Objekt gibt, sonst die des Default-Objekts."""
        return objekt_id if self.get(objekt_id) else self.default

    def name(self, objekt_id: Any) -> str | None:
        item = self.get(objekt_id)
        return item["name"] if item else None

    @property
    def vorauswahl(self) -> str:
        """Objekt fuer einen neuen Vertrag: das im Panel gewaehlte, sonst das Default-Objekt."""
        return self.gueltig(self.aktiv)

    def payload(self) -> dict[str, Any]:
        return {
            "objekte": [dict(item) for item in self.items],
            "default_objekt": self.default,
        }

    # ------------------------------------------------------------ aendern
    async def _save(self) -> None:
        await self._store.async_save({"objekte": self.items, "default": self.default})

    async def async_save_objekt(self, objekt_id: str | None, name: Any, icon: Any = None) -> str:
        """Legt ein Objekt an (ohne Id) oder aendert Name und Icon eines vorhandenen."""
        name = _clean_name(name)
        icon = _clean_icon(icon)
        if any(item["name"].casefold() == name.casefold() and item["id"] != objekt_id for item in self.items):
            raise ObjektError("name_exists")
        if objekt_id is None:
            if len(self.items) >= MAX_OBJEKTE:
                raise ObjektError("too_many")
            objekt_id = uuid.uuid4().hex
            self.items.append({"id": objekt_id, "name": name, "icon": icon})
        else:
            item = self.get(objekt_id)
            if item is None:
                raise ObjektError("not_found")
            item["name"] = name
            item["icon"] = icon
        await self._save()
        return objekt_id

    async def async_set_default(self, objekt_id: str) -> None:
        if self.get(objekt_id) is None:
            raise ObjektError("not_found")
        self.default = objekt_id
        await self._save()

    async def async_delete(self, objekt_id: str, ziel: str | None = None) -> int:
        """Loescht ein Objekt; seine Vertraege ziehen nach `ziel` um. Gibt die Zahl der verschobenen Vertraege zurueck."""
        if self.get(objekt_id) is None:
            raise ObjektError("not_found")
        if len(self.items) <= 1:
            raise ObjektError("last_objekt")
        betroffen = vertraege_von(self.hass, objekt_id)
        if betroffen:
            if ziel is None:
                raise ObjektError("has_contracts")
            if ziel == objekt_id or self.get(ziel) is None:
                raise ObjektError("invalid_target")
        for entry in betroffen:
            zuordnen(self.hass, entry, ziel)
        self.items = [item for item in self.items if item["id"] != objekt_id]
        if self.default == objekt_id:
            self.default = ziel if ziel and self.get(ziel) else self.items[0]["id"]
        if self.aktiv == objekt_id:
            self.aktiv = None
        await self._save()
        return len(betroffen)


# ------------------------------------------------------------ Vertraege
def objekt_von(entry: ConfigEntry) -> str | None:
    value = entry.data.get(CONF_OBJEKT)
    return value if isinstance(value, str) and value else None


def vertraege_von(hass: HomeAssistant, objekt_id: str) -> list[ConfigEntry]:
    return [entry for entry in hass.config_entries.async_entries(DOMAIN) if objekt_von(entry) == objekt_id]


@callback
def zuordnen(hass: HomeAssistant, entry: ConfigEntry, objekt_id: str) -> None:
    """Ordnet einen Vertrag einem Objekt zu (ohne Neuladen: das Objekt aendert keine Berechnung)."""
    if objekt_von(entry) == objekt_id:
        return
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_OBJEKT: objekt_id})


@callback
def async_refresh_entities(hass: HomeAssistant) -> None:
    """Die Sensoren zeigen den Objektnamen als Attribut: nach Umbenennen/Umziehen neu schreiben."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if entry.state is ConfigEntryState.LOADED and coordinator is not None:
            coordinator.async_update_listeners()


# ------------------------------------------------------------ Zugriff
@callback
def cached(hass: HomeAssistant) -> Objekte | None:
    """Die Objekte, wenn sie schon geladen sind (fuer synchronen Code)."""
    return hass.data.get(DATA_KEY)


async def async_get(hass: HomeAssistant) -> Objekte:
    """Laedt die Objekte einmal; gibt es keine, wird das erste angelegt und Default."""
    if (objekte := hass.data.get(DATA_KEY)) is not None:
        return objekte
    lock = hass.data.setdefault(LOCK_KEY, asyncio.Lock())
    async with lock:
        if (objekte := hass.data.get(DATA_KEY)) is not None:
            return objekte
        store: Store = Store(hass, STORE_VERSION, STORE_KEY)
        stored = await store.async_load() or {}
        items: list[dict[str, Any]] = []
        for raw in stored.get("objekte") or []:
            try:
                if isinstance(raw, dict) and isinstance(raw.get("id"), str) and raw["id"]:
                    items.append({"id": raw["id"], "name": _clean_name(raw.get("name")), "icon": _clean_icon(raw.get("icon"))})
            except ObjektError:
                _LOGGER.warning("Tariffy: unbrauchbares Objekt im Speicher uebersprungen")
        default = stored.get("default")
        neu = False
        if not items:
            items = [{"id": uuid.uuid4().hex, "name": _default_name(hass), "icon": DEFAULT_ICON}]
            neu = True
        if not any(item["id"] == default for item in items):
            default = items[0]["id"]
            neu = True
        objekte = Objekte(hass, store, items, default)
        if neu:
            await objekte._save()  # noqa: SLF001
        hass.data[DATA_KEY] = objekte
        return objekte


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> Objekte:
    """Beim Einrichten eines Vertrags: Objekte laden und den Vertrag fest einem Objekt zuordnen."""
    objekte = await async_get(hass)
    objekt_id = objekt_von(entry)
    if objekte.get(objekt_id) is None:
        if objekt_id is not None:
            _LOGGER.info("Tariffy '%s': Objekt %s gibt es nicht mehr, jetzt im Default-Objekt", entry.title, objekt_id)
        zuordnen(hass, entry, objekte.default)
    return objekte


async def async_restore(hass: HomeAssistant, raw: Any) -> dict[str, str]:
    """Objekte aus einer Sicherung: fehlende anlegen (gleiche Id oder gleicher Name gilt als vorhanden).

    Gibt die Zuordnung Id in der Sicherung -> Id hier zurueck, damit die Vertraege im richtigen Objekt landen.
    """
    objekte = await async_get(hass)
    mapping: dict[str, str] = {}
    if not isinstance(raw, list):
        return mapping
    geaendert = False
    for item in raw[:MAX_OBJEKTE]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        try:
            name = _clean_name(item.get("name"))
            icon = _clean_icon(item.get("icon"))
        except ObjektError:
            continue
        if objekte.get(item["id"]):
            mapping[item["id"]] = item["id"]
            continue
        gleich = next((o for o in objekte.items if o["name"].casefold() == name.casefold()), None)
        if gleich:
            mapping[item["id"]] = gleich["id"]
            continue
        if len(objekte.items) >= MAX_OBJEKTE:
            continue
        objekte.items.append({"id": item["id"], "name": name, "icon": icon})
        mapping[item["id"]] = item["id"]
        geaendert = True
    if geaendert:
        await objekte._save()  # noqa: SLF001
    return mapping
