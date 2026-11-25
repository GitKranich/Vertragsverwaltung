"""Dokumente aus Paperless-ngx mit einem Vertrag verknuepfen.

Gespeichert wird nur der Verweis (Verbindung der Integration "Paperless-ngx Insights",
Dokument-ID, Titel, Datum, eigene Bezeichnung, Adresse der Paperless-Oberflaeche). Suche, Vorschau und Download laufen im Panel
direkt ueber die Befehle jener Integration, mit deren eigener Rechtepruefung pro Benutzer.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DOKUMENTE, DOKUMENTE_MAX

ID_MAX = 2_147_483_647
TITEL_MAX = 300
BEZEICHNUNG_MAX = 100
URL_MAX = 300


class DokumentError(ValueError):
    """Eingabe unbrauchbar; `code` steht fuer die Meldung des Panels."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _text(value: Any, limit: int, code: str, required: bool = False) -> str | None:
    if value in (None, ""):
        if required:
            raise DokumentError(code)
        return None
    if not isinstance(value, str):
        raise DokumentError(code)
    text = " ".join(value.split())
    if (required and not text) or len(text) > limit:
        raise DokumentError(code)
    return text or None


def _eintrag(values: dict[str, Any]) -> dict[str, Any]:
    quelle = _text(values.get("paperless_entry"), 64, "invalid_entry", required=True)
    dokument = values.get("id")
    if isinstance(dokument, float) and dokument.is_integer():
        dokument = int(dokument)
    if isinstance(dokument, bool) or not isinstance(dokument, int) or not 1 <= dokument <= ID_MAX:
        raise DokumentError("invalid_id")
    eintrag: dict[str, Any] = {
        "paperless_entry": quelle,
        "id": dokument,
        "titel": _text(values.get("titel"), TITEL_MAX, "invalid_titel", required=True),
    }
    erstellt = values.get("erstellt")
    if erstellt not in (None, ""):
        try:
            eintrag["erstellt"] = date.fromisoformat(str(erstellt)[:10]).isoformat()
        except ValueError as err:
            raise DokumentError("invalid_erstellt") from err
    if bezeichnung := _text(values.get("bezeichnung"), BEZEICHNUNG_MAX, "invalid_bezeichnung"):
        eintrag["bezeichnung"] = bezeichnung
    # Adresse der Paperless-Oberflaeche (fuer "In Paperless oeffnen"); nur http(s)
    if web_url := _text(values.get("web_url"), URL_MAX, "invalid_web_url"):
        teile = urlsplit(web_url)
        if teile.scheme not in ("http", "https") or not teile.netloc or " " in web_url:
            raise DokumentError("invalid_web_url")
        eintrag["web_url"] = web_url.rstrip("/")
    return eintrag


def liste(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Die gespeicherten Verweise (fehlerhafte Eintraege werden uebergangen)."""
    return [item for item in data.get(CONF_DOKUMENTE) or [] if isinstance(item, dict)]


def save_dokument(
    hass: HomeAssistant, entry: ConfigEntry, values: dict[str, Any], index: int | None = None
) -> None:
    """Verknuepft ein Dokument oder ersetzt einen Verweis (index)."""
    eintrag = _eintrag(values)
    dokumente = liste(entry.data)
    if index is not None and not 0 <= index < len(dokumente):
        raise DokumentError("not_found")
    for position, item in enumerate(dokumente):
        if position != index and (item.get("paperless_entry"), item.get("id")) == (
            eintrag["paperless_entry"],
            eintrag["id"],
        ):
            raise DokumentError("duplicate")
    if index is None:
        if len(dokumente) >= DOKUMENTE_MAX:
            raise DokumentError("too_many")
        dokumente.append(eintrag)
    else:
        dokumente[index] = eintrag
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DOKUMENTE: dokumente})


def delete_dokument(hass: HomeAssistant, entry: ConfigEntry, index: int) -> None:
    """Entfernt einen Verweis (das Dokument in Paperless bleibt unberuehrt)."""
    dokumente = liste(entry.data)
    if not 0 <= index < len(dokumente):
        raise DokumentError("not_found")
    del dokumente[index]
    data = dict(entry.data)
    if dokumente:
        data[CONF_DOKUMENTE] = dokumente
    else:
        data.pop(CONF_DOKUMENTE, None)
    hass.config_entries.async_update_entry(entry, data=data)


def payload(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Die Verweise fuer das Panel, nach Datum (neueste zuerst), mit ihrer Position."""
    eintraege = [{**item, "index": position} for position, item in enumerate(liste(data))]
    return sorted(eintraege, key=lambda item: str(item.get("erstellt") or ""), reverse=True)
