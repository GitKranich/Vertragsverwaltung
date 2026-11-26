"""Sicherung und Wiederherstellung der Vertraege.

Zwei Dinge, beide nur fuer Administratoren und ohne neue Entitaeten:

* Export aller Vertraege als Datei und Wiederherstellung aus einer solchen Datei.
  Es werden nur Vertraege neu angelegt, die es noch nicht gibt (gleicher Name und
  gleiche Sparte); vorhandene werden nie veraendert.
* Beim Loeschen eines Vertrags (im Panel oder in Home Assistant) bleibt eine
  Sicherung fuer 30 Tage erhalten, damit er sich zurueckholen laesst.

Die Sicherung enthaelt alles, was zum Vertrag gehoert, auch Kundennummer,
Zaehlernummer, Marktlokation und Benachrichtigungsziel: Sie ist fuer den
Nutzer selbst gedacht. Beim Einlesen wird jede Datei streng geprueft: nur bekannte
Schluessel, nur einfache Werte, begrenzte Groesse.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import math
from typing import Any
import uuid

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import const, objekte as objekte_mod
from .const import BASIS_FELDER, CONF_OBJEKT, CONF_SPARTE, DOMAIN, NEXT_PREFIX, SPARTEN

_LOGGER = logging.getLogger(__name__)

BACKUP_FORMAT = 1
MAX_CONTRACTS = 200
MAX_DEPTH = 3
MAX_ITEMS = 200
MAX_TEXT = 2000
STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}.deleted"
DELETED_MAX = 20
DELETED_DAYS = 30

# Schluessel, die in den Vertragsdaten vorkommen duerfen: alle Einstellungen und der
# interne Zustand der Integration (Konstanten CONF_*) und die des Folgevertrags.
ALLOWED_DATA = frozenset(
    value for name, value in vars(const).items() if name.startswith("CONF_") and isinstance(value, str)
)
ALLOWED_OPTIONS = frozenset(NEXT_PREFIX + field for field in BASIS_FELDER)


class BackupError(ValueError):
    """Sicherung unbrauchbar; `code` steht fuer die Meldung des Panels."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _clean(value: Any, depth: int = 0) -> Any:
    """Nur einfache JSON-Werte, begrenzt in Tiefe und Groesse."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BackupError("invalid_backup")
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise BackupError("invalid_backup")
        return value
    if depth >= MAX_DEPTH:
        raise BackupError("invalid_backup")
    if isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise BackupError("invalid_backup")
        return [_clean(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise BackupError("invalid_backup")
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 100:
                raise BackupError("invalid_backup")
            result[key] = _clean(item, depth + 1)
        return result
    raise BackupError("invalid_backup")


def _keep(raw: Any, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BackupError("invalid_backup")
    return {key: _clean(value) for key, value in raw.items() if key in allowed}


def sanitize_contract(raw: Any) -> dict[str, Any]:
    """Ein Vertrag aus einer Sicherung: Name, Daten und Folgevertrag, geprueft."""
    if not isinstance(raw, dict):
        raise BackupError("invalid_backup")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise BackupError("invalid_backup")
    data = _keep(raw.get("data"), ALLOWED_DATA)
    if data.get(CONF_SPARTE) not in SPARTEN:
        raise BackupError("invalid_backup")
    return {
        "title": title.strip(),
        "data": data,
        "options": _keep(raw.get("options") or {}, ALLOWED_OPTIONS),
    }


def sanitize_backup(raw: Any) -> list[dict[str, Any]]:
    """Alle Vertraege einer Sicherung, geprueft. Wirft BackupError."""
    if not isinstance(raw, dict) or raw.get("tariffy_backup") != BACKUP_FORMAT:
        raise BackupError("invalid_backup")
    contracts = raw.get("contracts")
    if not isinstance(contracts, list) or len(contracts) > MAX_CONTRACTS:
        raise BackupError("invalid_backup")
    return [sanitize_contract(contract) for contract in contracts]


def sanitize_objekte(raw: Any) -> list[dict[str, Any]]:
    """Die Objekte einer Sicherung (Id, Name, Icon); unbrauchbare werden uebersprungen.

    Aeltere Sicherungen haben keine Objekte: dann ist die Liste leer und alles kommt ins Default-Objekt."""
    items = raw.get("objekte") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    result = []
    for item in items[: objekte_mod.MAX_OBJEKTE]:
        if not isinstance(item, dict):
            continue
        objekt_id, name, icon = item.get("id"), item.get("name"), item.get("icon")
        if not isinstance(objekt_id, str) or not objekt_id or len(objekt_id) > 64:
            continue
        if not isinstance(name, str) or not name.strip() or len(name) > objekte_mod.MAX_NAME:
            continue
        result.append({"id": objekt_id, "name": name.strip(), "icon": icon if isinstance(icon, str) and len(icon) <= 64 else None})
    return result


def _objekt_hier(hass: HomeAssistant, objekt_id: Any, backup_objekte: list[dict[str, Any]]) -> str | None:
    """In welchem Objekt hier ein Vertrag der Sicherung laege. None = es gibt noch keine Objekte (nur Name und Sparte
    zaehlen), "" = ein Objekt, das erst angelegt wird (dort gibt es noch keinen Vertrag)."""
    objekte = objekte_mod.cached(hass)
    if objekte is None:
        return None
    if not isinstance(objekt_id, str) or not objekt_id:
        return objekte.default
    if objekte.get(objekt_id):
        return objekt_id
    name = next((o["name"] for o in backup_objekte if o["id"] == objekt_id), None)
    if name is None:
        return objekte.default  # Objekt fehlt auch in der Sicherung: der Vertrag kommt ins Default-Objekt
    gleich = next((o for o in objekte.items if o["name"].casefold() == name.casefold()), None)
    return gleich["id"] if gleich else ""


def _contract_of(entry: ConfigEntry) -> dict[str, Any]:
    return {"title": entry.title, "data": dict(entry.data), "options": dict(entry.options)}


async def async_build_backup(hass: HomeAssistant) -> dict[str, Any]:
    """Alle Vertraege als Sicherung (auch nicht geladene)."""
    version = (await async_get_integration(hass, DOMAIN)).version
    return {
        "tariffy_backup": BACKUP_FORMAT,
        "created": dt_util.now().isoformat(timespec="seconds"),
        "version": str(version),
        "contracts": [_contract_of(entry) for entry in hass.config_entries.async_entries(DOMAIN)],
        **(objekte.payload() if (objekte := objekte_mod.cached(hass)) is not None else {}),
    }


def _exists(hass: HomeAssistant, title: str, sparte: str | None, objekt: str | None = None) -> bool:
    """Gibt es den Vertrag schon (gleicher Name und gleiche Sparte, mit Objekten auch im gleichen Objekt)?"""
    return any(
        entry.title == title
        and entry.data.get(CONF_SPARTE) == sparte
        and (objekt is None or objekte_mod.objekt_von(entry) == objekt)
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


def plan(
    hass: HomeAssistant, contracts: list[dict[str, Any]], backup_objekte: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Was beim Wiederherstellen passiert: anlegen oder ueberspringen."""
    backup_objekte = backup_objekte or []
    namen = {o["id"]: o["name"] for o in backup_objekte}
    objekte = objekte_mod.cached(hass)
    mehrere = len(backup_objekte) > 1 or (objekte is not None and len(objekte.items) > 1)
    seen: set[tuple[str, str | None, str | None]] = set()
    result = []
    for contract in contracts:
        sparte = contract["data"].get(CONF_SPARTE)
        backup_objekt = contract["data"].get(CONF_OBJEKT)
        objekt = _objekt_hier(hass, backup_objekt, backup_objekte)
        key = (contract["title"], sparte, objekt if objekt else namen.get(backup_objekt))
        skip = key in seen or (objekt != "" and _exists(hass, contract["title"], sparte, objekt))
        seen.add(key)
        step = {"title": contract["title"], "sparte": sparte, "action": "skip" if skip else "create"}
        if mehrere:  # mit mehreren Objekten zeigt die Vorschau, wohin der Vertrag kommt
            step["objekt"] = namen.get(backup_objekt) or (objekte.name(objekt) if objekte and objekt else None)
        result.append(step)
    return result


