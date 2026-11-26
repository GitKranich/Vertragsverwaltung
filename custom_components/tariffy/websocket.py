"""WebSocket commands of the Tariffy panel.

Everything the panel asks for or changes goes through these commands (`tariffy/...`). The panel itself (sidebar entry,
static files, the data of a contract it shows) lives in panel.py; this module only answers the panel. Commands that
change something require an administrator.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import ablesung, backup, dokumente, history, meter_import, objekte
from .const import CONF_VERBRAUCH_SENSOR, CONF_ZAEHLERSTAND_EINGABE, DOMAIN
from .panel import SIGNAL_CONTRACTS, async_notify, async_snapshot

_LOGGER = logging.getLogger(__name__)

# Actions of the panel. Same coordinator methods and same preconditions as the
# button entities (see button.py), so the panel can never do more than the
# buttons can. "Switch now" is deliberately not offered: the next contract
# switches in by itself on its start date.
ACTION_CONFIRM_CANCELLATION = "confirm_cancellation"
ACTION_CONFIRM_WARNING = "confirm_abschlag_warning"
# Archive an expired contract / bring it back. Panel only: it stores a flag in the
# contract data and changes no entity.
ACTION_ARCHIVE = "archive_contract"
ACTION_RESTORE = "restore_contract"
ACTIONS = (
    ACTION_CONFIRM_CANCELLATION,
    ACTION_CONFIRM_WARNING,
    ACTION_ARCHIVE,
    ACTION_RESTORE,
)
@websocket_api.websocket_command({vol.Required("type"): "tariffy/contracts"})
@callback
def ws_contracts(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Return all contracts."""
    connection.send_result(msg["id"], async_snapshot(hass))


@websocket_api.websocket_command({vol.Required("type"): "tariffy/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Send all contracts now and again after every change."""

    @callback
    def _push() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], async_snapshot(hass))
        )

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, SIGNAL_CONTRACTS, _push
    )
    connection.send_result(msg["id"])
    _push()


def _action_available(action: str, data: dict[str, Any]) -> bool:
    """Whether an action is currently possible, like the button's `available`."""
    if action == ACTION_CONFIRM_CANCELLATION:
        return bool(data.get("erinnerung_aktiv"))
    if action == ACTION_CONFIRM_WARNING:
        return bool(data.get("abschlag_warnung_zu_bestaetigen"))
    if action == ACTION_ARCHIVE:
        return bool(data.get("archivierbar"))
    if action == ACTION_RESTORE:
        return bool(data.get("archiviert"))
    return False


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/action",
        vol.Required("entry_id"): str,
        vol.Required("action"): vol.In(ACTIONS),
    }
)
@websocket_api.async_response
async def ws_action(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Run one of the contract actions (administrators only)."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
        or getattr(entry, "runtime_data", None) is None
    ):
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return

    coordinator = entry.runtime_data
    action = msg["action"]
    if not _action_available(action, coordinator.data or {}):
        connection.send_error(
            msg["id"], "not_available", f"Action {action} is not available right now"
        )
        return

    if action == ACTION_CONFIRM_CANCELLATION:
        await coordinator.async_confirm_kuendigung()
    elif action == ACTION_CONFIRM_WARNING:
        await coordinator.async_confirm_abschlag_warnung()
    elif action == ACTION_ARCHIVE:
        await coordinator.async_archive()
    else:
        await coordinator.async_restore()
    connection.send_result(msg["id"], {"ok": True})


def _loaded_entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
        or getattr(entry, "runtime_data", None) is None
    ):
        return None
    return entry


