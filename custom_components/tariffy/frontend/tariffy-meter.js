/* Meter readings: entered by hand, imported, corrected; and the outdoor meters of a water contract.
 * Part of the Tariffy panel: tariffy-panel.js loads it with its own version and copies the methods onto the
 * panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, icon, isNum, I18N }) => class {
  /* ----- meter reading entered by hand (number helper behind a template sensor) ----- */
  _meterState(c) {
    const st = c.zaehlerstand_eingabe && this._hass && this._hass.states[c.zaehlerstand_eingabe];
    if (!st) return null;
    const value = Number(st.state);
    return {
      value: Number.isFinite(value) && value > 0 ? value : null,
      when: st.last_changed || st.last_updated,
      unit: (st.attributes && st.attributes.unit_of_measurement) || this._unit(c),
      max: st.attributes && isNum(st.attributes.max) ? st.attributes.max : null,
      step: st.attributes && st.attributes.step,
    };
  }

  _meterSection(c) {
    if (!c.zaehlerstand_eingabe) return this._noMeterSection(c);
    const m = this._meterState(c);
    const text = !m ? this._t("meterMissing", { e: c.zaehlerstand_eingabe })
      : m.value === null ? this._t("meterNone")
        : `${this._num(m.value, 3)} ${m.unit} · ${this._date(String(m.when).slice(0, 10))}`;
    return h("div", { class: "sec" }, h("h4", { text: this._t("secMeter") }),
      h("div", { class: "row once-row" },
        h("span", {}, this._t("meterLast"), h("span", { class: "t2", text })),
        h("span", {}),
        m ? h("button", { class: "btn", type: "button", onclick: () => this._openMeter(c) }, icon("meter"), this._t("meterRecord")) : null),
      this._isAdmin ? h("div", { class: "once-add" }, h("button", {
        class: "btn", type: "button", onclick: () => this._openImport(c),
      }, icon("plus"), this._t("impOpen")), m && m.value !== null ? h("button", {
        class: "btn", type: "button", onclick: () => this._openFix(c),
      }, icon("meter"), this._t("fixOpen")) : null) : null);
  }

  /* ----- no meter yet: create a number helper and a template sensor in Home Assistant (administrators) ----- */
  _noMeterSection(c) {
    if (!this._isAdmin || c.hat_zaehler || !["electricity", "gas", "water"].includes(c.sparte)) return null;
    return h("div", { class: "sec" }, h("h4", { text: this._t("secMeter") }),
      h("p", { class: "hint", text: this._t("zNoMeter") }),
      h("div", { class: "once-add" }, h("button", { class: "btn", type: "button", onclick: () => this._openZaehler(c) },
        icon("plus"), this._t("zCreate"))));
  }

  async _openZaehler(c) {
    this._closeFlow();
    let plan = null;
    let problem = null;
    try {
      plan = await this._hass.callWS({ type: "tariffy/zaehler_vorschlag", entry_id: c.entry_id });
    } catch (err) {
      problem = this._zError(err);
      plan = { name: "", ablesung: "", unit: this._unit(c), step: 0.001 };
    }
    this._dlg = {
      mode: "zaehler", stage: "form", id: c.entry_id, title: c.title, plan, flowId: null, localize: () => "", asking: null,
      fields: [
        { name: "name", kind: "string", label: this._t("zName"), required: true, min: null, domains: [], custom: false, options: [], initial: plan.name },
        { name: "stand", kind: "number", label: `${this._t("zStart")} (${plan.unit})`, required: true, min: 0, domains: [], custom: false, options: [], initial: "" },
      ],
      values: { name: plan.name, stand: "" }, fieldErrors: {}, message: problem, blocked: Boolean(problem),
    };
    this._renderZaehlerDialog();
  }

  _zError(err) {
    const code = String((err && (err.code || (err.body && err.body.code))) || "");
    const key = `zErr_${code}`;
    return I18N[this._lang][key] || I18N.en[key] ? this._t(key) : `${this._t("zErr")}${err && err.message ? ` (${err.message})` : ""}`;
  }

  async _saveZaehler() {
    const dlg = this._dlg;
    const { values, errors } = this._collectFlow(dlg);
    dlg.fieldErrors = errors;
    dlg.message = null;
    const name = String(values.name || "").trim();
    if (!errors.name && (!name || name.length > 60)) dlg.fieldErrors.name = this._t("zErr_name");
    if (!errors.stand && (!isNum(values.stand) || values.stand <= 0)) dlg.fieldErrors.stand = this._t("meterInvalid");
    if (Object.keys(dlg.fieldErrors).length) { this._renderZaehlerDialog(); return; }
    dlg.stage = "saving";
    this._renderZaehlerDialog();
    const plan = dlg.plan;
    let helper = null;
    try {
      // the number helper, the same way the helper settings of Home Assistant create it
      helper = await this._hass.callWS({
        type: "input_number/create", name: `${name} ${plan.ablesung}`, min: 0, max: 999999999, step: plan.step,
        mode: "box", unit_of_measurement: plan.unit, initial: values.stand, icon: "mdi:counter",
      });
      // the template sensor with the statistics, and both assigned to the contract
      await this._hass.callWS({ type: "tariffy/zaehler_anlegen", entry_id: dlg.id, eingabe: helper.id, name });
    } catch (err) {
      if (helper && helper.id) { // do not leave half a meter behind
        try { await this._hass.callWS({ type: "input_number/delete", input_number_id: helper.id }); } catch (_e) { /* stays a helper */ }
      }
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.message = this._zError(err);
      this._renderZaehlerDialog();
      return;
    }
    if (this._dlg !== dlg) return;
    dlg.stage = "done";
    dlg.done = name;
    this._renderZaehlerDialog();
  }

  _renderZaehlerDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving";
    if (dlg.stage === "done") {
      this._mountDialog(dlg, [
        h("h2", { id: "dlg-title", text: this._t("zCreate") }),
        h("p", { class: "zok", role: "status", text: this._t("zDone", { name: dlg.done }) }),
        h("p", { class: "hint", text: this._t("zStartHint") }),
        h("div", { class: "actions" }, h("button", { class: "btn go", type: "button", text: this._t("dlgClose"), onclick: () => this._closeFlow() })),
      ], false);
      const button = this._dlgEl.querySelector(".actions .btn");
      if (button) button.focus();
      return;
    }
    const name = String(dlg.values.name || dlg.plan.name).trim();
    const body = [
      h("h2", { id: "dlg-title", text: this._t("zCreate") }),
      h("p", { class: "hint", text: dlg.title }),
      h("p", { class: "hint", text: this._t("zHint", { input: `${name} ${dlg.plan.ablesung}`, sensor: name }) }),
      h("div", { class: "fields" }, dlg.fields.map((f) => {
        const error = dlg.fieldErrors[f.name];
        return h("label", { class: `fld${error ? " bad" : ""}`, for: `f-${f.name}` },
          h("span", { class: "lbl", text: f.label }), this._fieldInput(dlg, f),
          error ? h("div", { class: "ferr", text: error }) : null);
      })),
      h("p", { class: "hint", text: this._t("zStartHint") }),
    ];
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    body.push(h("div", { class: "actions" },
      dlg.blocked ? null : h("button", { class: "btn go", type: "button", disabled: busy ? "" : null, text: this._t(busy ? "dlgSaving" : "zCreateGo"), onclick: () => this._saveZaehler() }),
      h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() })));
    this._mountDialog(dlg, body, busy);
    if (!dlg.focused) {
      dlg.focused = true;
      const first = this._dlgEl.querySelector("[name=stand]");
      if (first) first.focus();
    }
  }

  /* ----- import historical meter readings into the sensor's long-term statistics ----- */
  _openImport(c) {
    this._closeFlow();
    this._dlg = { mode: "import", stage: "form", id: c.entry_id, name: c.title, text: "", report: null, message: null, asking: null,
      fields: [], values: {}, fieldErrors: {} };
    this._renderImportDialog();
  }

  async _runImport(pruefen) {
    const dlg = this._dlg;
    dlg.message = null;
    if (!dlg.text.trim()) { dlg.message = this._t("impErr_keine_werte"); this._renderImportDialog(); return; }
    dlg.stage = "saving";
    this._renderImportDialog();
    try {
      const report = await this._hass.callWS({ type: "tariffy/zaehler_import", entry_id: dlg.id, text: dlg.text, pruefen });
      if (this._dlg !== dlg) return;
      dlg.report = report;
      dlg.stage = report.importiert ? "done" : "form";
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.report = null;
      const code = err && err.code;
      dlg.message = code && this._t(`impErr_${code}`) !== `impErr_${code}` ? this._t(`impErr_${code}`) : this._errorText(err);
    }
    this._renderImportDialog();
  }

  _renderImportDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving";
    const r = dlg.report;
    const body = [
      h("h2", { id: "dlg-title", text: this._t("impTitle") }),
      h("p", { class: "hint", text: dlg.name }),
      h("p", { class: "hint", text: this._t("impHint") }),
    ];
    if (dlg.stage !== "done") {
      const area = h("textarea", { id: "f-import", rows: "8", spellcheck: "false", placeholder: "01.01.2026;72,0\n01.02.2026;80,5",
        style: "font-family:monospace",
        oninput: (ev) => { dlg.text = ev.target.value; dlg.report = null; } });
      area.value = dlg.text;
      const file = h("input", { type: "file", accept: ".csv,.txt,text/csv,text/plain", onchange: async (ev) => {
        const f = ev.target.files && ev.target.files[0];
        if (!f) return;
        dlg.text = await f.text();
        dlg.report = null;
        this._renderImportDialog();
      } });
      body.push(h("label", { class: "fld", for: "f-import" }, h("span", { class: "lbl", text: this._t("impText") }), area));
      body.push(h("label", { class: "fld" }, h("span", { class: "lbl", text: this._t("impFile") }), file));
    }
    if (r) {
      const unit = r.einheit || "";
      const lines = [
        this._t(r.importiert ? "impDone" : "impPreview", { n: String(r.anzahl) }),
        this._t("impRange", { a: this._date(r.von.slice(0, 10)), b: this._date(r.bis.slice(0, 10)) }),
        this._t("impUse", { v: `${this._num(r.verbrauch, 3)} ${unit}`, a: this._num(r.erster_stand, 3), b: this._num(r.letzter_stand, 3) }),
        r.zaehlerwechsel ? this._t("impSwaps", { n: String(r.zaehlerwechsel) }) : null,
        r.uebersprungen ? this._t("impSkipped", { n: String(r.uebersprungen) }) : null,
        r.nicht_gelesen_anzahl ? this._t("impUnread", { n: String(r.nicht_gelesen_anzahl), l: r.nicht_gelesen.join(", ") }) : null,
      ].filter(Boolean);
      body.push(h("div", { class: "hint" }, lines.map((t) => h("div", { text: t }))));
    }
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    const buttons = dlg.stage === "done"
      ? [h("button", { class: "btn go", type: "button", text: this._t("impClose"), onclick: () => this._closeFlow() })]
      : [
        h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("impCheck"), onclick: () => this._runImport(true) }),
        r && r.anzahl ? h("button", { class: "btn go", type: "button", disabled: busy ? "" : null, text: this._t("impRun"), onclick: () => this._runImport(false) }) : null,
        h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() }),
      ];
    body.push(h("div", { class: "actions" }, buttons));
    this._mountDialog(dlg, body, busy);
  }

  /* ----- correct a reading entered wrongly (rewrites the statistics without a meter change) ----- */
  async _openFix(c) {
    this._closeFlow();
    const dlg = { mode: "fix", stage: "loading", id: c.entry_id, name: c.title, list: [], unit: "", start: "", value: "",
      report: null, message: null, asking: null, fields: [], values: {}, fieldErrors: {} };
    this._dlg = dlg;
    this._renderFixDialog();
    try {
      const r = await this._hass.callWS({ type: "tariffy/ablesungen", entry_id: dlg.id });
      if (this._dlg !== dlg) return;
      dlg.list = r.ablesungen || [];
      dlg.unit = r.einheit || "";
      dlg.start = dlg.list.length ? dlg.list[0].start : "";
      dlg.stage = "form";
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.message = this._fixError(err);
    }
    this._renderFixDialog();
  }

  _fixError(err) {
    const code = err && err.code;
    return code && this._t(`fixErr_${code}`) !== `fixErr_${code}` ? this._t(`fixErr_${code}`) : this._errorText(err);
  }

  _fixLabel(a) {
    const when = new Date(a.zeit);
    const time = Number.isNaN(when.getTime()) ? "" : ` ${String(when.getHours()).padStart(2, "0")}:00`;
    return `${this._date(a.zeit.slice(0, 10))}${time} · ${this._num(a.stand, 3)} ${this._dlg.unit}${a.wechsel ? ` (${this._t("fixSwap")})` : ""}`;
  }

  async _runFix(pruefen) {
    const dlg = this._dlg;
    dlg.message = null;
    const stand = Number(String(dlg.value).replace(",", "."));
    if (String(dlg.value).trim() === "" || !Number.isFinite(stand) || stand < 0) {
      dlg.message = this._t("fixErr_ungueltig");
      this._renderFixDialog();
      return;
    }
    dlg.stage = "saving";
    this._renderFixDialog();
    try {
      const r = await this._hass.callWS({ type: "tariffy/ablesung_korrigieren", entry_id: dlg.id, start: dlg.start, stand, pruefen });
      if (this._dlg !== dlg) return;
      dlg.report = r;
      dlg.stage = r.korrigiert || r.nachziehen ? "done" : "form";
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.report = null;
      dlg.message = this._fixError(err);
    }
    this._renderFixDialog();
  }

  _renderFixDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving" || dlg.stage === "loading";
    const r = dlg.report;
    const body = [
      h("h2", { id: "dlg-title", text: this._t("fixTitle") }),
      h("p", { class: "hint", text: dlg.name }),
      h("p", { class: "hint", text: this._t("fixHint") }),
    ];
    if (dlg.stage === "loading") body.push(h("p", { class: "hint", text: this._t("fixLoading") }));
    else if (dlg.stage !== "done") {
      if (!dlg.list.length) body.push(h("p", { class: "hint", text: this._t("fixNone") }));
      else {
        const pick = h("select", { id: "f-fix-pick", onchange: (ev) => { dlg.start = ev.target.value; dlg.report = null; this._renderFixDialog(); } },
          dlg.list.map((a) => h("option", { value: a.start, text: this._fixLabel(a) })));
        pick.value = dlg.start;
        const input = h("input", { id: "f-fix-value", type: "text", inputmode: "decimal", autocomplete: "off",
          oninput: (ev) => { dlg.value = ev.target.value; dlg.report = null; } });
        input.value = dlg.value;
        body.push(h("label", { class: "fld", for: "f-fix-pick" }, h("span", { class: "lbl", text: this._t("fixPick") }), pick));
        body.push(h("label", { class: "fld", for: "f-fix-value" }, h("span", { class: "lbl", text: `${this._t("fixValue")} (${dlg.unit})` }), input));
      }
    }
    if (r) {
      const sign = r.aenderung > 0 ? "+" : "";
      const lines = dlg.stage === "done"
        ? [this._t(r.nachziehen ? "fixPending" : "fixDone")]
        : [
          this._t("fixPreview", { a: this._num(r.alt, 3), b: this._num(r.neu, 3), d: this._date(r.von.slice(0, 10)) }),
          this._t("fixUse", { v: `${sign}${this._num(r.aenderung, 3)} ${r.einheit || dlg.unit}` }),
          r.aktuell ? this._t("fixCurrent") : null,
        ].filter(Boolean);
      body.push(h("div", { class: "hint" }, lines.map((t) => h("div", { text: t }))));
    }
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    const buttons = dlg.stage === "done"
      ? [h("button", { class: "btn go", type: "button", text: this._t("fixClose"), onclick: () => this._closeFlow() })]
      : [
        dlg.list.length ? h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("fixCheck"), onclick: () => this._runFix(true) }) : null,
        r ? h("button", { class: "btn go", type: "button", disabled: busy ? "" : null, text: this._t("fixRun"), onclick: () => this._runFix(false) }) : null,
        h("button", { class: "btn", type: "button", disabled: dlg.stage === "saving" ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() }),
      ];
    body.push(h("div", { class: "actions" }, buttons));
    this._mountDialog(dlg, body, busy);
  }

  _openMeter(c) {
    this._closeFlow();
    const m = this._meterState(c) || {};
    this._dlg = {
      mode: "meter", stage: "form", id: c.entry_id, name: c.title, entity: c.zaehlerstand_eingabe, last: m.value, unit: m.unit || "",
      max: m.max, flowId: null, localize: () => "", asking: null,
      fields: [
        { name: "wert", kind: "number", label: `${this._t("meterValue")} (${m.unit || ""})`, required: true, min: 0, domains: [], custom: false, options: [], initial: "" },
        { name: "wechsel", kind: "boolean", label: this._t("meterSwap"), required: false, min: null, domains: [], custom: false, options: [], initial: "" },
      ],
      values: { wert: "", wechsel: false }, fieldErrors: {}, message: null,
    };
    this._renderMeterDialog();
  }

  async _saveMeter() {
    const dlg = this._dlg;
    const { values, errors } = this._collectFlow(dlg);
    dlg.fieldErrors = errors;
    dlg.message = null;
    const wert = values.wert;
    if (!Object.keys(errors).length) {
      if (!isNum(wert) || wert <= 0) dlg.fieldErrors.wert = this._t("meterInvalid");
      else if (isNum(dlg.max) && wert > dlg.max) dlg.fieldErrors.wert = this._t("meterTooBig", { v: this._num(dlg.max, 0) });
      else if (isNum(dlg.last) && wert < dlg.last && !dlg.values.wechsel) {
        dlg.fieldErrors.wert = this._t("meterLower", { v: `${this._num(dlg.last, 3)} ${dlg.unit}` });
      }
    }
    if (Object.keys(dlg.fieldErrors).length) { this._renderMeterDialog(); return; }
    dlg.stage = "saving";
    this._renderMeterDialog();
    try {
      await this._hass.callService("input_number", "set_value", { value: wert }, { entity_id: dlg.entity });
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.message = (err && err.message) || String(err);
      this._renderMeterDialog();
      return;
    }
    if (this._dlg === dlg) this._closeFlow();
    this._render();
  }

  _renderMeterDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving";
    const body = [
      h("h2", { id: "dlg-title", text: this._t("meterRecord") }),
      h("p", { class: "hint", text: dlg.name }),
      h("p", { class: "hint", text: isNum(dlg.last)
        ? this._t("meterHintLast", { v: `${this._num(dlg.last, 3)} ${dlg.unit}` }) : this._t("meterHintFirst") }),
      h("div", { class: "fields" }, dlg.fields.map((f) => {
        const error = dlg.fieldErrors[f.name];
        return h("label", { class: `fld${error ? " bad" : ""}`, for: `f-${f.name}` },
          h("span", { class: "lbl", text: f.label }), this._fieldInput(dlg, f),
          error ? h("div", { class: "ferr", text: error }) : null);
      })),
      h("p", { class: "hint", text: this._t("meterSwapHint") }),
    ];
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    body.push(h("div", { class: "actions" },
      h("button", { class: "btn go", type: "button", disabled: busy ? "" : null, text: this._t(busy ? "dlgSaving" : "dlgSave"), onclick: () => this._saveMeter() }),
      h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() })));
    this._mountDialog(dlg, body, busy);
    if (!dlg.focused) {
      dlg.focused = true;
      const first = this._dlgEl.querySelector("input");
      if (first) first.focus();
    }
  }

  /* the "price changes" block of an electricity/gas contract */

  /* ----- outdoor meters of a water contract (garden, pool, ...): not charged wastewater, administrators only ----- */
  _openAussen(c, index) {
    this._closeFlow();
    const spec = (name, kind, label, extra = {}) => ({
      name, kind, label, required: false, min: null, domains: [], custom: false, options: [], initial: "", ...extra,
    });
    const item = index === null ? {} : (c.aussenzaehler || [])[index] || {};
    this._dlg = {
      mode: "aussen", stage: "form", id: c.entry_id, name: c.title, index, flowId: null, localize: () => "", asking: null,
      fields: [
        spec("name", "text", this._t("aussenName"), { required: true }),
        spec("sensor", "entity", this._t("aussenSensor"), { required: true, domains: ["sensor"] }),
        spec("start_wert", "number", this._t("aussenStart"), { min: 0 }),
        spec("preis", "number", this._t("aussenPrice"), { min: 0 }),
      ],
      values: {
        name: item.name || "", sensor: item.sensor || "",
        start_wert: isNum(item.start_wert) ? String(item.start_wert) : "", preis: isNum(item.preis) ? String(item.preis) : "",
      },
      fieldErrors: {}, message: null,
    };
    this._renderAussenDialog();
  }

  async _saveAussen() {
    const dlg = this._dlg;
    const { values, errors } = this._collectFlow(dlg);
    dlg.fieldErrors = errors;
    dlg.message = null;
    if (Object.keys(errors).length) { this._renderAussenDialog(); return; }
    await this._callAussen(dlg, { type: "tariffy/aussen_save", entry_id: dlg.id, values, ...(dlg.index === null ? {} : { index: dlg.index }) });
  }

  async _deleteAussen() {
    const dlg = this._dlg;
    await this._callAussen(dlg, { type: "tariffy/aussen_delete", entry_id: dlg.id, index: dlg.index });
  }

  async _callAussen(dlg, message) {
    dlg.stage = "saving";
    this._renderAussenDialog();
    try {
      await this._hass.callWS(message);
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.asking = null;
      const code = String((err && (err.code || (err.body && err.body.code))) || "");
      dlg.message = code === "too_many" ? this._t("aussenTooMany") : this._termError(err);
      this._renderAussenDialog();
      return;
    }
    if (this._dlg === dlg) this._closeFlow();
    this._render();
  }

  _renderAussenDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving";
    const body = [
      h("h2", { id: "dlg-title", text: this._t(dlg.index === null ? "aussenTitleAdd" : "aussenTitleEdit") }),
      h("p", { class: "hint", text: dlg.name }),
      h("p", { class: "hint", text: this._t("aussenHint") }),
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
        h("span", { text: this._t("aussenDeleteAsk") }),
        h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("onceDeleteYes"), onclick: () => this._deleteAussen() }),
        h("button", { class: "btn", type: "button", text: this._t("actCancel"), onclick: () => { dlg.asking = null; this._renderAussenDialog(); } })));
    } else {
      buttons.push(h("button", { class: "btn go", type: "button", disabled: busy ? "" : null,
        text: this._t(busy ? "dlgSaving" : "dlgSave"), onclick: () => this._saveAussen() }));
      buttons.push(h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() }));
      if (dlg.index !== null) {
        buttons.push(h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("aussenDelete"),
          onclick: () => { dlg.asking = "delete"; this._renderAussenDialog(); } }));
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

  /* the "outdoor meters" block of a water contract */
  _aussenSection(c) {
    if (c.sparte !== "water") return null;
    const items = c.aussenzaehler || [];
    if (!items.length && !this._isAdmin) return null;
    const unit = this._unit(c);
    const amount = (v) => (isNum(v) ? `${this._num(v, 1)} ${unit}` : "–");
    // name and sensor on top (a long entity id wraps), the figures below, the pencil at the right
    const rows = items.map((item, index) => h("div", { class: "aussen-row" },
      h("div", { class: "an" }, item.name, h("span", { class: "t2", text: item.sensor })),
      h("div", { class: "av", text: isNum(item.verbrauch) ? `${amount(item.verbrauch)}${isNum(item.kosten) ? ` · ${this._money(item.kosten, c.currency, { digits: 2 })}` : ""}` : "–" }),
      this._isAdmin ? h("button", {
        class: "icon-btn", type: "button", title: this._t("aussenEdit"), "aria-label": this._t("aussenEdit"),
        onclick: () => this._openAussen(c, index),
      }, icon("edit")) : null));
    return h("div", { class: "sec aussen-sec" }, h("h4", { text: this._t("secAussen") }),
      rows.length ? h("div", { class: "aussen-list" }, rows) : null,
      this._isAdmin ? h("div", { class: "once-add" }, h("button", {
        class: "btn", type: "button", onclick: () => this._openAussen(c, null),
      }, icon("plus"), this._t("aussenAdd"))) : null);
  }
};
