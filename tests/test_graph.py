"""Tests for dependency graph + blast radius."""

from __future__ import annotations

import json
from pathlib import Path

from tfreport import graph as graph_mod
from tfreport import plan as summarize_plan

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


def test_build_graph_from_configuration():
    plan = _load("blast_radius.json")
    g = graph_mod.build_graph(plan)
    assert "azurerm_virtual_network.hub" in g.nodes
    # subnet depends on vnet
    assert "azurerm_virtual_network.hub" in g.upstream_of("azurerm_subnet.app")
    # vnet has subnet downstream
    assert "azurerm_subnet.app" in g.downstream_of("azurerm_virtual_network.hub")


def test_blast_radius_transitive():
    plan = _load("blast_radius.json")
    g = graph_mod.build_graph(plan)
    # vnet -> subnet -> nic (transitive)
    radius = g.blast_radius("azurerm_virtual_network.hub")
    assert "azurerm_subnet.app" in radius
    assert "azurerm_network_interface.web" in radius


def test_build_graph_empty_when_no_configuration():
    plan = {"resource_changes": [{"address": "x.y"}]}
    g = graph_mod.build_graph(plan)
    assert "x.y" in g.nodes
    assert g.blast_radius("x.y") == set()


def test_parse_plan_sets_blast_radius_score():
    plan = _load("blast_radius.json")
    summary = summarize_plan.parse_plan(plan)
    by_addr = {c.address: c for c in summary.changes}
    assert by_addr["azurerm_virtual_network.hub"].blast_radius_score >= 2


def test_render_blast_radius_section():
    plan = _load("blast_radius.json")
    summary = summarize_plan.parse_plan(plan)
    md = summarize_plan.render_markdown(summary)
    assert "## Blast radius" in md
    assert "azurerm_subnet.app" in md
    # Should appear after Executive summary, before Stats.
    assert md.index("## Blast radius") < md.index("## Stats")


def test_no_blast_radius_section_when_no_destructive_with_dependents():
    plan = _load("mixed.json")
    summary = summarize_plan.parse_plan(plan)
    md = summarize_plan.render_markdown(summary)
    # mixed.json has no configuration block, so nothing downstream; section absent.
    assert "## Blast radius" not in md


def test_normalize_ref_filters_locals_and_vars():
    assert graph_mod._normalize_ref("var.region", "") is None
    assert graph_mod._normalize_ref("local.tags", "") is None
    assert graph_mod._normalize_ref("each.value", "") is None
    assert graph_mod._normalize_ref("azurerm_subnet.app.id", "") == "azurerm_subnet.app"
    assert (
        graph_mod._normalize_ref("azurerm_subnet.app.id", "module.net")
        == "module.net.azurerm_subnet.app"
    )
