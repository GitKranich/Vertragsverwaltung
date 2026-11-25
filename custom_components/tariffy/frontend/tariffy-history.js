/* Tab "History": earlier terms of a contract, also entered by hand.
 * Part of the Tariffy panel: tariffy-panel.js loads it with its own version and copies the methods onto the
 * panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, icon, isNum, METERED, I18N }) => class {
  /* ----- old contracts: a term entered by hand (administrators only) ----- */

  _openTerm(c, index) {
    this._closeFlow();
    const target = c || this._live[0] || this._contracts[0];
    const term = c && index !== null ? (c.historie || [])[index] : null;
    const AUTO = ["verbrauch", "kosten", "abschlag_gezahlt", "saldo"];
    const spec = (name, kind, extra = {}) => ({
      name, kind, label: this._t(`tf_${name}`), required: false, min: kind === "number" ? 0 : null,
      domains: [], custom: false, options: [], initial: "", ...extra,
    });
    const fields = [
      spec("entry_id", "select", { options: this._contracts.map((x) => ({ value: x.entry_id, label: x.title })) }),
      spec("beginn", "date", { required: true }), spec("ende", "date", { required: true }),
      spec("anbieter", "text"), spec("tarif", "text"),
      spec("arbeitspreis", "number"), spec("grundpreis", "number"), spec("abschlag", "number"),
      spec("verbrauch", "number", { auto: true }), spec("kosten", "number", { auto: true }),
      spec("abschlag_gezahlt", "number", { auto: true }), spec("saldo", "number", { auto: true, min: null }),
    ];
    const values = { entry_id: target.entry_id };
    if (term) {
      // the values the user typed in stay filled in; the ones that were worked out are worked out again
      const own = Array.isArray(term.eigene) ? term.eigene : AUTO;
      for (const f of fields) {
        const stored = term[f.name];
        if (f.name === "entry_id" || stored === undefined || stored === null) continue;
        if (!f.auto || own.includes(f.name)) values[f.name] = String(stored);
      }
    }
    const dlg = {
      mode: "term", stage: "form", id: target.entry_id, name: target.title, index: term ? index : null, fields, values,
      fieldErrors: {}, message: null, asking: null, calc: null, calcSeq: 0, flowId: null, localize: () => "",
    };
    this._dlg = dlg;
    this._renderTermDialog();
    this._termCalc(dlg);
  }

  async _termCalc(dlg) {
    const { values } = this._collectFlow(dlg);
    if (!values.beginn || !values.ende) {
      dlg.calc = null;
      this._termPaint(dlg);
      return;
    }
    const seq = ++dlg.calcSeq;
    const { entry_id: _id, ...rest } = values;
    let calc = null;
    try {
      calc = await this._hass.callWS({ type: "tariffy/history_calc", entry_id: dlg.values.entry_id, values: rest });
    } catch (_err) { /* invalid dates and the like: nothing to show yet */ }
    if (this._dlg !== dlg || seq !== dlg.calcSeq) return;
    dlg.calc = calc;
    this._termPaint(dlg);
  }

  /* Shows the worked-out values as grey placeholders and says where each one comes from. */
  _termPaint(dlg) {
    const el = this._dlgEl;
    if (!el || this._dlg !== dlg) return;
    const calc = dlg.calc;
    const contract = (this._contracts || []).find((c) => c.entry_id === dlg.values.entry_id);
    for (const f of dlg.fields.filter((x) => x.auto)) {
      const typed = String(dlg.values[f.name] ?? "").trim() !== "";
      const computed = calc ? calc.values[f.name] : null;
      const input = el.querySelector(`[name="${f.name}"]`);
      const note = el.querySelector(`[data-note="${f.name}"]`);
      if (input) input.placeholder = isNum(computed) ? this._num(computed, 2) : "";
      let text;
      if (typed) text = this._t("autoOwn");
      else if (f.name === "verbrauch") {
        if (!calc) text = this._t("autoWait");
        else if (isNum(computed)) text = this._t("autoStat", { s: (calc.sensors || []).join(", ") });
        else if (contract && !METERED.has(contract.sparte)) text = this._t("autoFlat");
        else if (!(calc.sensors || []).length) text = this._t("autoNoSensor");
        else text = this._t("autoNoStat");
      } else text = isNum(computed) ? this._t("autoCalc") : this._t("autoWait");
      if (note) note.textContent = text;
    }
  }

  _termError(err) {
    const code = String((err && (err.code || (err.body && err.body.code))) || "");
    const specific = `termErr_${code}`;
    const has = (key) => Boolean(I18N[this._lang][key] || I18N.en[key]);
    const key = has(specific) ? specific : (code.startsWith("invalid_") ? "termErr_invalid" : specific);
    return has(key) ? this._t(key) : this._t("dlgSaveFailed", { m: this._errorText(err) });
  }

  async _saveTerm() {
    const dlg = this._dlg;
    const { values, errors } = this._collectFlow(dlg);
    dlg.fieldErrors = errors;
    dlg.message = null;
    if (Object.keys(errors).length) {
      this._renderTermDialog();
      return;
    }
    dlg.stage = "saving";
    this._renderTermDialog();
    const { entry_id: _id, ...rest } = values;
    const message = { type: "tariffy/history_save", entry_id: dlg.values.entry_id, values: rest };
    if (dlg.index !== null) message.index = dlg.index;
    try {
      await this._hass.callWS(message);
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.message = this._termError(err);
      this._renderTermDialog();
      return;
    }
    if (this._dlg === dlg) this._closeFlow();
    this._render();
  }

  async _deleteTerm() {
    const dlg = this._dlg;
    dlg.stage = "saving";
    this._renderTermDialog();
    try {
      await this._hass.callWS({ type: "tariffy/history_delete", entry_id: dlg.values.entry_id, index: dlg.index });
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.asking = null;
      dlg.message = this._termError(err);
      this._renderTermDialog();
      return;
    }
    if (this._dlg === dlg) this._closeFlow();
    this._render();
  }

  _renderTermDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving";
    const body = [
      h("h2", { id: "dlg-title", text: this._t(dlg.index === null ? "termTitleAdd" : "termTitleEdit") }),
      h("p", { class: "hint", text: this._t("termHint") }),
      h("div", { class: "fields" }, dlg.fields.map((f) => {
        const error = dlg.fieldErrors[f.name];
        return h("label", { class: `fld${error ? " bad" : ""}`, for: `f-${f.name}` },
          h("span", { class: "lbl", text: f.label }), this._fieldInput(dlg, f),
          f.auto ? h("div", { class: "fnote", "data-note": f.name }) : null,
          error ? h("div", { class: "ferr", text: error }) : null);
      })),
    ];
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    const buttons = [];
    if (dlg.asking === "delete") {
      buttons.push(h("div", { class: "ask" },
        h("span", { text: this._t("termDeleteAsk") }),
        h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("termDeleteYes"), onclick: () => this._deleteTerm() }),
        h("button", { class: "btn", type: "button", text: this._t("actCancel"), onclick: () => { dlg.asking = null; this._renderTermDialog(); } })));
    } else {
      buttons.push(h("button", { class: "btn go", type: "button", disabled: busy ? "" : null,
        text: this._t(busy ? "dlgSaving" : "dlgSave"), onclick: () => this._saveTerm() }));
      buttons.push(h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() }));
      if (dlg.index !== null) {
        buttons.push(h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("termDelete"),
          onclick: () => { dlg.asking = "delete"; this._renderTermDialog(); } }));
      }
    }
    body.push(h("div", { class: "actions" }, buttons));
    this._mountDialog(dlg, body, busy);
    const el = this._dlgEl;
    // a finished edit of any field (blur, drop-down) works the automatic values out again
    el.onchange = () => this._termCalc(dlg);
    if (dlg.index !== null) {
      const select = el.querySelector('[name="entry_id"]');
      if (select) select.disabled = true; // a term stays with its contract
    }
    this._termPaint(dlg);
    if (!dlg.focused) { // keyboard users start in the dialog (Escape closes it)
      dlg.focused = true;
      const first = el.querySelector("select:not(:disabled), input");
      if (first) first.focus();
    }
  }

  _history() {
    const archived = this._contracts.filter((c) => c.archiviert).sort((a, b) => a.title.localeCompare(b.title));
    const earlier = this._contracts.filter((c) => !c.archiviert && (c.historie || []).length)
      .sort((a, b) => a.title.localeCompare(b.title));
    const toolbar = this._isAdmin
      ? h("div", { class: "list-head" },
        h("button", { class: "btn blue", type: "button", onclick: () => this._openTerm(null, null) }, icon("plus"), this._t("termAdd")))
      : null;
    if (!archived.length && !earlier.length) {
      return h("div", {}, toolbar,
        h("div", { class: "state" }, h("h2", { text: this._t("histEmptyTitle") }), h("div", { text: this._t("histEmpty") })));
    }
    const tone = (v) => (isNum(v) ? (v < 0 ? "red-t" : v > 0 ? "green-t" : "") : "muted-t");
    const cell = (label, value, cls) => h("td", { "data-label": label, class: cls || null, text: value });
    const termRow = (c, t, index) => h("tr", {},
      h("td", {}, `${this._date(t.beginn)} – ${t.ende ? this._date(t.ende) : "–"}`,
        h("span", { class: "t2", text: [t.anbieter, t.tarif].filter(Boolean).join(" · ") || "–" })),
      cell(this._t("colInstalment"), isNum(t.abschlag) ? this._money(t.abschlag, c.currency, { digits: 2 }) : "–"),
      cell(this._t("colTermCost"), this._money(isNum(t.kosten) ? t.kosten : t.abschlag_gezahlt, c.currency, { digits: 2 })),
      cell(this._t("colUsage"), isNum(t.verbrauch) ? `${this._num(t.verbrauch, 0)} ${this._unit(c)}` : "–"),
      cell(this._t("colTermBalance"), isNum(t.saldo) ? this._money(t.saldo, c.currency, { sign: true, digits: 2 }) : "–", tone(t.saldo)),
      this._isAdmin ? h("td", { class: "act" }, h("button", {
        class: "icon-btn", type: "button", title: this._t("termEdit"), "aria-label": this._t("termEdit"),
        onclick: () => this._openTerm(c, index),
      }, icon("edit"))) : null);
    const contractBlock = (c) => h("div", { class: "hist", "data-id": c.entry_id },
      h("div", { class: "hist-head" },
        h("div", { class: `badge ${c.sparte}` }, this._icon(c)),
        this._who(c),
        c.archiviert ? h("span", { class: "chip", text: this._t("archived") }) : null),
      h("table", {},
        h("thead", {}, h("tr", {}, [this._t("colTerm"), this._t("colInstalment"), this._t("colTermCost"), this._t("colUsage"), this._t("colTermBalance")]
          .concat(this._isAdmin ? [""] : []).map((label) => h("th", { text: label })))),
        // newest first; `index` is the position in the stored list (oldest first), needed to edit a term
        h("tbody", {}, (c.historie || []).map((t, index) => [t, index]).reverse().map(([t, index]) => termRow(c, t, index)))),
      this._total(c) ? h("p", { class: "hint hist-total" }, `${this._t("totalCost")}: `,
        h("b", { text: this._total(c).value }), ` · ${this._total(c).since}`) : null,
      // the acknowledgements belong to the contract list; here only archiving and restoring
      this._actions(c, ["archive_contract", "restore_contract"]));
    const group = (key, list) => (list.length ? h("section", { class: "group" },
      h("h3", { class: "group-title" }, h("span", { text: this._t(key) }), h("span", { class: "count", text: String(list.length) })),
      list.map(contractBlock)) : null);
    return h("div", {}, toolbar, group("groupArchived", archived), group("groupEarlier", earlier));
  }
};
