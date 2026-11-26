/* Tab "Backup": export, restore from a file, deleted contracts (administrators only).
 * Part of the Tariffy panel: tariffy-panel.js loads it with its own version and copies the methods onto the
 * panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, I18N }) => class {
  /* ----- backup: export, restore from a file, deleted contracts (administrators only) ----- */

  _bkError(err) {
    const code = String((err && (err.code || (err.body && err.body.code))) || "");
    const key = `bkErr_${code}`;
    return I18N[this._lang][key] || I18N.en[key] ? this._t(key) : this._t("bkErr_invalid_backup");
  }

  async _bkLoad() {
    const bk = this._bk;
    bk.loading = true;
    try {
      bk.deleted = (await this._hass.callWS({ type: "tariffy/deleted_list" })).deleted || [];
    } catch (_err) {
      bk.deleted = [];
    }
    bk.loading = false;
    this._render();
  }

  async _bkDownload() {
    const bk = this._bk;
    bk.message = null;
    bk.error = null;
    try {
      const { backup } = await this._hass.callWS({ type: "tariffy/backup_export" });
      const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" }));
      const link = h("a", { href: url, download: `tariffy-backup-${new Date().toISOString().slice(0, 10)}.json` });
      this.shadowRoot.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      bk.message = this._t("bkDownloaded", { n: backup.contracts.length });
    } catch (err) {
      bk.error = this._bkError(err);
    }
    this._render();
  }

  async _bkPick(file) {
    const bk = this._bk;
    Object.assign(bk, { plan: null, backup: null, file: null, message: null, error: null, asking: null });
    if (file && file.size > 1048576) {
      bk.error = this._t("bkErrBig");
    } else if (file) {
      try {
        const backup = JSON.parse(await file.text());
        bk.plan = (await this._hass.callWS({ type: "tariffy/backup_preview", backup })).contracts;
        bk.backup = backup;
        bk.file = file.name;
      } catch (err) {
        bk.error = this._bkError(err);
      }
    }
    this._render();
  }

  async _bkRun(task, done) {
    const bk = this._bk;
    Object.assign(bk, { busy: true, asking: null, message: null, error: null });
    this._render();
    try {
      bk.message = done(await task());
    } catch (err) {
      bk.error = this._bkError(err);
    }
    bk.busy = false;
    await this._bkLoad();
  }

  _bkRestore() {
    const bk = this._bk;
    return this._bkRun(() => this._hass.callWS({ type: "tariffy/backup_restore", backup: bk.backup }), (r) => {
      bk.plan = null;
      bk.backup = null;
      return this._t("bkDone", { c: r.created.length, s: r.skipped.length });
    });
  }

  _bkDiscard(item) {
    return this._bkRun(() => this._hass.callWS({ type: "tariffy/deleted_discard", item_id: item.id }),
      () => this._t("bkDiscarded", { name: item.title }));
  }

  _bkRestoreOne(item) {
    return this._bkRun(() => this._hass.callWS({ type: "tariffy/deleted_restore", item_id: item.id }),
      (r) => this._t("bkRestoredOne", { name: r.title }));
  }

  _backup() {
    const bk = this._bk;
    if (bk.deleted === null && !bk.loading) this._bkLoad();
    const ask = (text, yes, onYes) => h("div", { class: "ask" }, h("span", { text }),
      h("button", { class: "btn go", type: "button", disabled: bk.busy ? "" : null, text: yes, onclick: onYes }),
      h("button", { class: "btn", type: "button", text: this._t("actCancel"), onclick: () => { bk.asking = null; this._render(); } }));
    const section = (title, ...children) => h("section", { class: "group" },
      h("h3", { class: "group-title" }, h("span", { text: this._t(title) })), h("div", { class: "bk" }, children));
    const text = (key) => h("p", { text: this._t(key) });

    const creating = (bk.plan || []).filter((step) => step.action === "create").length;
    const plan = bk.plan ? [
      h("table", {}, h("thead", {}, h("tr", {}, [this._t("colContract"), ""].map((label) => h("th", { text: label })))),
        h("tbody", {}, bk.plan.map((step) => h("tr", {},
          h("td", {}, step.title, h("span", { class: "t2", text: [this._t("sparte")[step.sparte] || step.sparte, step.objekt].filter(Boolean).join(" · ") })),
          h("td", { class: step.action === "create" ? "green-t" : "muted-t", text: this._t(step.action === "create" ? "bkPlanCreate" : "bkPlanSkip") }))))),
      creating === 0 ? h("p", { text: this._t("bkNothing") }) : null,
      creating > 0 && bk.asking === "restore"
        ? ask(this._t("bkAskRestore", { n: creating }), this._t("bkYesRestore"), () => this._bkRestore())
        : creating > 0 ? h("div", { class: "actions" }, h("button", { class: "btn go", type: "button", disabled: bk.busy ? "" : null,
          text: this._t("bkRestoreGo", { n: creating }), onclick: () => { bk.asking = "restore"; this._render(); } })) : null,
    ] : [];

    const deleted = (bk.deleted || []).map((item) => h("div", { class: "bk-row" },
      h("div", { class: "who" }, h("div", { class: "title", text: item.title }),
        h("div", { class: "meta", text: `${this._t("sparte")[item.sparte] || item.sparte} · ${this._t("bkDeletedOn", { d: this._date(item.deleted) })}` })),
      bk.asking === item.id
        ? ask(this._t("bkAskOne", { name: item.title }), this._t("bkYesOne"), () => this._bkRestoreOne(item))
        : bk.asking === `discard:${item.id}`
          ? ask(this._t("bkAskDiscard", { name: item.title }), this._t("bkYesDiscard"), () => this._bkDiscard(item))
          : h("div", { class: "actions" },
            h("button", { class: "btn blue", type: "button", disabled: bk.busy ? "" : null, text: this._t("bkRestoreOne"),
              onclick: () => { bk.asking = item.id; this._render(); } }),
            h("button", { class: "btn red", type: "button", disabled: bk.busy ? "" : null, text: this._t("bkDiscard"),
              onclick: () => { bk.asking = `discard:${item.id}`; this._render(); } }))));

    return h("div", {},
      bk.message ? h("p", { class: "bk-msg", role: "status", text: bk.message }) : null,
      bk.error ? h("div", { class: "err", role: "alert", text: bk.error }) : null,
      section("bkTitleExport", text("bkExportText"),
        h("div", { class: "actions" }, h("button", { class: "btn blue", type: "button", disabled: bk.busy ? "" : null,
          text: this._t("bkDownload"), onclick: () => this._bkDownload() }))),
      section("bkTitleRestore", text("bkRestoreText"),
        h("input", { class: "file", type: "file", accept: ".json,application/json", "aria-label": this._t("bkTitleRestore"),
          onchange: (ev) => this._bkPick(ev.target.files && ev.target.files[0]) }),
        plan),
      section("bkTitleDeleted", text("bkDeletedText"),
        deleted.length ? deleted : (bk.deleted === null ? null : h("p", { text: this._t("bkDeletedEmpty") }))));
  }
};
