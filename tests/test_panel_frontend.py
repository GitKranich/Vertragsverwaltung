"""Static guards for the panel JavaScript (no browser needed)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.tariffy.panel import PANEL_ELEMENT, PANEL_FILE

FRONTEND = Path(__file__).parent.parent / "custom_components" / "tariffy" / "frontend"
# the panel is split into the code (SOURCE) and the files with its texts, icons and styles
SOURCE = (FRONTEND / PANEL_FILE).read_text(encoding="utf-8")
TEXTS = (FRONTEND / "tariffy-i18n.js").read_text(encoding="utf-8")
STYLE = (FRONTEND / "tariffy-style.js").read_text(encoding="utf-8")
FILES = sorted(FRONTEND.glob("*.js"))
DATA_PARTS = {"tariffy-i18n.js": "I18N", "tariffy-icons.js": "ICONS", "tariffy-style.js": "STYLE"}
# the tabs and dialogs: modules that return a class whose methods are copied onto the panel
MIXINS = [p for p in FILES if p.name != PANEL_FILE and p.name not in DATA_PARTS]
# all the code of the panel (the element and its tabs and dialogs), without texts, icons and styles
CODE = "\n".join(p.read_text(encoding="utf-8") for p in FILES if p.name not in DATA_PARTS)

# Contract names, providers and tariffs are typed in by the user. They must only
# ever reach the page as text, never as HTML.
FORBIDDEN = (
    "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
    "eval(", "new Function", "setAttribute(\"on", "javascript:",
)


@pytest.mark.parametrize("sink", FORBIDDEN)
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_html_sinks(sink, path):
    assert sink not in path.read_text(encoding="utf-8")


def test_registers_the_element_the_backend_announces():
    assert f'customElements.define("{PANEL_ELEMENT}"' in SOURCE


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_file_is_small_enough_to_ship(path):
    # eigene Version: Preisaenderungen, Boni, Prognose, Zaehlerstand-Erfassung und Dokumente brauchen Platz
    assert len(path.read_bytes()) < 190_000


def test_the_element_is_defined_without_waiting_for_its_parts():
    """Home Assistant may create the element and set `hass` before it is defined; a module that waits (top-level
    await) for its parts defines it late, the values then hide the setters and the panel stays black."""
    assert not re.search(r"^(const|let|var)\s[^=\n]*=\s*await\b|^await\b", SOURCE, flags=re.M)
    assert "customElements.define(" in SOURCE
    # values given before the definition are taken back through the setters
    assert 'for (const prop of ["hass", "narrow", "panel"])' in SOURCE
    # nothing draws before the parts are there
    assert "if (!STYLE) return;" in SOURCE


def test_the_panel_loads_its_parts_with_its_own_version():
    """The static path is cached for a long time: the texts, icons and styles must be requested with the
    `?v=` of the panel file, or a browser could mix new code with old texts after an update."""
    assert "new URL(import.meta.url).search" in SOURCE
    for part in ("tariffy-i18n.js", "tariffy-icons.js", "tariffy-style.js"):
        assert f"import(`./{part}${{VERSION}}`)" in SOURCE
        assert (FRONTEND / part).is_file()
    # nothing is imported without the version
    assert not re.search(r"^\s*import\s.*from\s", SOURCE, flags=re.M)
    assert not re.search(r"import\(\"", SOURCE)


@pytest.mark.parametrize("path", list(DATA_PARTS), ids=str)
def test_the_parts_export_what_the_panel_imports(path):
    text = (FRONTEND / path).read_text(encoding="utf-8")
    assert f"export const {DATA_PARTS[path]} =" in text
    assert "customElements.define" not in text


def test_every_tab_module_is_loaded_with_the_version_and_every_module_is_used():
    listed = re.search(r"const MIXINS = \[(.*?)\];", SOURCE).group(1)
    names = set(re.findall(r'"([a-z]+)"', listed))
    assert names == {p.name.removeprefix("tariffy-").removesuffix(".js") for p in MIXINS}
    assert "import(`./tariffy-${name}.js${VERSION}`)" in SOURCE


@pytest.mark.parametrize("path", MIXINS, ids=lambda p: p.name)
def test_a_tab_module_returns_a_class_and_imports_nothing(path):
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^export default \(\{[^}]*\}\) => class \{$", text, flags=re.M)
    assert "customElements.define" not in text
    assert not re.search(r"^\s*import\b", text, flags=re.M)  # helpers come from the panel, with its version


def test_no_method_is_defined_twice():
    """The methods of all modules end up on one element: a name used twice would silently replace the other."""
    seen: dict[str, str] = {}
    for path in [FRONTEND / PANEL_FILE, *MIXINS]:
        for name in re.findall(r"^  (?:async |get |set )?([_a-zA-Z]+)\(.*\) \{$", path.read_text(encoding="utf-8"), flags=re.M):
            key = name
            if key in seen and key not in {"hass", "narrow", "panel"}:
                assert False, f"{name} in {path.name} and {seen[key]}"
            seen[key] = path.name
    assert "is defined twice" in SOURCE  # and the panel refuses it when loading


def _block(language: str) -> str:
    start = TEXTS.index(f"  {language}: {{")
    other = {"en": "de", "de": "en"}[language]
    # the two language blocks follow each other, the second ends at the first "};"
    if TEXTS.index(f"  {other}: {{") > start:
        end = TEXTS.index(f"  {other}: {{")
    else:
        end = TEXTS.index("};", start)
    return TEXTS[start:end]


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
    used = set(re.findall(r'this\._t\("([A-Za-z]+)"', CODE))
    # texts selected through a variable (tabs, tariff rows)
    used |= {"overview", "costs", "contracts", "highTariff", "lowTariff", "sparte"}
    missing = used - _keys(_block("en"))
    assert missing == set(), f"used but not defined: {missing}"


def test_empty_overview_groups_are_not_appended():
    """Issue #50: with only metered or only flat-rate contracts one group is null.
    `appendChild(null)` throws and leaves the whole panel empty, so the overview
    must never append a group directly."""
    assert "wrap.appendChild(group(" not in CODE
    assert "if (section) wrap.appendChild(section)" in CODE


# ------------------------------------------------------------------------------ light and dark design
def _style() -> str:
    return STYLE


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
    assert "rgba(255,255,255" not in STYLE and "rgba(255,255,255" not in CODE
    assert not re.findall(r"#[0-9a-fA-F]{6}\b", body), re.findall(r"#[0-9a-fA-F]{6}\b", body)
    # the code: only the chart palette and the message when a part cannot be loaded have fixed colours
    palette = re.search(r"CHART_COLORS = \[(.*?)\]", SOURCE).group(1)
    stray = [c for c in re.findall(r'"#[0-9a-fA-F]{3,6}"', CODE) if c not in palette]
    assert not stray, stray


def test_the_panel_follows_the_theme_of_home_assistant():
    assert "this._hass.themes.darkMode" in SOURCE
    assert 'this.classList.toggle("light"' in SOURCE
    assert "this._applyTheme();" in SOURCE  # at every update of hass: a change of the theme shows at once


def test_an_own_icon_is_only_drawn_when_it_looks_like_one():
    assert "/^[a-z0-9_-]+:[a-z0-9_-]+$/i" in CODE  # mdi:car, nothing that could be markup
    assert 'customElements.get("ha-icon")' in CODE  # without Home Assistant's icon element: the category icon
