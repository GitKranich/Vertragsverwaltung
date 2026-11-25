"""The calculation modules are plain Python: importable and usable without Home Assistant."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from custom_components.tariffy import abschlaege, preise, prognose, werte
from custom_components.tariffy.const import (
    CONF_ABSCHLAG,
    CONF_ARBEITSPREIS,
    CONF_GRUNDPREIS,
    CONF_PREISAENDERUNGEN,
)

PACKAGE = Path(__file__).parent.parent / "custom_components" / "tariffy"
MODULES = ("werte", "preise", "prognose", "abschlaege")


@pytest.mark.parametrize("name", MODULES)
def test_module_does_not_need_home_assistant_at_runtime(name):
    tree = ast.parse((PACKAGE / f"{name}.py").read_text(encoding="utf-8"))
    runtime = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and "homeassistant" in (getattr(node, "module", None) or ",".join(a.name for a in node.names))
    ]
    assert runtime == [], f"{name}.py imports Home Assistant at runtime"


@pytest.mark.parametrize("name", MODULES)
def test_module_does_not_import_the_coordinator(name):
    text = (PACKAGE / f"{name}.py").read_text(encoding="utf-8")
    assert "coordinator" not in text.split('"""', 2)[2]  # outside the module docstring


def test_months_and_segments():
    assert werte.plus_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    seg = preise.preis_segmente(
        {CONF_ARBEITSPREIS: 0.30, CONF_GRUNDPREIS: 10.0, CONF_ABSCHLAG: 50.0,
         CONF_PREISAENDERUNGEN: [{"datum": "2026-07-01", "arbeitspreis": 0.35}]},
        date(2026, 1, 1),
    )
    assert len(seg) == 2
    assert preise.segment_am(seg, date(2026, 8, 1))["arbeitspreis"] == pytest.approx(0.35)
    assert preise.segment_am(seg, date(2026, 3, 1))["arbeitspreis"] == pytest.approx(0.30)


def test_standard_profiles_cover_the_year():
    for sparte in ("electricity", "gas"):
        profil = prognose.standardprofil(sparte)
        assert sorted(profil) == list(range(1, 13))
        assert all(value > 0 for value in profil.values())
    gas = prognose.standardprofil("gas")
    assert gas[1] > gas[7]  # winter needs more gas than summer
    assert callable(abschlaege.abschlag_zahlungen)
