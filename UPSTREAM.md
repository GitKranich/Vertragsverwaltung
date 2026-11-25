# Abgleich mit dem Original

Diese Version ist eine eigene Weiterentwicklung von [weskona/tariffy](https://github.com/weskona/tariffy)
(abgezweigt bei **1.30.0**, ohne gemeinsame Git-Historie). Änderungen aus dem Original werden gezielt und meist
von Hand übernommen. Diese Datei hält fest, bis wohin verglichen wurde und was mit jeder Änderung geschehen ist,
damit der nächste Vergleich dort weitermacht.

## Stand

| | Version | Commit | Datum |
|---|---|---|---|
| Zuletzt verglichen (stabil) | 1.32.0 | `1f9c3cb` (main bis `6bbd25e`) | 2026-11-19 |
| Zuletzt verglichen (beta) | 1.33.0-beta.1 | `c82176b` | 2026-11-20 |

Nächster Vergleich: alles im Original nach `6bbd25e` (main) bzw. `c82176b` (beta).

```bash
git remote add upstream https://github.com/weskona/tariffy.git   # einmalig
git fetch upstream --tags
git log --oneline --no-merges 6bbd25e..upstream/main -- custom_components
```

## Übernommen

| Original | Was | Hier in |
|---|---|---|
| 1.32.0 | Zahlweise eines Pauschalvertrags bleibt beim Tarifwechsel erhalten | 1.39.1 |
| 1.32.0 (#102) | Offenes Panel blieb nach Anlegen/Ändern eines Vertrags leer | 1.39.1 |
| 1.32.0 | Helles Design (folgt Home Assistant), erweitert auf Dokumente, Diagramme, Preisänderungen | 1.40.0-beta.1 / 1.41.0 |
| 1.32.0 (#93) | Eigenes Icon pro Vertrag | 1.40.0-beta.1 / 1.41.0 |
| 1.32.0 | Panel aufgeteilt in Code, Texte, Icons, Styles; Laden mit `?v=`, kein top-level await | 1.42.0-beta.1 |

## Offen (noch nicht übernommen)

| Original | Was | Einschätzung |
|---|---|---|
| 1.31.0 (#86) | PV-Autarkie in der Prognose (`autarkie.py`) | eigenes Modul, gut übernehmbar; mit der saisonalen Prognose dieser Version abzustimmen |
| 1.32.0 (#90) | Sparte Leasing mit Kilometern (`leasing.py`, 3 Sensoren) | eigenes Modul, gut übernehmbar |
| 1.32.0 | Abschläge je Zahlung korrigieren, auch für frühere Laufzeiten (`tariffy/abschlag_verlauf_save`) | überschneidet sich mit Abschlags-Korrektur und erster Abschlagszahlung (1.33.0 hier); zuerst entscheiden, welche Logik gilt |
| 1.32.0 | Kundennummer, Zählernummer, MaLo-ID in den Details (Admins) | klein, Panel |
| 1.32.0 | Gesamtverbrauch und PV-Anteil im Block Verbrauch | hängt an der PV-Autarkie |
| 1.32.0 (#103) | Pauschalvertrag ohne Enddatum verlängern | klein |
| 1.32.0 | Kurzes Folgevertrag-Formular für Streaming, "Preis" statt "Abschlag" | klein, Formular und Texte |
| 1.32.0 | Pauschal-Formular nur noch mit einem Betrag | prüfen gegen Preisänderungen bei Pauschalverträgen (1.35.0 hier) |
| 1.32.0 | Panel stand schwarz beim Öffnen | mit 1.42.0-beta.1 enthalten (Ladeverfahren) |
| 1.33.0-beta.1 (#122) | Sparte Hauskosten mit Art aus Liste oder frei | erst nach stabiler Version im Original |
| unreleased | Neues Logo und Icon | bewusst offen: eigene Version, eigenes Erscheinungsbild |

## Bewusst nicht übernommen

| Original | Was | Grund |
|---|---|---|
| 1.32.0 ff. | Neues Wiki (69 Seiten) mit Prüfskript und Sync-Workflow | Doku dieser Version ist eigenständig (`docs/wiki`) |

## Unterschiede im Aufbau

Diese Version weicht seit 1.42.0 auch im Aufbau ab, damit ihre eigenen Funktionen getrennt bleiben. Übernahmen aus
dem Original gehen deshalb nicht als Patch, sondern von Hand an diese Stellen:

| Im Original | Hier |
|---|---|
| `coordinator.py` (alle Rechnungen) | `coordinator.py` plus reine Rechenmodule (siehe `custom_components/tariffy/README.md`) |
| WebSocket-Befehle in `panel.py` | `websocket.py`; `panel.py` registriert nur das Panel |
| `frontend/tariffy-panel.js` (alle Tabs) | `tariffy-panel.js` plus ein Modul je Tab / Bereich unter `frontend/` |
