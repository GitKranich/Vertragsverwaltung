/* Changes inside a contract: renew it, one-time items and price changes (administrators only).
 * Part of the Tariffy panel: tariffy-panel.js loads it with its own version and copies the methods onto the
 * panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, icon, isNum, METERED, INTERVAL_MONTHS }) => class {
  /* ----- renew a flat-rate contract: new start, end and amount; the old term goes to the history ----- */

  _isoShift(iso, { days = 0, years = 0 }) {
    const date = this._parse(iso);
    if (!date) return "";
    date.setFullYear(date.getFullYear() + years);
    date.setDate(date.getDate() + days);
    const two = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}`;
  }

  /* follow-up financing: the remaining debt becomes the new loan amount */
  _openRenewLoan(c) {
    this._closeFlow();
    const spec = (name, kind, label, extra = {}) => ({
      name, kind, label, required: false, min: null, domains: [], custom: false, options: [], initial: "", ...extra,
    });
    const zahlweise = c.zahlweise && INTERVAL_MONTHS[c.zahlweise] ? c.zahlweise : "monthly";
    const text = (v) => (isNum(v) ? String(v) : "");
    this._dlg = {
      mode: "renew", kredit: true, stage: "form", id: c.entry_id, name: c.title, contract: c, flowId: null, localize: () => "",
      fields: [
        spec("beginn", "date", this._t("tf_beginn"), { required: true }),
        spec("kredit_summe", "number", this._t("lf_summe"), { required: true, min: 0.01 }),
        spec("sollzins", "number", this._t("lf_soll"), { min: 0 }),
        spec("effektivzins", "number", this._t("lf_effektiv"), { min: 0 }),
        spec("laufzeit_monate", "number", this._t("lf_laufzeit"), { required: true, min: 1 }),
        spec("zahlweise", "select", this._t("rf_zahlweise"), { options: Object.keys(INTERVAL_MONTHS).map((k) => ({ value: k, label: this._t("interval")[k] })) }),
        spec("kredit_rate", "number", this._t("lf_rate"), { min: 0 }),
        spec("schlussrate", "number", this._t("lf_schluss"), { min: 0 }),
        spec("erste_zahlung", "date", this._t("rf_erste")),
      ],
      values: {
        beginn: c.ende || "", kredit_summe: text(c.kredit_restschuld), sollzins: text(c.kredit_sollzins),
        effektivzins: text(c.kredit_effektivzins), laufzeit_monate: text(c.laufzeit_monate), zahlweise,
        kredit_rate: "", schlussrate: "", erste_zahlung: "",
      },
      fieldErrors: {}, message: null,
    };
    this._renderRenewDialog();
  }

  _openRenew(c) {
    if (c.sparte === "loan") { this._openRenewLoan(c); return; }
    this._closeFlow();
    const spec = (name, kind, label, extra = {}) => ({
      name, kind, label, required: false, min: null, domains: [], custom: false, options: [], initial: "", ...extra,
    });
    const zahlweise = c.zahlweise && INTERVAL_MONTHS[c.zahlweise] ? c.zahlweise : "monthly";
    const dlg = {
      mode: "renew", stage: "form", id: c.entry_id, name: c.title, contract: c, flowId: null, localize: () => "",
      fields: [
        spec("beginn", "date", this._t("tf_beginn"), { required: true }),
        spec("ende", "date", this._t("tf_ende"), { required: true }),
        spec("zahlweise", "select", this._t("rf_zahlweise"), { options: Object.keys(INTERVAL_MONTHS).map((k) => ({ value: k, label: this._t("interval")[k] })) }),
        spec("abschlag", "number", this._t("rf_abschlag"), { required: true, min: 0.01 }),
        spec("erste_zahlung", "date", this._t("rf_erste")),
      ],
      values: {
        beginn: this._isoShift(c.ende, { days: 1 }), ende: this._isoShift(c.ende, { years: 1 }), zahlweise,
        abschlag: String(isNum(c.abschlag_je_zahlung) ? c.abschlag_je_zahlung : (c.abschlag ?? "")),
      },
      fieldErrors: {}, message: null,
    };
    this._dlg = dlg;
    this._renderRenewDialog();
  }

  /* "5 % dearer": what the new amount means compared with the current one */
  _renewChange(dlg) {
    const amount = Number(String(dlg.values.abschlag ?? "").replace(",", "."));
    const months = INTERVAL_MONTHS[dlg.values.zahlweise] || 1;
    const old = dlg.contract.abschlag;
    if (!Number.isFinite(amount) || amount <= 0 || !isNum(old) || old <= 0) return "";
    const monthly = amount / months;
    const percent = ((monthly - old) / old) * 100;
    if (Math.abs(percent) < 0.05) return this._t("renewSame");
    return this._t(percent > 0 ? "renewUp" : "renewDown", {
      p: this._num(Math.abs(percent), 1), n: this._money(monthly, dlg.contract.currency, { digits: 2 }), o: this._money(old, dlg.contract.currency, { digits: 2 }),
    });
  }

  async _saveRenew() {
    const dlg = this._dlg;
    const { values, errors } = this._collectFlow(dlg);
    dlg.fieldErrors = errors;
    dlg.message = null;
    if (Object.keys(errors).length) {
      this._renderRenewDialog();
      return;
    }
    dlg.stage = "saving";
    this._renderRenewDialog();
    try {
      await this._hass.callWS({ type: "tariffy/renew", entry_id: dlg.id, values });
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.message = this._termError(err);
      this._renderRenewDialog();
      return;
    }
    if (this._dlg === dlg) this._closeFlow();
    this._render();
  }

  _renderRenewDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving";
    const body = [
      h("h2", { id: "dlg-title", text: this._t(dlg.kredit ? "loanRenewTitle" : "renewTitle") }),
      h("p", { class: "hint", text: dlg.name }),
      h("p", { class: "hint", text: this._t(dlg.kredit ? "loanRenewHint" : "renewHint") }),
      h("div", { class: "fields" }, dlg.fields.map((f) => {
        const error = dlg.fieldErrors[f.name];
        return h("label", { class: `fld${error ? " bad" : ""}`, for: `f-${f.name}` },
          h("span", { class: "lbl", text: f.label }), this._fieldInput(dlg, f),
          f.name === "abschlag" ? h("div", { class: "fnote", "data-note": "change", text: this._renewChange(dlg) }) : null,
          error ? h("div", { class: "ferr", text: error }) : null);
      })),
    ];
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    body.push(h("div", { class: "actions" },
      h("button", { class: "btn go", type: "button", disabled: busy ? "" : null, text: this._t(busy ? "dlgSaving" : "renewGo"), onclick: () => this._saveRenew() }),
      h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() })));
    this._mountDialog(dlg, body, busy);
    const el = this._dlgEl;
    // a finished edit of the amount or the interval updates the note under the amount
    el.onchange = () => {
      const note = el.querySelector('[data-note="change"]');
      if (note) note.textContent = this._renewChange(dlg);
    };
    el.oninput = el.onchange;
    if (!dlg.focused) {
      dlg.focused = true;
      const first = el.querySelector("input, select");
      if (first) first.focus();
    }
  }

  /* ----- one-time items of a contract: bonus (+) or bill (-), administrators only ----- */
  _openOnce(c, index) {
    this._closeFlow();
    const spec = (name, kind, label, extra = {}) => ({
      name, kind, label, required: false, min: null, domains: [], custom: false, options: [], initial: "", ...extra,
    });
    const item = index === null ? {} : (c.einmalposten || [])[index] || {};
    this._dlg = {
      mode: "once", stage: "form", id: c.entry_id, name: c.title, index, flowId: null, localize: () => "", asking: null,
      fields: [
        spec("bezeichnung", "text", this._t("onceName"), { required: true }),
        spec("betrag", "number", this._t("onceAmount"), { required: true }),
        spec("datum", "date", this._t("onceDate")),
      ],
      values: { bezeichnung: item.bezeichnung || "", betrag: isNum(item.betrag) ? String(item.betrag) : "", datum: item.datum || "" },
      fieldErrors: {}, message: null,
    };
    this._renderOnceDialog();
  }

  async _saveOnce() {
    const dlg = this._dlg;
    const { values, errors } = this._collectFlow(dlg);
    dlg.fieldErrors = errors;
    dlg.message = null;
    if (Object.keys(errors).length) { this._renderOnceDialog(); return; }
    await this._callOnce(dlg, { type: "tariffy/einmal_save", entry_id: dlg.id, values, ...(dlg.index === null ? {} : { index: dlg.index }) });
  }

  async _deleteOnce() {
    const dlg = this._dlg;
    await this._callOnce(dlg, { type: "tariffy/einmal_delete", entry_id: dlg.id, index: dlg.index });
  }

  async _callOnce(dlg, message) {
    dlg.stage = "saving";
    this._renderOnceDialog();
    try {
      await this._hass.callWS(message);
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.asking = null;
      dlg.message = this._termError(err);
      this._renderOnceDialog();
      return;
    }
    if (this._dlg === dlg) this._closeFlow();
    this._render();
  }

  _renderOnceDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving";
    const body = [
      h("h2", { id: "dlg-title", text: this._t(dlg.index === null ? "onceTitleAdd" : "onceTitleEdit") }),
      h("p", { class: "hint", text: dlg.name }),
      h("p", { class: "hint", text: this._t("onceHint") }),
      h("div", { class: "fields" }, dlg.fields.map((f) => {
        const error = dlg.fieldErrors[f.name];
        return h("label", { class: `fld${error ? " bad" : ""}`, for: `f-${f.name}` },
          h("span", { class: "lbl", text: f.label }), this._fieldInput(dlg, f),
          error ? h("div", { class: "ferr", text: error }) : null);
      })),
    ];
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    const buttons = [];
    if (dlg.asking === "delete") {
      buttons.push(h("div", { class: "ask" },
        h("span", { text: this._t("onceDeleteAsk") }),
        h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("onceDeleteYes"), onclick: () => this._deleteOnce() }),
        h("button", { class: "btn", type: "button", text: this._t("actCancel"), onclick: () => { dlg.asking = null; this._renderOnceDialog(); } })));
    } else {
      buttons.push(h("button", { class: "btn go", type: "button", disabled: busy ? "" : null,
        text: this._t(busy ? "dlgSaving" : "dlgSave"), onclick: () => this._saveOnce() }));
      buttons.push(h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() }));
      if (dlg.index !== null) {
        buttons.push(h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("onceDelete"),
          onclick: () => { dlg.asking = "delete"; this._renderOnceDialog(); } }));
      }
    }
    body.push(h("div", { class: "actions" }, buttons));
    this._mountDialog(dlg, body, busy);
    if (!dlg.focused) {
      dlg.focused = true;
      const first = this._dlgEl.querySelector("input, select");
      if (first) first.focus();
    }
  }

  /* the "one-time items" block of a contract */
  _onceSection(c) {
    const items = c.einmalposten || [];
    if (!items.length && !this._isAdmin) return null;
    const rows = items.map((item, index) => h("div", { class: "row once-row" },
      h("span", {}, item.bezeichnung, item.datum ? h("span", { class: "t2", text: this._date(item.datum) }) : null),
      h("span", { class: item.betrag < 0 ? "red-t" : "green-t", text: this._money(item.betrag, c.currency, { sign: true, digits: 2 }) }),
      this._isAdmin ? h("button", {
        class: "icon-btn", type: "button", title: this._t("onceEdit"), "aria-label": this._t("onceEdit"),
        onclick: () => this._openOnce(c, index),
      }, icon("edit")) : null));
    return h("div", { class: "sec" }, h("h4", { text: this._t("secOnce") }), rows,
      items.length > 1 && isNum(c.einmal_summe)
        ? h("div", { class: "row" }, h("span", { text: this._t("onceSum") }),
          h("span", { text: this._money(c.einmal_summe, c.currency, { sign: true, digits: 2 }) })) : null,
      this._isAdmin ? h("div", { class: "once-add" }, h("button", {
        class: "btn", type: "button", onclick: () => this._openOnce(c, null),
      }, icon("plus"), this._t("onceAdd"))) : null);
  }

  /* ----- price changes within a running electricity/gas contract, administrators only ----- */
  _openPreis(c, index) {
    this._closeFlow();
    const spec = (name, kind, label, extra = {}) => ({
      name, kind, label, required: false, min: null, domains: [], custom: false, options: [], initial: "", ...extra,
    });
    const item = index === null ? {} : (c.preisaenderungen || []).find((p) => p.index === index) || {};
    const str = (v) => (isNum(v) ? String(v) : "");
    // Versicherung, Internet, ...: neuer Betrag pro Zahlung statt Arbeitspreis/Grundpreis/Abschlag
    const flat = !METERED.has(c.sparte);
    const months = INTERVAL_MONTHS[c.zahlweise] || 1;
    this._dlg = {
      mode: "preis", stage: "form", id: c.entry_id, name: c.title, index, flowId: null, localize: () => "", asking: null,
      flat,
      fields: flat ? [
        spec("datum", "date", this._t("priceDate"), { required: true }),
        spec("betrag", "number", `${this._t("rf_abschlag")} · ${this._t("interval")[c.zahlweise] || this._t("interval").monthly}`, { min: 0, required: true }),
      ] : [
        spec("datum", "date", this._t("priceDate"), { required: true }),
        spec("arbeitspreis", "number", this._t("priceUnit"), { min: 0 }),
        spec("grundpreis", "number", this._t("priceBase"), { min: 0 }),
        spec("abschlag", "number", this._t("priceInstalment"), { min: 0 }),
        spec("zaehlerstand", "number", this._t("priceMeter"), { min: 0 }),
      ],
      values: flat ? {
        datum: item.datum || "", betrag: isNum(item.abschlag) ? String(Math.round(item.abschlag * months * 100) / 100) : "",
      } : {
        datum: item.datum || "", arbeitspreis: str(item.arbeitspreis), grundpreis: str(item.grundpreis),
        abschlag: str(item.abschlag), zaehlerstand: str(item.zaehlerstand),
      },
      fieldErrors: {}, message: null,
    };
    this._renderPreisDialog();
  }

  async _savePreis() {
    const dlg = this._dlg;
    const { values, errors } = this._collectFlow(dlg);
    dlg.fieldErrors = errors;
    dlg.message = null;
    if (Object.keys(errors).length) { this._renderPreisDialog(); return; }
    if (!["arbeitspreis", "grundpreis", "abschlag", "betrag"].some((k) => k in values)) {
      dlg.message = this._t("termErr_invalid_preis_leer");
      this._renderPreisDialog();
      return;
    }
    await this._callPreis(dlg, { type: "tariffy/preis_save", entry_id: dlg.id, values, ...(dlg.index === null ? {} : { index: dlg.index }) });
  }

  async _deletePreis() {
    const dlg = this._dlg;
    await this._callPreis(dlg, { type: "tariffy/preis_delete", entry_id: dlg.id, index: dlg.index });
  }

  async _callPreis(dlg, message) {
    dlg.stage = "saving";
    this._renderPreisDialog();
    try {
      await this._hass.callWS(message);
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.asking = null;
      dlg.message = this._termError(err);
      this._renderPreisDialog();
      return;
    }
    if (this._dlg === dlg) this._closeFlow();
    this._render();
  }

  _renderPreisDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving";
    const body = [
      h("h2", { id: "dlg-title", text: this._t(dlg.index === null ? "priceTitleAdd" : "priceTitleEdit") }),
      h("p", { class: "hint", text: dlg.name }),
      h("p", { class: "hint", text: this._t(dlg.flat ? "priceHintFlat" : "priceHint") }),
      h("div", { class: "fields" }, dlg.fields.map((f) => {
        const error = dlg.fieldErrors[f.name];
        return h("label", { class: `fld${error ? " bad" : ""}`, for: `f-${f.name}` },
          h("span", { class: "lbl", text: f.label }), this._fieldInput(dlg, f),
          error ? h("div", { class: "ferr", text: error }) : null);
      })),
    ];
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    const buttons = [];
    if (dlg.asking === "delete") {
      buttons.push(h("div", { class: "ask" },
        h("span", { text: this._t("priceDeleteAsk") }),
        h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("onceDeleteYes"), onclick: () => this._deletePreis() }),
        h("button", { class: "btn", type: "button", text: this._t("actCancel"), onclick: () => { dlg.asking = null; this._renderPreisDialog(); } })));
    } else {
      buttons.push(h("button", { class: "btn go", type: "button", disabled: busy ? "" : null,
        text: this._t(busy ? "dlgSaving" : "dlgSave"), onclick: () => this._savePreis() }));
      buttons.push(h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() }));
      if (dlg.index !== null) {
        buttons.push(h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("priceDelete"),
          onclick: () => { dlg.asking = "delete"; this._renderPreisDialog(); } }));
      }
    }
    body.push(h("div", { class: "actions" }, buttons));
    this._mountDialog(dlg, body, busy);
    if (!dlg.focused) {
      dlg.focused = true;
      const first = this._dlgEl.querySelector("input, select");
      if (first) first.focus();
    }
  }

  _preisSection(c) {
    const items = c.preisaenderungen || [];
    const change = c.abschlag_aenderung;
    if (!items.length && !change && !(this._isAdmin && c.preisaenderung_moeglich)) return null;
    const pu = this._priceUnit(c);
    const money = (v) => this._money(v, c.currency, { digits: 2 });
    const flat = !METERED.has(c.sparte);
    const months = INTERVAL_MONTHS[c.zahlweise] || 1;
    const describe = (p) => flat ? (isNum(p.abschlag)
      ? `${money(p.abschlag * months)} ${this._t("interval")[c.zahlweise] || this._t("interval").monthly}` : "") : [
      isNum(p.arbeitspreis) ? this._price(p.arbeitspreis, c.currency, pu) : null,
      isNum(p.grundpreis) ? `${money(p.grundpreis)} ${this._t("perMonth")}` : null,
      isNum(p.abschlag) ? `${this._t("instalment")} ${money(p.abschlag)}` : null,
    ].filter(Boolean).join(" · ");
    const status = { aktiv: "priceActive", kuenftig: "priceFuture", ungueltig: "priceInvalid" };
    const rows = [];
    if (items.length && c.preis_basis) {
      rows.push(h("div", { class: "row once-row" },
        h("span", {}, this._t("priceFromStart"), h("span", { class: "t2", text: describe(c.preis_basis) })), h("span", {})));
    }
    items.forEach((p) => rows.push(h("div", { class: "row once-row" },
      h("span", {}, p.datum ? this._date(p.datum) : "–", h("span", { class: "t2", text: describe(p) })),
      h("span", { class: p.status === "ungueltig" ? "red-t" : p.status === "aktiv" ? "green-t" : "", text: status[p.status] ? this._t(status[p.status]) : "" }),
      this._isAdmin ? h("button", {
        class: "icon-btn", type: "button", title: this._t("priceEdit"), "aria-label": this._t("priceEdit"),
        onclick: () => this._openPreis(c, p.index),
      }, icon("edit")) : null)));
    if (change) {
      const asking = this._fixAsk === c.entry_id;
      rows.push(h("div", { class: "row once-row" },
        h("span", {}, this._t("abschlagBefore"),
          h("span", { class: "t2", text: `${money(change.vorher)} ${this._t("abschlagUntil", { d: this._date(change.datum) })}` }),
          this._isAdmin ? h("span", { class: "t2", text: this._t("abschlagFixHint") }) : null),
        h("span", {}),
        this._isAdmin ? (asking
          ? h("span", {},
            h("button", { class: "btn red", type: "button", text: this._t("abschlagFixYes"), onclick: async () => {
              this._fixAsk = null;
              try { await this._hass.callWS({ type: "tariffy/abschlag_korrektur", entry_id: c.entry_id }); } catch (err) { /* the next snapshot shows the state */ }
              this._render();
            } }),
            h("button", { class: "btn", type: "button", text: this._t("actCancel"), onclick: () => { this._fixAsk = null; this._render(); } }))
          : h("button", { class: "btn", type: "button", title: this._t("abschlagFixAsk"), text: this._t("abschlagFix"),
            onclick: () => { this._fixAsk = c.entry_id; this._render(); } })) : null));
    }
    return h("div", { class: "sec" }, h("h4", { text: this._t("secPriceChanges") }), rows,
      this._isAdmin && c.preisaenderung_moeglich ? h("div", { class: "once-add" }, h("button", {
        class: "btn", type: "button", onclick: () => this._openPreis(c, null),
      }, icon("plus"), this._t("priceAdd"))) : null);
  }
};
