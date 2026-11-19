# Fehlerbehebung

## Verbrauch oder Kosten bleiben leer
Kosten, Guthaben und Prognose brauchen einen konfigurierten Arbeitspreis; `Kosten (Bisher)`, `Guthaben/Nachzahlung (Bisher)` und `Guthaben/Nachzahlung (Vertragsende)` zusätzlich einen Verbrauchssensor. Der Sensor muss Long-Term Statistics aktiviert haben (siehe [Verbrauchsmessung](Verbrauchsmessung)).

## Statistik reicht nicht bis zum Vertragsbeginn
Wurde der Sensor umbenannt oder neu angelegt, trage unter den Vertragsdaten einen manuellen **„Zählerstand bei Vertragsbeginn"** ein. Er hat Vorrang vor den automatischen Methoden.

## Verbrauch war negativ / Attribut `statistik_brueche` größer 0
Die Summe in der Statistik des Verbrauchssensors hat mitten im Vertrag neu (kleiner) angefangen, meist weil der Sensor neu angelegt wurde. Tariffy überbrückt das und rechnet den Verbrauch Zeile für Zeile aus der Statistik. Dauerhaft reparieren (auch für Energie-Dashboard und Verlauf): **Entwicklerwerkzeuge → Statistik**, beim Sensor die erste Zeile nach dem Bruch wählen und die Summe um den fehlenden Betrag anpassen (letzte Summe vor dem Bruch + Verbrauch bis dahin).

## Verbrauch in m³, Kosten in kWh
Die Sensoren „Verbrauch (Bisher)" und „Verbrauch (Vertragslaufzeit hochgerechnet)" zeigen die rohe Zählerdifferenz in der Einheit deines Sensors (meist m³). Nur für die Kostenberechnung wird intern über Brennwert × Zustandszahl in kWh umgerechnet.

## Sensoren erscheinen nicht
Sensoren für Nachttarif, Staffelpreise und Zweizählertarif (HT/NT) gibt es nur, wenn die jeweilige Sparte oder Option genutzt wird.

## Tarifwechsel hat noch nicht stattgefunden
Der Wechsel wird täglich um 00:00:01 Uhr, alle 6 Stunden und nach dem HA-Start geprüft. Über den Button **Jetzt wechseln** lässt er sich sofort auslösen.

## HA-Warnung „update listener … should use it for scheduling a reload"
Behoben in Tariffy 1.26.3 ([Issue 2](https://github.com/weskona/tariffy/issues/2)). Bitte aktualisieren.

## Weitere Hilfe
[Issue eröffnen](https://github.com/weskona/tariffy/issues/new/choose) mit Tariffy-Version, HA-Version und Logs.