async def _create(hass: HomeAssistant, contract: dict[str, Any]) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=contract
    )
    if result["type"] is not FlowResultType.CREATE_ENTRY:
        raise BackupError("restore_failed")


async def async_restore(hass: HomeAssistant, raw: Any) -> dict[str, list[str]]:
    """Legt die fehlenden Vertraege einer Sicherung an. Vorhandene bleiben unberuehrt."""
    contracts = sanitize_backup(raw)
    backup_objekte = sanitize_objekte(raw)
    steps = plan(hass, contracts, backup_objekte)
    # erst die fehlenden Objekte anlegen, dann die Vertraege dort hinein
    mapping = await objekte_mod.async_restore(hass, backup_objekte)
    created: list[str] = []
    skipped: list[str] = []
    for contract, step in zip(contracts, steps, strict=True):
        if step["action"] == "skip":
            skipped.append(contract["title"])
            continue
        backup_objekt = contract["data"].get(CONF_OBJEKT)
        if backup_objekt in mapping:
            contract = {**contract, "data": {**contract["data"], CONF_OBJEKT: mapping[backup_objekt]}}
        await _create(hass, contract)
        created.append(contract["title"])
    return {"created": created, "skipped": skipped}


# ------------------------------------------------------- geloeschte Vertraege
def _store(hass: HomeAssistant) -> Store:
    return Store(hass, STORE_VERSION, STORE_KEY)


