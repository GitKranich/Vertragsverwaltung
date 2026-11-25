/* Documents from Paperless-ngx linked to a contract (integration "Paperless-ngx Insights").
 * Part of the Tariffy panel: tariffy-panel.js loads it with its own version and copies the methods onto the
 * panel element. The methods are written as in the class (they use `this` of the panel). */

export default ({ h, icon, isNum, I18N }) => class {
  /* ----- documents from Paperless-ngx (integration "Paperless-ngx Insights"): only links are stored ----- */
  _plLoad() {
    if (this._pl || !this._hass) return;
    this._pl = { state: "loading", entries: [] };
    Promise.resolve(this._hass.callWS({ type: "paperless_ngx_insights/entries" }))
      .then((list) => {
        const entries = Array.isArray(list) ? list.filter((e) => e && typeof e.entry_id === "string") : [];
        this._pl = { state: entries.length ? "ok" : "none", entries };
      })
      .catch(() => { this._pl = { state: "none", entries: [] }; }) // not installed, or no access for this user
      .then(() => this._render());
  }

  _docSection(c) {
    const items = c.dokumente || [];
    this._plLoad();
    const pl = this._pl || { state: "loading", entries: [] };
    const canLink = this._isAdmin && pl.state === "ok";
    if (!items.length && !canLink) return null;
    const btn = (name, label, onclick, off) => h("button", {
      class: "icon-btn", type: "button", title: label, "aria-label": label, disabled: off ? "" : null, onclick,
    }, icon(name));
    const rows = items.map((item) => {
      const off = !pl.entries.some((e) => e.entry_id === item.paperless_entry);
      const sub = [item.bezeichnung ? item.titel : null, item.erstellt ? this._date(item.erstellt) : null].filter(Boolean).join(" · ");
      const web = typeof item.web_url === "string" && /^https?:\/\//.test(item.web_url)
        ? `${item.web_url}/documents/${encodeURIComponent(item.id)}/details` : null;
      return h("div", { class: "doc-row" },
        h("span", { class: "dn" }, item.bezeichnung || item.titel, sub ? h("span", { class: "t2", text: sub }) : null),
        h("span", { class: "da" },
          btn("eye", this._t("docOpen"), () => this._docOpen(c, item), off),
          btn("download", this._t("docDownload"), () => this._docDownload(c, item), off),
          web ? h("a", { class: "icon-btn", href: web, target: "_blank", rel: "noopener noreferrer",
            title: this._t("docInPaperless"), "aria-label": this._t("docInPaperless") }, icon("external")) : null,
          this._isAdmin ? btn("edit", this._t("docEdit"), () => this._openDoc(c, item.index)) : null));
    });
    const msg = this._docMsg && this._docMsg.id === c.entry_id ? this._docMsg.text : null;
    return h("div", { class: "sec" }, h("h4", { text: this._t("secDocs") }), rows,
      items.length && pl.state === "none" ? h("p", { class: "hint", text: this._t("docOffline") }) : null,
      msg ? h("div", { class: "err", role: "alert", text: msg }) : null,
      canLink ? h("div", { class: "once-add" }, h("button", {
        class: "btn", type: "button", onclick: () => this._openDoc(c, null),
      }, icon("plus"), this._t("docLink"))) : null);
  }

  _docFail(c, text) {
    this._docMsg = { id: c.entry_id, text };
    this._render();
  }

  /* preview in a new tab: the PDF is fetched with the HA login (the URL needs it), then shown from memory */
  async _docOpen(c, item) {
    this._docMsg = null;
    if (typeof this._hass.fetchWithAuth !== "function") { this._docDownload(c, item); return; }
    const win = window.open("", "_blank"); // opened right away, a later one would be blocked as a pop-up
    if (win) win.opener = null;
    try {
      const res = await this._hass.fetchWithAuth(
        `/api/paperless_ngx_insights/${encodeURIComponent(item.paperless_entry)}/documents/${encodeURIComponent(item.id)}/preview`);
      if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status });
      const url = URL.createObjectURL(await res.blob());
      setTimeout(() => URL.revokeObjectURL(url), 300000);
      if (win) win.location.href = url;
      else window.open(url, "_blank");
    } catch (err) {
      if (win) win.close();
      this._docFail(c, err && (err.status === 413 || err.status === 415)
        ? this._t("docTooLarge") : this._t("docOpenFailed", { m: this._errorText(err) }));
    }
  }

  /* download: Paperless-ngx Insights hands out a path that is valid for 30 seconds and bound to this login */
  async _docDownload(c, item) {
    this._docMsg = null;
    try {
      const res = await this._hass.callWS({
        type: "paperless_ngx_insights/download", entry_id: item.paperless_entry, document_id: item.id, kind: "pdf",
      });
      if (!res || typeof res.url !== "string" || !res.url.startsWith("/")) throw new Error("no url");
      const a = h("a", { href: res.url, download: "" });
      this.shadowRoot.appendChild(a);
      a.click();
      a.remove();
    } catch (err) {
      this._docFail(c, this._t("docOpenFailed", { m: this._errorText(err) }));
    }
  }

  _openDoc(c, index) {
    this._closeFlow();
    const pl = this._pl || { entries: [] };
    const item = index === null ? null : (c.dokumente || []).find((d) => d.index === index) || null;
    this._dlg = {
      mode: "doc", stage: "form", id: c.entry_id, name: c.title, index, item, flowId: null, asking: null, fields: [], values: {},
      linked: new Set((c.dokumente || []).map((d) => `${d.paperless_entry}/${d.id}`)),
      entry: pl.entries.length ? pl.entries[0].entry_id : null,
      text: index === null ? String(c.anbieter || "") : "", searchMode: "text",
      results: null, count: 0, page: 1, searching: false, picked: null, webUrl: null,
      label: item ? item.bezeichnung || "" : "", message: null,
    };
    this._renderDocDialog();
    if (index === null && this._dlg.text.trim()) this._docSearch(false); // the provider is a good first guess
  }

  async _docSearch(more) {
    const dlg = this._dlg;
    if (!dlg || dlg.searching || !dlg.entry || !dlg.text.trim()) return;
    dlg.searching = true;
    dlg.message = null;
    const page = more ? dlg.page + 1 : 1;
    this._renderDocDialog();
    try {
      const res = await this._hass.callWS({
        type: "paperless_ngx_insights/search", entry_id: dlg.entry, text: dlg.text.trim().slice(0, 500),
        mode: dlg.searchMode, page, page_size: 20,
      });
      if (this._dlg !== dlg) return;
      const docs = (Array.isArray(res && res.documents) ? res.documents : []).filter((d) => d && isNum(d.id));
      dlg.results = more ? [...(dlg.results || []), ...docs] : docs;
      dlg.count = isNum(res.count) ? res.count : dlg.results.length;
      dlg.page = page;
      dlg.webUrl = typeof res.web_url === "string" && /^https?:\/\//.test(res.web_url) ? res.web_url.slice(0, 300) : null;
      if (!more) dlg.picked = null;
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.message = this._t("docSearchFailed", { m: this._errorText(err) });
    }
    dlg.searching = false;
    this._renderDocDialog();
  }

  async _saveDoc() {
    const dlg = this._dlg;
    const label = dlg.label.trim().slice(0, 100) || null;
    let values;
    if (dlg.index === null) {
      if (!dlg.picked) { dlg.message = this._t("docPickFirst"); this._renderDocDialog(); return; }
      const d = dlg.picked;
      values = {
        paperless_entry: dlg.entry, id: d.id, titel: String(d.title || `#${d.id}`).slice(0, 300),
        erstellt: typeof d.created === "string" ? d.created.slice(0, 10) : null, bezeichnung: label, web_url: dlg.webUrl,
      };
    } else {
      const it = dlg.item || {};
      values = {
        paperless_entry: it.paperless_entry, id: it.id, titel: it.titel, erstellt: it.erstellt || null,
        bezeichnung: label, web_url: it.web_url || null,
      };
    }
    await this._callDoc(dlg, { type: "tariffy/dokument_save", entry_id: dlg.id, values, ...(dlg.index === null ? {} : { index: dlg.index }) });
  }

  async _callDoc(dlg, message) {
    dlg.stage = "saving";
    this._renderDocDialog();
    try {
      await this._hass.callWS(message);
    } catch (err) {
      if (this._dlg !== dlg) return;
      dlg.stage = "form";
      dlg.asking = null;
      const code = String((err && (err.code || (err.body && err.body.code))) || "");
      const key = `docErr_${code}`;
      dlg.message = I18N[this._lang][key] || I18N.en[key] ? this._t(key) : this._termError(err);
      this._renderDocDialog();
      return;
    }
    if (this._dlg === dlg) this._closeFlow();
    this._render();
  }

  _renderDocDialog() {
    const dlg = this._dlg;
    if (!dlg) return;
    const busy = dlg.stage === "saving";
    const pl = this._pl || { entries: [] };
    const body = [
      h("h2", { id: "dlg-title", text: this._t(dlg.index === null ? "docTitleLink" : "docTitleEdit") }),
      h("p", { class: "hint", text: dlg.name }),
    ];
    if (dlg.index === null) {
      body.push(h("p", { class: "hint", text: this._t("docHint") }));
      if (pl.entries.length > 1) {
        body.push(h("label", { class: "fld" }, h("span", { class: "lbl", text: this._t("docConnection") }),
          h("select", { name: "doc-entry", onchange: (ev) => { dlg.entry = ev.target.value; dlg.results = null; dlg.picked = null; this._renderDocDialog(); } },
            pl.entries.map((e) => h("option", { value: e.entry_id, text: e.title || e.entry_id })))));
      }
      body.push(h("div", { class: "doc-search" },
        h("label", { class: "fld" }, h("span", { class: "lbl", text: this._t("docQuery") }),
          h("input", { name: "doc-q", type: "search", autocomplete: "off", maxlength: "500",
            oninput: (ev) => { dlg.text = ev.target.value; },
            onkeydown: (ev) => { if (ev.key === "Enter") { ev.preventDefault(); this._docSearch(false); } } })),
        h("label", { class: "fld mode" }, h("span", { class: "lbl", text: " " }),
          h("select", { name: "doc-mode", onchange: (ev) => { dlg.searchMode = ev.target.value; } },
            h("option", { value: "text", text: this._t("docModeText") }), h("option", { value: "title", text: this._t("docModeTitle") }))),
        h("button", { class: "btn", type: "button", disabled: dlg.searching ? "" : null,
          text: this._t(dlg.searching ? "docSearching" : "docSearch"), onclick: () => this._docSearch(false) })));
      if (dlg.results) {
        const hits = dlg.results.map((d) => {
          const done = dlg.linked.has(`${dlg.entry}/${d.id}`);
          const on = dlg.picked && dlg.picked.id === d.id;
          return h("button", {
            class: `doc-hit${on ? " on" : ""}`, type: "button", disabled: done ? "" : null, "aria-pressed": on ? "true" : "false",
            onclick: () => { dlg.picked = d; dlg.message = null; this._renderDocDialog(); },
          }, String(d.title || `#${d.id}`),
          h("span", { class: "t2", text: [d.created ? this._date(String(d.created).slice(0, 10)) : null, done ? this._t("docLinked") : null].filter(Boolean).join(" · ") }));
        });
        body.push(h("div", { class: "doc-results" },
          h("p", { class: "hint", text: hits.length ? this._t("docFound", { n: dlg.count }) : this._t("docNone") }), hits,
          dlg.results.length < dlg.count ? h("button", { class: "btn", type: "button", disabled: dlg.searching ? "" : null,
            text: this._t("docMore"), onclick: () => this._docSearch(true) }) : null));
      }
    } else if (dlg.item) {
      body.push(h("p", { class: "hint", text: [dlg.item.titel, dlg.item.erstellt ? this._date(dlg.item.erstellt) : null].filter(Boolean).join(" · ") }));
    }
    body.push(h("label", { class: "fld" }, h("span", { class: "lbl", text: this._t("docLabel") }),
      h("input", { name: "doc-label", type: "text", autocomplete: "off", maxlength: "100", oninput: (ev) => { dlg.label = ev.target.value; } })));
    if (dlg.message) body.push(h("div", { class: "err", role: "alert", text: dlg.message }));
    const buttons = [];
    if (dlg.asking === "delete") {
      buttons.push(h("div", { class: "ask" },
        h("span", { text: this._t("docRemoveAsk") }),
        h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("docRemoveYes"),
          onclick: () => this._callDoc(dlg, { type: "tariffy/dokument_delete", entry_id: dlg.id, index: dlg.index }) }),
        h("button", { class: "btn", type: "button", text: this._t("actCancel"), onclick: () => { dlg.asking = null; this._renderDocDialog(); } })));
    } else {
      buttons.push(h("button", { class: "btn go", type: "button", disabled: busy || (dlg.index === null && !dlg.picked) ? "" : null,
        text: this._t(busy ? "dlgSaving" : "dlgSave"), onclick: () => this._saveDoc() }));
      buttons.push(h("button", { class: "btn", type: "button", disabled: busy ? "" : null, text: this._t("actCancel"), onclick: () => this._closeFlow() }));
      if (dlg.index !== null) {
        buttons.push(h("button", { class: "btn red", type: "button", disabled: busy ? "" : null, text: this._t("docRemove"),
          onclick: () => { dlg.asking = "delete"; this._renderDocDialog(); } }));
      }
    }
    body.push(h("div", { class: "actions" }, buttons));
    // keep what was typed and the place of the cursor: the dialog is drawn again after every search
    const active = this._dlgEl && this.shadowRoot.activeElement;
    const focusName = active && active.getAttribute ? active.getAttribute("name") : null;
    this._mountDialog(dlg, body, busy);
    const set = (name, value) => { const el = this._dlgEl.querySelector(`[name="${name}"]`); if (el) el.value = value; };
    set("doc-q", dlg.text);
    set("doc-mode", dlg.searchMode);
    set("doc-entry", dlg.entry || "");
    set("doc-label", dlg.label);
    const target = !dlg.focused ? (dlg.index === null ? "doc-q" : "doc-label") : focusName;
    dlg.focused = true;
    const el = target && this._dlgEl.querySelector(`[name="${target}"]`);
    if (el) el.focus();
  }
};
