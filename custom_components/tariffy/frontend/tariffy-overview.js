/* Tab "Overview": the totals, the cards of the contracts and loans, and the actions of a contract.
 * Part of the Tariffy panel: tariffy-panel.js loads it with its own version and copies the methods onto the
 * panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, svg, icon, isNum, METERED, GRID }) => class {
  /* ----- loans / financing (kept apart from the contracts and their totals) ----- */
  get _loans() {
    return (this._contracts || []).filter((c) => c.sparte === "loan" && !c.archiviert);
  }

  _loanSub(c) {
    const iv = this._t("interval")[c.zahlweise] || this._t("interval").monthly;
    const rates = [
      isNum(c.kredit_rate) ? `${this._money(c.kredit_rate, c.currency, { digits: 2 })} ${iv}` : null,
      isNum(c.kredit_sollzins) ? `${this._num(c.kredit_sollzins, 2)} %` : null,
      isNum(c.kredit_effektivzins) ? this._t("loanEffective", { v: this._num(c.kredit_effektivzins, 2) }) : null,
    ].filter(Boolean);
    return rates.join(" · ") || null;
  }

  _loanNote(c) {
    if (c.kredit_rate_passt !== false || !isNum(c.kredit_rate_abweichung)) return null;
    return this._t("loanMismatch", {
      p: this._num(Math.abs(c.kredit_rate_abweichung), 1),
      r: this._money(c.kredit_rate_berechnet, c.currency, { digits: 2 }),
    });
  }

  _loanCard(c) {
    const d = this._derive(c);
    const money = (v) => this._money(v, c.currency, { digits: 2 });
    const next = isNum(c.kredit_naechste_betrag);
    const tiles = [
      this._tile(this._t("loanPaid"), money(c.kredit_gezahlt),
        isNum(c.kredit_zinsen_bisher) ? this._t("loanInterestOf", { v: money(c.kredit_zinsen_bisher) }) : null),
      this._tile(this._t("loanRemainingSum"), money(c.kredit_restsumme),
        isNum(c.kredit_zinsen_gesamt) ? this._t("loanInterestTotalOf", { v: money(c.kredit_zinsen_gesamt) }) : null),
      next ? this._tile(this._t("loanNext"), money(c.kredit_naechste_betrag),
        c.kredit_naechste_zahlung ? this._date(c.kredit_naechste_zahlung) : null) : null,
    ];
    const repaid = isNum(c.kredit_tilgung_prozent) ? c.kredit_tilgung_prozent : 0;
    const bar = h("div", { class: "bars" }, h("div", { class: "bar" },
      h("span", { text: this._t("loanRepaid") }), h("b", { text: `${this._num(repaid, 0)} %` }),
      h("span", { class: "tr" }, h("span", { class: "fl", style: `width:${Math.max(2, repaid).toFixed(1)}%;background:var(--green)` }))));
    const foot = [];
    if (isNum(c.restlaufzeit)) {
      foot.push(h("span", {}, `${this._t("remaining")} `, h("b", { text: this._t("daysLeft", { n: c.restlaufzeit }) }),
        c.ende ? ` · ${this._t("until", { d: this._date(c.ende) })}` : ""));
    }
    if (isNum(c.kredit_zahlungen_gesamt)) {
      foot.push(h("span", {}, `${this._t("loanPayments")} `, h("b", { text: `${c.kredit_zahlungen_geleistet} / ${c.kredit_zahlungen_gesamt}` })));
    }
    if (next) { // how the next rate is made up
      foot.push(h("span", {}, `${this._t("loanNext")} `, h("b", { text: this._t("loanSplit", { z: money(c.kredit_zins_anteil), t: money(c.kredit_tilgung_anteil) }) })));
    }
    const notes = [];
    if (this._loanNote(c)) notes.push(h("div", { class: "callout orange", text: this._loanNote(c) }));
    if (c.archivierbar) notes.push(h("div", { class: "callout", text: this._t("noteExpired") }));
    if (c.erinnerung_aktiv) notes.push(h("div", { class: "callout orange", text: this._t("noteCancel", { d: this._date(c.ende) }) }));
    if (!isNum(c.kredit_restschuld)) notes.push(h("div", { class: "callout", text: this._t("loanIncomplete") }));
    const hero = h("div", { class: "hero" },
      h("div", {},
        h("div", { class: "label", text: this._t("loanDebt") }),
        h("div", { class: "big", text: isNum(c.kredit_restschuld) ? money(c.kredit_restschuld) : "–" }),
        this._loanSub(c) ? h("div", { class: "note", text: this._loanSub(c) }) : null));
    const body = [hero, h("div", { class: "tiles" }, tiles.filter(Boolean)), bar,
      foot.length ? h("div", { class: "foot" }, foot) : null,
      notes.length ? h("div", { class: "notes" }, notes) : null];
    if (this._compact) {
      const card = h("details", { class: "card compact tone-none".replace(" tone-none", ""), "data-id": c.entry_id },
        h("summary", {},
          h("div", { class: `badge ${c.sparte}` }, this._icon(c)),
          this._who(c),
          h("div", { class: "end", text: isNum(c.kredit_restschuld) ? this._money(c.kredit_restschuld, c.currency) : "–" }),
          h("span", { class: "caret", "aria-hidden": "true" }, icon("chevron")),
          this._chips(d.chips)),
        h("div", { class: "body" }, body));
      if (this._open.has(c.entry_id)) card.setAttribute("open", "");
      card.addEventListener("toggle", () => {
        if (card.open) this._open.add(c.entry_id);
        else this._open.delete(c.entry_id);
      });
      return card;
    }
    return h("section", { class: "card flat", "data-id": c.entry_id },
      h("div", { class: "head" }, h("div", { class: `badge ${c.sparte}` }, this._icon(c)), this._who(c), this._chips(d.chips)),
      body.map((part) => part || h("div", { class: "slot" })));
  }

  /* the "loan" block of the contract details */
  _kreditSection(c) {
    if (c.sparte !== "loan") return null;
    const money = (v) => (isNum(v) ? this._money(v, c.currency, { digits: 2 }) : null);
    const pct = (v) => (isNum(v) ? `${this._num(v, 2)} %` : null);
    const row = (label, value) => (value === null || value === undefined ? null
      : h("div", { class: "row" }, h("span", { text: label }), h("span", { text: value })));
    const iv = this._t("interval")[c.zahlweise] || this._t("interval").monthly;
    const rows = [
      row(this._t("loanAmount"), money(c.kredit_summe)),
      row(this._t("loanNominal"), pct(c.kredit_sollzins)),
      row(this._t("loanEffectiveRow"), pct(c.kredit_effektivzins)),
      row(this._t("loanTerm"), isNum(c.laufzeit_monate) ? this._t("months", { n: c.laufzeit_monate }) : null),
      row(this._t("loanRatePaid"), isNum(c.kredit_rate) ? `${money(c.kredit_rate)} ${iv}` : null),
      row(this._t("loanRateCalc"), c.kredit_rate_passt === false ? money(c.kredit_rate_berechnet) : null),
      row(this._t("loanFinal"), isNum(c.kredit_schlussrate) && c.kredit_schlussrate > 0 ? money(c.kredit_schlussrate) : null),
      row(this._t("firstPayment"), c.kredit_erste_zahlung ? this._date(c.kredit_erste_zahlung) : null),
      row(this._t("loanPayments"), isNum(c.kredit_zahlungen_gesamt) ? `${c.kredit_zahlungen_geleistet} / ${c.kredit_zahlungen_gesamt}` : null),
      row(this._t("loanDebt"), money(c.kredit_restschuld)),
      row(this._t("loanPaid"), money(c.kredit_gezahlt)),
      row(this._t("loanInterestPaid"), money(c.kredit_zinsen_bisher)),
      row(this._t("loanPrincipalPaid"), money(c.kredit_tilgung_bisher)),
      row(this._t("loanRemainingSum"), money(c.kredit_restsumme)),
      row(this._t("loanInterestTotal"), money(c.kredit_zinsen_gesamt)),
      row(this._t("loanTotalCost"), money(c.kredit_gesamtkosten)),
      row(this._t("nextPayment"), c.kredit_naechste_zahlung ? `${this._date(c.kredit_naechste_zahlung)} · ${this._t("loanSplit", { z: money(c.kredit_zins_anteil), t: money(c.kredit_tilgung_anteil) })}` : null),
    ].filter(Boolean);
    return rows.length ? h("div", { class: "sec" }, h("h4", { text: this._t("secLoan") }), rows,
      this._loanNote(c) ? h("div", { class: "hint", text: this._loanNote(c) }) : null) : null;
  }

  _loanTable(loans) {
    if (!loans.length) return null;
    const money = (v, c) => this._money(v, c.currency, { digits: 2 });
    const cell = (label, value) => h("td", { "data-label": label, class: value === "–" ? "empty" : null, text: value });
    const rows = loans.map((c) => h("tr", {},
      h("td", {}, c.title, h("span", { class: "t2", text: c.anbieter || this._t("sparte").loan })),
      cell(this._t("loanPaid"), money(c.kredit_gezahlt, c)),
      cell(this._t("loanDebt"), money(c.kredit_restschuld, c)),
      cell(this._t("loanRemainingSum"), money(c.kredit_restsumme, c)),
      cell(this._t("colInstalment"), isNum(c.kredit_rate) ? money(c.kredit_rate, c) : "–"),
      cell(this._t("loanInterestTotal"), money(c.kredit_zinsen_gesamt, c))));
    return h("section", { class: "group" },
      h("h3", { class: "group-title" }, h("span", { text: this._t("groupLoans") }), h("span", { class: "count", text: String(loans.length) })),
      h("table", {},
        h("colgroup", {}, GRID.map((width) => h("col", { style: `width:${width}` }))),
        h("thead", {}, h("tr", {}, [this._t("colContract"), this._t("loanPaid"), this._t("loanDebt"), this._t("loanRemainingSum"),
          this._t("colInstalment"), this._t("loanInterestTotal")].map((label) => h("th", { text: label })))),
        h("tbody", {}, rows)),
      h("div", { class: "hint", text: this._t("loansHint") }));
  }

  /* Contracts that are not archived (loans are kept apart): the overview, the costs and the list only show these. */
  /* house costs (waste fees, street cleaning, ...): flat-rate contracts that count in the totals, in a group of their own */
  get _house() {
    return this._live.filter((c) => c.sparte === "hauskosten");
  }

  /* the type of house costs under the name, unless the name already says it ("Abfallgebühren 2026") */
  _houseCostMeta(c) {
    if (c.sparte !== "hauskosten" || !c.hauskostenart) return null;
    const type = this._houseCostType(c.hauskostenart);
    return String(c.title).toLowerCase().includes(type.toLowerCase()) ? null : type;
  }

  get _live() {
    return (this._contracts || []).filter((c) => !c.archiviert && c.sparte !== "loan");
  }

  _summary() {
    const contracts = this._live;
    const currency = contracts[0].currency || "EUR";
    const metered = contracts.filter((c) => METERED.has(c.sparte));
    const flat = contracts.filter((c) => !METERED.has(c.sparte));
    const sum = (list, fn) => list.reduce((s, c) => s + (fn(c) || 0), 0);
    const money = (v, o = {}) => this._money(v, currency, o);
    const tone = (value) => (value < 0 ? "red-t" : value > 0 ? "green-t" : "");

    // instalments: per month, split by kind of contract, and what that makes per year
    const monthlyMetered = sum(metered, (c) => c.abschlag);
    const monthlyFlat = sum(flat, (c) => c.abschlag);
    const monthly = monthlyMetered + monthlyFlat;

    // costs so far: measured for metered contracts, instalments due for flat-rate ones
    const costs = sum(contracts, (c) => this._costSoFar(c));
    const due = sum(metered, (c) => (isNum(c.kosten_bisher) && isNum(c.guthaben_bisher) ? c.kosten_bisher + c.guthaben_bisher
      : this._fromInstalments(c) ? c.abschlag_bisher_gezahlt : 0))
      + sum(flat, (c) => c.abschlag_bisher_gezahlt);
    const withBalance = metered.filter((c) => isNum(c.guthaben_bisher));
    const balance = sum(withBalance, (c) => c.guthaben_bisher);

    // forecast at contract end, worst first
    const withMetering = contracts.filter((c) => isNum(c.prognose_real));
    const forecast = sum(withMetering, (c) => c.prognose_real);
    const worstFirst = [...withMetering].sort((a, b) => a.prognose_real - b.prognose_real).slice(0, 4);

    // the next dates: cancellation deadline, switch, contract end; one entry per contract and date
    const candidates = [];
    for (const c of contracts) {
      const endDays = this._days(c.ende);
      if (c.erinnerung_aktiv && endDays !== null && endDays >= 0) {
        candidates.push({ days: endDays, rank: 0, key: `${c.entry_id}${c.ende}`, text: `${this._t("cancelDue")} · ${c.title}`, date: c.ende });
      }
      const switchDays = this._days(c.wechsel);
      if (switchDays !== null && switchDays >= 0) {
        candidates.push({ days: switchDays, rank: 1, key: `${c.entry_id}${c.wechsel}`, text: this._t("switchOn", { d: this._date(c.wechsel) }) + ` · ${c.title}`, date: c.wechsel });
      }
      if (endDays !== null && endDays >= 0) {
        candidates.push({ days: endDays, rank: 2, key: `${c.entry_id}${c.ende}`, text: `${this._t("contractEnd")} · ${c.title}`, date: c.ende });
      }
    }
    candidates.sort((a, b) => a.days - b.days || a.rank - b.rank);
    const seen = new Set();
    const dates = candidates.filter((x) => (seen.has(x.key) ? false : seen.add(x.key))).slice(0, 3);
    const rel = (days) => (days === 0 ? this._t("today") : days === 1 ? this._t("tomorrow") : this._t("inDays", { n: days }));

    const row = (name, value, cls) => h("div", { class: "mrow" },
      h("span", { class: "n" }, name), h("span", { class: `v ${cls || ""}`, text: value }));
    const segment = (value, color) => (value > 0 ? h("span", { style: `width:${(value / monthly * 100).toFixed(1)}%;background:${color}` }) : null);
    const legend = (color, label, value) => (value > 0 ? h("span", {}, h("i", { style: `background:${color}` }), `${label} `, h("b", { text: money(value, { digits: 2 }) })) : null);

    return h(
      "div", { class: "summary" },
      h("div", { class: "sum" },
        h("div", { class: "label", text: this._t("monthlyTotal") }),
        h("div", { class: "value", text: money(monthly, { digits: 2 }) }),
        h("div", { class: "sub", text: this._t("perYear", { v: money(monthly * 12) }) }),
        monthly > 0 ? h("div", { class: "split" }, segment(monthlyMetered, "var(--blue)"), segment(monthlyFlat, "var(--c-flat)")) : null,
        monthly > 0 ? h("div", { class: "legend" },
          legend("var(--blue)", this._t("shortMetered"), monthlyMetered), legend("var(--c-flat)", this._t("shortFlat"), monthlyFlat)) : null),
      h("div", { class: "sum" },
        h("div", { class: "label", text: this._t("colCost") }),
        h("div", { class: "value", text: money(costs, { digits: 2 }) }),
        h("div", { class: "sub", text: this._t("dueSoFar", { v: money(due) }) }),
        withBalance.length ? h("div", { class: "mini" }, row(this._t("saldoShort"), money(balance, { sign: true, digits: 2 }), tone(balance))) : null),
      h("div", { class: "sum" },
        h("div", { class: "label", text: this._t("forecastTotal") }),
        h("div", { class: `value ${withMetering.length ? tone(forecast) : "muted-t"}`,
          text: withMetering.length ? money(forecast, { sign: true }) : "–" }),
        h("div", { class: "sub", text: this._t("forecastSub", { n: withMetering.length, m: contracts.length }) }),
        worstFirst.length ? h("div", { class: "mini" }, worstFirst.map((c) => row(c.title, money(c.prognose_real, { sign: true }), tone(c.prognose_real)))) : null),
      h("div", { class: "sum" },
        h("div", { class: "label", text: this._t("nextDates") }),
        h("div", { class: "value", text: dates.length ? this._date(dates[0].date) : "–" }),
        h("div", { class: "sub", text: dates.length ? `${dates[0].text} · ${rel(dates[0].days)}` : this._t("nothingPlanned") }),
        dates.length > 1 ? h("div", { class: "mini" }, dates.slice(1).map((x) => row(
          h("span", {}, h("b", { text: this._date(x.date) }), ` ${x.text}`), rel(x.days)))) : null)
    );
  }


  _chips(chips) {
    return h("div", { class: "chips" }, chips.map((chip) => h("span", { class: `chip ${chip.tone}`, text: chip.text })));
  }

  _who(c) {
    return h(
      "div", { class: "who" },
      h("div", { class: "title", text: c.title }),
      h("div", { class: "meta", text: [this._houseCostMeta(c), c.anbieter, c.tarif].filter(Boolean).join(" · ") })
    );
  }

  /* Cost over all terms of a contract (earlier ones from the history plus the running one): value and
   * "since 01.06.2024 · 3 terms". Only for contracts that have a history; the sum is a lower bound when a
   * term has no cost. */
  _total(c) {
    if (!((c.historie || []).length || (c.einmalposten || []).length) || !isNum(c.kosten_gesamt)) return null;
    const value = this._money(c.kosten_gesamt, c.currency, { digits: 2 });
    const n = c.laufzeiten_anzahl;
    const since = [c.seit ? this._t("totalSince", { d: this._date(c.seit) }) : null,
      isNum(n) ? this._t(n === 1 ? "termsOne" : "termsMany", { n }) : null].filter(Boolean).join(" · ");
    return { value: c.kosten_gesamt_vollstaendig === false ? this._t("atLeast", { v: value }) : value, since };
  }

  /* Cost so far: measured for metered contracts, the instalments due until today for flat-rate ones. */
  _costSoFar(c) {
    if (isNum(c.kosten_bisher)) return c.kosten_bisher;
    return (!METERED.has(c.sparte) || this._fromInstalments(c)) && isNum(c.abschlag_bisher_gezahlt) ? c.abschlag_bisher_gezahlt : null;
  }

  /* A metered contract without any measured consumption (no consumption sensor): its cost so far are the instalments
   * due until today, marked as such, so it still counts in the costs (the real cost comes with the bill). */
  _fromInstalments(c) {
    return METERED.has(c.sparte) && !isNum(c.kosten_bisher) && !isNum(c.verbrauch_bisher) && isNum(c.abschlag_bisher_gezahlt);
  }

  _tile(label, value, sub, tone) {
    return h(
      "div", { class: "tile" },
      h("div", { class: "label", text: label }),
      h("div", { class: `v ${tone || ""}`, text: value }),
      sub ? h("div", { class: "s", text: sub }) : null
    );
  }

  _ring(elapsed) {
    if (elapsed === null) return null; // no start and end date: nothing to draw
    const radius = 38;
    const circumference = 2 * Math.PI * radius;
    const pct = elapsed === null ? null : Math.round(elapsed * 100);
    return h(
      "div", { class: "ring", role: "img", "aria-label": `${this._t("term")}: ${pct === null ? "–" : pct + " %"}` },
      svg("svg", { viewBox: "0 0 96 96" },
        svg("circle", { class: "track", cx: 48, cy: 48, r: radius, fill: "none", "stroke-width": 8 }),
        pct === null ? null : svg("circle", {
          class: "arc", cx: 48, cy: 48, r: radius, fill: "none", "stroke-width": 8,
          "stroke-dasharray": `${(circumference * elapsed).toFixed(2)} ${circumference.toFixed(2)}`,
        })),
      h("div", { class: "center" },
        h("div", { class: "pct", text: pct === null ? "–" : `${pct} %` }),
        h("div", { class: "pcap", text: this._t("term") }))
    );
  }

  _bars(c, d) {
    if (d.paid === null || !isNum(c.kosten_bisher)) return null;
    const max = Math.max(d.paid, c.kosten_bisher, 1);
    const over = c.kosten_bisher > d.paid;
    const bar = (label, value, color) => h(
      "div", { class: "bar" },
      h("span", { text: label }),
      h("b", { text: this._money(value, c.currency) }),
      h("span", { class: "tr" }, h("span", { class: "fl", style: `width:${Math.max(2, (value / max) * 100).toFixed(1)}%;background:${color}` }))
    );
    return h("div", { class: "bars" },
      bar(this._t("paid"), d.paid, "var(--blue)"),
      bar(this._t("cost"), c.kosten_bisher, over ? "var(--red)" : "var(--green)"));
  }

  _card(c) {
    if (c.sparte === "loan") return this._loanCard(c);
    const d = this._derive(c);
    const unit = this._unit(c);
    const alert = c.abschlag_warnung_aktiv;
    const flat = !METERED.has(c.sparte); // no consumption, often no end date
    // the colour that tints the top of the card
    const tone = alert || (d.hasMetering && c.prognose_real < 0) ? "red"
      : c.erinnerung_aktiv ? "orange" : d.hasMetering ? "green" : "none";

    // flat-rate contracts lead with the monthly amount: it exists always, a total over the term only with an end date
    const heroValue = flat ? c.abschlag : d.hasMetering ? c.prognose_real : c.jahreskosten;
    const heroLabel = flat ? this._t("instalment") : d.hasMetering ? this._t("forecastTotal") : this._t("endTotal");
    const heroTone = !flat && d.hasMetering ? (heroValue < 0 ? "red-t" : "green-t") : "";
    const heroSub = flat
      ? [
        c.zahlweise && c.zahlweise !== "monthly" && isNum(c.abschlag_je_zahlung)
          ? `${this._money(c.abschlag_je_zahlung, c.currency, { digits: 2 })} ${this._t("interval")[c.zahlweise] || c.zahlweise}` : null,
        isNum(c.abschlag_jahr) && c.zahlweise !== "yearly" ? this._t("perYearShort", { v: this._money(c.abschlag_jahr, c.currency) }) : null,
        c.ende && isNum(c.jahreskosten) ? this._t("overTerm", { v: this._money(c.jahreskosten, c.currency) }) : null,
      ].filter(Boolean).join(" · ") || null
      : isNum(c.abschlag)
      ? `${this._t("instalment")}: ${this._money(c.abschlag, c.currency, { digits: 2 })}` +
        (isNum(c.abschlag_anpassung_empfohlen)
          ? ` · ${this._advice(c)}`
          : "")
      : null;

    // the balance today and the forecast can point in different directions: say why
    let hint = null;
    if (!flat && d.measured && isNum(c.guthaben_bisher) && isNum(c.prognose_real) && Math.abs(c.guthaben_bisher) >= 0.5) {
      const v = this._money(Math.abs(c.guthaben_bisher), c.currency, { digits: 2 });
      if (c.guthaben_bisher < 0 && c.prognose_real >= 0) hint = this._t("hintCatchUp", { v });
      else if (c.guthaben_bisher > 0 && c.prognose_real < 0) hint = this._t("hintUsedUp", { v });
    }

    // three figures at most; the remaining term goes into the footer line
    const tiles = flat ? [
      isNum(this._costSoFar(c)) ? this._tile(this._t("costSoFar"), this._money(this._costSoFar(c), c.currency, { digits: 2 })) : null,
      this._total(c) ? this._tile(this._t("totalCost"), this._total(c).value, this._total(c).since) : null,
      c.ende ? this._tile(this._t("contractEnd"), this._date(c.ende)) : null,
    ] : d.measured
      ? [
        this._tile(this._t("costSoFar"), this._money(c.kosten_bisher, c.currency, { digits: 2 })),
        isNum(c.guthaben_bisher)
          ? this._tile(this._t("saldoShort"), this._money(c.guthaben_bisher, c.currency, { sign: true, digits: 2 }), null,
            c.guthaben_bisher < 0 ? "red-t" : "green-t")
          : null,
        this._tile(this._t("consumption"), `${this._num(c.verbrauch_bisher, 0)} ${unit}`,
          isNum(c.verbrauch_hochgerechnet) ? this._t("projected", { v: `${this._num(c.verbrauch_hochgerechnet, 0)} ${unit}` }) : null),
      ]
      : [
        this._fromInstalments(c)
          ? this._tile(this._t("costSoFar"), this._money(c.abschlag_bisher_gezahlt, c.currency, { digits: 2 }), this._t("fromInstalments"))
          : null,
        this._tile(this._t("instalment"), this._money(c.abschlag, c.currency, { digits: 2 })),
        c.ende ? this._tile(this._t("contractEnd"), this._date(c.ende)) : null,
      ];
    const foot = [];
    if (isNum(c.restlaufzeit)) {
      foot.push(h("span", {}, `${this._t("remaining")} `, h("b", { text: this._t("daysLeft", { n: c.restlaufzeit }) }),
        c.ende ? ` · ${this._t("until", { d: this._date(c.ende) })}` : ""));
    } else if (flat) {
      foot.push(h("span", { text: this._t("noEnd") }));
    }
    if (!flat && this._total(c)) { // the flat-rate cards have a tile for it
      foot.push(h("span", {}, `${this._t("totalCost")} `, h("b", { text: this._total(c).value }), ` · ${this._total(c).since}`));
    }
    if (flat || !d.measured) {
      foot.push(h("span", {}, `${this._t("reminder")} `, h("b", { text: c.erinnerung_datum ? this._date(c.erinnerung_datum) : this._t("reminderOff") })));
    }

    const notes = [];
    if (c.abschlag_warnung_zu_bestaetigen) {
      const value = this._money(Math.abs(c.prognose_real), c.currency);
      notes.push(h("div", { class: "callout red", text: isNum(c.abschlag_anpassung_empfohlen) && c.abschlag_anpassung_empfohlen > 0
        ? this._t("noteWarning", { v: value, a: this._money(c.abschlag_anpassung_empfohlen, c.currency) })
        : this._t("noteWarningShort", { v: value }) }));
    }
    if (c.archivierbar) {
      notes.push(h("div", { class: "callout", text: this._t("noteExpired") }));
    }
    if (c.erinnerung_aktiv) {
      notes.push(h("div", { class: "callout orange", text: this._t("noteCancel", { d: this._date(c.ende) }) }));
    }
    if (c.next && c.next.beginn) {
      const name = [c.next.anbieter, c.next.tarif].filter(Boolean).join(" · ") || "–";
      notes.push(h("div", { class: "callout blue", text: this._t("noteNext", { d: this._date(c.next.beginn), name }) }));
    }

    const hero = h("div", { class: "hero" },
      h("div", {},
        h("div", { class: "label", text: heroLabel }),
        h("div", { class: `big ${heroTone}`, text: this._money(heroValue, c.currency, { sign: !flat && d.hasMetering, digits: flat ? 2 : undefined }) }),
        heroSub ? h("div", { class: "note", text: heroSub }) : null,
        hint ? h("div", { class: "note", text: hint }) : null),
      flat ? null : this._ring(d.elapsed));
    // how far the term has come, for contracts that have both dates
    const termBar = flat && d.elapsed !== null ? h("div", { class: "bars" }, h("div", { class: "bar" },
      h("span", { text: this._t("term") }), h("b", { text: `${Math.round(d.elapsed * 100)} %` }),
      h("span", { class: "tr" }, h("span", { class: "fl", style: `width:${Math.max(2, d.elapsed * 100).toFixed(1)}%;background:var(--blue)` })))) : null;
    const body = [hero, tiles.filter(Boolean).length ? h("div", { class: "tiles" }, tiles.filter(Boolean)) : null, this._bars(c, d) || termBar,
      foot.length ? h("div", { class: "foot" }, foot) : null,
      notes.length ? h("div", { class: "notes" }, notes) : null];

    if (this._compact) {
      const card = h(
        "details", { class: `card compact${alert ? " alert" : ""} tone-${tone}`.replace(" tone-none", ""), "data-id": c.entry_id },
        h("summary", {},
          h("div", { class: `badge ${c.sparte}` }, this._icon(c)),
          this._who(c),
          h("div", { class: `end ${heroTone}`, text: flat
            ? `${this._money(heroValue, c.currency, { digits: 2 })} ${this._t("perMonth")}`
            : this._money(heroValue, c.currency, { sign: d.hasMetering }) }),
          h("span", { class: "caret", "aria-hidden": "true" }, icon("chevron")),
          this._chips(d.chips)),
        h("div", { class: "body" }, body));
      if (this._open.has(c.entry_id)) card.setAttribute("open", "");
      card.addEventListener("toggle", () => {
        if (card.open) this._open.add(c.entry_id);
        else this._open.delete(c.entry_id);
      });
      return card;
    }
    return h(
      "section", { class: `card${flat ? " flat" : ""}${alert ? " alert" : ""} tone-${tone}`.replace(" tone-none", "") },
      h("div", { class: "head" },
        h("div", { class: `badge ${c.sparte}` }, this._icon(c)),
        this._who(c),
        this._chips(d.chips)),
      // always six blocks: cards in one row share their rows (subgrid), so the figures line up across the cards
      body.map((part) => part || h("div", { class: "slot" }))
    );
  }

  /* Actions are only offered to administrators; the backend checks it again. */
  _actions(c, only = null) {
    if (!this._isAdmin) return null;
    const list = [];
    if (c.abschlag_warnung_zu_bestaetigen) {
      list.push({ key: "confirm_abschlag_warning", label: this._t("actConfirmWarning"), tone: "red", ask: "actAskWarning", yes: "actYesAck" });
    }
    if (c.erinnerung_aktiv) {
      list.push({ key: "confirm_cancellation", label: this._t("actConfirmCancel"), tone: "orange", ask: "actAskCancel", yes: "actYesAck" });
    }
    if (c.archivierbar) {
      list.push({ key: "archive_contract", label: this._t("actArchive"), tone: "orange", ask: "actAskArchive", yes: "actYesArchive" });
    }
    if (c.archiviert) {
      list.push({ key: "restore_contract", label: this._t("actRestore"), tone: "blue", ask: "actAskRestore", yes: "actYesRestore" });
    }
    if (only) list.splice(0, list.length, ...list.filter((a) => only.includes(a.key)));
    if (!list.length) return null;

    const id = c.entry_id;
    const busy = this._busy === id;
    const children = [];
    const pending = this._confirming && this._confirming.id === id
      ? list.find((a) => a.key === this._confirming.key) : null;
    if (pending) {
      children.push(h("div", { class: "ask" },
        h("span", { text: this._t(pending.ask) }),
        h("button", { class: "btn go", type: "button", disabled: busy ? "" : null, text: this._t(pending.yes),
          onclick: () => this._run(c, pending.key) }),
        h("button", { class: "btn", type: "button", text: this._t("actCancel"),
          onclick: () => { this._confirming = null; this._render(); } })));
    } else {
      for (const action of list) {
        children.push(h("button", {
          class: `btn ${action.tone}`, type: "button", disabled: busy ? "" : null, text: action.label,
          onclick: () => {
            if (action.open) { this._openFlow(c, action.open); return; }
            this._confirming = { id, key: action.key };
            this._actionError = null;
            this._render();
          },
        }));
      }
    }
    if (this._actionError && this._actionError.id === id) {
      children.push(h("div", { class: "err", role: "alert", text: this._t("actFailed", { m: this._actionError.message }) }));
    }
    return h("div", { class: "actions" }, children);
  }

  async _run(c, action) {
    this._busy = c.entry_id;
    this._confirming = null;
    this._actionError = null;
    this._render();
    try {
      await this._hass.callWS({ type: "tariffy/action", entry_id: c.entry_id, action });
    } catch (err) {
      this._actionError = { id: c.entry_id, message: String((err && err.message) || err) };
    } finally {
      this._busy = null;
      this._render();
    }
  }
};