async def _load_deleted(hass: HomeAssistant) -> list[dict[str, Any]]:
    stored = await _store(hass).async_load()
    items = list((stored or {}).get("contracts") or [])
    cutoff = (dt_util.now() - timedelta(days=DELETED_DAYS)).date().isoformat()
    return [item for item in items if str(item.get("deleted", "")) >= cutoff][-DELETED_MAX:]


async def async_remember_deleted(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Merkt sich einen geloeschten Vertrag fuer 30 Tage. Wirft nie."""
    try:
        contract = sanitize_contract(_contract_of(entry))
        items = await _load_deleted(hass)
        items.append(
            {
                "id": uuid.uuid4().hex,
                "deleted": dt_util.now().date().isoformat(),
                **contract,
            }
        )
        await _store(hass).async_save({"contracts": items[-DELETED_MAX:]})
    except Exception as err:  # noqa: BLE001 - das Loeschen darf daran nie scheitern
        _LOGGER.warning("Tariffy: geloeschten Vertrag nicht gesichert (%s)", err)


async def async_deleted(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Die zurueckholbaren Vertraege, neueste zuerst (ohne die Daten selbst)."""
    items = await _load_deleted(hass)
    return [
        {
            "id": item["id"],
            "title": item["title"],
            "sparte": item["data"].get(CONF_SPARTE),
            "deleted": item["deleted"],
        }
        for item in reversed(items)
    ]


async def async_discard_deleted(hass: HomeAssistant, item_id: str) -> None:
    """Vergisst einen geloeschten Vertrag sofort (statt nach 30 Tagen)."""
    items = await _load_deleted(hass)
    if not any(entry.get("id") == item_id for entry in items):
        raise BackupError("not_found")
    await _store(hass).async_save({"contracts": [entry for entry in items if entry.get("id") != item_id]})


async def async_restore_deleted(hass: HomeAssistant, item_id: str) -> str:
    """Holt einen geloeschten Vertrag zurueck und gibt seinen Namen zurueck."""
    items = await _load_deleted(hass)
    item = next((entry for entry in items if entry.get("id") == item_id), None)
    if item is None:
        raise BackupError("not_found")
    contract = sanitize_contract(item)
    objekt = _objekt_hier(hass, contract["data"].get(CONF_OBJEKT), [])
    if _exists(hass, contract["title"], contract["data"].get(CONF_SPARTE), objekt):
        raise BackupError("exists")
    await _create(hass, contract)
    await _store(hass).async_save({"contracts": [entry for entry in items if entry is not item]})
    return contract["title"]
