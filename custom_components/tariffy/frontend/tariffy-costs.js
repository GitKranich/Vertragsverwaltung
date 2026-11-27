/* Tab "Costs": cost per year, the cost split and the chart.
 * Part of the Tariffy panel: tariffy-panel.js loads it with its own version and copies the methods onto the
 * panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, svg, isNum, METERED, GRID, CHART_COLORS }) => class {
  _recommendedCell(c) {
    const raw = c.empfohlener_abschlag ?? c.abschlag_anpassung_empfohlen;
    return h("td", {
      "data-label": this._t("colRecommended"), class: isNum(raw) ? "rec" : "rec empty",
    }, this._instalment(raw, c.currency),
    this._covered(raw) ? h("span", { class: "t2", text: this._t("coveredHint") }) : null);
  }

  /* Cost split: a ring with one slice per contract, switchable between what has been paid so far and a year */
  /* the cost of one calendar year: every term (earlier ones and the running one) counts for the days it has in that year */
  _yearTerms(c) {
    const today = new Date().toISOString().slice(0, 10);
    const terms = (c.historie || []).map((t) => ({ from: t.beginn, to: t.ende, cost: isNum(t.kosten) ? t.kosten : t.abschlag_gezahlt }));
    if (!c.archiviert && c.beginn) {
      const end = c.ende && c.ende < today ? c.ende : today; // the running term counts up to today
      terms.push({ from: c.beginn, to: end, cost: this._costSoFar(c) });
    }
    return terms.filter((t) => t.from && t.to && isNum(t.cost) && t.to >= t.from);
  }

  _costYears(contracts) {
    const years = new Set();
    for (const c of contracts) {
      for (const t of this._yearTerms(c)) for (let y = Number(t.from.slice(0, 4)); y <= Number(t.to.slice(0, 4)); y += 1) years.add(y);
    }
    return [...years].sort((a, b) => b - a);
  }

  _yearCost(c, year) {
    const day = (iso) => Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10))) / 86400000;
    const start = Date.UTC(year, 0, 1) / 86400000;
    const end = Date.UTC(year, 11, 31) / 86400000;
    let sum = 0;
    for (const t of this._yearTerms(c)) {
      const from = day(t.from);
      const to = day(t.to);
      const overlap = Math.min(to, end) - Math.max(from, start) + 1;
      if (overlap > 0) sum += t.cost * (overlap / (to - from + 1));
    }
    return sum;
  }

  _costChart(live) {
    const years = this._costYears(this._contracts.filter((c) => c.sparte !== "loan"));
    const year = years.includes(this._costYear) ? this._costYear : null;
    const contracts = year === null ? live : this._contracts.filter((c) => c.sparte !== "loan");
    const basis = this._costBasis || "sofar";
    const value = (c) => {
      if (year !== null) return this._yearCost(c, year);
      const v = basis === "year" ? (isNum(c.abschlag_jahr) ? c.abschlag_jahr : isNum(c.abschlag) ? c.abschlag * 12 : null) : this._costSoFar(c);
      return isNum(v) && v > 0 ? v : 0;
    };
    const currency = contracts[0].currency || "EUR";
    const grouped = this._costGroup !== "contract"; // by category unless "per contract" is chosen
    const perContract = contracts.map((c) => ({ label: c.title, sparte: c.sparte, currency: c.currency, n: 1, v: value(c) }))
      .filter((it) => it.v > 0.005);
    const merged = new Map(); // contracts of one category count together
    for (const it of perContract) {
      const old = merged.get(it.sparte);
      if (old) { old.v += it.v; old.n += 1; } else merged.set(it.sparte, { ...it, label: this._t("sparte")[it.sparte] || it.sparte });
    }
    const items = (grouped ? [...merged.values()] : perContract).sort((a, b) => b.v - a.v)
      .map((it, i) => ({ ...it, color: CHART_COLORS[i % CHART_COLORS.length] }));
    const total = items.reduce((s, it) => s + it.v, 0);
    const toggle = h("div", { class: "seg", role: "group" }, [["sofar", "chartSoFar"], ["year", "chartYear"]].map(([key, label]) => h("button", {
      class: year === null && basis === key ? "on" : "", type: "button", "aria-pressed": String(year === null && basis === key), text: this._t(label),
      onclick: () => { this._costBasis = key; this._costYear = null; this._render(); },
    })));
    // the years with data: the cost of that calendar year (earlier terms count for their days in it)
    const picker = years.length ? h("label", { class: "yr-pick" }, h("span", { text: this._t("chartYearLabel") }),
      h("select", {
        "aria-label": this._t("chartYearLabel"),
        onchange: (ev) => { this._costYear = ev.target.value ? Number(ev.target.value) : null; this._render(); },
      }, [h("option", { value: "", text: this._t("chartYearAll") })].concat(
        years.map((y) => h("option", { value: String(y), text: String(y), selected: y === year ? "" : null }))))) : null;
    const groupToggle = h("div", { class: "seg", role: "group" }, [["sparte", "chartBySparte"], ["contract", "chartByContract"]].map(([key, label]) => h("button", {
      class: (grouped ? "sparte" : "contract") === key ? "on" : "", type: "button", "aria-pressed": String((grouped ? "sparte" : "contract") === key), text: this._t(label),
      onclick: () => { this._costGroup = key; this._render(); },
    })));
    const card = h("section", { class: "chart" },
      h("h3", { class: "group-title" }, h("span", { text: this._t("chartTitle") })),
      h("div", { class: "chart-ctl" }, toggle, picker, groupToggle),
      year !== null ? h("p", { class: "hint", text: this._t("chartYearHint") }) : null);
    if (!total) {
      card.appendChild(h("div", { class: "hint", text: this._t("chartEmpty") }));
      return card;
    }
    const percent = (v) => `${this._num((v / total) * 100, 1)} %`;
    let offset = 0;
    const slices = items.map((it) => {
      const part = (it.v / total) * 100;
      const slice = svg("circle", {
        cx: "21", cy: "21", r: "15.9155", fill: "none", stroke: it.color, "stroke-width": "6",
        "stroke-dasharray": `${Math.max(part - 0.4, 0.01)} ${100 - Math.max(part - 0.4, 0.01)}`, "stroke-dashoffset": String(-offset),
        transform: "rotate(-90 21 21)",
      });
      offset += part;
      return slice;
    });
    const ring = svg("svg", { viewBox: "0 0 42 42", class: "cs-ring", role: "img", "aria-label": this._t("chartTitle") },
      svg("circle", { cx: "21", cy: "21", r: "15.9155", fill: "none", style: "stroke: rgba(var(--w),.06)", "stroke-width": "6" }), slices);
    const kinds = [["chartMetered", (c) => METERED.has(c.sparte)], ["chartFlat", (c) => !METERED.has(c.sparte)]]
      .map(([label, test]) => ({ label, v: items.filter((it) => test(it)).reduce((s, it) => s + it.v, 0) })).filter((k) => k.v > 0);
    card.appendChild(h("div", { class: "chart-body" },
      h("div", { class: "cs-ringwrap" }, ring,
        h("div", { class: "cs-mid" }, h("b", { text: this._money(total, currency, { digits: 0 }) }))),
      h("ul", { class: "cs-legend" }, items.map((it) => h("li", {},
        h("span", { class: "cs-dot", style: `background:${it.color}` }),
        h("span", { class: "nm", text: it.n > 1 ? `${it.label} (${it.n})` : it.label }),
        h("span", { class: "val", text: this._money(it.v, it.currency, { digits: 2 }) }),
        h("span", { class: "pct", text: percent(it.v) }))))));
    if (kinds.length > 1) {
      card.appendChild(h("div", { class: "kindbar" },
        h("div", { class: "cs-split" }, kinds.map((k, i) => h("span", { class: `k${i}`, style: `width:${(k.v / total) * 100}%` }))),
        h("div", { class: "kindleg" }, kinds.map((k, i) => h("span", {}, h("span", { class: `cs-dot k${i}` }),
          `${this._t(k.label)} ${percent(k.v)}`)))));
    }
    return card;
  }

  _costs() {
    const contracts = this._live;
    const loans = this._loans;
    const currency = (contracts[0] || loans[0] || {}).currency || "EUR";
    const tone = (v) => (isNum(v) ? (v < 0 ? "red-t" : v > 0 ? "green-t" : "") : "muted-t");
    const cell = (label, value, cls) => h("td", { "data-label": label, class: [cls, value === "–" ? "empty" : ""].filter(Boolean).join(" ") || null, text: value });
    const sum = (list, key) => list.reduce((s, c) => s + (isNum(c[key]) ? c[key] : 0), 0);
    const sumCost = (list) => list.reduce((s, c) => s + (this._costSoFar(c) || 0), 0);
    const money = (v, digits = 2, o = {}) => this._money(v, currency, { digits, ...o });
    const metered = contracts.filter((c) => METERED.has(c.sparte));
    const flat = contracts.filter((c) => !METERED.has(c.sparte));
    const name = (c) => h("td", {}, c.title, h("span", { class: "t2", text: c.anbieter || this._t("sparte")[c.sparte] || c.sparte }));

    // metered contracts: what the consumption cost, the balance against the instalments, the forecast
    const meteredRows = [];
    for (const c of metered) {
      meteredRows.push(h("tr", {},
        name(c),
        this._fromInstalments(c)
          ? h("td", { "data-label": this._t("colCost") }, this._money(this._costSoFar(c), c.currency, { digits: 2 }),
            h("span", { class: "t2", text: this._t("fromInstalments") }))
          : cell(this._t("colCost"), this._money(this._costSoFar(c), c.currency, { digits: 2 })),
        cell(this._t("colBalance"), this._money(c.guthaben_bisher, c.currency, { sign: true, digits: 2 }), tone(c.guthaben_bisher)),
        cell(this._t("colForecast"), this._money(c.prognose_real, c.currency, { sign: true }), tone(c.prognose_real)),
        cell(this._t("colInstalment"), this._money(c.abschlag, c.currency, { digits: 2 })),
        this._recommendedCell(c)));
      if (isNum(c.kosten_ht_bisher) || isNum(c.kosten_nt_bisher)) {
        for (const [label, key, vkey] of [["highTariff", "kosten_ht_bisher", "verbrauch_bisher_ht"], ["lowTariff", "kosten_nt_bisher", "verbrauch_bisher_nt"]]) {
          meteredRows.push(h("tr", { class: "sub" },
            h("td", { text: `${this._t(label)} · ${this._num(c[vkey], 0)} ${this._unit(c)}` }),
            cell(this._t("colCost"), this._money(c[key], c.currency, { digits: 2 }))));
        }
      }
    }
    meteredRows.push(h("tr", { class: "total" },
      h("td", { text: this._t("total") }),
      cell(this._t("colCost"), money(sumCost(metered))),
      cell(this._t("colBalance"), money(sum(metered, "guthaben_bisher"), 2, { sign: true }), tone(sum(metered, "guthaben_bisher"))),
      cell(this._t("colForecast"), money(sum(metered, "prognose_real"), 0, { sign: true }), tone(sum(metered, "prognose_real"))),
      cell(this._t("colInstalment"), money(sum(metered, "abschlag"))),
      h("td", { text: "" })));

    // flat-rate contracts: no consumption, so the cost so far is what was due, and the term.
    // Cost so far (2nd column) and instalment (5th column) sit exactly below those of the table above.
    const flatRows = flat.map((c) => h("tr", {},
      name(c),
      cell(this._t("colCost"), this._money(this._costSoFar(c), c.currency, { digits: 2 })),
      cell(this._t("remaining"), isNum(c.restlaufzeit) ? this._t("daysLeft", { n: c.restlaufzeit }) : "–"),
      cell(this._t("contractEnd"), c.ende ? this._date(c.ende) : this._t("noEnd")),
      h("td", { "data-label": this._t("colInstalment") }, this._money(c.abschlag, c.currency, { digits: 2 }),
        isNum(c.abschlag_jahr) ? h("span", { class: "t2", text: this._t("perYearShort", { v: this._money(c.abschlag_jahr, c.currency) }) }) : null),
      h("td", { class: "empty" })));
    flatRows.push(h("tr", { class: "total" },
      h("td", { text: this._t("total") }),
      cell(this._t("colCost"), money(sumCost(flat))),
      h("td", { text: "" }), h("td", { text: "" }),
      cell(this._t("colInstalment"), money(sum(flat, "abschlag"))),
      h("td", { text: "" })));

    const group = (key, list, heads, rows) => (list.length ? h("section", { class: "group" },
      h("h3", { class: "group-title" }, h("span", { text: this._t(key) }), h("span", { class: "count", text: String(list.length) })),
      h("table", {},
        // both tables share one column grid (the 6th column is empty in the flat-rate table)
        h("colgroup", {}, GRID.map((width) => h("col", { style: `width:${width}` }))),
        h("thead", {}, h("tr", {}, heads.map((label) => h("th", { text: label })))),
        h("tbody", {}, rows))) : null);

    return h(
      "div", {},
      contracts.length ? this._costChart(contracts) : null,
      group("groupMetered", metered, [
        this._t("colContract"), this._t("colCost"), this._t("colBalance"),
        this._t("colForecast"), this._t("colInstalment"), this._t("colRecommended"),
      ], meteredRows),
      group("groupFlat", flat, [
        this._t("colContract"), this._t("colCost"), this._t("remaining"), this._t("contractEnd"), this._t("colInstalment"), "",
      ], flatRows),
      contracts.length ? h("table", { class: "grand-table" },
        h("colgroup", {}, GRID.map((width) => h("col", { style: `width:${width}` }))),
        h("tbody", {}, h("tr", { class: "grandrow" },
          h("td", { text: this._t("grandTotal") }),
          cell(this._t("colCost"), money(sumCost(contracts))),
          h("td", { text: "" }), h("td", { text: "" }),
          cell(this._t("colInstalment"), money(sum(contracts, "abschlag"))),
          h("td", { text: "" })))) : null,
      contracts.length ? h("div", { class: "hint", text: this._t("costsHint") }) : null,
      this._loanTable(loans)
    );
  }


  /* History: archived contracts and the earlier terms of every contract. */
};