HISTORY_VALUES = vol.Schema({str: vol.Any(None, str, int, float)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/history_calc",
        vol.Required("entry_id"): str,
        vol.Required("values"): HISTORY_VALUES,
    }
)
@websocket_api.async_response
async def ws_history_calc(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Berechnet die automatisch ermittelbaren Werte einer alten Laufzeit (nur Vorschau)."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        result = await history.async_calc(hass, entry, msg["values"])
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/history_save",
        vol.Required("entry_id"): str,
        vol.Required("values"): HISTORY_VALUES,
        vol.Optional("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_history_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Legt eine alte Laufzeit an oder ersetzt eine vorhandene."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        index = await history.async_save_term(hass, entry, msg["values"], msg.get("index"))
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"index": index})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/renew",
        vol.Required("entry_id"): str,
        vol.Required("values"): HISTORY_VALUES,
    }
)
@websocket_api.async_response
async def ws_renew(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Verlaengert einen Pauschalvertrag mit neuem Betrag und Ende."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        await history.async_renew(hass, entry, msg["values"])
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/history_delete",
        vol.Required("entry_id"): str,
        vol.Required("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_history_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Loescht eine Laufzeit aus der Historie."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        history.delete_term(hass, entry, msg["index"])
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/einmal_save",
        vol.Required("entry_id"): str,
        vol.Required("values"): HISTORY_VALUES,
        vol.Optional("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_einmal_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Legt einen Einmalposten (Bonus oder Rechnung) an oder ersetzt ihn."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        history.save_einmal(hass, entry, msg["values"], msg.get("index"))
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/einmal_delete",
        vol.Required("entry_id"): str,
        vol.Required("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_einmal_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Loescht einen Einmalposten."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        history.delete_einmal(hass, entry, msg["index"])
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/zaehler_import",
        vol.Required("entry_id"): str,
        vol.Required("text"): vol.All(str, vol.Length(max=400_000)),
        vol.Optional("pruefen", default=True): bool,
    }
)
@websocket_api.async_response
async def ws_zaehler_import(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Prueft oder importiert historische Zaehlerstaende in die Statistik des Verbrauchssensors."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    sensor = entry.data.get(CONF_VERBRAUCH_SENSOR)
    if not sensor:
        connection.send_error(msg["id"], "kein_sensor", "kein_sensor")
        return
    try:
        ergebnis = await meter_import.zaehlerstaende_importieren(
            hass, sensor, msg["text"], nur_pruefen=msg["pruefen"]
        )
    except meter_import.ImportFehler as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    connection.send_result(msg["id"], ergebnis)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/ablesungen",
        vol.Required("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_ablesungen(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Die Ablesungen aus der Statistik des Verbrauchssensors, neueste zuerst."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    sensor = entry.data.get(CONF_VERBRAUCH_SENSOR)
    if not sensor:
        connection.send_error(msg["id"], "kein_sensor", "kein_sensor")
        return
    try:
        ergebnis = await ablesung.ablesungen(hass, sensor)
    except ablesung.KorrekturFehler as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    connection.send_result(msg["id"], ergebnis)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/ablesung_korrigieren",
        vol.Required("entry_id"): str,
        vol.Required("start"): str,
        vol.Required("stand"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("pruefen", default=True): bool,
    }
)
@websocket_api.async_response
async def ws_ablesung_korrigieren(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Prueft oder korrigiert eine falsch erfasste Ablesung, ohne dass ein Zaehlerwechsel entsteht."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    sensor = entry.data.get(CONF_VERBRAUCH_SENSOR)
    if not sensor:
        connection.send_error(msg["id"], "kein_sensor", "kein_sensor")
        return

    async def _neu_rechnen() -> None:
        if entry.state is ConfigEntryState.LOADED:
            await entry.runtime_data.async_refresh()

    try:
        ergebnis = await ablesung.ablesung_korrigieren(
            hass,
            sensor,
            entry.data.get(CONF_ZAEHLERSTAND_EINGABE),
            msg["start"],
            msg["stand"],
            nur_pruefen=msg["pruefen"],
            fertig=_neu_rechnen,
        )
    except ablesung.KorrekturFehler as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    connection.send_result(msg["id"], ergebnis)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/abschlag_korrektur",
        vol.Required("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_abschlag_korrektur(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Wertet die gemerkte Abschlagsaenderung als Korrektur (Abschlag gilt ab Vertragsbeginn)."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        history.abschlag_aenderung_entfernen(hass, entry)
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/preis_save",
        vol.Required("entry_id"): str,
        vol.Required("values"): HISTORY_VALUES,
        vol.Optional("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_preis_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Legt eine Preisaenderung im laufenden Vertrag an oder ersetzt sie."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        history.save_preisaenderung(hass, entry, msg["values"], msg.get("index"))
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/preis_delete",
        vol.Required("entry_id"): str,
        vol.Required("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_preis_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Loescht eine Preisaenderung."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        history.delete_preisaenderung(hass, entry, msg["index"])
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/aussen_save",
        vol.Required("entry_id"): str,
        vol.Required("values"): HISTORY_VALUES,
        vol.Optional("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_aussen_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Legt einen Aussenzaehler (Garten, Pool, ...) eines Wasservertrags an oder ersetzt ihn."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        first = history.save_aussenzaehler(hass, entry, msg["values"], msg.get("index"))
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    if first:  # the extra sensors exist only for a contract with an outdoor meter: set up again
        hass.config_entries.async_schedule_reload(entry.entry_id)
    else:
        await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/aussen_delete",
        vol.Required("entry_id"): str,
        vol.Required("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_aussen_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Loescht einen Aussenzaehler."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        last = history.delete_aussenzaehler(hass, entry, msg["index"])
    except history.HistoryError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    if last:
        hass.config_entries.async_schedule_reload(entry.entry_id)
    else:
        await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/dokument_save",
        vol.Required("entry_id"): str,
        vol.Required("values"): HISTORY_VALUES,
        vol.Optional("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_dokument_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Verknuepft ein Paperless-Dokument mit dem Vertrag oder aendert den Verweis."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        dokumente.save_dokument(hass, entry, msg["values"], msg.get("index"))
    except dokumente.DokumentError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/dokument_delete",
        vol.Required("entry_id"): str,
        vol.Required("index"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_dokument_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Entfernt den Verweis auf ein Dokument (in Paperless bleibt es erhalten)."""
    entry = _loaded_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Contract not found or not loaded")
        return
    try:
        dokumente.delete_dokument(hass, entry, msg["index"])
    except dokumente.DokumentError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await entry.runtime_data.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "tariffy/backup_export"})
@websocket_api.async_response
async def ws_backup_export(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Alle Vertraege als Sicherung (enthaelt persoenliche Daten, nur fuer Administratoren)."""
    connection.send_result(msg["id"], {"backup": await backup.async_build_backup(hass)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "tariffy/backup_preview", vol.Required("backup"): dict}
)
@callback
def ws_backup_preview(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Zeigt, was das Wiederherstellen taete (legt nichts an)."""
    try:
        contracts = backup.sanitize_backup(msg["backup"])
    except backup.BackupError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    connection.send_result(
        msg["id"], {"contracts": backup.plan(hass, contracts, backup.sanitize_objekte(msg["backup"]))}
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "tariffy/backup_restore", vol.Required("backup"): dict}
)
@websocket_api.async_response
async def ws_backup_restore(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Legt die fehlenden Vertraege einer Sicherung an, vorhandene bleiben unberuehrt."""
    try:
        result = await backup.async_restore(hass, msg["backup"])
    except backup.BackupError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "tariffy/deleted_list"})
@websocket_api.async_response
async def ws_deleted_list(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Die geloeschten Vertraege, die sich noch zurueckholen lassen."""
    connection.send_result(msg["id"], {"deleted": await backup.async_deleted(hass)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "tariffy/deleted_restore", vol.Required("item_id"): str}
)
@websocket_api.async_response
async def ws_deleted_restore(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Holt einen geloeschten Vertrag zurueck."""
    try:
        title = await backup.async_restore_deleted(hass, msg["item_id"])
    except backup.BackupError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    connection.send_result(msg["id"], {"title": title})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "tariffy/deleted_discard", vol.Required("item_id"): str}
)
@websocket_api.async_response
async def ws_deleted_discard(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Vergisst einen geloeschten Vertrag endgueltig."""
    try:
        await backup.async_discard_deleted(hass, msg["item_id"])
    except backup.BackupError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    connection.send_result(msg["id"], {"ok": True})


# ------------------------------------------------------------------ Objekte
async def _objekt_geaendert(hass: HomeAssistant) -> None:
    objekte.async_refresh_entities(hass)
    async_notify(hass)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/objekt_save",
        vol.Optional("objekt_id"): vol.Any(None, str),
        vol.Required("name"): str,
        vol.Optional("icon"): vol.Any(None, str),
    }
)
@websocket_api.async_response
async def ws_objekt_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Legt ein Objekt an (ohne objekt_id) oder aendert Name und Icon."""
    store = await objekte.async_get(hass)
    try:
        objekt_id = await store.async_save_objekt(msg.get("objekt_id"), msg["name"], msg.get("icon"))
    except objekte.ObjektError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await _objekt_geaendert(hass)
    connection.send_result(msg["id"], {"objekt_id": objekt_id})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "tariffy/objekt_delete",
        vol.Required("objekt_id"): str,
        vol.Optional("ziel"): vol.Any(None, str),
    }
)
@websocket_api.async_response
async def ws_objekt_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Loescht ein Objekt; seine Vertraege ziehen vorher in das Objekt `ziel` um."""
    store = await objekte.async_get(hass)
    try:
        moved = await store.async_delete(msg["objekt_id"], msg.get("ziel"))
    except objekte.ObjektError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    await _objekt_geaendert(hass)
    connection.send_result(msg["id"], {"moved": moved})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "tariffy/objekt_default", vol.Required("objekt_id"): str}
)
@websocket_api.async_response
async def ws_objekt_default(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Macht ein Objekt zum Default (das Panel zeigt es beim Oeffnen)."""
    store = await objekte.async_get(hass)
    try:
        await store.async_set_default(msg["objekt_id"])
    except objekte.ObjektError as err:
        connection.send_error(msg["id"], err.code, err.code)
        return
    async_notify(hass)
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {vol.Required("type"): "tariffy/objekt_aktiv", vol.Required("objekt_id"): str}
)
@websocket_api.async_response
async def ws_objekt_aktiv(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Merkt sich das im Panel gewaehlte Objekt (nur im Speicher): Vorauswahl beim Anlegen eines Vertrags."""
    store = await objekte.async_get(hass)
    if store.get(msg["objekt_id"]) is None:
        connection.send_error(msg["id"], "not_found", "not_found")
        return
    store.aktiv = msg["objekt_id"]
    connection.send_result(msg["id"], {"ok": True})


COMMANDS = (
    ws_contracts,
    ws_subscribe,
    ws_action,
    ws_history_calc,
    ws_history_save,
    ws_history_delete,
    ws_renew,
    ws_einmal_save,
    ws_einmal_delete,
    ws_abschlag_korrektur,
    ws_zaehler_import,
    ws_ablesungen,
    ws_ablesung_korrigieren,
    ws_preis_save,
    ws_preis_delete,
    ws_aussen_save,
    ws_aussen_delete,
    ws_dokument_save,
    ws_dokument_delete,
    ws_backup_export,
    ws_backup_preview,
    ws_backup_restore,
    ws_deleted_list,
    ws_deleted_restore,
    ws_deleted_discard,
    ws_objekt_save,
    ws_objekt_delete,
    ws_objekt_default,
    ws_objekt_aktiv,
)


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register every command of the panel (once, see panel.async_setup_panel)."""
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
