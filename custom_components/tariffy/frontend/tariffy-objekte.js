/* Properties ("Objekte"): the switcher in the head of the panel and the dialog that manages them.
 * Every contract belongs to one property; the panel shows the contracts of the chosen property only. When the panel
 * opens it shows the default property. Part of the Tariffy panel: tariffy-panel.js loads it with its own version and
 * copies the methods onto the panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, icon, ownIcon, I18N }) => class {
  /* ----- choosing a property ----- */

  _setObjekt(id) {
    if (!id || id === this._objekt) return;
    this._objekt = id;
    this._open = new Set();
    this._filterObjekt();
    this._tellObjekt();
    this._render();
  }

  _objektName(id) {
    const hit = (this._objekte || []).find((o) => o.id === id);
    return hit ? hit.name : "";
  }

  _objektIcon(o) {
    const own = ownIcon(o && o.icon);
    return own && customElements.get("ha-icon")
      ? Object.assign(document.createElement("ha-icon"), { icon: own, className: "own-icon" })
      : icon("hauskosten");
  }

  /* the switcher in the head: a choice from two properties on, for administrators always (to add the second one) */
  _objektBar() {
    const list = this._objekte || [];
    if (!list.length || (list.length < 2 && !this._isAdmin)) return null;
    const current = list.find((o) => o.id === this._objekt) || list[0];
    const choice = list.length > 1
      ? h("select", { class: "objsel-in", "aria-label": this._t("objekt"), onchange: (ev) => this._setObjekt(ev.target.value) },
        list.map((o) => h("option", { value: o.id, selected: o.id === this._objekt ? "" : null,
          text: o.id === this._defaultObjekt ? `${o.name} ★` : o.name })))
      : h("span", { class: "objsel-name", text: current.name });
    const manage = this._isAdmin
      ? h("button", { class: "icon-btn objsel-btn", type: "button", title: this._t("objManage"), "aria-label": this._t("objManage"),
        onclick: () => this._openObjekte() }, icon("edit"))
      : null;
    return h("div", { class: "objsel" }, h("span", { class: "objsel-ic", "aria-hidden": "true" }, this._objektIcon(current)), choice, manage);
  }

  /* the chosen property has no contracts (yet) */
  _objektEmpty() {
    return h("div", { class: "state" },
      h("h2", { text: this._t("objEmptyTitle", { name: this._objektName(this._objekt) }) }),
      h("p", { text: this._t("objEmptyText") }),
      this._isAdmin
        ? h("button", { class: "btn blue", type: "button", onclick: () => this._openFlow(null, "create") }, icon("plus"), this._t("actNew"))
        : null);
  }

  /* ----- managing the properties (administrators) ----- */

  _openObjekte() {
    this._closeFlow();
    this._dlg = { mode: "objekte", fields: [], values: {}, editing: null, deleting: null, ziel: null, busy: false, message: null,
      neu: { name: "", icon: "" }, edit: { name: "", icon: "" } };
    this._renderDialog();
  }

  _objErr(err) {
    const code = String((err && (err.code || (err.body && err.body.code))) || "");
    const key = `objErr_${code}`;
    return I18N[this._lang][key] || I18N.en[key] ? this._t(key) : this._t("objErr");
  }

  async _objCall(message, after) {
    const dlg = this._dlg;
    dlg.busy = true;
    dlg.message = null;
    this._renderDialog();
    try {
      const result = await this._hass.callWS(message);
      if (after) after(result);
    } catch (err) {
      dlg.message = this._objErr(err);
    }
    dlg.busy = false;
    if (this._dlg === dlg) this._renderDialog();
  }

  _countObjekt(id) {
    return (this._all || []).filter((c) => c.objekt_id === id).length;
  }

  _renderObjekteDialog() {
    const dlg = this._dlg;
    const list = this._objekte || [];
    const busy = dlg.busy;
    const input = (target, key, props) => h("input", { type: "text", autocomplete: "off", spellcheck: "false", value: target[key] || "",
      oninput: (ev) => { target[key] = ev.target.value; }, ...props });
    const body = [
      h("h2", { id: "dlg-title", text: this._t("objTitle") }),
      h("p", { class: "hint", text: this._t("objHint") }),
    ];

    const rows = list.map((o) => {
      const count = this._countObjekt(o.id);
      const isDefault = o.id === this._defaultObjekt;
      if (dlg.editing === o.id) {
        return h("div", { class: "obj-row editing" },
          h("div", { class: "fields" },
            h("label", { class: "fld" }, h("span", { class: "lbl", text: this._t("objName") }), input(dlg.edit, "name", { name: "obj-edit-name" })),
            h("label", { class: "fld" }, h("span", { class: "lbl", text: this._t("objIcon") }), input(dlg.edit, "icon", { name: "obj-edit-icon", placeholder: "mdi:home" }))),
          h("div", { class: "actions" },
            h("button", { class: "btn go", type: "button", disabled: busy ? "" : null, text: this._t("objSave"),
              onclick: () => this._objCall({ type: "tariffy/objekt_save", objekt_id: o.id, name: dlg.edit.name, icon: dlg.edit.icon || null },
                () => { dlg.editing = null; }) }),
            h("button", { class: "btn", type: "button", text: this._t("actCancel"), onclick: () => { dlg.editing = null; dlg.message = null; this._renderDialog(); } })));
      }
      if (dlg.deleting === o.id) {
        const others = list.filter((x) => x.id !== o.id);
        if (count && !others.some((x) => x.id === dlg.ziel)) dlg.ziel = (others.find((x) => x.id === this._defaultObjekt) || others[0] || {}).id || null;
        return h("div", { class: "obj-row" },
          h("div", { class: "ask" },
            h("span", { text: this._t("objDeleteAsk", { name: o.name }) }),
            count ? h("label", { class: "fld obj-move" },
              h("span", { class: "lbl", text: this._t(count === 1 ? "objMoveTo1" : "objMoveTo", { n: String(count) }) }),
              h("select", { name: "obj-ziel", onchange: (ev) => { dlg.ziel = ev.target.value; } },
                others.map((x) => h("option", { value: x.id, selected: x.id === dlg.ziel ? "" : null, text: x.name })))) : null,
            h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("objDeleteYes"),
              onclick: () => this._objCall({ type: "tariffy/objekt_delete", objekt_id: o.id, ...(count ? { ziel: dlg.ziel } : {}) },
                () => { dlg.deleting = null; }) }),
            h("button", { class: "btn", type: "button", text: this._t("actCancel"), onclick: () => { dlg.deleting = null; dlg.message = null; this._renderDialog(); } })));
      }
      return h("div", { class: "obj-row" },
        h("span", { class: "obj-ic", "aria-hidden": "true" }, this._objektIcon(o)),
        h("span", { class: "obj-name" }, h("b", { text: o.name }),
          h("span", { class: "t2", text: this._t(count === 1 ? "objContract" : "objContracts", { n: String(count) }) })),
        isDefault
          ? h("span", { class: "chip blue", text: this._t("objDefault") })
          : h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("objMakeDefault"),
            onclick: () => this._objCall({ type: "tariffy/objekt_default", objekt_id: o.id }) }),
        h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("objEdit"),
          onclick: () => { Object.assign(dlg, { editing: o.id, deleting: null, message: null, edit: { name: o.name, icon: o.icon || "" } }); this._renderDialog(); } }),
        list.length > 1
          ? h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("objDelete"),
            onclick: () => { Object.assign(dlg, { deleting: o.id, editing: null, ziel: null, message: null }); this._renderDialog(); } })
          : null);
    });
    body.push(h("div", { class: "obj-list" }, rows));

    body.push(h("h3", { class: "group-title obj-new-title" }, h("span", { text: this._t("objNew") })));
    body.push(h("div", { class: "fields" },
      h("label", { class: "fld" }, h("span", { class: "lbl", text: this._t("objName") }), input(dlg.neu, "name", { name: "obj-new-name" })),
      h("label", { class: "fld" }, h("span", { class: "lbl", text: this._t("objIcon") }), input(dlg.neu, "icon", { name: "obj-new-icon", placeholder: "mdi:home-city" }))));
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    body.push(h("div", { class: "actions" },
      h("button", { class: "btn go", type: "button", disabled: busy ? "" : null, text: this._t("objAdd"),
        onclick: () => this._objCall({ type: "tariffy/objekt_save", name: dlg.neu.name, icon: dlg.neu.icon || null },
          () => { dlg.neu = { name: "", icon: "" }; }) }),
      h("button", { class: "btn", type: "button", text: this._t("dlgClose"), onclick: () => this._closeFlow() })));
    this._mountDialog(dlg, body, busy);
    // keep the focus in the dialog (Escape closes it from there): the field that was just opened, else the first button
    const el = this._dlgEl;
    if (el && !el.contains(this.shadowRoot.activeElement)) {
      const target = el.querySelector("[name=obj-ziel], [name=obj-edit-name]") || el.querySelector(".dialog button");
      if (target) target.focus();
    }
  }
};
