"""Static guards for the panel JavaScript (no browser needed)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.tariffy.panel import PANEL_ELEMENT, PANEL_FILE

SOURCE = (
    Path(__file__).parent.parent / "custom_components" / "tariffy" / "frontend" / PANEL_FILE
).read_text(encoding="utf-8")

# Contract names, providers and tariffs are typed in by the user. They must only
# ever reach the page as text, never as HTML.
FORBIDDEN = (
    "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
    "eval(", "new Function", "setAttribute(\"on", "javascript:",
)


@pytest.mark.parametrize("sink", FORBIDDEN)
def test_no_html_sinks(sink):
    assert sink not in SOURCE


def test_registers_the_element_the_backend_announces():
    assert f'customElements.define("{PANEL_ELEMENT}"' in SOURCE


def test_is_small_enough_to_ship():
    # eigene Version: Preisaenderungen, Boni, Prognose, Zaehlerstand-Erfassung und Dokumente brauchen Platz
    assert len(SOURCE.encode("utf-8")) < 240_000


def _block(language: str) -> str:
    start = SOURCE.index(f"  {language}: {{")
    other = {"en": "de", "de": "en"}[language]
    # the two language blocks follow each other, the second ends at the first "};"
    if SOURCE.index(f"  {other}: {{") > start:
        end = SOURCE.index(f"  {other}: {{")
    else:
        end = SOURCE.index("};", start)
    return SOURCE[start:end]


def _keys(block: str) -> set[str]:
    body = block.split("\n", 1)[1]  # without the "en: {" / "de: {" line itself
    # drop the text literals first, so words inside a text ("Action failed: x")
    # are not mistaken for keys
    body = re.sub(r'"(?:\\.|[^"\\])*"', '""', body)
    return set(re.findall(r"\b([A-Za-z]+):", body))


def test_german_and_english_have_the_same_texts():
    en, de = _keys(_block("en")), _keys(_block("de"))
    assert en - de == set(), f"missing in German: {en - de}"
    assert de - en == set(), f"missing in English: {de - en}"


def test_every_used_text_exists():
    used = set(re.findall(r'this\._t\("([A-Za-z]+)"', SOURCE))
    # texts selected through a variable (tabs, tariff rows)
    used |= {"overview", "costs", "contracts", "highTariff", "lowTariff", "sparte"}
    missing = used - _keys(_block("en"))
    assert missing == set(), f"used but not defined: {missing}"


def test_empty_overview_groups_are_not_appended():
    """Issue #50: with only metered or only flat-rate contracts one group is null.
    `appendChild(null)` throws and leaves the whole panel empty, so the overview
    must never append a group directly."""
    assert "wrap.appendChild(group(" not in SOURCE
    assert "if (section) wrap.appendChild(section)" in SOURCE


# ------------------------------------------------------------------------------ light and dark design
def _style() -> str:
    start = SOURCE.index("const STYLE = `")
    return SOURCE[start : SOURCE.index("\n`;", start)]


def _block_of(selector: str) -> str:
    style = _style()
    start = style.index("\n" + selector + " {")
    return style[start : style.index("\n}\n", start)]


def test_the_light_design_redefines_every_colour_of_the_dark_one():
    """The panel is dark by default and light with the class `light` on its host: every colour variable of the dark
    block must have a light counterpart, or a colour would stay dark-on-light."""
    dark = set(re.findall(r"(--[a-z0-9-]+):", _block_of(":host")))
    light = set(re.findall(r"(--[a-z0-9-]+):", _block_of(":host(.light)")))
    colours = {name for name in dark if name not in {"--tile", "--tile-line", "--line"}}  # these derive from --w
    assert colours - light == set(), f"no light value for {colours - light}"
    assert "color-scheme: light" in _block_of(":host(.light)")


def test_no_colour_of_one_design_is_hard_coded():
    """White overlays and fixed hexadecimal colours would not follow the design: they live in the variables."""
    body = _style().split("* { box-sizing", 1)[1]  # everything after the two variable blocks
    assert "rgba(255,255,255" not in SOURCE
    assert not re.findall(r"#[0-9a-fA-F]{6}\b", body), re.findall(r"#[0-9a-fA-F]{6}\b", body)
    code = SOURCE.split("\n`;", 1)[1]  # the code after the styles: only the chart palette has fixed colours
    stray = [c for c in re.findall(r'"#[0-9a-fA-F]{3,6}"', code) if c not in re.search(r"CHART_COLORS = \[(.*?)\]", SOURCE).group(1)]
    assert not stray, stray


def test_the_panel_follows_the_theme_of_home_assistant():
    assert "this._hass.themes.darkMode" in SOURCE
    assert 'this.classList.toggle("light"' in SOURCE
    assert "this._applyTheme();" in SOURCE  # at every update of hass: a change of the theme shows at once


def test_an_own_icon_is_only_drawn_when_it_looks_like_one():
    assert "/^[a-z0-9_-]+:[a-z0-9_-]+$/i" in SOURCE  # mdi:car, nothing that could be markup
    assert 'customElements.get("ha-icon")' in SOURCE  # without Home Assistant's icon element: the category icon
