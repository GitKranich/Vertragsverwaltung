"""Browser smoke test of the Tariffy panel (Playwright, headless Chromium).

Loads tests/frontend/preview.html (a mock `hass` fed with fixtures.json) and checks that the panel draws without a
single browser error: every tab in German and English, wide and narrow, dark and light, the states loading / empty /
error, the element getting `hass` before it is defined, and every button in the contract rows and in the contract
details that opens a dialog.

Run it from the repository root:

    pip install playwright && python -m playwright install --with-deps chromium
    python tests/frontend/smoke.py

It is a script and not part of `pytest`, because the Home Assistant test plugin blocks sockets and threads that a
browser needs. CI runs it in its own job (see .github/workflows/validate.yml).
"""

from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent.parent
TABS = 5  # overview, costs, contracts, history, backup
FAILURES: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        FAILURES.append(what)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript"}

    def log_message(self, *args):  # noqa: D102
        pass


def serve() -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(_Quiet, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/tests/frontend/preview.html"


def open_page(browser, url: str, width: int = 1280) -> tuple[Page, list[str]]:
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.clock.set_fixed_time("2026-07-12T12:00:00Z")
    page.goto(url)
    page.wait_for_function("() => window.__panel && window.__panel.shadowRoot && window.__panel.shadowRoot.querySelector('.wrap')", timeout=10_000)
    return page, errors


def shadow_count(page: Page, selector: str) -> int:
    return page.evaluate("(s) => window.__panel.shadowRoot.querySelectorAll(s).length", selector)


def click_tab(page: Page, index: int) -> None:
    page.evaluate("(i) => window.__panel.shadowRoot.querySelectorAll('.tab')[i].click()", index)
    page.wait_for_timeout(150)


def tabs_and_variants(browser, base: str) -> None:
    for lang in ("de", "en"):
        for narrow in (False, True):
            for theme in ("dark", "light"):
                name = f"{lang} {'narrow' if narrow else 'wide'} {theme}"
                url = f"{base}?lang={lang}&theme={theme}" + ("&narrow=1" if narrow else "")
                page, errors = open_page(browser, url, 390 if narrow else 1280)
                check(shadow_count(page, ".tab") == TABS, f"{name}: {TABS} tabs")
                check(shadow_count(page, "style") == 1, f"{name}: styles loaded")
                light = page.evaluate("() => window.__panel.classList.contains('light')")
                check(light == (theme == "light"), f"{name}: follows the design of Home Assistant")
                for index in range(TABS):
                    click_tab(page, index)
                    check(shadow_count(page, ".wrap *") > 10, f"{name}: tab {index + 1} has content")
                # every contract opened in the list
                click_tab(page, 2)
                page.evaluate("() => window.__panel.shadowRoot.querySelectorAll('details').forEach((d) => { d.open = true; })")
                page.wait_for_timeout(150)
                check(not errors, f"{name}: no browser error {errors}")
                page.close()


def states(browser, base: str) -> None:
    for query, text in (("state=loading", "geladen"), ("state=empty", "Noch keine"), ("state=error", "Unknown command")):
        page, errors = open_page(browser, f"{base}?lang=de&{query}")
        shown = page.evaluate("() => window.__panel.shadowRoot.textContent")
        check(text in shown, f"{query}: shows '{text}'")
        check(not errors, f"{query}: no browser error {errors}")
        page.close()
    page, errors = open_page(browser, f"{base}?lang=de&late=1")
    check(shadow_count(page, ".tab") == TABS, "hass given before the element is defined: the panel draws itself")
    check(not errors, f"late definition: no browser error {errors}")
    page.close()
    page, errors = open_page(browser, f"{base}?lang=de&admin=0")
    click_tab(page, 2)
    check(shadow_count(page, ".row-actions") == 0, "without administrator rights there are no buttons to change a contract")
    check(not errors, f"no administrator: no browser error {errors}")
    page.close()


OVERLAP_JS = """() => { const r = window.__panel.shadowRoot; const bad = [];
  r.querySelectorAll('summary').forEach((s) => {
    const who = s.querySelector('.who'), end = s.querySelector(':scope > .end, :scope > .row-end');
    if (!who || !end) return;
    const box = end.getBoundingClientRect();
    const walker = document.createTreeWalker(who, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const range = document.createRange(); range.selectNodeContents(node);
      for (const k of range.getClientRects()) {
        if (k.right > box.left + 1 && k.left < box.right && k.bottom > box.top && k.top < box.bottom) {
          bad.push(who.textContent.slice(0, 40)); return;
        }
      }
    }
  });
  return bad; }"""

LONG_NAMES_JS = """() => { const p = window.__panel;
  p._contracts.filter((c) => !['loan'].includes(c.sparte)).forEach((c, i) => {
    c.title = ['Gebäudeversicherung', 'Mici Handyvertrag', 'Rechtsschutzversicherung'][i % 3];
    c.anbieter = 'Sparkassenversicherung'; c.tarif = 'Gebäude Feuer-/Elementarschadenversicherung';
  });
  p._render(); }"""


def long_names(browser, base: str) -> None:
    """Long names and tariffs must never run under the amount next to them (phones, large fonts)."""
    for width in (320, 390, 430, 760, 1280):
        for tab in (0, 2):
            page, errors = open_page(browser, f"{base}?lang=de&theme=light", width)
            page.evaluate(LONG_NAMES_JS)
            click_tab(page, tab)
            bad = page.evaluate(OVERLAP_JS)
            check(not bad, f"long names, {width} px, tab {tab + 1}: no text under the amount {bad}")
            check(not errors, f"long names, {width} px: no browser error {errors}")
            page.close()


def dialogs(browser, base: str) -> None:
    """Every button in the contract rows and in the opened contract details: a fresh page each time, so that one
    dialog cannot hide a problem of the next one."""
    url = f"{base}?lang=de"
    page, _ = open_page(browser, url)
    click_tab(page, 2)
    rows = shadow_count(page, ".row-actions button")
    page.evaluate("() => window.__panel.shadowRoot.querySelectorAll('details').forEach((d) => { d.open = true; })")
    details = shadow_count(page, ".detail button")
    page.close()
    check(rows > 0 and details > 0, f"buttons to try: {rows} in the rows, {details} in the details")
    opened = 0
    for where, count in ((".row-actions button", rows), (".detail button", details)):
        for i in range(count):
            page, errors = open_page(browser, url)
            click_tab(page, 2)
            page.evaluate("() => window.__panel.shadowRoot.querySelectorAll('details').forEach((d) => { d.open = true; })")
            label = page.evaluate(
                "([s, i]) => { const b = window.__panel.shadowRoot.querySelectorAll(s)[i];"
                " const l = b.getAttribute('aria-label') || b.textContent.trim(); b.click(); return l; }",
                [where, i],
            )
            page.wait_for_timeout(250)
            dialog = shadow_count(page, ".overlay .dialog")
            opened += 1 if dialog else 0
            if dialog:  # the first button of the dialog's own row ("Cancel", "Close") must close it again
                page.keyboard.press("Escape")
                page.wait_for_timeout(100)
            check(not errors, f"{where} #{i} '{label}': no browser error {errors}")
            page.close()
    check(opened > 0, f"{opened} buttons opened a dialog")


def main() -> int:
    server, base = serve()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            tabs_and_variants(browser, base)
            states(browser, base)
            long_names(browser, base)
            dialogs(browser, base)
            browser.close()
    finally:
        server.shutdown()
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
