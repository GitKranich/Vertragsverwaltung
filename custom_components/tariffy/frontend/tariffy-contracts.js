/* Tab "Contracts": the list of contracts with their details and buttons.
 * Part of the Tariffy panel: tariffy-panel.js loads it with its own version and copies the methods onto the
 * panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, icon, isNum, METERED }) => class {
  _contractList() {
    const head = this._isAdmin
      ? h("div", { class: "list-head" },
        h("button", { class: "btn blue", type: "button", onclick: () => this._openFlow(null, "create") }, icon("plus"), this._t("actNew")))
      : null;
    // the same two groups as in the overview
    const group = (key, list) => (list.length ? h("section", { class: "group" },
      h("h3", { class: "group-title" }, h("span", { text: this._t(key) }), h("span", { class: "count", text: String(list.length) })),
      list.map((c) => this._detail(c))) : null);
    return h("div", {}, head,
      group("groupMetered", this._live.filter((c) => METERED.has(c.sparte))),
      group("groupFlat", this._live.filter((c) => !METERED.has(c.sparte))),
      group("groupLoans", this._loans));
  }

  /* Everything that changes a contract lives here, in the list of contracts (administrators only). */
  _rowActions(c) {
    if (!this._isAdmin) return null;
    const button = (flow, label, name) => h("button", {
      class: "icon-btn", type: "button", title: label, "aria-label": label,
      // the row is a <summary>: a click on the icon must not open or close it
      onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); this._openFlow(c, flow); },
    }, icon(name));
    return h("div", { class: "row-actions" },
      c.verlaengerbar ? h("button", {
        class: "icon-btn", type: "button", title: this._t("actRenew"), "aria-label": this._t("actRenew"),
        onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); this._openRenew(c); },
      }, icon("renew")) : null,
      c.zaehlerstand_eingabe ? h("button", {
        class: "icon-btn", type: "button", title: this._t("meterRecord"), "aria-label": this._t("meterRecord"),
        onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); this._openMeter(c); },
      }, icon("meter")) : null,
      c.sparte === "loan" ? null : button("next", this._t(c.next && c.next.beginn ? "actNextEdit" : "actNextPlan"), "plan"),
      button("edit", this._t("actEdit"), "edit"));
  }

  _detail(c) {
    const d = this._derive(c);
    const unit = this._unit(c);
    const pu = this._priceUnit(c);
    const row = (label, value) => (value === null || value === undefined || value === "–" ? null
      : h("div", { class: "row" }, h("span", { text: label }), h("span", { text: value })));
    const section = (title, rows) => {
      const filled = rows.filter(Boolean);
      return filled.length ? h("div", { class: "sec" }, h("h4", { text: title }), filled) : null;
    };
    const money = (v, digits = 2) => (isNum(v) ? this._money(v, c.currency, { digits }) : null);
    const amount = (v) => (isNum(v) ? `${this._num(v, 0)} ${unit}` : null);
    const amount1 = (v) => (isNum(v) ? `${this._num(v, 1)} ${unit}` : null); // small amounts (outdoor water) need a decimal
    const nxt = c.next || {};

    const sections = [
      section(this._t("secContract"), [
        row(this._t("provider"), c.anbieter), row(this._t("tariff"), c.tarif),
        row(this._t("insuranceType"), c.versicherungsart ? this._insuranceType(c.versicherungsart) : null),
        row(this._t("kmStart"), isNum(c.km_start) ? `${this._num(c.km_start, 0)} km` : null),
        row(this._t("kmPerYear"), isNum(c.km_pro_jahr) ? `${this._num(c.km_pro_jahr, 0)} km` : null),
        row(this._t("start"), c.beginn ? this._date(c.beginn) : null),
        row(this._t("end"), c.ende ? this._date(c.ende) : null),
        row(this._t("remaining"), isNum(c.restlaufzeit) ? this._t("daysLeft", { n: c.restlaufzeit }) : null),
        row(this._t("minTerm"), isNum(c.mindestlaufzeit)
          ? `${this._t("months", { n: c.mindestlaufzeit })}${c.mindestlaufzeit_bis ? ` · ${this._t("untilDate", { d: this._date(c.mindestlaufzeit_bis) })}` : ""}` : null),
        row(this._t("noticePeriod"), isNum(c.kuendigungsfrist) ? this._t("months", { n: c.kuendigungsfrist }) : null),
        row(this._t("cancelBy"), c.kuendigung_bis ? this._date(c.kuendigung_bis) : null),
        row(this._t("reminderDate"), c.erinnerung_datum
          ? `${this._date(c.erinnerung_datum)} · ${c.erinnerung_bestaetigt ? this._t("reminderDone") : c.erinnerung_aktiv ? this._t("reminderActive") : this._t("months", { n: c.erinnerung_monate })}`
          : null),
      ]),
      this._kreditSection(c),
      section(this._t("secPrices"), [
        row(this._t("unitPrice"), isNum(c.arbeitspreis) ? this._price(c.arbeitspreis, c.currency, pu) : null),
        row(this._t("unitPriceLow"), isNum(c.arbeitspreis_nt) ? this._price(c.arbeitspreis_nt, c.currency, pu) : null),
        row(this._t("unitPriceNight"), isNum(c.arbeitspreis_nacht) ? this._price(c.arbeitspreis_nacht, c.currency, pu) : null),
        row(this._t("unitPriceEff"), isNum(c.effektiver_arbeitspreis) ? this._price(c.effektiver_arbeitspreis, c.currency, pu) : null),
        row(this._t("unitPriceWater"), isNum(c.arbeitspreis_gesamt_wasser) ? this._price(c.arbeitspreis_gesamt_wasser, c.currency, pu) : null),
        row(this._t("basePrice"), isNum(c.grundpreis) ? `${money(c.grundpreis)} ${this._t("perMonth")}` : null),
        row(this._t("instalment"), isNum(c.abschlag) ? `${money(c.abschlag)} ${this._t("perMonth")}` : null),
        row(this._t("instalmentYear"), isNum(c.abschlag_jahr) ? money(c.abschlag_jahr) : null),
        row(this._t("priceNext"), c.naechste_preisaenderung ? this._date(c.naechste_preisaenderung) : null),
        row(this._t("firstInstalment"), METERED.has(c.sparte) && c.erste_zahlung ? this._date(c.erste_zahlung) : null),
        row(this._t("stormwater"), isNum(c.niederschlag_jahr)
          ? `${this._t("perYearShort", { v: money(c.niederschlag_jahr) })} · ${this._num(c.niederschlag_flaeche, 1)} m² × ${this._money(c.niederschlag_gebuehr, c.currency, { digits: 2 })}` : null),
        row(this._t("bonusSignup"), isNum(c.bonus) && c.bonus ? `${money(c.bonus)} · ${this._t("bonusSignupHint")}` : null),
        row(this._t("bonusInstant"), isNum(c.sofortbonus) && c.sofortbonus ? `${money(c.sofortbonus)} · ${this._t("bonusInstantHint")}` : null),
        // flat-rate contracts: what the contract costs a year, and how it is paid when not monthly
        row(this._t("costYear"), !METERED.has(c.sparte) && isNum(c.kosten_jahr) ? money(c.kosten_jahr) : null),
        row(this._t("payment"), c.zahlweise && c.zahlweise !== "monthly" && isNum(c.abschlag_je_zahlung)
          ? `${money(c.abschlag_je_zahlung)} ${this._t("interval")[c.zahlweise] || c.zahlweise}` : null),
        row(this._t("firstDebit"), c.zahlweise && c.zahlweise !== "monthly" && c.erste_zahlung ? this._date(c.erste_zahlung) : null),
        row(this._t("nextPayment"), c.naechste_zahlung ? this._date(c.naechste_zahlung) : null),
        row(this._t("costSoFar"), !METERED.has(c.sparte) && isNum(this._costSoFar(c)) ? money(this._costSoFar(c)) : null),
        row(this._t("totalCost"), this._total(c) ? `${this._total(c).value} · ${this._total(c).since}` : null),
      ]),
      this._meterSection(c),
      this._preisSection(c),
      this._onceSection(c),
      this._docSection(c),
      d.measured || d.hasMetering || isNum(c.verbrauch_letzte_laufzeit) ? section(this._t("secConsumption"), [
        row(this._t("soFar"), amount(c.verbrauch_bisher)),
        row(this._t("gardenUse"), amount1(c.garten_verbrauch)),
        row(this._t("wwUse"), amount1(c.abwasserpflichtiger_verbrauch)),
        row(this._t("soFarHigh"), amount(c.verbrauch_bisher_ht)),
        row(this._t("soFarLow"), amount(c.verbrauch_bisher_nt)),
        row(this._t("projectedTerm"), amount(c.verbrauch_hochgerechnet)),
        row(this._t("forecastMethod"), c.prognose_methode ? this._t(`forecast_${c.prognose_methode}`) : null),
        row(this._t("lastPeriod"), amount(c.verbrauch_letzte_laufzeit)),
        row(this._t("costSoFar"), money(c.kosten_bisher)),
        row(this._t("gardenCost"), money(c.garten_kosten)),
        row(this._t("wwCost"), money(c.abwasser_kosten)),
        row(this._t("balanceSoFar"), isNum(c.guthaben_bisher) ? this._money(c.guthaben_bisher, c.currency, { sign: true, digits: 2 }) : null),
        row(this._t("endForecast"), isNum(c.prognose_real) ? this._money(c.prognose_real, c.currency, { sign: true, digits: 2 }) : null),
      ]) : null,
      section(this._t("secAdvice"), [
        row(this._t("recommended"), isNum(c.empfohlener_abschlag) ? `${money(c.empfohlener_abschlag)} ${this._t("perMonth")}` : null),
        row(this._t("adjustment"), this._advice(c)),
      ]),
      c.next ? section(this._t("secNext"), [
        row(this._t("nextStart"), nxt.beginn ? this._date(nxt.beginn) : null),
        row(this._t("nextEnd"), nxt.ende ? this._date(nxt.ende) : null),
        row(this._t("nextProvider"), nxt.anbieter), row(this._t("nextTariff"), nxt.tarif),
        row(this._t("nextPrice"), isNum(nxt.arbeitspreis) ? this._price(nxt.arbeitspreis, c.currency, pu) : null),
        row(this._t("nextInstalment"), isNum(nxt.abschlag) ? `${money(nxt.abschlag)} ${this._t("perMonth")}` : null),
      ]) : null,
      this._aussenSection(c), // over the whole width below the other blocks: up to ten meters fit side by side
    ];

    const flat = !METERED.has(c.sparte);
    const endValue = c.sparte === "loan" ? (isNum(c.kredit_restschuld) ? this._money(c.kredit_restschuld, c.currency, { digits: 2 }) : "–")
      : flat ? `${this._money(c.abschlag, c.currency, { digits: 2 })} ${this._t("perMonth")}`
      : d.hasMetering ? this._money(c.prognose_real, c.currency, { sign: true }) : this._money(c.jahreskosten, c.currency);
    const details = h(
      "details", { "data-id": c.entry_id },
      h("summary", {},
        h("div", { class: `badge ${c.sparte}` }, this._icon(c)),
        this._who(c),
        this._chips(d.chips),
        h("div", { class: `end ${!flat && d.hasMetering ? (c.prognose_real < 0 ? "red-t" : "green-t") : ""}`, text: endValue }),
        h("div", { class: "row-end" },
          this._rowActions(c),
          h("span", { class: "caret", "aria-hidden": "true" }, icon("chevron")))),
      h("div", { class: "detail" }, sections, this._actions(c))
    );
    if (this._open.has(c.entry_id)) details.setAttribute("open", "");
    details.addEventListener("toggle", () => {
      if (details.open) this._open.add(c.entry_id);
      else this._open.delete(c.entry_id);
    });
    return details;
  }
};
