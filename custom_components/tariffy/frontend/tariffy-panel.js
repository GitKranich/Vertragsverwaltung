/* Tariffy panel.
 *
 * Overview of all contracts, fed by the integration's websocket API
 * (tariffy/subscribe); administrators can also maintain the contracts. Plain web
 * component, no build step, system font.
 *
 * Security: text that comes from the user (contract names, providers, tariffs)
 * is only ever written with textContent / text nodes, never as HTML.
 */

/* The texts, icons and styles are in their own files next to this one. They are loaded with the version of this
 * file in the URL (?v=...): the static path is cached for a long time, so a browser must never combine new code
 * with old texts or styles after an update. */
const VERSION = new URL(import.meta.url).search;
let I18N;
let ICONS;
let STYLE;
/* No top-level await: the element must be defined the moment this file has run. Home Assistant may create the
 * element and hand it `hass` before it is defined (the properties then shadow the setters), and a module that is
 * still waiting for its parts defines it too late. The parts arrive a moment later and the panel draws itself then. */
/* The tabs and dialogs are modules of their own (tariffy-overview.js, tariffy-costs.js, ...). Each one returns a class
 * whose methods are copied onto the panel once everything has arrived, so they are written exactly as in the class. */
const MIXINS = ["overview", "flow", "backup", "contract", "meter", "docs", "history", "costs", "contracts", "objekte"];
const PARTS = Promise.all([
  import(`./tariffy-i18n.js${VERSION}`),
  import(`./tariffy-icons.js${VERSION}`),
  import(`./tariffy-style.js${VERSION}`),
  ...MIXINS.map((name) => import(`./tariffy-${name}.js${VERSION}`)),
]).then(([i18n, icons, style, ...mixins]) => {
  ({ I18N } = i18n);
  ({ ICONS } = icons);
  ({ STYLE } = style);
  const shared = { h, svg, icon, isNum, METERED, INTERVAL_MONTHS, GRID, TABS, FLOWS, CHART_COLORS, ownIcon, I18N, ICONS, SVG_NS, TAB_KEY };
  for (const mixin of mixins) {
    const methods = Object.getOwnPropertyDescriptors(mixin.default(shared).prototype);
    delete methods.constructor;
    for (const [name, descriptor] of Object.entries(methods)) {
      if (Object.prototype.hasOwnProperty.call(TariffyPanel.prototype, name)) throw new Error(`Tariffy: ${name} is defined twice`);
      Object.defineProperty(TariffyPanel.prototype, name, descriptor);
    }
  }
});

const SVG_NS = "http://www.w3.org/2000/svg";
const TABS = ["overview", "costs", "contracts", "history", "backup"];
// contracts that are billed by consumption; everything else (internet, mobile, ...) is a flat rate
// column widths shared by the cost tables, so that their columns line up
const GRID = ["24%", "15.2%", "15.2%", "15.2%", "15.2%", "15.2%"];
const INTERVAL_MONTHS = { monthly: 1, quarterly: 3, halfyearly: 6, yearly: 12 };
const METERED = new Set(["electricity", "electricity_ht_nt", "gas", "water"]);
const TAB_KEY = "tariffy-panel-tab";



/* ---------- DOM helpers (text only, never HTML) ---------- */

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return el;
}

function svg(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) el.setAttribute(key, value);
  for (const child of children.flat()) if (child) el.appendChild(child);
  return el;
}

function icon(sparte) {
  const parts = ICONS[sparte] || ICONS.other;
  return svg(
    "svg", { viewBox: "0 0 24 24", "aria-hidden": "true" },
    parts.map(([tag, attrs]) => svg(tag, attrs))
  );
}

const FLOWS = {
  // the integration's "Configure" dialog (next contract) and its "Reconfigure" dialog (the contract itself)
  next: { api: "config/config_entries/options/flow", body: (c) => ({ handler: c.entry_id }), category: "options" },
  edit: { api: "config/config_entries/flow", body: (c) => ({ handler: "tariffy", entry_id: c.entry_id }), category: "config" },
  // the integration's "Add entry" dialog
  create: { api: "config/config_entries/flow", body: () => ({ handler: "tariffy" }), category: "config" },
};

/* an own icon of a contract: "mdi:car" and the like, nothing else */
const ownIcon = (value) => (typeof value === "string" && /^[a-z0-9_-]+:[a-z0-9_-]+$/i.test(value.trim()) ? value.trim() : null);

const CHART_COLORS = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#b07aa1", "#edc948", "#9c755f"];

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/* ---------- the panel ---------- */

class TariffyPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._unsub = null;
    this._contracts = null; // null = loading; the contracts of the chosen property (see tariffy-objekte.js)
    this._all = null; // all contracts as the backend sends them
    this._objekte = []; // the properties ("Objekte") and the default one
    this._defaultObjekt = null;
    this._objekt = null; // the property shown; null = the default one when the panel opens
    this._error = null;
    this._narrow = false;
    this._tab = this._loadTab();
    this._open = new Set();
    this._compact = false;
    this._confirming = null;
    this._busy = null;
    this._actionError = null;
    this._bk = { deleted: null, loading: false, plan: null, backup: null, file: null, message: null, error: null, busy: false, asking: null };
    this.attachShadow({ mode: "open" });
    // values set before the element was defined (Home Assistant creates it first) sit on the object itself and
    // hide the setters: take them back through the setters
    for (const prop of ["hass", "narrow", "panel"]) {
      if (Object.prototype.hasOwnProperty.call(this, prop)) {
        const value = this[prop];
        delete this[prop];
        this[prop] = value;
      }
    }
    PARTS.then(() => { if (this._hass) this._render(); }, (err) => {
      this.shadowRoot.replaceChildren(Object.assign(document.createElement("pre"), {
        textContent: `Tariffy: ${(err && err.message) || err}`, style: "color:#f87171;padding:16px",
      }));
    });
  }

  set hass(hass) {
    const first = !this._hass;
    const languageChanged = this._hass && this._hass.language !== hass.language;
    this._hass = hass;
    this._applyTheme(); // at every update: a change of the design in Home Assistant shows at once
    if (first) {
      this._subscribe();
      this._render();
    } else if (languageChanged) {
      this._render();
    }
  }

  /* the light or the dark colours: whatever Home Assistant shows (its own light/dark/auto setting), else the system's */
  _applyTheme() {
    const ha = this._hass && this._hass.themes && this._hass.themes.darkMode;
    const dark = typeof ha === "boolean" ? ha
      : !(window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches);
    this.classList.toggle("light", !dark);
  }

  set narrow(value) {
    this._narrow = Boolean(value);
    if (this._hass) this._render();
  }

  set panel(value) {
    this._panel = value;
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
    if (typeof ResizeObserver === "function" && !this._observer) {
      this._observer = new ResizeObserver((entries) => {
        const width = entries[entries.length - 1].contentRect.width;
        const compact = width > 0 && width <= 760;
        if (compact !== this._compact) {
          this._compact = compact;
          if (this._hass) this._render();
        }
      });
    }
    if (this._observer) this._observer.observe(this);
  }

  disconnectedCallback() {
    if (this._observer) this._observer.disconnect();
    this._closeFlow();
    this._objekt = null; // opened again, the panel shows the default property
    if (this._unsub) {
      this._unsub();
      this._unsub = null;
    }
  }

  async _subscribe() {
    try {
      this._unsub = await this._hass.connection.subscribeMessage(
        (event) => {
          this._all = Array.isArray(event.contracts) ? event.contracts : [];
          this._objekte = Array.isArray(event.objekte) ? event.objekte : [];
          this._defaultObjekt = event.default_objekt || null;
          this._pickObjekt();
          this._filterObjekt();
          this._error = null;
          this._render();
          // the dialog that manages the properties shows the new state at once
          if (this._dlg && this._dlg.mode === "objekte" && this._renderObjekteDialog) this._renderDialog();
        },
        { type: "tariffy/subscribe" }
      );
    } catch (err) {
      this._error = String((err && err.message) || err);
      this._render();
    }
  }

  /* ----- the property shown (part of the element itself: the subscription calls it before the modules arrive) ----- */

  /* the property to show: the chosen one if it still exists, else the default one, else the first */
  _pickObjekt() {
    const list = this._objekte || [];
    const has = (id) => id && list.some((o) => o.id === id);
    const before = this._objekt;
    this._objekt = has(this._objekt) ? this._objekt : has(this._defaultObjekt) ? this._defaultObjekt : (list[0] && list[0].id) || null;
    if (this._objekt && this._objekt !== before) this._tellObjekt();
  }

  /* the contracts of the chosen property (all of them while there are no properties, for example an older backend) */
  _filterObjekt() {
    const all = this._all;
    if (all === null) { this._contracts = null; return; }
    this._contracts = this._objekt && (this._objekte || []).length ? all.filter((c) => c.objekt_id === this._objekt) : all;
  }

  /* the backend preselects this property when a new contract is added */
  _tellObjekt() {
    if (!this._hass || !this._objekt) return;
    try {
      Promise.resolve(this._hass.callWS({ type: "tariffy/objekt_aktiv", objekt_id: this._objekt })).catch(() => {});
    } catch (_err) { /* only a preselection */ }
  }

  _loadTab() {
    try {
      const tab = window.localStorage.getItem(TAB_KEY);
      return TABS.includes(tab) ? tab : "overview";
    } catch (_err) {
      return "overview";
    }
  }

  _saveTab(tab) {
    try {
      window.localStorage.setItem(TAB_KEY, tab);
    } catch (_err) { /* private mode */ }
  }

  get _isAdmin() {
    return Boolean(this._hass && this._hass.user && this._hass.user.is_admin);
  }

  /* ----- language, numbers, dates ----- */

  get _lang() {
    const code = String((this._hass && this._hass.language) || "en").toLowerCase();
    return code.startsWith("de") ? "de" : "en";
  }

  _t(key, vars) {
    let text = I18N[this._lang][key] ?? I18N.en[key] ?? key;
    for (const [name, value] of Object.entries(vars || {})) text = text.replace(`{${name}}`, value);
    return text;
  }

  get _locale() {
    return (this._hass && this._hass.locale && this._hass.locale.language) ||
      (this._hass && this._hass.language) || "en";
  }

  _money(value, currency, { sign = false, digits = 0 } = {}) {
    if (!isNum(value)) return "–";
    let text;
    try {
      text = new Intl.NumberFormat(this._locale, {
        style: "currency", currency: currency || "EUR",
        minimumFractionDigits: digits, maximumFractionDigits: digits,
      }).format(Math.abs(value));
    } catch (_err) {
      text = `${Math.abs(value).toFixed(digits)} ${currency || ""}`;
    }
    if (value < 0) return `−${text}`;
    return sign && value > 0 ? `+${text}` : text;
  }

  _num(value, digits = 0) {
    if (!isNum(value)) return "–";
    return new Intl.NumberFormat(this._locale, {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(value);
  }

  _price(value, currency, unit) {
    if (!isNum(value)) return "–";
    return `${this._money(value, currency, { digits: 4 })} / ${unit}`;
  }

  /* A negative recommended instalment means the credit already covers the costs
   * until the end of the contract: show 0 instead of a negative instalment. */
  _instalment(value, currency) {
    return isNum(value) ? this._money(Math.max(0, value), currency, { digits: 2 }) : "–";
  }

  /* What the recommended instalment means for the current one: raise it, lower it, none needed, or fine as it is. */
  _advice(c) {
    const rec = c.abschlag_anpassung_empfohlen;
    if (!isNum(rec)) return null;
    if (rec < 0) return this._t("adviceNone");
    const v = this._money(rec, c.currency, { digits: 2 });
    if (!isNum(c.abschlag) || Math.abs(rec - c.abschlag) < 0.5) return this._t("adviceOk");
    return this._t(rec > c.abschlag ? "adviceRaise" : "adviceLower", { v });
  }

  _covered(value) {
    return isNum(value) && value < 0;
  }

  _parse(iso) {
    if (typeof iso !== "string") return null;
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
  }

  _date(iso) {
    const d = this._parse(iso);
    return d
      ? new Intl.DateTimeFormat(this._locale, { day: "2-digit", month: "2-digit", year: "numeric" }).format(d)
      : "–";
  }

  _today() {
    const n = new Date();
    return new Date(n.getFullYear(), n.getMonth(), n.getDate());
  }

  _days(iso) {
    const d = this._parse(iso);
    return d ? Math.round((d - this._today()) / 86400000) : null;
  }

  _houseCostType(key) {
    const labels = (I18N[this._lang] && I18N[this._lang].hauskostenart) || I18N.en.hauskostenart;
    return labels[key] || key; // a type the user typed is shown as it is
  }

  _insuranceType(key) {
    const labels = (I18N[this._lang] && I18N[this._lang].versicherungsart) || I18N.en.versicherungsart;
    return labels[key] || key;
  }

  _unit(contract) {
    if (contract.verbrauch_einheit) return contract.verbrauch_einheit;
    return contract.sparte === "gas" || contract.sparte === "water" ? "m³" : "kWh";
  }

  _priceUnit(contract) {
    return contract.sparte === "water" ? this._unit(contract) : "kWh";
  }

  /* ----- derived values per contract ----- */

  _derive(c) {
    const begin = this._parse(c.beginn);
    const end = this._parse(c.ende);
    let elapsed = null;
    if (begin && end && end > begin) {
      elapsed = Math.min(1, Math.max(0, (this._today() - begin) / (end - begin)));
    }
    const paid = isNum(c.kosten_bisher) && isNum(c.guthaben_bisher) ? c.kosten_bisher + c.guthaben_bisher : null;
    const hasMetering = isNum(c.prognose_real);
    // consumption already measured (also on the first day, before there is a forecast)
    const measured = isNum(c.kosten_bisher) || isNum(c.verbrauch_bisher);
    // the state (credit / balance due / warning) always comes last, so it sits at the far right
    // of every card; further badges (cancel now, switch on ...) stand to its left
    const chips = [];
    if (c.archivierbar) chips.push({ text: this._t("expired"), tone: "muted" });
    if (c.erinnerung_aktiv) chips.push({ text: this._t("cancelDue"), tone: "orange" });
    if (c.wechsel) chips.push({ text: this._t("switchOn", { d: this._date(c.wechsel) }), tone: "blue" });
    if (c.abschlag_warnung_aktiv) chips.push({ text: this._t("warning"), tone: "red" });
    else if (hasMetering) {
      chips.push(c.prognose_real >= 0
        ? { text: this._t("credit"), tone: "green" }
        : { text: this._t("balanceDue"), tone: "red" });
    }
    return { elapsed, paid, hasMetering, measured, chips };
  }

  /* ----- rendering ----- */

  _render() {
    if (!STYLE) return; // the texts and styles are still loading: PARTS draws the panel when they are here
    const root = this.shadowRoot;
    root.replaceChildren(h("style", { text: STYLE }));
    // the language of the texts, so that the browser can hyphenate long words (names on narrow screens)
    const wrap = h("div", { class: "wrap", lang: this._lang });
    wrap.appendChild(this._top());

    if (this._error) {
      wrap.appendChild(h("div", { class: "state" }, h("h2", { text: this._t("error" ) }), h("div", { text: this._error })));
    } else if (this._contracts === null) {
      wrap.appendChild(h("div", { class: "state", text: this._t("loading") }));
    } else if (this._tab === "backup" && this._isAdmin) {
      wrap.appendChild(this._backup());
    } else if (this._all.length === 0) {
      wrap.appendChild(this._empty());
    } else if (this._contracts.length === 0) {
      wrap.appendChild(this._objektEmpty());
    } else if (this._tab === "history") {
      wrap.appendChild(this._history());
    } else if (this._live.length === 0 && this._loans.length === 0) {
      wrap.appendChild(h("div", { class: "state", text: this._t("allArchived") }));
    } else if (this._tab === "costs") {
      wrap.appendChild(this._costs());
    } else if (this._tab === "contracts") {
      wrap.appendChild(this._contractList());
    } else {
      if (this._live.length) wrap.appendChild(this._summary());
      const group = (key, list, extra) => (list.length ? h("section", { class: "group" },
        h("h3", { class: "group-title" }, h("span", { text: this._t(key) }), h("span", { class: "count", text: String(list.length) })),
        h("div", { class: `grid ${extra}` }, list.map((c) => this._card(c)))) : null);
      // a group is null when there is no contract of that kind: skip it (appendChild(null) throws
      // and leaves the whole panel empty for anyone who has only metered or only flat-rate contracts)
      for (const section of [
        group("groupMetered", this._live.filter((c) => METERED.has(c.sparte)), "metered"),
        group("groupFlat", this._live.filter((c) => !METERED.has(c.sparte) && c.sparte !== "hauskosten"), "flat"),
        group("groupHouse", this._house, "flat"),
        group("groupLoans", this._loans, "flat"),
      ]) {
        if (section) wrap.appendChild(section);
      }
    }
    root.appendChild(wrap);
    if (this._dlgEl) root.appendChild(this._dlgEl);
  }

  _top() {
    const menu = this._narrow && customElements.get("ha-menu-button")
      ? Object.assign(document.createElement("ha-menu-button"), { hass: this._hass, narrow: this._narrow })
      : null;
    const tabs = h(
      "div", { class: "tabs", role: "tablist" },
      TABS.filter((tab) => tab !== "backup" || this._isAdmin).map((tab) => h("button", {
        class: "tab", role: "tab", "aria-selected": String(this._tab === tab), text: this._t(tab),
        onclick: () => { this._tab = tab; this._saveTab(tab); this._render(); },
      }))
    );
    return h(
      "div", { class: "top" },
      menu,
      h("div", { class: "brand" }, "Tariffy"),
      this._objektBar(),
      tabs,
      h("div", { class: "spacer" })
    );
  }

  _empty() {
    return h(
      "div", { class: "state" },
      h("h2", { text: this._t("emptyTitle") }),
      h("p", { text: this._t("emptyText") }),
      h("a", { href: "/config/integrations/dashboard/add?domain=tariffy", text: this._t("emptyLink") })
    );
  }
}

if (!customElements.get("tariffy-panel")) {
  customElements.define("tariffy-panel", TariffyPanel);
}
