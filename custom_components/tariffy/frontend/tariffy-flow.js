/* Forms of the integration in a dialog: new contract, next contract, edit and delete a contract.
 * Part of the Tariffy panel: tariffy-panel.js loads it with its own version and copies the methods onto the
 * panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, icon, isNum, FLOWS, ownIcon }) => class {
  /* ---------- forms of the integration: next contract and contract data ----------
     Renders the flows of Home Assistant from its REST API (the "Configure" dialog for the
     next contract, the "Reconfigure" dialog for the contract itself), so validation and
     storage stay in the integration. Empty optional fields are left out, exactly as Home
     Assistant's own form does. */

  _errorText(err) {
    return String((err && ((err.body && err.body.message) || err.message || err.error)) || err);
  }

  /* the badge icon: the own icon of the contract (mdi:...) when Home Assistant can draw it, else the one of the category */
  _icon(c) {
    const own = ownIcon(c.icon);
    if (own && customElements.get("ha-icon")) {
      return Object.assign(document.createElement("ha-icon"), { icon: own, className: "own-icon" });
    }
    return icon(c.sparte);
  }

  _fieldSpec(raw, dlg, step) {
    const kind = ["date", "text", "number", "select", "entity", "boolean", "icon"].find((k) => raw.selector && k in raw.selector) || null;
    const sel = kind === "select" ? raw.selector.select : {};
    const suggested = raw.description && raw.description.suggested_value;
    const initial = suggested !== undefined && suggested !== null ? suggested : raw.default;
    const label = dlg.localize(`component.tariffy.${dlg.category}.step.${step}.data.${raw.name}`) ||
      raw.name.replace(/^next_/, "").replace(/_/g, " ");
    const optionLabel = (value, fallback) => (sel.translation_key
      ? dlg.localize(`component.tariffy.selector.${sel.translation_key}.options.${value}`) : "") || fallback;
    return {
      name: raw.name, kind, label, required: Boolean(raw.required),
      min: kind === "number" && isNum(raw.selector.number.min) ? raw.selector.number.min : null,
      domains: kind === "entity" ? [].concat(raw.selector.entity.domain || []) : [],
      custom: Boolean(sel.custom_value),
      labelled: Boolean(sel.translation_key), // the options have texts of their own (the stored value is the key)
      options: (sel.options || []).map((o) => {
        const value = typeof o === "string" ? o : o.value;
        return { value, label: optionLabel(value, typeof o === "string" ? o : o.label || o.value) };
      }),
      initial: kind === "boolean" ? Boolean(initial) : (initial === undefined || initial === null ? "" : String(initial)),
    };
  }

  async _flowLocalize(category) {
    try {
      if (typeof this._hass.loadBackendTranslation === "function") {
        await this._hass.loadBackendTranslation("selector", "tariffy"); // labels of the drop-downs
        const localize = await this._hass.loadBackendTranslation(category, "tariffy");
        if (typeof localize === "function") return localize;
      }
    } catch (_err) { /* fall back to the field names */ }
    return typeof this._hass.localize === "function" ? this._hass.localize : () => "";
  }

  _applyForm(dlg, form) {
    if (form.type === "create_entry" || (form.type === "abort" && form.reason === "reconfigure_successful")) {
      dlg.stage = "saved";
      return;
    }
    if (form.type === "abort") {
      dlg.stage = "error";
      dlg.message = this._t("dlgAborted", { m: form.reason || "" });
      return;
    }
    dlg.flowId = form.flow_id;
    dlg.stage = "form";
    if (dlg.step !== form.step_id) { // a new step starts with its own values
      dlg.step = form.step_id;
      dlg.values = {};
      dlg.refocus = true;
    }
    // Home Assistant says whether a step is the last one; without it every step after the first is
    dlg.final = dlg.mode === "next" || (typeof form.last_step === "boolean" ? form.last_step
      : form.step_id !== (dlg.mode === "create" ? "user" : "reconfigure"));
    dlg.fields = (form.data_schema || []).map((f) => this._fieldSpec(f, dlg, form.step_id));
    dlg.unsupported = dlg.fields.some((f) => !f.kind);
    dlg.hint = dlg.localize(`component.tariffy.${dlg.category}.step.${form.step_id}.description`) || "";
    dlg.fieldErrors = {};
    dlg.message = null;
    for (const [name, code] of Object.entries(form.errors || {})) {
      const text = dlg.localize(`component.tariffy.${dlg.category}.error.${code}`) || String(code);
      if (name === "base") dlg.message = text;
      else dlg.fieldErrors[name] = text;
    }
    for (const f of dlg.fields) if (!(f.name in dlg.values)) dlg.values[f.name] = f.initial;
  }

  async _openFlow(c, mode) {
    this._closeFlow();
    const flow = FLOWS[mode];
    const dlg = {
      mode, api: flow.api, category: flow.category, id: c ? c.entry_id : null, name: c ? c.title : "", stage: "loading", flowId: null,
      step: null, final: true, fields: [], values: {}, fieldErrors: {}, message: null, hint: "", unsupported: false,
      asking: null, localize: () => "", refocus: false,
      hasNext: mode === "next" && Boolean(c.next && c.next.beginn),
      reminder: mode === "next" && Boolean(c.erinnerung_aktiv), markDone: true, note: null, info: null, doneKey: "dlgSaved",
    };
    this._dlg = dlg;
    this._renderDialog();
    try {
      const [form, localize] = await Promise.all([
        this._hass.callApi("POST", flow.api, flow.body(c)),
        this._flowLocalize(flow.category),
      ]);
      if (this._dlg !== dlg) {
        if (form && form.flow_id) this._abortFlow(flow.api, form.flow_id);
        return;
      }
      dlg.localize = localize;
      this._applyForm(dlg, form);
    } catch (err) {
      dlg.stage = "error";
      dlg.message = this._t("dlgLoadFailed", { m: this._errorText(err) });
    }
    if (this._dlg === dlg) this._renderDialog();
  }

  _abortFlow(api, flowId) {
    try {
      Promise.resolve(this._hass.callApi("DELETE", `${api}/${flowId}`)).catch(() => {});
    } catch (_err) { /* the flow expires by itself */ }
  }

  _closeFlow() {
    const dlg = this._dlg;
    this._dlg = null;
    if (dlg && dlg.flowId && dlg.stage !== "saved") this._abortFlow(dlg.api, dlg.flowId);
    if (this._dlgEl) {
      this._dlgEl.remove();
      this._dlgEl = null;
    }
  }

  /* a free-text select with listed options (house costs): show the text of a listed option, store its key;
   * what the user typed himself is kept as typed */
  _optionKey(f, text) {
    const t = String(text).trim().toLowerCase();
    const hit = f.options.find((o) => o.value.toLowerCase() === t || String(o.label).toLowerCase() === t);
    return hit ? hit.value : String(text).trim();
  }

  _optionLabel(f, value) {
    const hit = f.options.find((o) => o.value === value);
    return hit ? hit.label : value;
  }

  _collectFlow(dlg) {
    const values = {};
    const errors = {};
    for (const f of dlg.fields) {
      if (f.kind === "boolean") {
        values[f.name] = Boolean(dlg.values[f.name]);
        continue;
      }
      const raw = String(dlg.values[f.name] ?? "").trim();
      if (raw === "") {
        if (f.required) errors[f.name] = this._t("dlgRequired");
        continue;
      }
      if (f.kind === "number") {
        const n = Number(raw.replace(",", "."));
        if (!Number.isFinite(n) || (f.min !== null && n < f.min)) errors[f.name] = this._t("dlgInvalidNumber");
        else values[f.name] = n;
      } else {
        values[f.name] = f.kind === "select" && f.custom && f.labelled ? this._optionKey(f, raw) : raw;
      }
    }
    return { values, errors };
  }

  async _sendFlow(dlg, values, removed) {
    dlg.stage = "saving";
    dlg.message = null;
    this._renderDialog();
    let result;
    try {
      result = await this._hass.callApi("POST", `${dlg.api}/${dlg.flowId}`, values);
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.asking = null;
      dlg.message = this._t("dlgSaveFailed", { m: this._errorText(err) });
      this._renderDialog();
      return;
    }
    if (this._dlg !== dlg) return;
    dlg.asking = null;
    this._applyForm(dlg, result);
    if (dlg.stage !== "saved") {
      this._renderDialog();
      return;
    }
    dlg.doneKey = removed ? "dlgRemoved" : dlg.mode === "create" ? "dlgCreated" : "dlgSaved";
    dlg.note = null;
    this._renderDialog();
    if (!removed && dlg.reminder && dlg.markDone) {
      const done = await this._markCancellationDone(dlg.id);
      if (this._dlg !== dlg) return;
      if (!done) dlg.note = this._t("dlgMarkDoneFailed");
      this._renderDialog();
    }
  }

  /* Saving reloads the contract, so the action may answer "not_found" for a moment. */
  async _markCancellationDone(entryId) {
    for (let attempt = 0; attempt < 12; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, attempt === 0 ? 700 : 600));
      try {
        await this._hass.callWS({ type: "tariffy/action", entry_id: entryId, action: "confirm_cancellation" });
        return true;
      } catch (err) {
        if (err && err.code === "not_available") return true; // nothing left to acknowledge
        if (!err || err.code !== "not_found") return false;
      }
    }
    return false;
  }

  /* Deleting is Home Assistant's own "remove entry"; the open form is closed first. */
  async _deleteContract(dlg) {
    dlg.stage = "saving";
    dlg.message = null;
    this._renderDialog();
    if (dlg.flowId) this._abortFlow(dlg.api, dlg.flowId);
    dlg.flowId = null;
    try {
      await this._hass.callApi("DELETE", `config/config_entries/entry/${dlg.id}`);
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "error";
      dlg.message = this._t("dlgDeleteFailed", { m: this._errorText(err) });
      this._renderDialog();
      return;
    }
    if (this._dlg !== dlg) return;
    dlg.stage = "saved";
    dlg.doneKey = "dlgDeleted";
    dlg.info = this._t("dlgDeleteNote");
    this._renderDialog();
  }

  /* Validates the form; the last step of editing a contract asks once more before it is sent. */
  _submitFlow(confirmed) {
    const dlg = this._dlg;
    if (!dlg || dlg.stage !== "form") return;
    const { values, errors } = this._collectFlow(dlg);
    dlg.fieldErrors = errors;
    if (Object.keys(errors).length) {
      dlg.message = null;
      dlg.asking = null;
      this._renderDialog();
      return;
    }
    if (dlg.mode === "edit" && dlg.final && !confirmed) {
      dlg.asking = "save";
      this._renderDialog();
      return;
    }
    this._sendFlow(dlg, values, false);
  }

  _fieldInput(dlg, f) {
    const forget = (target) => { // an error message is outdated as soon as its field is edited
      if (!dlg.fieldErrors[f.name]) return;
      delete dlg.fieldErrors[f.name];
      const label = target.closest("label");
      if (label) {
        label.classList.remove("bad");
        const note = label.querySelector(".ferr");
        if (note) note.remove();
      }
      target.removeAttribute("aria-invalid");
    };
    const common = {
      id: `f-${f.name}`, name: f.name, "aria-invalid": dlg.fieldErrors[f.name] ? "true" : null,
      oninput: (ev) => { dlg.values[f.name] = ev.target.value; forget(ev.target); },
    };
    if (f.kind === "boolean") {
      return [h("input", { id: common.id, name: f.name, type: "checkbox",
        onchange: (ev) => { dlg.values[f.name] = ev.target.checked; } })];
    }
    if (f.kind === "select" && !f.custom) {
      return [h("select", { ...common, onchange: (ev) => { dlg.values[f.name] = ev.target.value; } },
        f.options.map((o) => h("option", { value: o.value, text: o.label })))];
    }
    if (f.kind === "entity" || f.kind === "select") { // free text with suggestions
      const list = `dl-${f.name}`;
      const ids = f.kind === "select" ? f.options.map((o) => (f.labelled ? o.label : o.value))
        : Object.keys((this._hass && this._hass.states) || {})
          .filter((id) => !f.domains.length || f.domains.includes(id.split(".")[0])).sort();
      return [
        h("input", { ...common, type: "text", list, autocomplete: "off", spellcheck: "false" }),
        h("datalist", { id: list }, ids.map((id) => h("option", { value: id }))),
      ];
    }
    if (f.kind === "icon") { // mdi:... as text, with a preview of the symbol as Home Assistant draws it
      const preview = h("span", { class: "icon-prev", "aria-hidden": "true" });
      const show = (value) => {
        const own = ownIcon(value);
        preview.replaceChildren(own && customElements.get("ha-icon")
          ? Object.assign(document.createElement("ha-icon"), { icon: own }) : document.createTextNode(""));
      };
      show(dlg.values[f.name] || "");
      return [h("span", { class: "icon-fld" },
        h("input", { ...common, type: "text", autocomplete: "off", spellcheck: "false", placeholder: "mdi:car",
          oninput: (ev) => { common.oninput(ev); show(ev.target.value); } }),
        preview)];
    }
    const type = f.kind === "date" ? "date" : "text";
    return [h("input", { ...common, type, inputmode: f.kind === "number" ? "decimal" : null, autocomplete: "off" })];
  }

  _renderDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    if (dlg.mode === "term") { this._renderTermDialog(); return; }
    if (dlg.mode === "renew") { this._renderRenewDialog(); return; }
    if (dlg.mode === "once") { this._renderOnceDialog(); return; }
    if (dlg.mode === "preis") { this._renderPreisDialog(); return; }
    if (dlg.mode === "meter") { this._renderMeterDialog(); return; }
    if (dlg.mode === "import") { this._renderImportDialog(); return; }
    if (dlg.mode === "fix") { this._renderFixDialog(); return; }
    if (dlg.mode === "aussen") { this._renderAussenDialog(); return; }
    if (dlg.mode === "doc") { this._renderDocDialog(); return; }
    const busy = dlg.stage === "saving";
    const body = [h("h2", { id: "dlg-title", text: this._t({ edit: "dlgTitleEdit", create: "dlgTitleCreate", next: "dlgTitle" }[dlg.mode], { name: dlg.name }) })];
    const buttons = [];
    const ask = (text, yes, tone, onYes) => h("div", { class: "ask" },
      h("span", { text }),
      h("button", { class: `btn ${tone}`, type: "button", disabled: busy ? "" : null, text: yes, onclick: onYes }),
      h("button", { class: "btn", type: "button", text: this._t("actCancel"),
        onclick: () => { dlg.asking = null; this._renderDialog(); } }));

    if (dlg.stage === "loading") {
      body.push(h("p", { class: "hint", text: this._t("dlgLoading") }));
    } else if (dlg.stage === "error") {
      body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
      buttons.push(h("button", { class: "btn", type: "button", text: this._t("dlgClose"), onclick: () => this._closeFlow() }));
    } else if (dlg.stage === "saved") {
      body.push(h("p", { class: "hint", text: this._t(dlg.doneKey) }));
      if (dlg.info) body.push(h("p", { class: "hint", text: dlg.info }));
      if (dlg.note) body.push(h("div", { class: "err", role: "alert", text: dlg.note }));
      buttons.push(h("button", { class: "btn go", type: "button", text: this._t("dlgClose"), onclick: () => this._closeFlow() }));
    } else {
      if (dlg.hint) body.push(h("p", { class: "hint", text: dlg.hint }));
      if (dlg.unsupported) {
        body.push(h("div", { class: "err", role: "alert", text: this._t("dlgUnsupported") }));
      } else {
        body.push(h("div", { class: "fields" }, dlg.fields.map((f) => {
          const error = dlg.fieldErrors[f.name];
          const input = this._fieldInput(dlg, f);
          const text = h("span", { class: "lbl", text: f.label });
          return h("label", { class: `fld${f.kind === "boolean" ? " bool" : ""}${error ? " bad" : ""}`, for: `f-${f.name}` },
            f.kind === "boolean" ? [input, text] : [text, input],
            error ? h("div", { class: "ferr", text: error }) : null);
        })));
      }
      if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
      if (dlg.reminder && !dlg.unsupported) {
        body.push(h("label", { class: "check" },
          h("input", { type: "checkbox", checked: dlg.markDone ? "" : null, onchange: (ev) => { dlg.markDone = ev.target.checked; } }),
          h("span", { text: this._t("dlgMarkDone") })));
      }
      if (dlg.asking === "remove") {
        buttons.push(ask(this._t("dlgRemoveAsk"), this._t("dlgRemoveYes"), "red", () => this._sendFlow(dlg, {}, true)));
      } else if (dlg.asking === "delete") {
        const go = h("button", { class: "btn red", type: "button", disabled: "", text: this._t("dlgDeleteYes"),
          onclick: () => this._deleteContract(dlg) });
        buttons.push(h("div", { class: "ask" },
          h("span", { text: this._t("dlgDeleteAsk", { name: dlg.name }) }),
          h("input", { class: "confirm-name", type: "text", autocomplete: "off", spellcheck: "false", placeholder: dlg.name,
            "aria-label": this._t("dlgDeleteAsk", { name: dlg.name }),
            oninput: (ev) => { go.disabled = ev.target.value.trim() !== dlg.name; } }),
          go,
          h("button", { class: "btn", type: "button", text: this._t("actCancel"),
            onclick: () => { dlg.asking = null; this._renderDialog(); } })));
      } else if (dlg.asking === "save") {
        buttons.push(ask(this._t("dlgSaveAsk"), this._t("dlgSaveYes"), "go", () => this._submitFlow(true)));
      } else {
        if (!dlg.unsupported) {
          buttons.push(h("button", { class: "btn go", type: "button", disabled: busy ? "" : null,
            text: this._t(busy ? "dlgSaving" : dlg.final ? "dlgSave" : "dlgContinue"), onclick: () => this._submitFlow(false) }));
        }
        buttons.push(h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"),
          onclick: () => this._closeFlow() }));
        if (dlg.mode === "edit") {
          buttons.push(h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("dlgDelete"),
            onclick: () => { dlg.asking = "delete"; this._renderDialog(); } }));
        }
        if (dlg.hasNext && !dlg.unsupported) {
          buttons.push(h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("dlgRemove"),
            onclick: () => { dlg.asking = "remove"; this._renderDialog(); } }));
        }
      }
    }
    body.push(h("div", { class: "actions" }, buttons));
    this._mountDialog(dlg, body, busy);
  }

  _mountDialog(dlg, body, busy) {
    const el = this._dlgEl || (this._dlgEl = h("div", { class: "overlay" }));
    el.onkeydown = (ev) => { if (ev.key === "Escape" && !busy) this._closeFlow(); };
    el.replaceChildren(h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "dlg-title" }, body));
    if (!el.isConnected) this.shadowRoot.appendChild(el);

    // the inputs are filled through properties, attributes would not follow later changes
    for (const f of dlg.fields) {
      const input = el.querySelector(`[name="${f.name}"]`);
      if (!input) continue;
      if (f.kind === "boolean") input.checked = Boolean(dlg.values[f.name]);
      else input.value = f.kind === "select" && f.custom && f.labelled ? this._optionLabel(f, dlg.values[f.name] ?? "") : (dlg.values[f.name] ?? "");
    }
    const confirmName = el.querySelector(".confirm-name");
    if (confirmName) confirmName.focus();
    if (dlg.refocus && dlg.stage === "form") {
      dlg.refocus = false;
      const first = el.querySelector("input, select");
      if (first) first.focus();
    }
  }
};
